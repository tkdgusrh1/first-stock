"""후보 목록을 SEC 자료에서 만드는 부분.

여기가 중요한 이유: **무엇이 후보에 들어가느냐가 곧 무엇을 추천받느냐다.**
목록에 없는 회사는 아무리 좋아도 영영 화면에 안 나온다. 그래서 이 목록은
누군가의 판단이 아니라 SEC 가 공개한 매출 순위에서 나와야 하고, 못 받으면
**비워 둬야** 한다 — 대신 쓸 목록을 지어내면 안 된다.
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analysis.universe import (  # noqa: E402
    FRAMES_URL,
    REVENUE_CONCEPTS,
    Universe,
    UniverseBuilder,
)

TODAY = date(2026, 9, 2)


class FakeEdgar:
    """SEC 티커 목록. {TICKER: (10자리 CIK, 회사명)}

    실제 SEC 목록에는 매출을 신고한 기업 대부분이 들어 있다. 그래서 여기서도
    자리를 채우는 기업(9000번대)에 전부 티커를 붙여둔다. 티커가 없는 경우는
    그걸 확인하는 테스트에서 따로 만든다.
    """

    NAMED = {
        "AAPL": ("0000320193", "Apple Inc."),
        "MSFT": ("0000789019", "MICROSOFT CORP"),
        "WMT": ("0000104169", "Walmart Inc."),
        "SPY": ("0000884394", "SPDR S&P 500 ETF"),
    }

    def __init__(self, tickers=None, funds=None, filler=200):
        self.tickers = dict(self.NAMED)
        for n in range(filler):
            self.tickers[f"T{9000 + n}"] = (f"{9000 + n:010d}", f"회사{9000 + n}")
        if tickers is not None:
            self.tickers = tickers
        self.funds = funds if funds is not None else {"SPY": ("0000884394", "")}

    def ticker_map(self):
        return self.tickers

    def fund_map(self):
        return self.funds


class FakeHttp:
    """SEC frames 응답을 흉내낸다. {(개념, 연도): {CIK: 매출}}"""

    def __init__(self, frames=None, fail=False):
        self.frames = frames or {}
        self.fail = fail
        self.calls: list[str] = []

    def get_json(self, url, **kwargs):
        self.calls.append(url)
        if self.fail:
            raise OSError("SEC 가 막혔습니다")
        for (concept, year), values in self.frames.items():
            if FRAMES_URL.format(concept=concept, period=year) == url:
                return {"data": [{"cik": cik, "entityName": f"회사{cik}", "val": val}
                                 for cik, val in values.items()]}
        raise OSError("404")


def a_frame(count=60, year="2025", concept="Revenues", top=None):
    """SEC 가 준 것처럼 보이는 매출 자료. 기본 CIK 는 아무 숫자나."""
    values = {9000 + n: float(1000 - n) for n in range(count)}
    values.update(top or {})
    return {(concept, year): values}


def builder(tmp_path, http, edgar=None):
    return UniverseBuilder(http, edgar or FakeEdgar(), tmp_path)


# --- 제대로 받았을 때 -------------------------------------------------------
def test_the_biggest_companies_by_revenue_become_the_candidates(tmp_path):
    http = FakeHttp(a_frame(top={320193: 5e11, 789019: 4e11, 104169: 6e11}))
    got = builder(tmp_path, http).build(size=3, today=TODAY)

    assert got.tickers[:3] == ["WMT", "AAPL", "MSFT"]     # 매출 큰 순
    assert got.period == "2025년"
    assert "SEC" in got.source


def test_the_source_is_always_stated(tmp_path):
    """출처를 안 밝히면 이 목록이 어디서 왔는지 알 길이 없다."""
    http = FakeHttp(a_frame(top={320193: 5e11}))
    got = builder(tmp_path, http).build(size=5, today=TODAY)

    text = got.describe()
    assert "SEC" in text and "2025년" in text and "2026-09-02" in text


def test_an_etf_never_gets_into_the_candidates(tmp_path):
    """ETF 는 추천하지 않는다. 목록이 겹쳐 들어오면 여기서 걸러야 한다."""
    http = FakeHttp(a_frame(top={884394: 9e11, 320193: 5e11}))
    got = builder(tmp_path, http).build(size=5, today=TODAY)

    assert "SPY" not in got.tickers
    assert "AAPL" in got.tickers


def test_a_company_without_a_ticker_is_left_out(tmp_path):
    """SEC 에 보고서는 내지만 살 수 없는 회사가 많다 (비상장·ADR 없음)."""
    http = FakeHttp(a_frame(top={999999999: 9e11, 320193: 5e11}))
    got = builder(tmp_path, http).build(size=5, today=TODAY)

    assert "AAPL" in got.tickers
    assert len(got.tickers) < got.total_filers


def test_several_revenue_tags_are_merged(tmp_path):
    """회계 기준이 바뀌면서 매출을 담는 항목이 회사마다 다르다."""
    frames = a_frame(count=60, concept=REVENUE_CONCEPTS[0])
    frames[(REVENUE_CONCEPTS[1], "2025")] = {320193: 5e11}
    got = builder(tmp_path, FakeHttp(frames)).build(size=5, today=TODAY)

    assert got.tickers[0] == "AAPL"


def test_an_older_year_is_tried_when_the_latest_is_empty(tmp_path):
    """연간 자료는 회계연도가 끝나고도 한참 뒤에야 다 모인다."""
    got = builder(tmp_path, FakeHttp(a_frame(year="2024"))).build(size=5, today=TODAY)
    assert got.period == "2024년"


# --- 못 받았을 때 -----------------------------------------------------------
def test_nothing_is_made_up_when_sec_is_unreachable(tmp_path):
    """가장 중요한 규칙. 대신 쓸 목록을 지어내면 추천 전체가 거짓이 된다."""
    got = builder(tmp_path, FakeHttp(fail=True)).build(size=5, today=TODAY)

    assert got.empty
    assert got.tickers == []
    assert "받지 못했습니다" in got.describe()


def test_a_handful_of_companies_is_not_treated_as_the_whole_market(tmp_path):
    """몇 개만 돌아왔다면 제대로 받은 게 아니다. 그걸 '매출 상위' 라 부르면 안 된다."""
    got = builder(tmp_path, FakeHttp(a_frame(count=5))).build(size=5, today=TODAY)
    assert got.empty


def test_broken_rows_are_skipped_instead_of_crashing(tmp_path):
    class Broken(FakeHttp):
        def get_json(self, url, **kwargs):
            return {"data": [{"cik": 320193, "val": 5e11}, {"cik": "없음"}, None, {}]}

    got = Broken().get_json("x")
    assert got["data"][0]["cik"] == 320193      # 흉내가 맞는지 확인

    universe = builder(tmp_path, Broken()).build(size=5, today=TODAY)
    assert universe.empty                        # 한 개뿐이라 쓸 수 없다고 본다


# --- 저장하고 다시 쓰기 -----------------------------------------------------
def test_the_list_is_kept_so_we_do_not_ask_sec_every_time(tmp_path):
    http = FakeHttp(a_frame(top={320193: 5e11}))
    first = builder(tmp_path, http).ensure(size=5, today=TODAY)
    calls = len(http.calls)

    again = UniverseBuilder(http, FakeEdgar(), tmp_path).ensure(size=5, today=TODAY)

    assert again.tickers == first.tickers
    assert len(http.calls) == calls              # 다시 묻지 않았다


def test_an_old_list_is_used_when_a_refresh_fails(tmp_path):
    """새로 못 받았다고 있던 목록까지 버리면 추천이 통째로 멈춘다."""
    builder(tmp_path, FakeHttp(a_frame(top={320193: 5e11}))).ensure(size=5, today=TODAY)
    (tmp_path / "universe.json").touch()         # 오래된 것으로 만들기 어려우니 직접
    import os
    import time
    old = time.time() - 60 * 24 * 3600
    os.utime(tmp_path / "universe.json", (old, old))

    got = UniverseBuilder(FakeHttp(fail=True), FakeEdgar(), tmp_path).ensure(size=5, today=TODAY)

    assert "AAPL" in got.tickers


def test_a_broken_saved_file_does_not_crash(tmp_path):
    (tmp_path / "universe.json").write_text("{망가짐", encoding="utf-8")
    assert UniverseBuilder(FakeHttp(), FakeEdgar(), tmp_path).cached().empty


def test_what_was_saved_can_be_read_back(tmp_path):
    builder(tmp_path, FakeHttp(a_frame(top={320193: 5e11}))).ensure(size=5, today=TODAY)
    saved = json.loads((tmp_path / "universe.json").read_text(encoding="utf-8"))

    assert "AAPL" in saved["tickers"]
    assert saved["fetched"] == "2026-09-02"
    assert saved["total_filers"] > 0


def test_an_empty_universe_says_so_rather_than_pretending():
    assert Universe().empty
    assert "받지 못했습니다" in Universe().describe()


def test_a_failed_fetch_is_not_retried_every_few_minutes(tmp_path):
    """이 조회는 항목마다 수 MB 다. SEC 가 막힌 날 15분마다 열두 번씩
    시도하면 그것만으로 하루를 다 쓰고, 남의 서버에도 할 짓이 아니다.
    """
    http = FakeHttp(fail=True)
    build = builder(tmp_path, http)

    build.ensure(size=5, today=TODAY)
    tried = len(http.calls)
    build.ensure(size=5, today=TODAY)

    assert tried > 0
    assert len(http.calls) == tried          # 두 번째는 묻지 않았다


def test_a_success_clears_the_waiting_period(tmp_path):
    build = builder(tmp_path, FakeHttp(fail=True))
    build.ensure(size=5, today=TODAY)

    build.http = FakeHttp(a_frame(top={320193: 5e11}))
    build._failed_at = 0.0                   # 기다림이 끝난 상황
    got = build.ensure(size=5, today=TODAY)

    assert "AAPL" in got.tickers
    assert build._failed_at == 0.0


# --- 한국 후보 목록 -----------------------------------------------------------
class _FakeDart:
    """DART 대신. 무엇을 물어봤는지 기록한다."""

    ready = True

    def __init__(self, companies, revenues, fail_year=None):
        self.companies = companies
        self.revenues = revenues            # {종목코드: 매출}
        self.fail_year = fail_year
        self.years = []

    def corp_codes(self):
        return self.companies

    def many_financials(self, corp_codes, year, report="11011", chunk=100):
        from stock_analysis.dart import Financials

        self.years.append(year)
        if year == self.fail_year:
            return {}
        return {code: Financials(values={"revenue": value})
                for code, value in self.revenues.items()}


def _companies(count):
    return {f"{n:06d}": (f"{n:08d}", f"회사{n}") for n in range(1, count + 1)}


def _revenues(count):
    """매출이 클수록 앞 번호. 순위가 제대로 뒤집히는지 보려고."""
    return {f"{n:06d}": float(count - n + 1) * 1e9 for n in range(1, count + 1)}


def test_korean_candidates_are_ranked_by_revenue(tmp_path):
    from datetime import date

    from stock_analysis.universe import KoreanUniverseBuilder

    dart = _FakeDart(_companies(120), _revenues(120))
    found = KoreanUniverseBuilder(dart, tmp_path).build(size=5, today=date(2026, 9, 4))

    assert found.tickers == ["000001", "000002", "000003", "000004", "000005"]
    assert found.total_filers == 120
    assert found.period == "2025"
    assert "DART" in found.describe()
    assert "상장사 120곳" in found.describe()


def test_it_falls_back_to_the_year_before(tmp_path):
    """작년 사업보고서가 아직 다 안 올라온 시기가 있다."""
    from datetime import date

    from stock_analysis.universe import KoreanUniverseBuilder

    dart = _FakeDart(_companies(60), _revenues(60), fail_year=2025)
    found = KoreanUniverseBuilder(dart, tmp_path).build(size=5, today=date(2026, 9, 4))

    assert dart.years == [2025, 2024]
    assert found.period == "2024"
    assert found.tickers


def test_too_few_companies_means_no_list(tmp_path):
    """조금 받은 걸 전체인 양 쓰면 엉뚱한 순위가 된다."""
    from datetime import date

    from stock_analysis.universe import KoreanUniverseBuilder

    dart = _FakeDart(_companies(10), _revenues(10))
    found = KoreanUniverseBuilder(dart, tmp_path).build(size=5, today=date(2026, 9, 4))

    assert found.empty
    assert "받지 못했습니다" in found.describe()


def test_without_a_key_nothing_is_invented(tmp_path):
    from stock_analysis.universe import KoreanUniverseBuilder

    class _NoKey:
        ready = False

    assert KoreanUniverseBuilder(_NoKey(), tmp_path).build().empty


def test_a_saved_korean_list_comes_back(tmp_path):
    from datetime import date

    from stock_analysis.universe import KoreanUniverseBuilder

    dart = _FakeDart(_companies(120), _revenues(120))
    builder = KoreanUniverseBuilder(dart, tmp_path)
    builder.ensure(size=3, today=date(2026, 9, 4))

    again = KoreanUniverseBuilder(dart, tmp_path)
    assert again.cached().tickers == ["000001", "000002", "000003"]
    assert again.cached().source == "dart"


def test_the_two_markets_do_not_share_a_file(tmp_path):
    """한쪽을 받아오면서 다른 쪽 목록을 덮어쓰면 안 된다."""
    from stock_analysis.universe import KoreanUniverseBuilder, UniverseBuilder

    us = UniverseBuilder(None, None, tmp_path)
    kr = KoreanUniverseBuilder(_FakeDart({}, {}), tmp_path)

    assert us.path != kr.path
