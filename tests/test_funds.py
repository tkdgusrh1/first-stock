"""ETF 인식과 판정.

ETF 는 회사가 아니다. 매출·ROE 로 판정하면 전부 '판단 불가' 가 되므로
따로 본다. 특히 배수 상품은 구조를 정확히 알려주는 게 안전과 직결된다.
"""

import pytest

from stockbot.assessment import assess
from stockbot.edgar import _parse_fund_ids, _parse_ticker_payload
from stockbot.funds import FUND_FORMS, classify_name, detect_fund
from stockbot.metrics import build_fund_metrics

MF_PAYLOAD = {
    "fields": ["cik", "seriesId", "classId", "symbol"],
    "data": [
        [1730168, "S000075845", "C000236362", "ETHU"],
        [1689873, "S000058343", "C000191914", "CONL"],
        [36405, "S000002277", "C000005955", "VOO"],
    ],
}


# --- SEC 의 ETF 목록 형식 ---------------------------------------------------
def test_fund_ticker_list_is_understood():
    mapping = _parse_ticker_payload(MF_PAYLOAD)
    assert mapping["ETHU"][0] == "0001730168"
    assert set(mapping) == {"ETHU", "CONL", "VOO"}


def test_series_and_class_ids_are_kept():
    ids = _parse_fund_ids(MF_PAYLOAD)
    assert ids["CONL"] == ("S000058343", "C000191914")


def test_company_list_format_still_works():
    payload = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    assert _parse_ticker_payload(payload)["AAPL"] == ("0000320193", "Apple Inc.")


# --- 상품 성격 읽기 ---------------------------------------------------------
@pytest.mark.parametrize(
    "name,leverage,inverse,kind",
    [
        ("GraniteShares 2x Long COIN Daily ETF", 2.0, False, None),
        ("Volatility Shares 2x Ether ETF", 2.0, False, "암호화폐"),
        ("Direxion Daily Semiconductor Bull 3X Shares", 3.0, False, "테마·섹터"),
        ("ProShares UltraShort S&P500", 2.0, True, "지수 추종"),
        ("SPDR S&P 500 ETF Trust", None, False, "지수 추종"),
        ("Global X Data Center & Digital Infrastructure ETF", None, False, "테마·섹터"),
        ("iShares 20+ Year Treasury Bond ETF", None, False, "채권"),
    ],
)
def test_product_shape_is_read_from_the_name(name, leverage, inverse, kind):
    info = classify_name("XXX", name)
    assert info.leverage == leverage
    assert info.inverse == inverse
    if kind:
        assert info.kind == kind


def test_leveraged_products_explain_the_decay():
    info = classify_name("CONL", "GraniteShares 2x Long COIN Daily ETF")
    assert info.high_risk
    joined = " ".join(info.warnings)
    assert "매일" in joined
    assert "손실이 남을 수 있습니다" in joined
    assert "분산 효과가 없습니다" in joined       # 단일 종목 상품


def test_plain_index_etf_has_no_structure_warning():
    info = classify_name("SPY", "SPDR S&P 500 ETF Trust")
    assert not info.high_risk
    assert info.warnings == []


def test_risk_label_reads_at_a_glance():
    assert classify_name("X", "ProShares UltraShort S&P500").risk_label == "2배 인버스 · 지수 추종"
    assert classify_name("X", "SPDR S&P 500 ETF Trust").risk_label == "지수 추종"


# --- 펀드인지 아닌지 --------------------------------------------------------
def submissions(name, sic, forms):
    return {"name": name, "sic": sic, "sicDescription": "Investment offices",
            "filings": {"recent": {"form": forms}}}


def test_fund_is_detected_from_the_official_list():
    info = detect_fund("ETHU", None, in_fund_list=True, name_hint="2x Ether ETF")
    assert info is not None and info.kind == "암호화폐"


def test_fund_is_detected_from_its_filings():
    payload = submissions("SPDR S&P 500 ETF Trust", "6726", ["24F-2NT", "N-CSR", "8-K"])
    assert detect_fund("SPY", payload) is not None


def test_operating_company_is_not_a_fund():
    payload = {"name": "Apple Inc.", "sic": "3571",
               "filings": {"recent": {"form": ["10-Q", "10-K", "8-K", "4"]}}}
    assert detect_fund("AAPL", payload) is None


def test_company_that_also_files_8k_stays_a_company():
    payload = {"name": "Rocket Lab", "sic": "3760",
               "filings": {"recent": {"form": ["10-Q", "8-K", "S-3", "424B5"]}}}
    assert detect_fund("RKLB", payload) is None


# --- ETF 지표·판정 ----------------------------------------------------------
class FakePrices:
    def quote(self, ticker):
        from stockbot.prices import Quote

        return Quote(symbol=ticker, price=25.4, change_pct=3.1, source="Yahoo Finance",
                     extended_price=25.9, extended_change_pct=1.97, market_state="POST")

    def prev_close_change(self, ticker):
        return None


def test_etf_metrics_carry_price_but_no_invented_fundamentals():
    info = classify_name("CONL", "GraniteShares 2x Long COIN Daily ETF")
    m = build_fund_metrics("CONL", info, FakePrices())

    assert m.is_fund and m.price == 25.4
    assert m.extended_label == "애프터마켓" and m.extended_price == 25.9
    # 없는 숫자는 만들지 않는다
    assert m.revenue_ttm is None and m.roe is None and m.per is None
    assert len(m.checks) == 5


def test_etf_verdict_uses_etf_rules_not_the_memo_five_checks():
    info = classify_name("CONL", "GraniteShares 2x Long COIN Daily ETF")
    verdict = assess(build_fund_metrics("CONL", info, FakePrices()))

    assert verdict.level == "poor"
    assert verdict.headline.startswith("2배 레버리지")
    assert not any(a.name in ("성장", "수익성") for a in verdict.axes)


def test_plain_etf_is_not_flagged_as_dangerous():
    info = classify_name("SPY", "SPDR S&P 500 ETF Trust")
    verdict = assess(build_fund_metrics("SPY", info, FakePrices()))
    assert verdict.level == "fair"


def test_expense_ratio_is_never_guessed():
    info = classify_name("SPY", "SPDR S&P 500 ETF Trust")
    m = build_fund_metrics("SPY", info, FakePrices())
    fee = next(c for c in m.checks if "보수" in c.label)
    assert fee.status == "na"
    assert "직접 확인" in fee.detail


def test_fund_forms_replace_the_company_forms():
    assert "10-Q" not in FUND_FORMS
    assert "497" in FUND_FORMS and "N-CSR" in FUND_FORMS
