"""넓은 바구니 — 일봉 저장소 · 그 해 매출 순위 바구니 · 미국 재무 · 팩터 · 국면 필터.

지키는 약속:
1. 그 날 알 수 없던 것은 쓰지 않는다(바구니는 매출 공개 뒤부터, 재무는 처음 제출된 날부터, 장중 봉은 저장 안 함).
2. 받지 못한 것은 비워 둔다(다른 값으로 메우지 않는다).
3. 수백 종목에서도 같은 엔진·같은 규칙으로 돈다.
"""

import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from stock_analysis.quant import backtest
from stock_analysis.quant import strategies as strat
from stock_analysis.quant.basket import Basket, BasketBuilder, pick_ticker
from stock_analysis.quant.costs import CostModel
from stock_analysis.quant.data import last_session, regime_days, unfinished_day
from stock_analysis.quant.engine import Engine, from_bars
from stock_analysis.quant.factors import (Book, Company, Points, RollingVol, build_company, momentum_12_1,
                                          rank_pct, sue_events, ttm)
from stock_analysis.quant.pricedb import PriceDB, parse_chart, split_factor_after
from stock_analysis.quant.secfacts import FactsDB, pack, parse_concept, unpack
from stock_analysis.quant.sizing import Plan, RiskRules

FREE = CostModel(commission=0.0, sell_tax=0.0, slippage=0.0)
EVERY_DAY = Plan(check_days=(0, 1, 2, 3, 4), min_hold=0)


def weekdays(n, start=date(2020, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def chart(days, closes, splits=(), offset=-14400, opens=None):
    """야후 차트 응답 모양. 시각은 거래소 시각 9:30 쯤(UTC 로 바꿔 넣는다)."""
    stamps = [int(datetime(d.year, d.month, d.day, 13, 30, tzinfo=timezone.utc).timestamp()) for d in days]
    opens = opens or closes
    payload = {"meta": {"gmtoffset": offset}, "timestamp": stamps,
               "indicators": {"quote": [{"open": list(opens), "high": [c * 1.01 for c in closes],
                                         "low": [c * 0.99 for c in closes], "close": list(closes),
                                         "volume": [1000] * len(closes)}]}}
    if splits:
        payload["events"] = {"splits": {
            str(int(datetime(d.year, d.month, d.day, 13, 30, tzinfo=timezone.utc).timestamp())):
                {"date": int(datetime(d.year, d.month, d.day, 13, 30, tzinfo=timezone.utc).timestamp()),
                 "numerator": num, "denominator": den} for d, num, den in splits}}
    return {"chart": {"result": [payload]}}


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body

    def json(self):
        if self.body is None:
            raise ValueError("no body")
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    """주소 → 응답. 부른 주소를 기록한다."""

    def __init__(self, routes=None):
        self.routes, self.calls = dict(routes or {}), []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                return value(url) if callable(value) else value
        return Resp(404)


# --------------------------------------------------------------------------
# 1. 일봉 저장소
# --------------------------------------------------------------------------
def test_chart_parsing_drops_empty_days_and_reads_splits():
    days = weekdays(4)
    payload = chart(days, [10.0, 11.0, 12.0, 13.0], splits=[(days[2], 4, 1)])["chart"]["result"][0]
    payload["indicators"]["quote"][0]["close"][1] = None          # 값이 빈 날은 통째로 뺀다
    rows, splits = parse_chart(payload)
    assert [r[0] for r in rows] == [days[0], days[2], days[3]]
    assert rows[0][4] == 10.0
    assert splits == [(days[2], 4.0)]
    assert split_factor_after(splits, days[1]) == 4.0 and split_factor_after(splits, days[2]) == 1.0


def test_price_store_keeps_ten_years_and_only_tops_up_after(tmp_path):
    days = weekdays(30)
    closes = [100 + k for k in range(30)]
    http = FakeHttp({"range=10y": Resp(200, chart(days[:25], closes[:25])),
                     "range=1mo": Resp(200, chart(days[20:], closes[20:]))})
    db = PriceDB(tmp_path / "p.sqlite", http)
    r = db.update(["AAPL"], "us", today=days[24])
    assert r.fetched == 1 and "range=10y" in http.calls[-1]
    assert db.update(["AAPL"], "us", today=days[24]).fresh == 1      # 방금 받은 것은 다시 묻지 않는다
    # 마지막 거래일 봉이 이미 있으면 묻지 않는다
    assert db.update(["AAPL"], "us", today=days[24], expected=days[24]).fresh == 1
    db.db.execute("UPDATE meta SET fetched=0")
    r = db.update(["AAPL"], "us", today=days[29], expected=days[29])
    assert r.fetched == 1 and "range=1mo" in http.calls[-1]           # 겹치는 날 종가가 같으니 이어 붙인다
    bars = db.load(["AAPL"])["AAPL"]
    assert len(bars) == 30 and bars.days[-1] == days[29].isoformat()
    bar = bars[5]
    assert bar.close == closes[5] and bar.day == days[5] and bar.open == closes[5]


def test_a_split_in_between_makes_the_store_fetch_everything_again(tmp_path):
    days = weekdays(30)
    old = [100.0] * 25
    after = [25.0] * 30                                                 # 4:1 분할 — 야후가 옛 가격을 모두 고쳐 보낸다
    calls = {"n": 0}

    def full(url):
        calls["n"] += 1
        return Resp(200, chart(days[:25], old) if calls["n"] == 1 else
                    chart(days, after, splits=[(days[26], 4, 1)]))

    http = FakeHttp({"range=10y": full, "range=1mo": Resp(200, chart(days[20:], after[20:]))})
    db = PriceDB(tmp_path / "p.sqlite", http)
    db.update(["NVDA"], "us", today=days[24])
    db.db.execute("UPDATE meta SET fetched=0")
    r = db.update(["NVDA"], "us", today=days[29], expected=days[29])
    assert r.refetched == 1
    bars = db.load(["NVDA"])["NVDA"]
    assert set(bars.closes) == {25.0}                                   # 섞이지 않았다(100 이 남아 있지 않다)
    assert db.splits("NVDA") == [(days[26], 4.0)]


def test_unfinished_bar_is_never_stored_and_failures_stay_empty(tmp_path):
    days = weekdays(5)
    http = FakeHttp({"AAPL": Resp(200, chart(days, [1.0, 2.0, 3.0, 4.0, 5.0]))})
    db = PriceDB(tmp_path / "p.sqlite", http)
    db.update(["AAPL", "ZZZZ"], "us", today=days[4], unfinished=days[4])
    bars = db.load(["AAPL", "ZZZZ"])
    assert bars["AAPL"].days[-1] == days[3].isoformat()                # 장중 오늘 봉은 저장하지 않는다
    assert "ZZZZ" not in bars                                           # 못 받은 종목은 비운다
    assert db.meta("ZZZZ")["failed"] > 0
    assert db.update(["ZZZZ"], "us", today=days[4]).fresh == 1          # 하루 안에는 다시 묻지 않는다


def test_korean_codes_try_kospi_then_kosdaq_and_remember(tmp_path):
    days = weekdays(5)
    http = FakeHttp({"035720.KQ": Resp(200, chart(days, [1.0] * 5, offset=32400))})
    db = PriceDB(tmp_path / "p.sqlite", http)
    db.update(["035720"], "kr", today=days[4])
    assert any("035720.KS" in u for u in http.calls) and db.meta("035720")["symbol"] == "035720.KQ"


def test_yahoo_rate_limit_stops_the_batch_instead_of_hammering(tmp_path):
    http = FakeHttp({"chart": Resp(429)})
    db = PriceDB(tmp_path / "p.sqlite", http)
    r = db.update([f"T{k}" for k in range(40)], "us", today=date(2026, 1, 5))
    assert r.limited and len(http.calls) < 40


def test_market_clock_marks_today_unfinished_until_after_the_close():
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    during = datetime(2026, 10, 21, 11, 0, tzinfo=ny)                  # 수요일 장중
    after = datetime(2026, 10, 21, 16, 30, tzinfo=ny)
    saturday = datetime(2026, 10, 24, 12, 0, tzinfo=ny)
    assert unfinished_day("us", during) == date(2026, 10, 21)
    assert last_session("us", during) == date(2026, 10, 20)
    assert unfinished_day("us", after) is None and last_session("us", after) == date(2026, 10, 21)
    assert last_session("us", saturday) == date(2026, 10, 23)


# --------------------------------------------------------------------------
# 2. 그 해 매출 순위 바구니
# --------------------------------------------------------------------------
def test_basket_uses_a_years_ranking_only_after_it_was_public():
    us = Basket("us", 2, years={2019: ["AAA", "BBB"], 2020: ["CCC", "AAA"]})
    assert us.members(date(2020, 9, 30)) == []                         # 2019 순위는 2020년 10월 1일부터
    assert us.members(date(2020, 10, 1)) == ["AAA", "BBB"]
    assert us.members(date(2021, 10, 1)) == ["CCC", "AAA"]
    assert us.all_tickers() == ["CCC", "AAA", "BBB"]
    kr = Basket("kr", 2, years={2019: ["005930"]})
    assert kr.members(date(2020, 4, 14)) == [] and kr.members(date(2020, 4, 15)) == ["005930"]


def test_one_ticker_per_company_prefers_the_common_affordable_class():
    assert pick_ticker(["BRK-A", "BRK-B"]) == "BRK-B"
    assert pick_ticker(["GOOGL", "GOOG"]) == "GOOG"
    assert pick_ticker(["BAC-PB", "BAC"]) == "BAC"


def frames_routes(data, fail=None):
    """{(항목, 'CY' 뒤 기간): 행} 으로 frames 흉내. fail 이 주소에 있으면 500."""
    def route(url):
        if fail and fail in url:
            return Resp(500)
        concept = url.split("/USD/")[0].rsplit("/", 1)[-1]
        period = url.rsplit("/CY", 1)[-1].removesuffix(".json")
        rows = data.get((concept, period))
        return Resp(200, {"data": rows}) if rows is not None else Resp(404)
    return {"frames": route}


def fake_edgar(mapping, filler=60):
    """티커 목록 — 진짜 SEC 목록처럼 수천 개는 아니어도, 받다 끊긴 목록(50개 미만)으로 보이지 않게 채운다."""
    mapping = dict(mapping) | {f"Z{k:03d}": (f"{900000 + k:010d}", f"Filler {k}") for k in range(filler)}
    return SimpleNamespace(ticker_map=lambda: mapping, fund_map=lambda: {"SPY": ("884394", "")})


def small_rows(n=60, start=100):
    return [{"cik": start + k, "val": 1, "entityName": f"Tiny {k}"} for k in range(n)]


def test_us_basket_ranks_by_that_years_revenue_and_skips_years_it_could_not_get(tmp_path):
    rows = [{"cik": 1, "val": 300}, {"cik": 2, "val": 200}, {"cik": 3, "val": 100}] + small_rows()
    data = {("Revenues", "2024"): rows, ("Revenues", "2023"): rows[:1] + rows[2:]}
    http = FakeHttp(frames_routes(data, fail="CY2022"))
    edgar = fake_edgar({"BIG": ("0000000001", "Big Co"), "MID": ("0000000002", "Mid Co"),
                        "SMALL": ("0000000003", "Small Co"), "SPY": ("0000884394", "SPDR")})
    builder = BasketBuilder(tmp_path, http, edgar)
    basket = builder.ensure("us", today=date(2025, 11, 1))
    assert basket.years[2024][:3] == ["BIG", "MID", "SMALL"]
    assert basket.years[2023][:2] == ["BIG", "SMALL"]                 # 그 해에는 MID 매출이 없었다
    assert 2022 not in basket.years                                    # 받다 실패한 해는 비운다(다음에 다시)
    assert basket.ciks["MID"] == [2] and basket.names["BIG"] == "Big Co"
    assert builder.load("us").years[2024][:3] == ["BIG", "MID", "SMALL"]
    assert any("CY2013" in u for u in http.calls)                     # 맨 앞 해의 '앞 해' 도 견주려고 받는다
    calls = len(http.calls)
    builder.ensure("us", today=date(2025, 11, 2))                      # 받은 해는 다시 받지 않는다(실패한 해만)
    assert any("CY2022" in u for u in http.calls[calls:])
    assert not any("CY2023" in u or "CY2024" in u for u in http.calls[calls:])


def test_unit_errors_in_sec_revenue_are_left_out_not_corrected():
    from stock_analysis.quant.basket import suspicious

    def check(rev, year, assets=None, market="us"):
        return suspicious(market, year, "c", {y: {"c": v} for y, v in rev.items()},
                          {y: {"c": v} for y, v in (assets or {}).items()})

    assert "어느 기업보다도" in check({2023: 3.07e13}, 2023)                      # 실제 사례: 30조 달러
    assert "배" in check({2014: 2.3e9, 2015: 4.19e10, 2016: 7.3e8, 2017: 1.6e9}, 2015)   # 그 해만 앞뒤의 열 몇 배
    assert check({2020: 8.0e8, 2021: 1.85e10, 2022: 1.93e10}, 2021) is None        # 백신 회사처럼 진짜로 큰 해
    assert check({2020: 8.0e8, 2021: 1.85e10}, 2021) is None                       # 최근 해 23배 — 한쪽만이면 30배부터
    assert check({2024: 3.2e7, 2025: 3.2e10}, 2025) is not None                    # 최근 해 1000배
    assert check({2022: 3e11, 2023: 3e8, 2024: 3e11}, 2024) is None                # 옆 해가 거꾸로 틀려도 버틴다
    assert "총자산" in check({2023: 1.45e11}, 2023, {2023: 2.0e8})                 # 해마다 1000배로 적는 회사
    assert check({2023: 6.0e9}, 2023, {2022: 1e8, 2023: 3.5e8}) is None            # 귀금속 도매처럼 자산 대비 큰 매출
    assert check({2023: 2.76e10}, 2023, {2022: 9e9, 2023: 8.7e3, 2024: 1e10}) is None   # 자산 값이 틀린 해는 앞뒤 해로
    assert check({2024: 300e12}, 2024, market="kr") is None                        # 원화는 원화 기준


def test_basket_leaves_out_unit_errors_and_says_which():
    rows = {y: [{"cik": 1, "val": 6.5e11, "entityName": "Walmart Inc."},
                {"cik": 7, "val": 3.0e7, "entityName": "Rectitude Holdings Ltd"}] + small_rows() for y in (2022, 2024)}
    rows[2023] = [{"cik": 1, "val": 6.5e11}, {"cik": 7, "val": 3.07e13, "entityName": "Rectitude Holdings Ltd"}] + \
        small_rows()
    data = {("Revenues", str(y)): r for y, r in rows.items()}
    edgar = fake_edgar({"WMT": ("0000000001", "Walmart Inc."), "RECT": ("0000000007", "Rectitude Holdings Ltd")})
    import tempfile
    from pathlib import Path

    basket = BasketBuilder(Path(tempfile.mkdtemp()), FakeHttp(frames_routes(data)), edgar).ensure(
        "us", today=date(2025, 11, 1))
    assert basket.years[2023][0] == "WMT" and "RECT" not in basket.years[2023]
    assert basket.years[2024][:2] == ["WMT", "RECT"]                  # 다른 해의 제대로 된 값은 그대로
    name, value, why = basket.dropped[2023][0]
    assert name == "Rectitude Holdings Ltd" and value == 3.07e13 and "어느 기업보다도" in why


def test_a_company_whose_sec_number_changed_keeps_its_history(tmp_path):
    """지주회사로 바뀌며 CIK 가 새로 생기면 티커는 새 번호를 가리킨다 — 예전 번호의 매출을 이름으로 잇는다."""
    from stock_analysis.quant.basket import name_key

    assert name_key("Exxon Mobil Corporation") == name_key("EXXON MOBIL CORP /NJ/") == "exxon mobil"
    rows = [{"cik": 34088, "val": 3.4e11, "entityName": "Exxon Mobil Corporation"},
            {"cik": 5, "val": 2e11, "entityName": "Other Co"},
            {"cik": 6, "val": 1e11, "entityName": "Twin Inc"}] + small_rows()
    data = {("Revenues", "2024"): rows,
            ("Revenues", "2023"): rows + [{"cik": 2115436, "val": 9e10, "entityName": "Exxon Mobil Corp"}]}
    edgar = fake_edgar({"XOM": ("0002115436", "Exxon Mobil Corp"), "OTH": ("0000000005", "Other Co"),
                        "TWA": ("0000000061", "Twin Inc"), "TWB": ("0000000062", "Twin Inc")})
    basket = BasketBuilder(tmp_path, FakeHttp(frames_routes(data)), edgar).ensure("us", today=date(2025, 11, 1))
    assert basket.years[2024][:2] == ["XOM", "OTH"]
    assert basket.ciks["XOM"] == [2115436, 34088]                    # 재무는 두 번호 모두에서 받는다
    assert basket.years[2023][:2] == ["OTH", "XOM"]                   # 새 번호가 그 해 따로 냈으면 그 값만
    assert "TWA" not in basket.all_tickers() and "TWB" not in basket.all_tickers()   # 이름이 겹치면 잇지 않는다


def test_korean_basket_needs_most_companies_and_keeps_the_filing_date(tmp_path):
    from stock_analysis.dart import Financials

    companies = {f"{k:06d}": (f"C{k}", f"회사{k}") for k in range(100)}

    def many(corps, year, report="11011"):
        if year == 2023:
            return {f"{k:06d}": Financials(values={"revenue": 10.0}) for k in range(10)}   # 10% 만 — 끊긴 것
        return {f"{k:06d}": Financials(values={"revenue": float(k + 1), "operating_income": 1.0,
                                                "assets": 10.0}, rcept_no="20250311000123")
                for k in range(100)}

    dart = SimpleNamespace(ready=True, corp_codes=lambda: companies, many_financials=many)
    basket = BasketBuilder(tmp_path, dart=dart).ensure("kr", today=date(2025, 6, 1))
    assert basket.years[2024][0] == "000099"
    assert 2023 not in basket.years
    assert basket.fin[2024]["000099"]["known"] == "2025-03-11"


# --------------------------------------------------------------------------
# 3. 미국 재무 — 처음 제출된 값, 그 날부터
# --------------------------------------------------------------------------
def concept(entries, unit="USD"):
    return {"units": {unit: entries}}


def q(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def test_facts_store_keeps_only_what_was_received_and_retries_failures(tmp_path):
    good = concept([q("2024-01-01", "2024-03-31", 10, "2024-05-01")])
    http = FakeHttp({"/GrossProfit.json": Resp(200, good), "/NetIncomeLoss.json": Resp(200, good),
                     "/Assets.json": Resp(500)})
    db = FactsDB(tmp_path / "f.sqlite", http)
    assert db.fetch(7) is False                                          # 자산을 받다 실패 — 저장하지 않는다
    assert db.company(7) == {} and db.due(7, date(2024, 6, 1))
    http.routes["/Assets.json"] = Resp(200, concept([{"end": "2024-03-31", "val": 100, "filed": "2024-05-01",
                                                      "form": "10-Q"}]))
    assert db.fetch(7) is True
    got = db.company(7)
    assert got["gross_profit"]["GrossProfit"][0].val == 10
    assert "cogs" not in got                                            # 매출총이익이 있으면 매출원가는 묻지 않는다
    assert not any("CostOfRevenue" in u for u in http.calls[-12:])
    assert not db.due(7, date(2024, 6, 1))                              # 방금 받았다


def test_a_company_that_switched_tags_gets_the_new_tag_too(tmp_path):
    """브로드컴: 자본을 2019년까지는 StockholdersEquity, 그 뒤로는 '비지배지분 포함' 태그로만 낸다."""
    old = concept([{"end": f"{y}-11-03", "val": 20 + y % 10, "filed": f"{y}-12-20", "form": "10-K"}
                   for y in range(2012, 2020)])
    new = concept([{"end": f"{y}-11-03", "val": 30 + y % 10, "filed": f"{y}-12-20", "form": "10-K"}
                   for y in range(2015, 2026)])
    cover = concept([{"end": f"{y}-10-31", "val": 1, "filed": f"{y}-11-05", "form": "10-Q"} for y in range(2012, 2026)],
                    unit="shares")
    http = FakeHttp({"/StockholdersEquity.json": Resp(200, old),
                     "/StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest.json": Resp(200, new),
                     "/EntityCommonStockSharesOutstanding.json": Resp(200, cover)})
    db = FactsDB(tmp_path / "f.sqlite", http)
    assert db.fetch(9, today=date(2026, 1, 15))
    assert any("IncludingPortion" in u for u in http.calls)            # 앞 태그가 2019년에 끊겨 뒤 태그도 받았다
    assert not any("WeightedAverageNumberOfDilutedSharesOutstanding" in u for u in http.calls)   # 표지 숫자로 충분
    company = build_company(db.company(9))
    assert company.equity.at(date(2025, 12, 31))[1] == 35               # 2025년 값은 새 태그에서
    assert company.equity.at(date(2014, 12, 31))[1] == 24               # 그 전은 옛 태그가 채운다


def test_averaged_share_counts_count_a_split_only_if_it_came_after_the_filing():
    """표지에 합계가 없는 회사(클래스주가 여럿)는 분기 가중평균 주식 수를 쓴다. 분기가 끝나고 제출 전에
    분할했으면 보고서가 이미 분할 뒤 숫자로 고쳐 실으므로 그 분할을 다시 곱하지 않는다(구글 2022년 20:1)."""
    from stock_analysis.quant.factors import share_counts

    diluted = parse_concept(concept([q("2022-04-01", "2022-06-30", 13_200, "2022-07-27"),
                                     q("2022-01-01", "2022-03-31", 660, "2022-04-27")], unit="shares"),
                            "WeightedAverageNumberOfDilutedSharesOutstanding")
    equity = Points([(date(2022, 4, 27), date(2022, 3, 31), 250_000.0), (date(2022, 7, 27), date(2022, 6, 30), 255_000.0)])
    shares = Points((v[1], e, v[0]) for e, v in share_counts({"WeightedAverageNumberOfDilutedSharesOutstanding": diluted}).items())
    book = Book("us", {"GOOG": Company(equity=equity, shares=shares)}, splits={"GOOG": [(date(2022, 7, 18), 20.0)]})
    # 7월 27일 보고서(분할 뒤 제출)의 13,200 은 이미 분할 뒤 숫자 — 가격 100 이면 시가총액 1,320,000
    assert book.book_to_market("GOOG", date(2022, 8, 1), price=100.0) == pytest.approx(255_000 / (100 * 13_200))
    # 4월 보고서(분할 전)의 660 은 20배로 맞춘다
    assert book.book_to_market("GOOG", date(2022, 5, 2), price=100.0) == pytest.approx(250_000 / (100 * 660 * 20))


def test_two_sec_numbers_of_one_company_are_read_together():
    from stock_analysis.quant.factors import merge_companies

    early = {"assets": {"Assets": parse_concept(concept([{"end": "2025-12-31", "val": 1, "filed": "2026-02-18",
                                                          "form": "10-K"}]), "Assets")}}
    later = {"assets": {"Assets": parse_concept(concept([{"end": "2026-06-30", "val": 2, "filed": "2026-08-05",
                                                          "form": "10-Q"},
                                                         {"end": "2025-12-31", "val": 1, "filed": "2026-08-05",
                                                          "form": "10-Q"}]), "Assets")}}
    company = build_company(merge_companies([later, early]))
    assert company.assets.at(date(2026, 3, 1)) == (date(2025, 12, 31), 1)   # 예전 번호의 제출일부터 안다
    assert company.assets.at(date(2026, 9, 1)) == (date(2026, 6, 30), 2)


def test_packed_facts_round_trip():
    facts = parse_concept(concept([q("2024-01-01", "2024-03-31", 5.5, "2024-05-01")]), "Revenues")
    again = unpack(pack(facts), "Revenues")
    assert (again[0].start, again[0].end, again[0].val, again[0].filed) == \
        (facts[0].start, facts[0].end, facts[0].val, facts[0].filed)


def quarters_facts(values, first_year=2020, tag="X"):
    """연속 분기(1~3분기는 10-Q, 4분기는 10-K 연간으로) → Fact 목록."""
    from stock_analysis.quant.secfacts import parse_concept

    entries = []
    for k, v in enumerate(values):
        y, quarter = first_year + k // 4, k % 4
        if quarter < 3:
            start = date(y, 1 + 3 * quarter, 1)
            end = date(y, 3 + 3 * quarter, [31, 30, 30][quarter])
            entries.append(q(start.isoformat(), end.isoformat(), v, (end + timedelta(days=40)).isoformat()))
        else:
            year_vals = values[k - 3:k + 1]
            entries.append(q(f"{y}-01-01", f"{y}-12-31", sum(year_vals), f"{y + 1}-02-20", "10-K"))
    return parse_concept(concept(entries), tag)


def test_trailing_sums_and_surprises_use_the_day_the_numbers_were_filed():
    from stock_analysis.quant.factors import quarters

    known = quarters({"X": quarters_facts([10, 10, 10, 10, 12, 12, 12, 12])})
    points = ttm(known)
    assert points[0][1] == date(2020, 12, 31) and points[0][2] == pytest.approx(40)
    assert points[0][0] == date(2021, 2, 20)                             # 4분기는 10-K 제출일에 알게 된다
    # SUE: 13분기 — 이번 분기 차이 ÷ 앞선 8번 차이의 표준편차
    values = [10, 11, 12, 13, 11, 13, 12, 15, 13, 13, 15, 16, 30]
    events = sue_events(quarters({"X": quarters_facts(values)}))
    diffs = [values[j] - values[j - 4] for j in range(4, 12)]
    expected = (values[12] - values[8]) / statistics.stdev(diffs)
    assert events and events[-1][2] == pytest.approx(expected)
    assert events[-1][0] == date(2023, 3, 31) + timedelta(days=40)


def test_points_answer_with_the_latest_period_known_on_that_day():
    p = Points([(date(2024, 5, 1), date(2024, 3, 31), 1.0), (date(2024, 8, 1), date(2024, 6, 30), 2.0)])
    assert p.at(date(2024, 4, 30)) is None
    assert p.at(date(2024, 5, 1)) == (date(2024, 3, 31), 1.0)
    assert p.at(date(2024, 9, 1)) == (date(2024, 6, 30), 2.0)
    assert p.at(date(2025, 6, 1), max_age=200) is None                   # 너무 오래된 값은 '지금 값' 이 아니다


def test_book_to_market_puts_old_share_counts_on_todays_split_adjusted_prices():
    shares = Points([(date(2024, 2, 1), date(2024, 1, 20), 2_000.0)])
    equity = Points([(date(2024, 2, 1), date(2023, 12, 31), 10_000.0)])
    book = Book("us", {"NV": Company(equity=equity, shares=shares)},
                splits={"NV": [(date(2024, 6, 10), 10.0)]})
    # 6월 분할 뒤 받은 가격은 옛날 것도 10분의 1로 고쳐져 있다. 주식 수를 10배로 맞춰야 시가총액이 맞다.
    bm = book.book_to_market("NV", date(2024, 3, 1), price=5.0)
    assert bm == pytest.approx(10_000 / (5.0 * 2_000 * 10))
    assert book.book_to_market("NV", date(2023, 12, 1), price=5.0) is None   # 그 날엔 몰랐다


def test_gross_profit_falls_back_to_revenue_minus_cost_and_banks_stay_empty():
    revenue = quarters_facts([100] * 8, tag="Revenues")
    cost = quarters_facts([60] * 8, tag="CostOfRevenue")
    assets = parse_concept(concept([{"end": "2021-12-31", "val": 400, "filed": "2022-02-20", "form": "10-K"}]),
                           "Assets")
    company = build_company({"revenue": {"Revenues": revenue}, "cogs": {"CostOfRevenue": cost},
                             "assets": {"Assets": assets}})
    book = Book("us", {"A": company, "BANK": build_company({"assets": {"Assets": assets}})})
    assert book.quality("A", date(2022, 3, 1)) == pytest.approx(160 / 400)
    assert book.quality("BANK", date(2022, 3, 1)) is None


def test_korean_quality_waits_for_the_filing_date():
    book = Book("kr", kr_fin={2024: {"005930": {"operating_income": 20.0, "assets": 100.0, "known": "2025-03-14"}}})
    assert book.quality("005930", date(2025, 3, 13)) is None
    assert book.quality("005930", date(2025, 3, 14)) == pytest.approx(0.2)
    assert book.book_to_market("005930", date(2025, 6, 1), 1000.0) is None    # 한국 가치는 아직 없다


def test_price_factors_and_ranks():
    closes = [100.0 * (1.001 ** k) for k in range(300)]
    assert momentum_12_1(closes, 299) == pytest.approx(closes[278] / closes[47] - 1)
    assert momentum_12_1(closes, 200) is None
    rets = [closes[k] / closes[k - 1] - 1 for k in range(48, 300)]
    noisy = [c * (1.02 if k % 2 else 0.98) for k, c in enumerate(closes)]
    rets = [noisy[k] / noisy[k - 1] - 1 for k in range(48, 300)]
    assert RollingVol().at(noisy, 299) == pytest.approx(statistics.stdev(rets) * math.sqrt(252))
    ranks = rank_pct({"A": 1.0, "B": 2.0, "C": 2.0, "D": None})
    assert ranks == {"A": 0.0, "B": 0.75, "C": 0.75}
    assert rank_pct({"A": 1.0, "B": 2.0}, higher_better=False) == {"B": 0.0, "A": 1.0}


# --------------------------------------------------------------------------
# 4. 엔진 — 바구니 · 국면 · 월 1회 · 새 전략
# --------------------------------------------------------------------------
def candles(closes, start=date(2020, 1, 1)):
    from stock_analysis.prices import Candle

    return [Candle(d, c, c * 1.01, c * 0.99, c, 1e6) for d, c in zip(weekdays(len(closes), start), closes)]


def rising(n, step=0.004, start=100.0):
    return [start * (1 + step) ** k for k in range(n)]


def test_only_stocks_in_that_days_basket_are_bought():
    data = {"IN": candles(rising(260)), "OUT": candles(rising(260, step=0.008))}
    r = backtest.run(strat.STRATEGIES["rotation"], RiskRules(max_positions=1, daily_loss_stop=0), FREE, 10_000,
                     data, plan=EVERY_DAY, members=lambda day: ["IN"])
    bought = {t["ticker"] for t in r["trades"]} | {p["ticker"] for p in r["open"]}
    assert bought == {"IN"}                     # OUT 이 더 많이 올랐어도 바구니 밖이라 사지 않는다


def test_a_holding_that_leaves_the_basket_is_sold_with_the_reason():
    days = weekdays(260)
    cut = days[230]
    data = {"A": candles(rising(260))}
    r = backtest.run(strat.STRATEGIES["rotation"], RiskRules(max_positions=1, daily_loss_stop=0), FREE, 10_000,
                     data, plan=EVERY_DAY, members=lambda day: ["A"] if day < cut else [])
    assert any("바구니에서 빠짐" in t["reason"] for t in r["trades"])


def test_market_filter_pause_and_exit():
    closes = rising(300)
    data = {"A": candles(closes)}
    days = [d.isoformat() for d in weekdays(300)]
    risk_off = {d: k < 250 for k, d in enumerate(days)}               # 250번째 날부터 지수가 200일선 아래
    rules = RiskRules(max_positions=1, daily_loss_stop=0)
    held = backtest.run(strat.STRATEGIES["rotation"], rules, FREE, 10_000, data, plan=EVERY_DAY)
    paused = backtest.run(strat.STRATEGIES["rotation"], rules, FREE, 10_000, data, regime=risk_off,
                          plan=Plan(check_days=(0, 1, 2, 3, 4), min_hold=0, market_filter="pause"))
    exited = backtest.run(strat.STRATEGIES["rotation"], rules, FREE, 10_000, data, regime=risk_off,
                          plan=Plan(check_days=(0, 1, 2, 3, 4), min_hold=0, market_filter="exit"))
    assert held["open"]
    assert paused["open"]                                              # 이미 산 것은 각자 규칙대로 든다
    assert not exited["open"] and any("현금으로 쉼" in t["reason"] for t in exited["trades"])
    never = {d: False for d in days}
    blocked = backtest.run(strat.STRATEGIES["rotation"], rules, FREE, 10_000, data, regime=never,
                           plan=Plan(check_days=(0, 1, 2, 3, 4), min_hold=0, market_filter="pause"))
    assert not blocked["trades"] and not blocked["open"] and blocked["regime_blocked"] > 0
    assert "200일선 아래라 사지 않은 신호" in " ".join(blocked["warnings"])


def test_regime_is_close_above_its_200_day_average():
    from stock_analysis.quant.pricedb import Bars

    closes = [100.0] * 200 + [90.0, 110.0]
    days = [d.isoformat() for d in weekdays(202)]
    bars = Bars(days, closes, closes, closes, closes)
    regime = regime_days(bars)
    assert days[198] not in regime                                     # 200일이 안 되면 판단하지 않는다
    assert regime[days[199]] is False                                  # 같으면 '위' 가 아니다
    assert regime[days[200]] is False and regime[days[201]] is True


def test_factor_strategy_reranks_once_a_month_and_prefers_quality():
    data = {t: candles(rising(330, step=0.003)) for t in ("GOOD", "BAD", "MID")}
    book = SimpleNamespace(market="kr", quality=lambda t, day: {"GOOD": 0.3, "BAD": 0.01, "MID": 0.1}[t],
                           book_to_market=lambda *a: None)
    r = backtest.run(strat.STRATEGIES["factor"], RiskRules(max_positions=1, daily_loss_stop=0), FREE, 10_000,
                     data, plan=EVERY_DAY, book=book)
    bought = {t["ticker"] for t in r["trades"]} | {p["ticker"] for p in r["open"]}
    assert bought == {"GOOD"}                   # 가격 요인이 같으면 수익성이 높은 쪽
    eng = Engine(strat.STRATEGIES["factor"], RiskRules(), FREE, 10_000, plan=EVERY_DAY)
    assert strat.STRATEGIES["factor"].rebalance == "month" and eng.rebalanced_week is None


def test_earnings_drift_buys_within_a_week_of_the_filing_and_holds_sixty_days():
    days = weekdays(200)
    filed = days[100]
    book = SimpleNamespace(recent_sue=lambda t, day, within=7: 2.5 if t == "EPS" and 0 <= (day - filed).days <= 7
                           else None)
    data = {"EPS": candles(rising(200, step=0.001)), "FLAT": candles([100.0] * 200)}
    r = backtest.run(strat.STRATEGIES["pead"], RiskRules(max_positions=2, daily_loss_stop=0), FREE, 10_000,
                     data, plan=EVERY_DAY, book=book)
    trades = [t for t in r["trades"] if t["ticker"] == "EPS"]
    assert {t["ticker"] for t in r["trades"]} | {p["ticker"] for p in r["open"]} == {"EPS"}
    assert trades and trades[0]["entry_day"] == days[101].isoformat()   # 다음 날 시가
    assert "60거래일" in trades[0]["reason"]


def test_wide_baskets_run_on_compact_bars_and_compare_with_the_index():
    from stock_analysis.quant.pricedb import Bars
    from array import array

    def bars(closes):
        days = [d.isoformat() for d in weekdays(len(closes))]
        a = array("d", closes)
        return Bars(days, a, array("d", (c * 1.01 for c in closes)), array("d", (c * 0.99 for c in closes)), a)

    prepared = from_bars({f"T{k}": bars(rising(300, step=0.001 * (k + 1))) for k in range(30)})
    index = ("S&P 500 (SPY) 그냥 보유", from_bars({"SPY": bars(rising(300, step=0.002))}))
    r = backtest.run(strat.STRATEGIES["rotation"], RiskRules(max_positions=3, daily_loss_stop=0), FREE, 10_000,
                     None, plan=Plan(universe="us500"), prepared=prepared, index=index,
                     basket_note="2016~2025년 매출 상위 500 중 지금 시세가 있는 30종목")
    assert r["bench_name"] == "S&P 500 (SPY) 그냥 보유" and r["bench"]["cagr"] > 0
    text = " ".join(r["warnings"])
    assert "그 해 매출 순위" in text and "생존 편향" in text
    assert json.dumps(r)                                              # 그대로 저장할 수 있다
