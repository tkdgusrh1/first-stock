"""보고서 본문 발췌. 요약을 지어내지 않고 원문을 그대로 가져와야 한다."""

from stock_analysis.filing_text import (
    extract_sections,
    html_to_paragraphs,
    notable_sentences,
    parse_filing,
    split_mdna,
)

SAMPLE = """
<html><body>
<p>TABLE OF CONTENTS</p>
<p>Item 1. Business .......... 3</p>
<p>Item 2. Management's Discussion and Analysis .......... 12</p>
<p>Item 1. Business</p>
<p>We design, manufacture and launch small orbital rockets and spacecraft
components for commercial and government customers worldwide.</p>
<p>Our launch vehicle has completed multiple missions to orbit since inception,
and we continue to expand our space systems segment.</p>
<p>Item 1A. Risk Factors</p>
<p>We have a history of losses and may not achieve or maintain profitability in
the future, which could materially affect our business and results.</p>
<p>Item 2. Management's Discussion and Analysis of Financial Condition</p>
<p>Overview</p>
<p>We are a space company delivering reliable launch services and spacecraft
components to our customers across commercial and government markets.</p>
<p>Results of Operations</p>
<p>Revenues increased by 32% to $105.6 million for the quarter, driven primarily
by higher launch cadence and increased space systems deliveries to customers.</p>
<p>Liquidity and Capital Resources</p>
<p>We believe our existing cash and cash equivalents will be sufficient to fund
our operations for at least the next twelve months from the date of this report.</p>
<p>Outlook</p>
<p>We expect to continue investing in our next generation launch vehicle and
we anticipate additional contract awards during the second half of the year.</p>
<p>Item 3. Quantitative and Qualitative Disclosures</p>
<p>This section is not part of MD&amp;A and should be excluded from the extract.</p>
</body></html>
"""


def parsed():
    return parse_filing(SAMPLE, "10-Q", "2026-08-05", "https://sec.gov/x.htm", "2026-06-30")


# --- HTML 처리 --------------------------------------------------------------
def test_html_becomes_readable_paragraphs():
    paragraphs = html_to_paragraphs(SAMPLE)
    assert any("small orbital rockets" in p for p in paragraphs)
    assert not any("<p>" in p for p in paragraphs)


def test_scripts_and_styles_are_dropped():
    paragraphs = html_to_paragraphs(
        "<style>.a{color:red}</style><script>alert(1)</script>"
        "<p>" + "실제 본문입니다. " * 6 + "</p>"
    )
    joined = " ".join(paragraphs)
    assert "alert(1)" not in joined and "color:red" not in joined
    assert "실제 본문입니다" in joined


def test_html_entities_are_decoded():
    paragraphs = html_to_paragraphs("<p>" + ("R&amp;D spending rose. " * 5) + "</p>")
    assert "R&D" in " ".join(paragraphs)


# --- 항목 추출 --------------------------------------------------------------
def test_finds_standard_items():
    keys = {s.key for s in extract_sections(html_to_paragraphs(SAMPLE))}
    assert {"business", "risk"} <= keys


def test_table_of_contents_is_not_mistaken_for_body():
    """목차의 'Item 1. Business .... 3' 줄을 본문으로 잡으면 안 된다."""
    sections = extract_sections(html_to_paragraphs(SAMPLE))
    business = next(s for s in sections if s.key == "business")
    assert "design, manufacture" in business.text
    assert "..." not in business.text


def test_section_stops_at_the_next_item():
    sections = parsed().sections
    joined = " ".join(s.text for s in sections)
    assert "should be excluded from the extract" not in joined


def test_mdna_is_split_into_subsections():
    report = parsed()
    keys = {s.key for s in report.sections}
    assert "mdna_results" in keys
    assert "mdna_liquidity" in keys
    assert "mdna_outlook" in keys

    results = report.section("mdna_results")
    assert "Revenues increased by 32%" in results.text
    # 소제목이 본문 앞에 붙어 있으면 안 된다
    assert not results.text.startswith("Results of Operations")


def test_sections_keep_original_wording():
    """원문을 바꾸지 않는다."""
    report = parsed()
    assert "driven primarily by higher launch cadence" in " ".join(
        s.text for s in report.sections
    )


# --- 눈에 띄는 문장 ----------------------------------------------------------
def test_notable_sentences_are_labelled_and_verbatim():
    report = parsed()
    joined = " ".join(report.company_words)
    assert "[변화]" in joined or "[전망]" in joined or "[계획]" in joined
    assert "Revenues increased by 32%" in joined
    # 라벨만 붙이고 문장 자체는 손대지 않는다
    for sentence in report.company_words:
        assert sentence.startswith("[")
        assert "]" in sentence


def test_funding_statement_is_picked_up():
    report = parsed()
    joined = " ".join(report.company_words)
    assert "sufficient to fund" in joined


def test_notable_sentences_are_deduplicated():
    section_paragraphs = ["Revenues increased by 10% this year. " * 3]
    from stock_analysis.filing_text import Section

    found = notable_sentences([Section("x", "x", section_paragraphs)])
    assert len(found) <= 1


# --- 메타 -------------------------------------------------------------------
def test_metadata_is_preserved():
    report = parsed()
    assert report.form == "10-Q"
    assert report.filing_date == "2026-08-05"
    assert report.period == "2026-06-30"
    assert report.url.startswith("https://")


def test_document_without_items_returns_empty_sections():
    report = parse_filing("<p>아무 항목도 없는 문서입니다.</p>", "10-Q", "2026-01-01", "u")
    assert report.sections == []
    assert report.company_words == []


def test_mdna_without_subsections_stays_whole():
    from stock_analysis.filing_text import Section

    section = Section("mdna", "MD&A", ["소제목 없이 이어지는 긴 문단입니다. " * 5])
    assert split_mdna(section) == [section]
