"""세 번째 점검에서 나온 숫자 오류들 — 다시 생기지 않게 막아둔다.

1. 10-Q 현금흐름표는 연초부터 **누적**이다. 3개월 값이 없으면 누적끼리 빼서 분기를 만든다.
2. 위안·유로로 보고한 숫자를 달러로 보여주지 않는다.
3. 부채를 받지 못했으면 0 이 아니라 빈칸. 합계 항목을 두 번 더하지 않는다.
4. 분기가 하나 빠진 채 4개를 더해 '최근 1년' 이라 부르지 않는다.
5. 항목 이름을 바꾼 회사는 최근 이름의 값을 쓴다.
6. 과거 PER 은 실적이 **처음** 공개된 날의 주가와 맞춘다.
7. 주가가 바뀌면 시가총액·PER 도 다시 계산한다.
"""

from datetime import date, timedelta

from stock_analysis.metrics import Metrics, _balance_day, _cash, _debt, _surprise, build_metrics, valuation
from stock_analysis.prices import PriceClient
from stock_analysis.quant import paper
from stock_analysis.quant import strategies as strat
from stock_analysis.quant.costs import CostModel
from stock_analysis.quant.engine import Engine
from stock_analysis.quant.sizing import RiskRules
from stock_analysis.screener import BLUE, Pick, overall_scores
from stock_analysis.xbrl import CompanyFacts, consecutive


def row(val, start, end, filed=None, form="10-Q", accn="0000000001-25-000001"):
    out = {"val": val, "end": end, "form": form, "filed": filed or end, "accn": accn}
    if start:
        out["start"] = start
    return out


def company(concepts: dict, unit: str = "USD") -> CompanyFacts:
    return CompanyFacts({"cik": 1, "entityName": "Test", "facts": {"us-gaap": {
        name: {"units": {unit: rows}} for name, rows in concepts.items()}}})


# 실제 10-Q·10-K 와 같은 모양: 3·6·9·12 개월 누적만 있다. 분기마다 100, 110, 120, 130.
def ytd_year(year: int, base: float) -> list[dict]:
    s = f"{year}-01-01"
    q = [base, base + 10, base + 20, base + 30]
    return [
        row(q[0], s, f"{year}-03-31", f"{year}-05-01"),
        row(q[0] + q[1], s, f"{year}-06-30", f"{year}-08-01"),
        row(sum(q[:3]), s, f"{year}-09-30", f"{year}-11-01"),
        row(sum(q), s, f"{year}-12-31", f"{year + 1}-02-15", form="10-K"),
    ]


OCF = "NetCashProvidedByUsedInOperatingActivities"


# --- 1. 누적 현금흐름 → 분기 -------------------------------------------------
def test_year_to_date_cash_flow_becomes_discrete_quarters():
    facts = company({OCF: ytd_year(2024, 100) + ytd_year(2025, 200)})
    quarters = facts.quarterly("ocf", limit=8)

    assert [q.val for q in quarters] == [100, 110, 120, 130, 200, 210, 220, 230]
    assert consecutive(quarters)
    assert facts.ttm("ocf") == 860            # 2025년 연간과 같다
    assert facts.ttm_prior("ocf") == 460


def test_a_real_three_month_value_wins_over_the_derived_one():
    rows = ytd_year(2025, 200) + [row(999, "2025-04-01", "2025-06-30", "2025-08-01")]
    facts = company({OCF: rows})
    assert [q.val for q in facts.quarterly("ocf")][:2] == [200, 999]


def test_share_counts_are_never_derived_by_subtraction():
    rows = [row(1000, "2025-01-01", "2025-03-31"), row(1010, "2025-01-01", "2025-06-30")]
    facts = company({"WeightedAverageNumberOfDilutedSharesOutstanding": rows}, unit="shares")
    assert [q.val for q in facts.quarterly("shares")] == [1000]


# --- 2. 달러가 아닌 회사 -----------------------------------------------------
def test_yuan_numbers_are_not_shown_as_dollars():
    facts = company({"Revenues": ytd_year(2025, 1e9)}, unit="CNY")
    m = build_metrics("BABA", facts)

    assert m.revenue_ttm is None
    assert facts.currency_status() == (["CNY"], True)
    assert any("CNY" in w for w in m.warnings)


# --- 3. 부채·현금 -------------------------------------------------------------
def debt(facts):
    return _debt(facts, _balance_day(facts))


def test_no_debt_facts_means_unknown_not_zero():
    assert debt(company({})) is None


def test_long_term_debt_total_is_not_added_to_its_current_part():
    facts = company({
        "LongTermDebt": [row(500, None, "2025-12-31")],
        "LongTermDebtCurrent": [row(100, None, "2025-12-31")],
    })
    assert debt(facts) == 500


def test_noncurrent_plus_current_is_added():
    facts = company({
        "LongTermDebtNoncurrent": [row(400, None, "2025-12-31")],
        "LongTermDebtCurrent": [row(100, None, "2025-12-31")],
    })
    assert debt(facts) == 500


def test_balances_from_different_dates_are_not_added():
    facts = company({
        "CashAndCashEquivalentsAtCarryingValue": [row(50, None, "2025-12-31")],
        "ShortTermInvestments": [row(30, None, "2024-12-31")],
    })
    assert _cash(facts, _balance_day(facts)) == 50


# --- 4. 빠진 분기 -------------------------------------------------------------
def test_a_missing_quarter_is_not_summed_as_a_year():
    rows = [row(v, s, e) for v, s, e in (
        (10, "2024-04-01", "2024-06-30"), (11, "2024-07-01", "2024-09-30"),
        # 2024년 4분기 없음
        (12, "2025-01-01", "2025-03-31"), (13, "2025-04-01", "2025-06-30"),
    )]
    facts = company({"Revenues": rows})

    assert facts.last_quarters("revenue", 4) == []
    assert facts.ttm("revenue") is None        # 연간도 없으니 비운다


# --- 5. 항목 이름이 바뀐 회사 -------------------------------------------------
def test_the_concept_with_the_latest_period_is_used():
    facts = company({
        "RevenueFromContractWithCustomerExcludingAssessedTax": [row(1, "2019-01-01", "2019-03-31")],
        "Revenues": [row(9, "2025-01-01", "2025-03-31")],
    })
    assert facts.quarterly("revenue")[-1].val == 9


# --- 6. 처음 공개된 날 ---------------------------------------------------------
def test_first_filed_ignores_the_next_years_comparative_column():
    facts = company({"EarningsPerShareDiluted": [
        row(1.0, "2024-01-01", "2024-03-31", "2024-05-01"),
        row(1.0, "2024-01-01", "2024-03-31", "2025-05-01"),     # 다음 해 10-Q 의 비교 칸
    ]}, unit="USD/shares")
    assert facts.first_filed("eps")[date(2024, 3, 31)] == date(2024, 5, 1)


# --- 7. 주가가 바뀌면 밸류에이션도 ---------------------------------------------
def test_valuation_follows_the_price():
    m = Metrics(ticker="X", price=10.0, shares=100.0, eps_ttm=2.0, revenue_ttm=500.0)
    valuation(m)
    m.price = 20.0
    valuation(m)
    assert (m.market_cap, m.per, m.psr) == (2000.0, 10.0, 4.0)


# --- 그 밖 ---------------------------------------------------------------------
def test_ties_get_the_same_overall_score():
    picks = [Pick("A", category=BLUE, score=5), Pick("B", category=BLUE, score=5), Pick("C", category=BLUE, score=1)]
    scores = overall_scores(picks)
    assert scores["A"] == scores["B"] > scores["C"]


def test_paper_waits_until_every_tickers_bar_has_arrived():
    def days(n, start=date(2025, 1, 6)):
        ds = [(start + timedelta(k)).isoformat() for k in range(n)]
        return (None, None, ds, None)

    prepared = {"A": days(5), "B": days(4), "HALTED": days(1, date(2024, 1, 1))}
    assert paper.settled_day(prepared) == "2025-01-09"     # B 의 마지막 날. 멈춘 종목은 기다리지 않는다


def test_a_fully_invested_account_keeps_zero_cash_after_reload():
    eng = Engine(strat.STRATEGIES["breakout"], RiskRules(), CostModel(0, 0, 0), 10_000)
    eng.cash = 0.0
    raw = eng.to_dict()
    assert Engine.from_dict(raw, "us").cash == 0.0


def test_a_failed_history_download_keeps_the_old_rows():
    client = PriceClient(http=None)
    client._history_cache["X"] = [(date(2025, 1, 2), 10.0)]
    client._fetched_at["X"] = 0.0                      # 오래돼서 다시 받으려 한다
    client._yahoo_history = lambda key: []
    client._stooq_history = lambda key: []
    client._ensure_long("X")
    assert client._history_cache["X"] == [(date(2025, 1, 2), 10.0)]


# --- 한국 부채총계는 미국 차입금과 다르다 ---------------------------------------
def test_korean_stability_uses_debt_ratio_not_net_cash():
    from stock_analysis import money
    from stock_analysis.assessment import _stability, debt_label

    # 현금 10, 부채총계 50(외상값 포함), 자기자본 100 — 순현금으로 보면 '빚쟁이' 지만 부채비율 50%
    m = Metrics(ticker="005930", currency=money.KRW, cash=10.0, total_debt=50.0, equity=100.0)
    axis = _stability(m)

    assert debt_label(m) == "부채총계"
    assert "부채비율 50%" in axis.headline
    assert not any("순현금" in e for e in axis.evidence)
    assert debt_label(Metrics(ticker="AAPL")) == "차입금"


# --- 독립 검증(2차)에서 나온 것들 ------------------------------------------------
def test_a_16_week_quarter_still_counts_as_consecutive():
    """코스트코형: 12·12·12·16주. 4분기가 112일이라 분기말 간격만 보면 '빠졌다' 고 본다."""
    spans = [("2024-09-02", "2024-11-24", 100), ("2024-11-25", "2025-02-16", 100),
             ("2025-02-17", "2025-05-11", 100), ("2025-05-12", "2025-08-31", 133),
             ("2025-09-01", "2025-11-23", 150)]
    facts = company({"Revenues": [row(v, s, e) for s, e, v in spans]})
    assert facts.ttm("revenue") == 100 + 100 + 133 + 150


def test_q4_is_derived_when_10q_and_10k_start_a_day_apart():
    rows = [row(10, "2024-12-31", "2025-03-31"), row(30, "2024-12-31", "2025-06-30"),
            row(60, "2024-12-31", "2025-09-30"), row(100, "2025-01-01", "2025-12-31", form="10-K")]
    facts = company({OCF: rows})
    assert [q.val for q in facts.quarterly("ocf")] == [10, 20, 30, 40]


def test_total_revenue_wins_over_a_narrow_line_only_in_the_10k():
    total = [row(100 + k, s, e) for k, (s, e) in enumerate(
        [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
         ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31")])]
    total.append(row(406, "2025-01-01", "2025-12-31", form="10-K"))
    narrow = [row(300, "2025-01-01", "2025-12-31", form="10-K")]
    facts = company({"RevenueFromContractWithCustomerExcludingAssessedTax": narrow, "Revenues": total})

    assert facts.ttm("revenue") == 406
    assert facts.annual("revenue")[-1].val == 406


def test_old_dollar_numbers_are_dropped_when_the_company_switched_to_euros():
    facts = CompanyFacts({"cik": 1, "entityName": "T", "facts": {"us-gaap": {"Revenues": {"units": {
        "USD": [row(500, "2020-01-01", "2020-12-31", form="10-K")],
        "EUR": [row(900, "2025-01-01", "2025-12-31", form="20-F")],
    }}}}})
    m = build_metrics("T", facts)
    assert m.revenue_ttm is None
    assert any("EUR" in w for w in m.warnings)


def test_an_annual_value_years_older_than_the_rest_is_not_used_as_ttm():
    facts = company({
        "GrossProfit": [row(50, "2019-01-01", "2019-12-31", form="10-K")],
        "Revenues": [row(400, "2025-01-01", "2025-12-31", form="10-K")],
    })
    assert facts.ttm("gross_profit") is None


def test_eps_shown_and_its_source_are_the_same_numbers():
    eps = [row(v, s, e) for v, s, e in (
        (0.5, "2025-01-01", "2025-03-31"), (0.6, "2025-04-01", "2025-06-30"),
        (0.7, "2025-07-01", "2025-09-30"), (0.8, "2025-10-01", "2025-12-31"))]
    facts = company({"EarningsPerShareDiluted": eps}, unit="USD/shares")
    m = build_metrics("T", facts)
    assert m.sources["eps"].total == m.eps_ttm


def test_a_long_term_debt_balance_from_years_ago_is_not_added():
    facts = company({
        "StockholdersEquity": [row(1000, None, "2025-06-30")],
        "LongTermDebtNoncurrent": [row(500, None, "2021-12-31")],
        "LongTermDebtCurrent": [row(50, None, "2025-06-30")],
    })
    assert debt(facts) == 50


def test_commercial_paper_inside_debt_current_is_kept():
    facts = company({
        "StockholdersEquity": [row(1000, None, "2025-06-30")],
        "LongTermDebt": [row(500, None, "2025-06-30")],            # 1년 안에 갚을 100 포함
        "LongTermDebtCurrent": [row(100, None, "2025-06-30")],
        "DebtCurrent": [row(1100, None, "2025-06-30")],            # 100 + CP 1000
    })
    assert debt(facts) == 1500


def test_roic_is_blank_when_debt_was_not_received():
    from stock_analysis.metrics import _roic
    m = Metrics(ticker="T", operating_income_ttm=100.0, equity=500.0, cash=50.0, total_debt=None)
    assert _roic(company({}), m) is None


def test_margin_is_not_formed_from_two_different_periods():
    quarters = [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
                ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31")]
    facts = company({
        "Revenues": [row(100, s, e) for s, e in quarters],
        "OperatingIncomeLoss": [row(80, "2024-01-01", "2024-12-31", form="10-K")],   # 한 해 전 연간뿐
    })
    m = build_metrics("T", facts)
    assert m.operating_income_ttm == 80 and m.op_margin is None


def test_one_class_on_the_cover_does_not_halve_the_share_count():
    facts = CompanyFacts({"cik": 1, "entityName": "T", "facts": {
        "us-gaap": {"CommonStockSharesOutstanding": {"units": {"shares": [row(12.1e9, None, "2025-06-30")]}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [row(5.8e9, None, "2025-07-20")]}}},
    }})
    assert facts.shares_outstanding() == 12.1e9


def test_manual_consensus_is_not_compared_with_an_earlier_quarter():
    eps = [row(0.5, "2025-01-01", "2025-03-31", "2025-05-01")]
    facts = company({"EarningsPerShareDiluted": eps}, unit="USD/shares")
    assert _surprise(facts, 0.6, None, since=date(2025, 6, 1)) is None       # 넣은 뒤 아직 발표 전
    assert _surprise(facts, 0.6, None, since=None) is None                    # 언제 넣었는지 모름
    got = _surprise(facts, 0.4, None, since=date(2025, 4, 15))
    assert got["period"] == "2025-03-31" and "GAAP" in got["basis"]
