"""가이던스 이행 이력.

메모: 가이던스는 회사가 관리할 수 있으니 '과거에 지켰는지' 를 확인하라.
그 확인을 자동으로 하되, 확신할 수 없는 것은 반드시 '확인 불가' 로 남긴다.
"""

from datetime import date

from stock_analysis.guidance import GuidanceItem, GuidanceReport
from stock_analysis.track_record import BEAT, MET, MISS, UNKNOWN, build_track_record, judge


class FakeFacts:
    """분기 매출만 있는 최소한의 XBRL 대역."""

    def __init__(self, quarters=None, years=None):
        self._quarters = quarters or {}
        self._years = years or {}

    def quarterly(self, key, limit=20):
        return [_F(end, val) for end, val in sorted(self._quarters.items())]

    def annual(self, key, limit=8):
        return [_F(end, val) for end, val in sorted(self._years.items())]


class _F:
    def __init__(self, end, val):
        self.end, self.val = end, val


def report(filed, sentence, metric="매출", low=None, high=None, period=None):
    return GuidanceReport(
        form="8-K", filing_date=filed, url=f"https://sec/{filed}",
        items=[GuidanceItem(sentence=sentence, metric=metric, period=period,
                            low=low, high=high, unit="$")],
    )


# --- 판정 규칙 --------------------------------------------------------------
def test_beating_the_top_of_the_range():
    verdict, why = judge(450e6, 470e6, 478e6)
    assert verdict == BEAT and "넘겼습니다" in why


def test_landing_inside_the_range():
    verdict, _ = judge(450e6, 470e6, 460e6)
    assert verdict == MET


def test_falling_short():
    verdict, why = judge(450e6, 470e6, 421e6)
    assert verdict == MISS and "못 미쳤" in why


def test_no_actual_means_no_verdict():
    assert judge(450e6, 470e6, None)[0] == UNKNOWN


# --- 실제 실적과 맞춰보기 ---------------------------------------------------
def test_guidance_is_matched_to_the_next_quarter():
    facts = FakeFacts({date(2026, 3, 31): 478_000_000})
    record = build_track_record(
        "RKLB",
        [report("2026-02-27", "We expect revenue of $450 million to $470 million for the first quarter.",
                low=450e6, high=470e6)],
        facts,
    )
    item = record.items[0]
    assert item.target_end == date(2026, 3, 31)
    assert item.actual == 478_000_000
    assert item.verdict == BEAT
    assert record.kept == 1 and record.missed == 0


def test_summary_counts_both_sides():
    facts = FakeFacts({date(2026, 3, 31): 478e6, date(2026, 6, 30): 400e6})
    record = build_track_record(
        "RKLB",
        [
            report("2026-02-27", "We expect revenue of $450 million to $470 million for Q1.",
                   low=450e6, high=470e6),
            report("2026-05-08", "We expect revenue of $480 million to $500 million for Q2.",
                   low=480e6, high=500e6),
        ],
        facts,
    )
    assert record.kept == 1 and record.missed == 1
    assert "1번 지켰고" in record.summary and "1번 못 지켰" in record.summary


def test_the_company_sentence_is_kept_verbatim():
    sentence = "We expect revenue of $450 million to $470 million for the first quarter of 2026."
    record = build_track_record("RKLB", [report("2026-02-27", sentence, low=450e6, high=470e6)],
                                FakeFacts({date(2026, 3, 31): 460e6}))
    assert record.items[0].sentence == sentence
    assert record.items[0].url == "https://sec/2026-02-27"


# --- 지어내지 않기 ----------------------------------------------------------
def test_adjusted_eps_is_never_judged_automatically():
    record = build_track_record(
        "NVDA",
        [report("2026-02-27", "We expect adjusted EPS of $1.20 to $1.30.", metric="EPS",
                low=1.20, high=1.30)],
        FakeFacts({date(2026, 3, 31): 478e6}),
    )
    item = record.items[0]
    assert item.verdict == UNKNOWN
    assert "정의를 정하므로" in item.reason
    assert record.judged == []


def test_results_not_yet_filed_stay_unknown():
    record = build_track_record(
        "RKLB",
        [report("2026-08-07", "We expect revenue of $500 million to $520 million.",
                low=500e6, high=520e6)],
        FakeFacts({date(2026, 3, 31): 478e6}),      # 발표 이후 분기가 아직 없다
    )
    assert record.items[0].verdict == UNKNOWN
    assert "아직 SEC 에 제출되지 않았" in record.items[0].reason


def test_suspicious_units_are_not_compared():
    record = build_track_record(
        "X",
        [report("2026-02-27", "We expect revenue per share of $2.10 to $2.30.", low=2.10, high=2.30)],
        FakeFacts({date(2026, 3, 31): 478e6}),
    )
    assert record.items[0].verdict == UNKNOWN
    assert "단위" in record.items[0].reason


def test_full_year_guidance_is_matched_to_the_annual_figure():
    facts = FakeFacts(
        quarters={date(2026, 3, 31): 478e6},
        years={date(2026, 12, 31): 2_100_000_000},
    )
    record = build_track_record(
        "RKLB",
        [report("2026-02-27", "For the full year we expect revenue of $1.9 billion to $2.0 billion.",
                low=1.9e9, high=2.0e9)],
        facts,
    )
    item = record.items[0]
    assert item.annual is True
    assert item.target_end == date(2026, 12, 31)
    assert item.verdict == BEAT


def test_no_reports_means_an_honest_empty_answer():
    record = build_track_record("X", [], FakeFacts())
    assert record.items == []
    assert record.level == "unknown"
    assert "찾지 못했습니다" in record.summary


def test_duplicate_sentences_are_counted_once():
    same = "We expect revenue of $450 million to $470 million for the first quarter."
    record = build_track_record(
        "RKLB",
        [report("2026-02-27", same, low=450e6, high=470e6),
         report("2026-02-28", same, low=450e6, high=470e6)],
        FakeFacts({date(2026, 3, 31): 478e6}),
    )
    assert len(record.items) == 1


# --- 화면에 쓰는 값 ---------------------------------------------------------
def test_level_reflects_how_reliable_the_company_has_been():
    facts = FakeFacts({date(2026, 3, 31): 478e6, date(2026, 6, 30): 495e6})
    record = build_track_record(
        "RKLB",
        [report("2026-02-27", "We expect revenue of $450 million to $470 million for Q1.",
                low=450e6, high=470e6),
         report("2026-05-08", "We expect revenue of $480 million to $490 million for Q2.",
                low=480e6, high=490e6)],
        facts,
    )
    assert record.level == "good"


def test_money_is_formatted_for_reading():
    record = build_track_record(
        "RKLB",
        [report("2026-02-27", "We expect revenue of $450 million to $470 million for Q1.",
                low=450e6, high=470e6)],
        FakeFacts({date(2026, 3, 31): 478e6}),
    )
    item = record.items[0]
    assert item.promised_text == "$450.00M ~ $470.00M"
    assert item.actual_text == "$478.00M"
    assert round(item.gap_pct, 1) == 1.7
