"""위험 요인 변화 감지.

회사가 **새 위험을 처음 적어 넣는 순간**을 잡는 게 목적이다.
해마다 반복되는 문장을 '새 위험' 으로 착각하면 아무 쓸모가 없다.
"""

from stockbot.filing_text import FilingText, Section
from stockbot.risk_watch import (
    build_risk_change,
    diff_paragraphs,
    find_flags,
)

OLD = [
    "Our business depends on the successful and timely launch of our vehicles. Any launch failure could "
    "damage our reputation and result in the loss of customers and revenue for an extended period.",
    "We operate in a highly competitive industry and face competition from companies with substantially "
    "greater financial resources than we have, which could reduce our market share over time.",
]

NEW_RISK = (
    "We may need to raise additional capital to fund our operations and planned expansion, and such "
    "capital may not be available on acceptable terms, which would require us to delay our programs."
)


def filing(paragraphs, form="10-Q", day="2026-08-05", url="https://sec/now"):
    return FilingText(form=form, filing_date=day, period=None, url=url,
                      sections=[Section("risk", "위험 요인", list(paragraphs))])


# --- 문단 비교 --------------------------------------------------------------
def test_new_paragraph_is_found():
    added, removed = diff_paragraphs(OLD + [NEW_RISK], OLD)
    assert added == [NEW_RISK]
    assert removed == 0


def test_reworded_paragraph_is_not_treated_as_new():
    """해마다 숫자·표현만 조금 바뀐다. 그걸 새 위험으로 세면 안 된다."""
    tweaked = OLD[0].replace("an extended period", "an extended period of time in 2026")
    added, _ = diff_paragraphs([tweaked, OLD[1]], OLD)
    assert added == []


def test_removed_paragraph_is_counted():
    _, removed = diff_paragraphs([OLD[0]], OLD)
    assert removed == 1


def test_short_fragments_are_ignored():
    """표 잔해나 제목 줄이 '새 위험' 으로 올라오면 안 된다."""
    added, _ = diff_paragraphs(OLD + ["Item 1A.", "Risk Factors", "(2)"], OLD)
    assert added == []


# --- 무겁게 볼 표현 ---------------------------------------------------------
def test_going_concern_is_flagged():
    text = ("Our auditors have expressed substantial doubt about our ability to continue as a "
            "going concern. We are pursuing financing alternatives.")
    flags = find_flags([text])
    assert [f.label for f in flags] == ["존속 의문"]
    assert "going concern" in flags[0].sentence
    assert "자금 조달" in flags[0].meaning


def test_several_flags_are_reported_once_each():
    text = [
        "We identified a material weakness in our internal control over financial reporting.",
        "We may need to raise additional capital to fund operations.",
        "We identified another material weakness in our internal control over financial reporting.",
    ]
    labels = [f.label for f in find_flags(text)]
    assert labels == ["내부통제 미비", "추가 자금 필요"]


def test_ordinary_risk_language_is_not_flagged():
    assert find_flags(["Our business is subject to seasonal fluctuations in customer demand."]) == []


# --- 전체 조립 --------------------------------------------------------------
def test_added_risk_and_flag_together():
    change = build_risk_change("RKLB", filing(OLD + [NEW_RISK]), filing(OLD, day="2026-05-06"))
    assert change.compared
    assert change.added == [NEW_RISK]
    assert change.added_total == 1
    assert [f.label for f in change.flags] == ["추가 자금 필요"]
    assert change.level == "poor"
    assert "추가 자금 필요" in change.summary


def test_no_new_risk_reads_as_good():
    change = build_risk_change("RKLB", filing(OLD), filing(OLD, day="2026-05-06"))
    assert change.compared and not change.added
    assert change.level == "good"
    assert "새로 추가된 위험 문단이 없습니다" in change.summary


def test_10q_saying_nothing_changed_is_understood():
    text = ("There have been no material changes to the risk factors previously disclosed in our "
            "Annual Report on Form 10-K for the year ended December 31, 2025.")
    change = build_risk_change("RKLB", filing([text]))
    assert change.no_material_changes
    assert "중요한 변화 없음" in change.summary
    assert change.level == "good"


def test_without_a_previous_filing_nothing_is_claimed():
    change = build_risk_change("RKLB", filing(OLD))
    assert not change.compared
    assert change.added == []
    assert "비교할 직전 보고서를 찾지 못했습니다" in change.summary


def test_a_flag_without_a_comparison_does_not_claim_it_is_new():
    """'새로 들어왔다' 는 직전 보고서와 맞춰봤을 때만 할 수 있는 말이다."""
    change = build_risk_change("RKLB", filing(OLD + [NEW_RISK]))
    assert [f.label for f in change.flags] == ["추가 자금 필요"]
    assert "새로" not in change.summary
    assert "비교하지는 못했습니다" in change.summary


def test_missing_report_is_handled():
    change = build_risk_change("RKLB", None)
    assert change.level == "unknown"
    assert change.added == []


def test_source_links_are_kept():
    change = build_risk_change("RKLB", filing(OLD, url="https://sec/a"),
                               filing(OLD, day="2026-05-06", url="https://sec/b"))
    assert change.current_url == "https://sec/a"
    assert change.previous_url == "https://sec/b"
    assert change.current_date == "2026-08-05" and change.previous_date == "2026-05-06"
