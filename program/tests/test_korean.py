"""영어 공시 문장 → 한글.

규칙으로 옮긴 문장은 **숫자를 손대지 않는다.** 그게 이 방식의 근거다.
규칙에 없는 문장은 억지로 옮기지 않고 빈 값을 돌려준다.
"""

import pytest

from stock_analysis.korean import (
    annotate,
    guidance_line,
    money,
    note_for,
    range_ko,
    strip_label,
    topic_of,
    translate_sentence,
)


# --- 금액 표기 --------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$213.0 million", "$213.0M"),
        ("$1.1 billion", "$1.1B"),
        ("$450,000", "$450,000"),
        ("revenue of $2.5 billion in 2026", "revenue of $2.5B in 2026"),
    ],
)
def test_money_is_shortened_but_not_changed(raw, expected):
    assert money(raw) == expected


# --- 문장 옮기기 ------------------------------------------------------------
@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("Revenue increased 78% year over year to $213.0 million.",
         "매출 $213.0M — 전년 대비 78% 증가"),
        ("Revenue decreased 12% to $88.0 million in the period.",
         "매출 $88.0M — 전년 대비 12% 감소"),
        ("Revenue for the second quarter of 2026 was $246.0 million.",
         "2분기 매출 $246.0M"),
        ("Net loss was $33.0 million compared to a net loss of $48.0 million.",
         "순손실 $33.0M — 비교 대상 $48.0M"),
        ("Gross margin improved to 32.5% from 28.1%.",
         "매출총이익률 28.1% → 32.5% (개선)"),
        ("We ended the quarter with $420.0 million in cash.",
         "기말 현금 $420.0M"),
        ("Backlog of $1.1 billion as of June 30, 2026.",
         "수주잔고 $1.1B"),
        ("We expect revenue in the third quarter to be in the range of $230 million to $240 million.",
         "다음 기간 매출 전망 $230M ~ $240M"),
        ("The Company completed 14 successful launches during the quarter.",
         "이 기간 발사 14회"),
        ("The company raised its full-year revenue guidance.", "연간 전망 상향"),
        ("The company lowered its full-year guidance.", "연간 전망 하향"),
    ],
)
def test_formulaic_sentences_become_korean(sentence, expected):
    assert translate_sentence(sentence) == expected


def test_numbers_are_never_invented_or_changed():
    """옮긴 문장에 나오는 숫자는 전부 원문에 있던 것이어야 한다."""
    sentence = "Revenue increased 78% year over year to $213.0 million."
    line = translate_sentence(sentence)
    for number in ("78", "213.0"):
        assert number in line
    assert "79" not in line and "214" not in line


def test_unusual_sentences_are_left_alone():
    """규칙에 없으면 억지로 옮기지 않는다. 그래야 지어낸 말이 안 생긴다."""
    assert translate_sentence("The board appointed a new advisory committee last week.") == ""
    assert translate_sentence("") == ""


def test_filing_labels_are_stripped_first():
    assert strip_label("[변화] Revenue increased 78% to $213.0 million.").startswith("Revenue")
    assert translate_sentence("[변화] Revenue increased 78% year over year to $213.0 million.")


def test_word_units_become_korean():
    line = translate_sentence(
        "We believe our cash will be sufficient to fund our operations for at least twelve months."
    )
    assert "12개월" in line


# --- 위험 요인 주제 ---------------------------------------------------------
@pytest.mark.parametrize(
    "sentence,topic",
    [
        ("We depend on a limited number of suppliers for certain components.", "공급망"),
        ("We may need to raise additional capital to fund our operations.", "자금 조달"),
        ("A significant portion of revenue comes from a small number of customers.", "고객 집중"),
        ("Our auditors expressed substantial doubt about our ability to continue as a going concern.",
         "존속 우려"),
        ("Cyberattacks on our information systems could disrupt operations.", "사이버 보안"),
        ("We face intense competition from larger companies.", "경쟁"),
        ("Changes in tariff policy could increase our costs.", "무역·지정학"),
        ("Our patents may not adequately protect our technology.", "지식재산"),
    ],
)
def test_risk_paragraphs_get_a_korean_topic(sentence, topic):
    assert topic_of(sentence)[0] == topic


def test_every_topic_explains_itself():
    topic, meaning = topic_of("We depend on a limited number of suppliers.")
    assert topic and meaning.endswith("내용입니다.")


def test_unknown_risk_topic_is_left_empty():
    assert topic_of("The quick brown fox jumped over the lazy dog.") == ("", "")


# --- 가이던스 한 줄 ---------------------------------------------------------
def test_guidance_line_reads_naturally():
    assert guidance_line("매출", "second quarter", "$230 million to $240 million") == \
        "2분기 매출 전망 $230M ~ $240M"
    assert guidance_line("EPS", "full year", "$1.20 to $1.30") == \
        "연간 주당순이익(EPS) 전망 $1.20 ~ $1.30"


def test_range_separator_becomes_a_tilde():
    assert range_ko("$230 million to $240 million") == "$230M ~ $240M"
    assert range_ko(None) == ""


# --- 조립 -------------------------------------------------------------------
class FakeTranslator:
    def __init__(self, mapping=None, enabled=True):
        self.mapping = mapping or {}
        self.enabled = enabled
        self.asked = []

    def translate_many(self, texts, limit=12):
        self.asked.extend(texts)
        return {t: self.mapping[t] for t in texts if t in self.mapping}


def test_rules_win_and_the_translator_is_not_asked():
    """규칙으로 옮긴 문장까지 번역기에 보내면 낭비다."""
    sentence = "Revenue increased 78% year over year to $213.0 million."
    translator = FakeTranslator()
    notes = annotate([sentence], "sentence", translator)

    assert notes[sentence].line == "매출 $213.0M — 전년 대비 78% 증가"
    assert translator.asked == []


def test_leftover_sentences_go_to_the_translator():
    sentence = "The board appointed a new advisory committee last week."
    translator = FakeTranslator({sentence: "이사회가 지난주 새 자문위원회를 임명했습니다."})
    notes = annotate([sentence], "sentence", translator)

    assert notes[sentence].line == ""
    assert notes[sentence].machine.startswith("이사회가")
    assert translator.asked == [sentence]


def test_risk_paragraphs_get_both_topic_and_translation():
    """위험 문단은 길어서 주제만으로 부족하다. 번역도 함께 시도한다."""
    text = "We depend on a limited number of suppliers for certain critical components."
    translator = FakeTranslator({text: "우리는 일부 핵심 부품을 소수 공급업체에 의존합니다."})
    notes = annotate([text], "risk", translator)

    assert notes[text].topic == "공급망"
    assert notes[text].machine.startswith("우리는")


def test_without_a_translator_only_rules_are_used():
    text = "The board appointed a new advisory committee last week."
    notes = annotate([text], "sentence", None)
    assert notes[text].empty


def test_note_reports_whether_anything_was_found():
    good = note_for("Revenue increased 78% year over year to $213.0 million.")
    assert good.line and not good.empty
    assert note_for("Nothing matches this sentence at all.").empty
