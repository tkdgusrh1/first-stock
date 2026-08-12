"""실적 3자 대조 — 실제 · 컨센서스 · 가이던스."""

from datetime import date

from stock_analysis.guidance import GuidanceItem, GuidanceReport
from stock_analysis.metrics import Metrics
from stock_analysis.recap import BEAT, MET, MISS, build_recap, judge


def metrics(**surprise):
    m = Metrics(ticker="RKLB")
    m.surprise = surprise or None
    m.quarterly_revenue = [(date(2026, 6, 30), 246_000_000)]
    return m


def guidance(low, high, metric="매출"):
    return GuidanceReport(
        form="8-K", filing_date="2026-05-08", url="https://sec/1",
        items=[GuidanceItem(sentence="We expect revenue of ...", metric=metric,
                            period="second quarter", low=low, high=high, unit="$")],
    )


# --- 판정 -------------------------------------------------------------------
def test_judgements():
    assert judge(478e6, 450e6, 470e6) == BEAT
    assert judge(460e6, 450e6, 470e6) == MET
    assert judge(440e6, 450e6, 470e6) == MISS
    assert judge(None, 450e6, 470e6) == "확인 불가"


# --- 조립 -------------------------------------------------------------------
def test_all_three_appear_together():
    m = metrics(actual_revenue=246e6, consensus_revenue=240e6,
                actual_eps=-0.12, consensus_eps=-0.15, period="2026-06-30")
    recap = build_recap("RKLB", m, guidance(230e6, 240e6))

    labels = [line.label for line in recap.lines]
    assert labels == ["매출 vs 컨센서스", "EPS vs 컨센서스", "매출 vs 가이던스"]
    assert all(line.verdict == BEAT for line in recap.lines)
    assert recap.level == "good"
    assert recap.period == "2026-06-30"
    assert recap.guidance_url == "https://sec/1"


def test_guidance_alone_still_works():
    """컨센서스가 없어도 회사가 한 약속과는 견줄 수 있다."""
    recap = build_recap("RKLB", metrics(), guidance(230e6, 240e6))
    assert len(recap.known) == 1
    assert recap.known[0].label == "매출 vs 가이던스"
    assert recap.known[0].actual == 246_000_000       # 최근 분기 매출로 채운다


def test_consensus_alone_still_works():
    recap = build_recap("RKLB", metrics(actual_revenue=246e6, consensus_revenue=250e6))
    assert [line.label for line in recap.known] == ["매출 vs 컨센서스"]
    assert recap.known[0].verdict == MISS
    assert recap.level == "poor"


def test_nothing_to_compare_is_said_plainly():
    recap = build_recap("RKLB", metrics())
    assert recap.empty
    assert recap.level == "unknown"
    assert "아직 없습니다" in recap.summary


def test_eps_guidance_is_not_matched_to_revenue():
    """조정 EPS 가이던스를 매출과 비교하면 엉뚱한 판정이 나온다."""
    recap = build_recap("RKLB", metrics(), guidance(1.2, 1.3, metric="EPS"))
    assert recap.empty


def test_per_share_amounts_are_not_treated_as_revenue():
    recap = build_recap("RKLB", metrics(), guidance(2.1, 2.3))
    assert recap.empty


def test_summary_names_what_passed_and_failed():
    m = metrics(actual_revenue=246e6, consensus_revenue=250e6)
    recap = build_recap("RKLB", m, guidance(230e6, 240e6))
    assert "매출 vs 가이던스 충족" in recap.summary
    assert "매출 vs 컨센서스 미달" in recap.summary


def test_display_text_is_formatted_for_reading():
    m = metrics(actual_revenue=246e6, consensus_revenue=240e6)
    line = build_recap("RKLB", m).lines[0]
    assert line.actual_text == "$246.00M"
    assert line.expected_text == "$240.00M"
    assert round(line.gap_pct, 1) == 2.5
    assert line.icon == "✅"


def test_eps_is_not_shown_as_money():
    m = metrics(actual_eps=-0.12, consensus_eps=-0.15)
    line = build_recap("RKLB", m).lines[0]
    assert line.actual_text == "-0.12"
    assert "$" not in line.actual_text
