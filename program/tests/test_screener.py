"""'눈여겨볼 종목' 을 고르는 규칙.

여기서 틀리면 **틀린 줄 모른 채로 종목을 권하게 된다.** 화면에 뜨는 다섯
개는 사용자가 실제로 돈을 넣을지 판단하는 출발점이라, 다음 세 가지를
특히 조심한다.
  · 자료가 모자란 회사를 '좋다' 고 하지 않는다
  · 나쁜 회사가 뽑히지 않는다
  · 회사와 ETF 를 한 점수로 섞어 비교하지 않는다
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analysis import screener  # noqa: E402
from stock_analysis.assessment import assess  # noqa: E402
from stock_analysis.funds import FundInfo  # noqa: E402
from stock_analysis.metrics import Metrics  # noqa: E402
from stock_analysis.screener import Pick, PickStore, rank, score_company, score_fund  # noqa: E402


def strong(ticker: str = "GOOD") -> Metrics:
    """어느 축으로 봐도 괜찮은 회사."""
    m = Metrics(ticker=ticker, company=f"{ticker} 주식회사")
    m.revenue_ttm, m.revenue_ttm_prior, m.revenue_growth = 1000e6, 700e6, 0.43
    m.operating_income_ttm, m.op_margin, m.op_margin_prior = 250e6, 0.25, 0.19
    m.net_income_ttm, m.equity, m.roe, m.roic = 200e6, 800e6, 0.25, 0.18
    m.price, m.per, m.psr = 100.0, 20.0, 6.0
    m.cash, m.total_debt, m.ocf_ttm, m.fcf_ttm = 900e6, 100e6, 300e6, 220e6
    m.profitable = True
    return m


def weak(ticker: str = "BAD") -> Metrics:
    m = Metrics(ticker=ticker, company=f"{ticker} 주식회사")
    m.revenue_ttm, m.revenue_ttm_prior, m.revenue_growth = 100e6, 200e6, -0.5
    m.operating_income_ttm, m.op_margin, m.op_margin_prior = -80e6, -0.8, -0.2
    m.net_income_ttm, m.equity, m.roe = -90e6, 50e6, -1.8
    m.price, m.psr = 5.0, 12.0
    m.cash, m.total_debt, m.ocf_ttm, m.fcf_ttm = 30e6, 200e6, -70e6, -85e6
    m.profitable, m.runway_years = False, 0.4
    return m


def judge(m: Metrics, recap=None) -> Pick | None:
    return score_company(m, assess(m), recap)


# --- 뽑히면 안 되는 것 ------------------------------------------------------
def test_a_company_in_trouble_is_not_recommended():
    assert judge(weak()) is None


def test_a_company_we_could_not_read_is_not_recommended():
    """숫자가 없으면 '모른다' 지 '좋다' 가 아니다.

    빈 재무제표를 나쁘지 않다고 읽어서 추천 목록에 올리면, 아무 근거 없이
    종목을 권하는 셈이 된다. 가장 조심해야 할 실패다.
    """
    assert judge(Metrics(ticker="THIN")) is None


def test_a_single_stock_etf_is_never_offered():
    """사용자가 직접 빼달라고 한 것. 회사 하나에 파생을 얹은 상품이다."""
    m = Metrics(ticker="CONL", is_fund=True)
    m.fund = FundInfo(ticker="CONL", name="2x Long COIN", single_stock=True, underlying="COIN")
    assert score_fund(m) is None


@pytest.mark.parametrize("info", [
    FundInfo(ticker="TQQQ", name="3x QQQ", leverage=3.0),
    FundInfo(ticker="SQQQ", name="-3x QQQ", inverse=True),
])
def test_leveraged_and_inverse_etfs_are_never_offered(info):
    """하루 단위로 되맞추는 단기 매매용이다. '괜찮은 종목' 으로 권할 물건이 아니다."""
    m = Metrics(ticker=info.ticker, is_fund=True)
    m.fund = info
    assert score_fund(m) is None
    assert screener.excluded_fund(info)


# --- 점수 ------------------------------------------------------------------
def test_a_good_company_is_recommended_with_reasons_that_carry_numbers():
    """근거 없는 추천은 하지 않는다. 이유마다 숫자가 붙어 있어야 한다."""
    pick = judge(strong())
    assert pick is not None
    assert pick.reasons
    assert any(any(ch.isdigit() for ch in reason) for reason in pick.reasons)


def test_a_better_company_scores_higher():
    good = judge(strong())
    plain = strong("PLAIN")
    plain.op_margin, plain.op_margin_prior = 0.19, 0.25   # 마진이 나빠지는 중
    plain.roic = 0.03
    assert judge(plain).score < good.score


def test_not_knowing_costs_points():
    """확인 못 한 축이 많을수록 아래로 내려가야 한다."""
    full = judge(strong())
    partial = strong("PART")
    partial.cash = partial.total_debt = partial.ocf_ttm = partial.fcf_ttm = None
    partial.per = partial.psr = None
    thin = judge(partial)
    assert thin is None or thin.score < full.score


def test_dilution_pulls_a_company_down():
    """발행주식이 늘면 같은 회사를 사고도 내 몫이 줄어든다."""
    before = judge(strong())
    diluted = strong("DILUTE")
    diluted.share_growth_1y = 0.22
    after = judge(diluted)
    assert after.score < before.score
    assert any("희석" in c for c in after.cautions)


# --- 가이던스·컨센서스은 순위에 넣지 않는다 ---------------------------------
class _Line:
    def __init__(self, label, verdict):
        self.label, self.verdict = label, verdict
        self.actual_text, self.expected_text = "$246.00M", "$230.00M"


class _Recap:
    def __init__(self, lines):
        self.lines = lines


def test_guidance_is_shown_but_does_not_change_the_ranking():
    """감시 목록 종목에만 있는 값으로 가점을 주면 순위가 아니라 편향이 된다.

    가이던스는 공시 원문을 통째로 받아 읽어야 나오는데, 후보 250개에 그걸
    다 할 수는 없다. 그래서 이미 보고 있던 종목만 가점을 받는 일이 생긴다.
    """
    plain = judge(strong())
    beat = judge(strong(), _Recap([_Line("매출 vs 가이던스", "상회")]))

    assert beat.score == plain.score               # 점수는 그대로
    assert any("[참고]" in r for r in beat.reasons)  # 대신 근거로 보여준다


def test_a_missed_guidance_shows_up_as_a_caution():
    missed = judge(strong(), _Recap([_Line("매출 vs 가이던스", "미달")]))
    assert any("[참고]" in c and "미달" in c for c in missed.cautions)


# --- 줄 세우기 --------------------------------------------------------------
def _company(ticker, score):
    return Pick(ticker=ticker, score=score)


def _fund(ticker, score):
    return Pick(ticker=ticker, score=score, is_fund=True)


def test_companies_come_first_and_etfs_keep_their_own_slots():
    picks = [_company("A", 30), _company("B", 25), _company("C", 20), _company("D", 15),
             _fund("X", 13), _fund("Y", 12), _fund("Z", 11)]
    chosen = rank(picks, limit=5, etf_slots=2)

    assert [p.ticker for p in chosen] == ["A", "B", "C", "X", "Y"]


def test_an_empty_slot_is_filled_from_the_other_side():
    """ETF 가 모자란다고 자리를 비워둘 이유는 없다."""
    picks = [_company(t, 30 - i) for i, t in enumerate("ABCDEF")]
    assert len(rank(picks, limit=5, etf_slots=2)) == 5


def test_etfs_alone_still_fill_the_list():
    picks = [_fund(t, 13) for t in ("X", "Y", "Z")]
    assert len(rank(picks, limit=5, etf_slots=2)) == 3


def test_a_high_scoring_etf_never_outranks_a_company_by_number():
    """회사 점수와 ETF 점수는 재는 대상이 달라서 나란히 놓으면 안 된다."""
    chosen = rank([_company("A", 5), _fund("X", 99)], limit=5, etf_slots=2)
    assert [p.ticker for p in chosen] == ["A", "X"]


# --- 후보 목록 --------------------------------------------------------------
def test_the_shipped_universe_loads_and_has_both_kinds():
    stocks, etfs = screener.load_universe()
    assert len(stocks) > 50
    assert len(etfs) > 10
    assert not set(stocks) & set(etfs)


def test_a_broken_universe_file_does_not_crash(tmp_path):
    bad = tmp_path / "universe.yml"
    bad.write_text("이건: [열린 채로", encoding="utf-8")
    assert screener.load_universe(bad) == ([], [])


def test_a_missing_universe_file_does_not_crash(tmp_path):
    assert screener.load_universe(tmp_path / "없음.yml") == ([], [])


def test_duplicates_are_dropped_and_order_is_kept():
    assert screener.tickers([" nvda ", "AAPL", "NVDA", "", None]) == ["NVDA", "AAPL"]


# --- 본 결과를 기억한다 -----------------------------------------------------
def test_what_we_looked_at_survives_a_restart(tmp_path):
    """후보를 다 보는 데 반나절이 걸린다. 껐다 켤 때마다 처음부터면 순위가 안 나온다."""
    path = tmp_path / "screen.json"
    store = PickStore(path)
    store.remember("NVDA", Pick(ticker="NVDA", score=21.0, reasons=["성장 좋음"]), "2026-09-02")
    store.remember("XYZ", None, "2026-09-02", error="재무 없음")
    store.save()

    again = PickStore(path)
    assert again.looked_at == 2
    assert [p.ticker for p in again.picks()] == ["NVDA"]
    assert again.picks()[0].reasons == ["성장 좋음"]


def test_unseen_candidates_are_looked_at_first(tmp_path):
    store = PickStore(tmp_path / "screen.json")
    store.remember("NVDA", None, "2026-09-02")
    assert store.stale(["NVDA", "AAPL", "MSFT"], "2026-09-02") == ["AAPL", "MSFT"]


def test_old_results_are_looked_at_again(tmp_path):
    """분기 실적이 바뀌면 판단도 바뀐다. 한 번 보고 영영 두면 안 된다."""
    store = PickStore(tmp_path / "screen.json")
    store.remember("NVDA", None, "2026-09-02")
    assert store.stale(["NVDA"], "2026-09-20") == ["NVDA"]
    assert store.stale(["NVDA"], "2026-09-03") == []


def test_a_ticker_dropped_from_the_universe_is_forgotten(tmp_path):
    """후보에서 뺐는데도 계속 추천되면 뺀 의미가 없다."""
    store = PickStore(tmp_path / "screen.json")
    store.remember("NVDA", Pick(ticker="NVDA", score=21.0), "2026-09-02")
    store.remember("GONE", Pick(ticker="GONE", score=30.0), "2026-09-02")

    store.forget_missing(["NVDA"])

    assert [p.ticker for p in store.picks()] == ["NVDA"]


def test_a_broken_result_file_starts_over_instead_of_crashing(tmp_path):
    path = tmp_path / "screen.json"
    path.write_text("{망가짐", encoding="utf-8")
    assert PickStore(path).looked_at == 0


# --- 화면에 적는 말 ---------------------------------------------------------
def test_the_scope_is_always_stated():
    """'후보 몇 개 중 몇 개' 를 안 적으면 '미국 주식 전체에서 고른 것' 으로 읽힌다."""
    line = screener.summary_line([_company("A", 20)], 37, 250)
    assert "37" in line and "250" in line


def test_finding_nothing_says_so_plainly():
    line = screener.summary_line([], 3, 250)
    assert "찾지 못했습니다" in line and "250" in line
