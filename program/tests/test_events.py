"""공시 이벤트 스터디 — 미래 정보 없이 '알고 나서 산' 수익률을 재는가."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from stock_analysis import events
from stock_analysis.dart import DartClient
from stock_analysis.dart import Filing as DartFiling
from stock_analysis.edgar import EdgarClient, Filing
from stock_analysis.events import Event, entry_index, measure, summarize
from stock_analysis.prices import Candle


def bars_from(start: date, opens_closes: list[tuple[float, float]]) -> list[Candle]:
    """평일만 이어 붙인 일봉."""
    out, day = [], start
    for o, c in opens_closes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append(Candle(day, o, max(o, c), min(o, c), c))
        day += timedelta(days=1)
    return out


MON = date(2025, 3, 3)          # 월요일
WEEK = bars_from(MON, [(100, 101), (102, 104), (105, 103), (103, 106), (106, 110),
                       (110, 108), (108, 111)])
DAYS = [b.day for b in WEEK]


def us(accepted: str | None, filed: date = MON) -> Event:
    moment = datetime.fromisoformat(accepted) if accepted else None
    return Event("us", "AAA", "8-K 2.02", "실적 발표", filed, moment)


# --- 언제 산 것으로 치나 ---------------------------------------------------
def test_filed_before_cutoff_buys_same_day_open():
    assert DAYS[entry_index(DAYS, us("2025-03-04 07:45:00"))] == date(2025, 3, 4)


@pytest.mark.parametrize("clock", ["09:00:00", "09:10:00", "12:00:00", "16:05:00"])
def test_filed_at_or_after_cutoff_buys_next_day(clock):
    """장 시작 직전·장중·장 마감 뒤 공시는 그날 시가에 살 수 없었다."""
    assert DAYS[entry_index(DAYS, us(f"2025-03-04 {clock}"))] == date(2025, 3, 5)


def test_weekend_filing_buys_monday():
    assert DAYS[entry_index(DAYS, us("2025-03-08 10:00:00"))] == date(2025, 3, 10)


def test_korean_filing_without_time_always_waits_a_day():
    """시각을 모르면 아침 공시라도 다음 거래일 시가 — 늦게 사는 쪽으로 틀린다."""
    event = Event("kr", "005930", "유상증자 결정", "유상증자 결정", date(2025, 3, 4), None)
    assert DAYS[entry_index(DAYS, event)] == date(2025, 3, 5)


def test_no_bar_after_filing_means_no_entry():
    assert entry_index(DAYS, us("2025-03-11 17:00:00")) is None


# --- 수익률 --------------------------------------------------------------
def test_returns_run_from_entry_open_to_hth_close():
    bench = bars_from(MON, [(50, 50), (50, 51), (50, 50), (50, 50), (50, 52), (50, 50), (50, 50)])
    o = measure(us("2025-03-03 16:30:00"), WEEK, bench, horizons=(1, 2, 5, 10))
    assert o.entry_day == date(2025, 3, 4) and o.entry_price == 102
    assert o.returns[1] == pytest.approx(104 / 102 - 1)      # 같은 날 종가
    assert o.returns[2] == pytest.approx(103 / 102 - 1)
    assert o.returns[5] == pytest.approx(108 / 102 - 1)
    assert o.returns[10] is None                             # 아직 기간이 안 지났다 — 0 이 아니다
    assert o.bench[1] == pytest.approx(51 / 50 - 1)
    assert o.abnormal(1) == pytest.approx((104 / 102 - 1) - (51 / 50 - 1))
    assert o.abnormal(10) is None


def test_missing_benchmark_day_leaves_abnormal_empty():
    bench = [b for b in bars_from(MON, [(50, 50)] * 7) if b.day != date(2025, 3, 4)]
    o = measure(us("2025-03-03 16:30:00"), WEEK, bench, horizons=(1,))
    assert o.returns[1] is not None
    assert o.bench[1] is None and o.abnormal(1) is None


def test_event_after_last_bar_is_explained():
    o = measure(us("2025-03-11 17:00:00"), WEEK, WEEK)
    assert o.entry_day is None and "거래일" in o.note


def test_settled_drops_todays_moving_bar():
    assert events.settled(WEEK, date(2025, 3, 11))[-1].day == date(2025, 3, 10)


# --- 공시 → 사건 ---------------------------------------------------------
def sec(form: str, items: list[str], accepted="2025-03-04T16:05:12.000Z") -> Filing:
    return Filing("0000000001", "AAA", "Co", form, "0000000001-25-000001", "2025-03-04",
                  accepted, None, "a.htm", items)


def test_8k_items_become_separate_events_without_attachments():
    found = events.us_events([sec("8-K", ["2.02", "7.01", "9.01"]), sec("8-K/A", ["2.02"]),
                              sec("10-Q", [])])
    assert [e.kind for e in found] == ["8-K 2.02", "8-K 7.01"]
    # 'Z' 를 UTC 로 읽지 않는다. 미 동부 시각 그대로, 시간대 없이 둔다
    assert found[0].accepted == datetime(2025, 3, 4, 16, 5, 12)  # noqa: DTZ001


def test_dart_corrections_and_unknown_reports_are_skipped():
    filings = [
        DartFiling("1", "주요사항보고서(유상증자결정)", day=date(2025, 3, 4)),
        DartFiling("2", "[기재정정]주요사항보고서(유상증자결정)", day=date(2025, 3, 5)),
        DartFiling("3", "기업설명회(IR)개최", day=date(2025, 3, 5)),
        DartFiling("4", "단일판매ㆍ공급계약체결", day=None),
    ]
    found = events.kr_events(filings, "005930")
    assert [(e.kind, e.filed) for e in found] == [("유상증자 결정", date(2025, 3, 4))]


# --- 묶어서 보기 ---------------------------------------------------------
def outcome(kind: str, filed: date, abn: float) -> events.Outcome:
    o = events.Outcome(Event("us", "AAA", kind, kind, filed, None))
    o.returns, o.bench = {5: abn}, {5: 0.0}
    return o


def test_summary_groups_by_kind_and_flags_small_samples():
    base = date(2020, 1, 1)
    items = [outcome("A", base + timedelta(days=i), v)
             for i, v in enumerate([0.02, 0.04, -0.01, 0.03, 0.02, 0.01, 0.05, -0.02, 0.03, 0.06])]
    items.append(outcome("B", base, 0.10))
    items.append(events.Outcome(Event("us", "AAA", "C", "C", base, None)))   # 빈 결과는 안 센다
    rows = summarize(items, 5)
    assert [r["kind"] for r in rows] == ["A", "B"]
    a = rows[0]
    assert a["n"] == 10 and a["hit"] == pytest.approx(0.8)
    assert a["mean"] == pytest.approx(0.023)
    assert a["early"] == pytest.approx(sum([0.02, 0.04, -0.01, 0.03, 0.02, 0.01, 0.05]) / 7)
    assert a["late"] == pytest.approx(sum([-0.02, 0.03, 0.06]) / 3)
    assert not a["enough"]
    assert rows[1]["t"] is None                               # 한 건으로는 t 를 못 낸다


def test_t_stat_is_none_without_spread():
    assert events.t_stat([0.01, 0.01, 0.01]) is None


def test_csv_keeps_empty_values_empty(tmp_path):
    o = measure(us("2025-03-03 16:30:00"), WEEK, WEEK, horizons=events.HORIZONS)
    path = tmp_path / "e.csv"
    assert events.write_csv([o], path) == 1
    with open(path, encoding="utf-8-sig") as fh:
        row = next(csv.DictReader(fh))
    assert row["entry_day"] == "2025-03-04" and row["accepted_et"] == "2025-03-03 16:30:00"
    assert float(row["r1"]) == pytest.approx(104 / 102 - 1)
    assert row["r20"] == "" and row["abn20"] == ""


# --- 과거 공시 목록 받기 ---------------------------------------------------
def block(rows: list[tuple[str, str]]) -> dict:
    """[(접수번호, 날짜)] → submissions 의 열 묶음 (최신순)."""
    return {
        "accessionNumber": [a for a, _ in rows], "form": ["8-K"] * len(rows),
        "filingDate": [d for _, d in rows], "acceptanceDateTime": [f"{d}T17:00:00.000Z" for _, d in rows],
        "reportDate": [""] * len(rows), "primaryDocument": ["x.htm"] * len(rows),
        "items": ["2.02"] * len(rows),
    }


class SecHttp:
    def __init__(self, pages: dict):
        self.pages, self.asked = pages, []

    def get_json(self, url, **kwargs):
        self.asked.append(url)
        return self.pages[url]


def test_filings_since_reads_only_older_pages_that_overlap(tmp_path):
    main = {"name": "Co", "filings": {
        "recent": block([("a-3", "2024-06-01"), ("a-2", "2023-02-01")]),
        "files": [
            {"name": "CIK1-submissions-001.json", "filingFrom": "2021-01-01", "filingTo": "2023-01-31"},
            {"name": "CIK1-submissions-002.json", "filingFrom": "2010-01-01", "filingTo": "2020-12-31"},
        ]}}
    older = block([("a-2", "2023-01-31"), ("a-1", "2022-05-01"), ("a-0", "2021-03-01")])
    http = SecHttp({"https://data.sec.gov/submissions/CIK0000000001.json": main,
                    "https://data.sec.gov/submissions/CIK1-submissions-001.json": older})
    client = EdgarClient(http, cache_dir=tmp_path)
    found = client.filings_since("0000000001", "AAA", ["8-K"], date(2022, 1, 1))
    assert [f.accession for f in found] == ["a-3", "a-2", "a-1"]        # 겹친 a-2 는 한 번, 2022 전은 뺌
    assert not any("002" in url for url in http.asked)                   # 필요 없는 파일은 안 받는다


class PagedHttp:
    def __init__(self, pages: list, fail_at: int | None = None):
        self.pages, self.fail_at, self.calls = pages, fail_at, []

    def get_json(self, url, params=None, **kwargs):
        self.calls.append(params)
        page = int(params["page_no"])
        if page == self.fail_at:
            raise OSError("DART 가 응답하지 않습니다")
        return self.pages[page - 1]


def page(n: int, total: int) -> dict:
    return {"status": "000", "total_page": total, "list": [
        {"rcept_no": f"2025{n:02d}", "report_nm": "주요사항보고서(유상증자결정)", "rcept_dt": "20250304"}]}


def test_dart_filings_all_follows_pages(tmp_path):
    http = PagedHttp([page(1, 3), page(2, 3), page(3, 3)])
    client = DartClient(http, api_key="키", cache_dir=tmp_path)
    found = client.filings_all("00126380", date(2020, 1, 1), date(2025, 12, 31))
    assert [f.rcept_no for f in found] == ["202501", "202502", "202503"]
    assert [c["page_no"] for c in http.calls] == ["1", "2", "3"]


def test_dart_filings_all_stops_at_a_failed_page(tmp_path):
    http = PagedHttp([page(1, 3), page(2, 3), page(3, 3)], fail_at=2)
    client = DartClient(http, api_key="키", cache_dir=tmp_path)
    assert [f.rcept_no for f in client.filings_all("00126380", date(2020, 1, 1))] == ["202501"]


def test_dart_filings_all_without_key_asks_nothing(tmp_path):
    http = PagedHttp([])
    assert DartClient(http, api_key="", cache_dir=tmp_path).filings_all("1", date(2020, 1, 1)) == []
    assert http.calls == []


# --- 명령 전체 ----------------------------------------------------------
def test_events_command_writes_csv_and_summary(tmp_path, capsys, monkeypatch):
    import main as cli

    monkeypatch.setattr(cli, "now", lambda _tz: datetime(2025, 3, 20, 12, 0, tzinfo=timezone.utc))
    target = SimpleNamespace(ticker="AAA", market="us", price_symbol="AAA", cik="0000000001",
                             corp_code="", is_fund=False)
    filings = [sec("8-K", ["2.02"], accepted="2025-03-03T16:30:00.000Z")]
    long_week = WEEK + bars_from(date(2025, 3, 12), [(111, 112)] * 6)
    bot = SimpleNamespace(
        targets=lambda: [target],
        config=SimpleNamespace(sec_ready=True),
        edgar=SimpleNamespace(filings_since=lambda *a: filings),
        dart=SimpleNamespace(ready=False),
        prices=SimpleNamespace(candles=lambda symbol: long_week),
    )
    out = tmp_path / "events.csv"
    assert cli.cmd_events(bot, [], 5, 5, str(out)) == 0
    printed = capsys.readouterr().out
    assert "8-K 2.02" in printed and "생존 편향" in printed
    with open(out, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1 and rows[0]["entry_day"] == "2025-03-04"
    assert float(rows[0]["abn5"]) == pytest.approx(0.0)     # 지수와 똑같이 움직였다
