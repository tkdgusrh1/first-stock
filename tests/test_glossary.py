"""용어 사전. 화면에 나오는 용어는 전부 설명이 있어야 한다."""

import pytest

from stockbot.dashboard import SUMMARY_COLUMNS, term
from stockbot.glossary import BY_KEY, LABEL_TO_KEY, TERMS, groups, lookup


def test_every_term_has_a_one_line_explanation():
    for entry in TERMS:
        assert entry.short, f"{entry.name} 에 한 줄 설명이 없습니다"
        assert entry.short.endswith("."), f"{entry.name} 설명이 문장으로 끝나지 않습니다"


def test_keys_are_unique():
    keys = [t.key for t in TERMS]
    assert len(keys) == len(set(keys))


def test_labels_point_at_real_terms():
    for label, key in LABEL_TO_KEY.items():
        assert key in BY_KEY, f"{label} → {key} 항목이 사전에 없습니다"


@pytest.mark.parametrize("label", ["매출(TTM)", "영업이익률", "ROE", "ROIC", "PER", "PSR", "런웨이", "시총"])
def test_summary_table_terms_are_explained(label):
    assert lookup(label) is not None


def test_key_metrics_show_their_formula():
    for key in ("roe", "roic", "per", "psr", "runway", "fcf", "revenue_growth"):
        assert BY_KEY[key].formula, f"{key} 에 계산식이 없습니다"


def test_risky_terms_carry_a_caution():
    """오해하기 쉬운 지표에는 반드시 주의사항이 있어야 한다."""
    for key in ("roe", "per", "runway", "guidance", "form4", "ocf"):
        assert BY_KEY[key].caution, f"{key} 에 주의사항이 없습니다"


def test_groups_cover_every_term():
    grouped = sum(len(v) for v in groups().values())
    assert grouped == len(TERMS)


def test_term_helper_renders_a_link_and_tooltip():
    html = term("ROE")
    assert 'href="#term-roe"' in html
    assert "title=" in html
    assert "<sup>?</sup>" in html


def test_term_helper_passes_through_unknown_labels():
    assert term("종목") == "종목"


def test_summary_columns_are_mostly_explained():
    explained = [c for c in SUMMARY_COLUMNS if lookup(c)]
    assert len(explained) >= 8


def test_memo_priorities_are_in_the_glossary():
    for key in ("guidance", "consensus", "surprise"):
        assert key in BY_KEY
    assert "관리" in BY_KEY["guidance"].caution      # 가이던스는 조작 가능하다는 경고
