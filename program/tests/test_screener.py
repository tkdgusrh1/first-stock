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
from stock_analysis.screener import Pick, PickStore, rank, score_company  # noqa: E402


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


def _shares(growth: float, quarters: int = 8):
    """발행주식수 추이. 실제로는 이 목록에서 증가율이 계산된다."""
    from datetime import date

    base = 100e6
    return [(date(2025, 3 * (i % 4) + 1, 1), base * (1 + growth) ** (i / 4))
            for i in range(quarters)]


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


def test_a_single_stock_etf_is_flagged_for_exclusion():
    """사용자가 직접 빼달라고 한 것. 회사 하나에 파생을 얹은 상품이다."""
    info = FundInfo(ticker="CONL", name="2x Long COIN", single_stock=True, underlying="COIN")
    assert screener.excluded_fund(info) == "단일 종목 ETF"


@pytest.mark.parametrize("info", [
    FundInfo(ticker="TQQQ", name="3x QQQ", leverage=3.0),
    FundInfo(ticker="SQQQ", name="-3x QQQ", inverse=True),
])
def test_leveraged_and_inverse_etfs_are_flagged_for_exclusion(info):
    """하루 단위로 되맞추는 단기 매매용이다. '괜찮은 종목' 으로 권할 물건이 아니다."""
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
    diluted.shares_trend = _shares(0.22)
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


def test_the_highest_scores_come_first():
    picks = [_company("C", 20), _company("A", 30), _company("B", 25)]
    assert [p.ticker for p in rank(picks, limit=5)] == ["A", "B", "C"]


def test_only_as_many_as_asked_for():
    picks = [_company(t, 30 - i) for i, t in enumerate("ABCDEFG")]
    assert len(rank(picks, limit=5)) == 5


def test_a_tie_keeps_a_fixed_order():
    """순서가 흔들리면 바뀐 게 없는데도 뭔가 달라진 것처럼 보인다."""
    picks = [_company("Z", 20), _company("A", 20), _company("M", 20)]
    assert [p.ticker for p in rank(picks, limit=3)] == ["A", "M", "Z"]


# --- 티커 정리 --------------------------------------------------------------
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


# --- 성장 가능성 (적자여도 본다) --------------------------------------------
#
# 다섯 축 판정은 흑자 기업에 유리하게 짜여 있어서, 매출이 두 배로 늘고
# 있어도 적자면 '주의' 로 떨어진다. 그런 회사를 아예 안 보겠다는 것은
# 판단이 아니라 회피다. 그래서 갈래를 따로 뒀다.
from stock_analysis.screener import (  # noqa: E402
    BLUE, GROWTH, MOMENTUM, rank_by_category, score_growth, score_momentum,
)


def growing(ticker="GROW", growth=0.65, profitable=False, runway=3.0) -> Metrics:
    m = Metrics(ticker=ticker, company=f"{ticker} 주식회사")
    m.revenue_ttm, m.revenue_ttm_prior = 300e6, 300e6 / (1 + growth)
    m.revenue_growth = growth
    m.profitable, m.runway_years = profitable, runway
    m.op_margin, m.op_margin_prior = -0.15, -0.30
    m.price = 20.0
    return m


def test_a_fast_growing_company_is_offered_even_at_a_loss():
    """적자라고 빼면 성장주는 영영 안 나온다."""
    m = growing()
    pick = score_growth(m, assess(m))

    assert pick is not None
    assert pick.category == GROWTH
    assert any("65.0%" in r for r in pick.reasons)


def test_a_loss_making_company_says_it_is_loss_making():
    m = growing()
    pick = score_growth(m, assess(m))
    assert any("아직 적자입니다" in c for c in pick.cautions)


def test_a_company_that_runs_out_of_money_soon_is_not_offered():
    """1년 반도 못 버티는 적자 회사는 성장이 빨라도 권하지 않는다."""
    m = growing(runway=0.8)
    assert score_growth(m, assess(m)) is None


def test_a_slow_grower_is_not_called_a_growth_stock():
    m = growing(growth=0.05)
    assert score_growth(m, assess(m)) is None


def test_faster_growth_scores_higher():
    fast, slow = growing("FAST", growth=0.90), growing("SLOW", growth=0.25)
    assert score_growth(fast, assess(fast)).score > score_growth(slow, assess(slow)).score


def test_growing_revenue_with_worsening_margin_is_flagged():
    """매출은 느는데 남는 게 줄고 있으면 그 사실을 말해야 한다."""
    m = growing()
    m.op_margin, m.op_margin_prior = -0.30, -0.15
    pick = score_growth(m, assess(m))
    assert any("나빠졌습니다" in c for c in pick.cautions)


def test_dilution_weighs_heavier_for_a_loss_making_grower():
    plain = growing("PLAIN")
    diluted = growing("DILUTE")
    diluted.shares_trend = _shares(0.25)
    diluted.share_growth_1y = 0.25
    assert score_growth(diluted, assess(diluted)).score < score_growth(plain, assess(plain)).score


# --- 시장 흐름 --------------------------------------------------------------
#
# 이건 재무제표가 아니라 주가 이야기다. 지나간 값이고, 앞으로를 말해주지
# 않는다. 화면에서 그 한계를 매번 밝히는지가 여기서 제일 중요하다.


def moving(ticker="MOVE", r3=25.0, r6=40.0) -> Metrics:
    m = strong(ticker)
    m.return_3m, m.return_6m = r3, r6
    return m


def test_a_stock_that_beat_the_market_is_offered():
    m = moving()
    pick = score_momentum(m, assess(m), market_3m=8.0, market_6m=12.0)

    assert pick is not None
    assert pick.category == MOMENTUM
    assert any("시장" in r for r in pick.reasons)


def test_a_stock_that_merely_matched_the_market_is_not_offered():
    m = moving(r3=9.0)
    assert score_momentum(m, assess(m), market_3m=8.0, market_6m=12.0) is None


def test_without_a_market_number_nothing_is_offered():
    """기준 없이 '많이 올랐다' 고 말할 수는 없다."""
    m = moving()
    assert score_momentum(m, assess(m), market_3m=None, market_6m=None) is None


def test_a_three_month_only_run_is_flagged():
    """3개월만 오른 것과 꾸준히 오른 것은 다르다."""
    m = moving(r3=30.0, r6=5.0)
    pick = score_momentum(m, assess(m), market_3m=8.0, market_6m=12.0)
    assert any("최근 3개월에만 오른 것일 수 있습니다" in c for c in pick.cautions)


def test_a_price_run_without_the_numbers_behind_it_is_flagged():
    """주가만 오른 상태일 수 있다는 것을 말해야 한다."""
    m = moving()
    m.revenue_growth, m.op_margin, m.op_margin_prior = -0.5, -0.8, -0.2
    m.net_income_ttm, m.roe, m.roic = -90e6, -1.8, None
    m.ocf_ttm, m.fcf_ttm, m.cash, m.total_debt = -70e6, -85e6, 30e6, 200e6
    m.profitable, m.runway_years, m.per = False, 0.4, None

    pick = score_momentum(m, assess(m), market_3m=8.0, market_6m=12.0)

    assert pick is not None                       # 오른 건 사실이니 보여준다
    assert any("주가만 오른 상태일 수 있습니다" in c for c in pick.cautions)


def test_every_category_carries_its_own_warning():
    """숫자만 남기고 한계를 빼면 그게 제일 위험하다."""
    for key in (BLUE, GROWTH, MOMENTUM):
        assert screener.CATEGORY_WARNING[key].strip()
        assert screener.CATEGORY_NAME[key].strip()
        assert screener.CATEGORY_HOW[key].strip()
    assert "앞으로 오른다는 뜻이 전혀 아닙니다" in screener.CATEGORY_WARNING[MOMENTUM]
    assert "증자" in screener.CATEGORY_WARNING[GROWTH]


# --- 갈래별로 줄 세우기 -----------------------------------------------------
def test_each_category_is_ranked_on_its_own():
    """'탄탄함 22점' 과 '시장보다 18%p' 는 단위부터 다른 값이다."""
    picks = [
        Pick(ticker="A", category=BLUE, score=22),
        Pick(ticker="B", category=BLUE, score=19),
        Pick(ticker="C", category=GROWTH, score=40),
        Pick(ticker="D", category=MOMENTUM, score=18),
    ]
    groups = rank_by_category(picks, limit=5)

    assert [p.ticker for p in groups[BLUE]] == ["A", "B"]
    assert [p.ticker for p in groups[GROWTH]] == ["C"]
    assert [p.ticker for p in groups[MOMENTUM]] == ["D"]


def test_one_company_can_appear_in_several_categories():
    """같은 회사를 다른 질문으로 본 것이라, 그게 오히려 정보다."""
    picks = [Pick(ticker="AAPL", category=BLUE, score=22),
             Pick(ticker="AAPL", category=MOMENTUM, score=15)]
    groups = rank_by_category(picks, limit=5)

    assert groups[BLUE][0].ticker == "AAPL"
    assert groups[MOMENTUM][0].ticker == "AAPL"


def test_an_empty_category_stays_empty():
    groups = rank_by_category([Pick(ticker="A", category=BLUE, score=22)], limit=5)
    assert groups[GROWTH] == [] and groups[MOMENTUM] == []


# --- 저장해둔 결과가 갈래를 기억하는가 --------------------------------------
def test_all_categories_of_one_company_survive_a_restart(tmp_path):
    path = tmp_path / "screen.json"
    store = PickStore(path)
    store.remember("AAPL", [Pick(ticker="AAPL", category=BLUE, score=22),
                            Pick(ticker="AAPL", category=MOMENTUM, score=15)], "2026-09-03")
    store.save()

    back = PickStore(path).picks()

    assert sorted(p.category for p in back) == [BLUE, MOMENTUM]


def test_a_file_from_the_one_category_days_still_loads(tmp_path):
    """예전 파일에는 'pick' 하나만 들어 있다. 그걸로 터지면 반나절이 날아간다."""
    path = tmp_path / "screen.json"
    path.write_text(
        '{"AAPL": {"checked": "2026-09-02", "error": "",'
        ' "pick": {"ticker": "AAPL", "score": 20.0, "is_fund": false}}}',
        encoding="utf-8")

    picks = PickStore(path).picks()

    assert len(picks) == 1
    assert picks[0].ticker == "AAPL"
    assert picks[0].category == BLUE          # 갈래가 없던 시절 것은 '탄탄한 회사'
