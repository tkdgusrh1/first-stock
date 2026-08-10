from fixtures import FORM4_XML

from stockbot.edgar import Filing, parse_form4
from stockbot.messages import format_filing


def _filing(form="4", items=None):
    return Filing(
        cik="0001045810",
        ticker="NVDA",
        company="NVIDIA CORP",
        form=form,
        accession="0001045810-26-000123",
        filing_date="2026-08-08",
        accepted="2026-08-08T16:31:05.000Z",
        report_date="2026-08-07",
        primary_doc="xslF345X03/wf-form4_123.xml",
        items=items or [],
    )


def test_parse_form4_extracts_owner_and_transaction():
    filing = parse_form4(FORM4_XML, _filing())
    assert filing.insider == "Hong Gildong"
    assert "이사" in filing.insider_title
    assert "Chief Executive Officer" in filing.insider_title

    tx = filing.transactions[0]
    assert tx["code"] == "S"
    assert tx["code_label"] == "공개시장 매도"
    assert tx["shares"] == 100000
    assert tx["price"] == 120.50
    assert tx["value"] == 100000 * 120.50
    assert tx["direction"] == "처분"
    assert tx["shares_after"] == 1234567


def test_form4_message_flags_sale():
    filing = parse_form4(FORM4_XML, _filing())
    text = format_filing(filing, "Asia/Seoul")
    assert "Form 4 내부자 거래" in text
    assert "공개시장 매도" in text
    assert "10b5-1" in text          # 사전계획 매도 확인 안내
    assert "$12.05M" in text


def test_urls_are_built_correctly():
    filing = _filing()
    assert filing.doc_url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/"
        "xslF345X03/wf-form4_123.xml"
    )
    assert filing.index_url.endswith("0001045810-26-000123-index.htm")


def test_8k_message_highlights_earnings_item():
    filing = _filing(form="8-K", items=["2.02", "9.01"])
    filing.primary_doc = "nvda-8k.htm"
    text = format_filing(filing, "Asia/Seoul")
    assert "🚨" in text
    assert "실적 발표" in text
    assert "‼️" in text                      # 중요 항목 표시
    assert "1) <b>가이던스</b>" in text       # 메모 우선순위 안내가 붙는다
    assert "재무제표 및 첨부자료" in text


def test_8k_message_without_critical_items():
    filing = _filing(form="8-K", items=["5.03"])
    text = format_filing(filing, "Asia/Seoul")
    assert "📄" in text
    assert "가이던스" not in text


def test_acceptance_time_is_converted_to_local_timezone():
    filing = _filing(form="8-K", items=["8.01"])
    text = format_filing(filing, "Asia/Seoul")
    # 2026-08-08 16:31 ET = 2026-08-09 05:31 KST
    assert "2026-08-09 05:31" in text


def test_generic_form_message():
    filing = _filing(form="10-Q")
    text = format_filing(filing, "Asia/Seoul")
    assert "[10-Q]" in text
    assert "NVDA" in text
