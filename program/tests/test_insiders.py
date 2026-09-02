"""내부자 거래 집계.

핵심은 **무엇을 세지 않느냐** 다. 보상으로 받은 주식까지 더하면
"임원이 수백만 달러어치 취득" 이라는 무의미한 숫자가 나온다.
"""

from datetime import date

from stock_analysis.edgar import Filing
from stock_analysis.insiders import DEFAULT_DAYS, since_day, summarize


def form4(person, title, day, transactions, acc="0001819994-26-000031"):
    filing = Filing(cik="0001819994", ticker="RKLB", company="Rocket Lab", form="4",
                    accession=acc, filing_date=day, accepted=None, report_date=day,
                    primary_doc="")
    filing.insider, filing.insider_title = person, title
    filing.transactions = transactions
    return filing


def tx(code, shares, price, derivative=False):
    return {"code": code, "security": "Common Stock", "date": "2026-07-28",
            "shares": shares, "price": price,
            "value": (shares * price) if price else None,
            "derivative": derivative}


# --- 무엇을 세는가 ----------------------------------------------------------
def test_open_market_buys_and_sells_are_counted():
    summary = summarize("RKLB", [
        form4("Beck Peter", "CEO", "2026-07-28", [tx("P", 50000, 41.20)]),
        form4("Powell Sandra", "이사", "2026-06-14", [tx("S", 8000, 46.10)], acc="a-2"),
    ])
    assert len(summary.buys) == 1 and len(summary.sells) == 1
    assert round(summary.buy_value) == 2_060_000
    assert round(summary.sell_value) == 368_800
    assert summary.verdict == "순매수"


def test_compensation_and_tax_filings_are_excluded():
    """A(무상 취득)·F(세금 반납)·M(옵션 행사)·G(증여)는 매매 의사가 아니다."""
    summary = summarize("RKLB", [
        form4("A", "임원", "2026-07-01", [tx("A", 100000, 0)]),
        form4("B", "임원", "2026-07-02", [tx("F", 30000, 45.0)], acc="a-2"),
        form4("C", "임원", "2026-07-03", [tx("M", 50000, 12.0)], acc="a-3"),
        form4("D", "이사", "2026-07-04", [tx("G", 5000, 0)], acc="a-4"),
    ])
    assert summary.trades == []
    assert summary.other_filings == 4
    assert summary.verdict == "거래 없음"
    assert "보상·세금 목적 신고는 4건" in summary.summary


def test_derivatives_are_excluded():
    summary = summarize("RKLB", [
        form4("Beck", "CEO", "2026-07-28", [tx("P", 1000, 40.0, derivative=True)])
    ])
    assert summary.trades == []


def test_rows_without_a_share_count_are_skipped():
    summary = summarize("RKLB", [form4("Beck", "CEO", "2026-07-28", [tx("P", 0, 40.0)])])
    assert summary.trades == []


# --- 어떻게 읽히는가 --------------------------------------------------------
def test_several_buyers_get_a_stronger_note():
    summary = summarize("RKLB", [
        form4("Beck Peter", "CEO", "2026-07-28", [tx("P", 50000, 41.20)]),
        form4("Spice Adam", "CFO", "2026-07-29", [tx("P", 12000, 41.80)], acc="a-2"),
    ])
    assert summary.buyers == ["Beck Peter", "Spice Adam"]
    assert "여러 명" in summary.note
    assert summary.level == "good"


def test_selling_alone_is_not_called_bad():
    """매도는 세금·분산 때문일 수 있다. 악재로 단정하지 않는다."""
    summary = summarize("RKLB", [
        form4("Powell", "이사", "2026-06-14", [tx("S", 8000, 46.10)])
    ])
    assert summary.verdict == "순매도"
    assert summary.level == "fair"
    assert "그 자체로 악재는 아닙니다" in summary.note


def test_summary_reads_as_a_sentence():
    summary = summarize("RKLB", [
        form4("Beck Peter", "CEO", "2026-07-28", [tx("P", 50000, 41.20)]),
        form4("Powell Sandra", "이사", "2026-06-14", [tx("S", 8000, 46.10)], acc="a-2"),
    ])
    assert "최근 90일" in summary.summary
    assert "1명이 $2.06M 매수" in summary.summary
    assert "순매수" in summary.summary


def test_nothing_at_all_is_stated_plainly():
    summary = summarize("RKLB", [])
    assert summary.level == "unknown"
    assert "공개시장 매매가 없었습니다" in summary.summary
    assert summary.note == ""


def test_trades_are_newest_first():
    summary = summarize("RKLB", [
        form4("A", "임원", "2026-06-01", [{**tx("P", 100, 10.0), "date": "2026-06-01"}]),
        form4("B", "임원", "2026-07-01", [{**tx("P", 100, 10.0), "date": "2026-07-01"}], acc="a-2"),
    ])
    assert [t.day for t in summary.trades] == ["2026-07-01", "2026-06-01"]


def test_window_is_ninety_days():
    assert since_day(date(2026, 8, 12)) == date(2026, 5, 14)
    assert DEFAULT_DAYS == 90
