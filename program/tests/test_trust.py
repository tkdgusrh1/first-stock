"""판단에 쓸 값과 참고로만 볼 값을 가르는 규칙.

같은 SEC 공시에서 나온 숫자라도 다 같은 무게가 아니다. 어떤 값은 그 위에
나눗셈을 얹은 것이라, 분모가 이상해지면 **숫자만 멀쩡하고 뜻은 사라진다.**
그런 값으로 '양호' 를 주면 맞는 숫자로 틀린 판단을 하게 된다.

여기서 지키는 두 가지:
  1) 못 미더운 값은 점수에서 뺀다
  2) 그래도 화면에서 지우지는 않는다 — 값이 없는 것과 못 미더운 것은 다르다
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analysis.assessment import GOOD, assess  # noqa: E402
from stock_analysis.metrics import Metrics  # noqa: E402
from stock_analysis.screener import score_company, score_growth  # noqa: E402
from stock_analysis.trust import doubts, notes_from  # noqa: E402


def solid(ticker="OK") -> Metrics:
    """어느 값도 이상하지 않은 평범한 회사."""
    m = Metrics(ticker=ticker, company=f"{ticker} 주식회사")
    m.revenue_ttm, m.revenue_ttm_prior, m.revenue_growth = 1000e6, 700e6, 0.43
    m.operating_income_ttm, m.op_margin, m.op_margin_prior = 250e6, 0.25, 0.19
    m.net_income_ttm, m.equity, m.roe, m.roic = 200e6, 800e6, 0.25, 0.18
    m.price, m.per, m.psr = 100.0, 20.0, 6.0
    m.cash, m.total_debt, m.ocf_ttm, m.fcf_ttm = 900e6, 100e6, 300e6, 220e6
    m.profitable = True
    return m


# --- 자본 효율 --------------------------------------------------------------
#
# 실제로 화면에서 본 값이다. 애플이 "자본 효율 100.5%" 로 떴는데, 그건
# 자사주를 오래 사들여 자기자본이 쪼그라든 결과지 사업이 그만큼 좋다는
# 뜻이 아니다.


def test_a_sky_high_return_on_equity_is_not_used_to_judge():
    m = solid()
    m.roe, m.roic = 1.005, 1.005

    found = doubts(m)

    assert "roe" in found and "roic" in found
    assert "자사주" in found["roe"].reason


def test_a_normal_return_on_equity_is_trusted():
    assert "roe" not in doubts(solid())
    assert "roic" not in doubts(solid())


def test_negative_equity_makes_the_ratio_meaningless():
    m = solid()
    m.equity, m.roe = -50e6, -4.0
    assert "roe" in doubts(m)
    assert "0 이하" in doubts(m)["roe"].reason


def test_a_collapsed_denominator_cannot_earn_a_good_rating():
    """맞는 숫자로 틀린 판단을 하지 않는다."""
    m = solid()
    m.roe, m.roic = 1.005, 1.005

    axis = next(a for a in assess(m).axes if a.key == "profit")

    assert axis.level != GOOD
    assert "믿기 어려워" in axis.headline
    assert any("판단에서 뺌(참고)" in e for e in axis.evidence)   # 값은 남는다


def test_the_value_is_still_shown_after_being_excluded():
    """값이 없는 것과 못 미더운 것은 다르다. ROE 100% 도 그 자체로 정보다."""
    m = solid()
    m.roe, m.roic = 1.005, 1.005

    pick = score_company(m, assess(m))

    assert pick is not None
    assert any("100.5%" in note for note in pick.notes)
    assert not any("ROIC" in r and "잘 벌고" in r for r in pick.reasons)   # 점수엔 안 씀


def test_excluding_a_shaky_value_lowers_the_score():
    plain, inflated = solid(), solid("INFLATED")
    inflated.roe, inflated.roic = 1.005, 1.005

    assert score_company(inflated, assess(inflated)).score < score_company(plain, assess(plain)).score


# --- 성장률의 밑 ------------------------------------------------------------
def test_growth_off_a_tiny_base_is_not_called_growth():
    """$1M → $3M 도 +200% 다. 회사가 커지는 속도라고 부를 수 없다."""
    m = solid()
    m.revenue_ttm, m.revenue_ttm_prior, m.revenue_growth = 9e6, 3e6, 2.0

    assert "revenue_growth" in doubts(m)
    assert score_growth(m, assess(m)) is None      # 성장 갈래에 넣지 않는다


def test_growth_off_a_real_base_is_trusted():
    m = solid()
    m.revenue_ttm, m.revenue_ttm_prior, m.revenue_growth = 900e6, 600e6, 0.5
    assert "revenue_growth" not in doubts(m)


# --- 런웨이 ------------------------------------------------------------------
def test_runway_means_nothing_for_a_profitable_company():
    m = solid()
    m.runway_years = 12.0
    assert "runway_years" in doubts(m)
    assert "흑자 기업이라" in doubts(m)["runway_years"].reason


def test_an_absurdly_long_runway_is_a_side_effect_of_the_maths():
    m = solid()
    m.profitable, m.runway_years = False, 140.0
    assert "runway_years" in doubts(m)


def test_a_normal_runway_is_trusted():
    m = solid()
    m.profitable, m.runway_years = False, 3.2
    assert "runway_years" not in doubts(m)


# --- PER ---------------------------------------------------------------------
def test_a_per_in_the_hundreds_means_the_denominator_collapsed():
    m = solid()
    m.per = 850.0
    assert "per" in doubts(m)
    assert "비싸다는 뜻으로 읽으면 안 됩니다" in doubts(m)["per"].reason


def test_the_five_year_per_median_is_always_reference_only():
    """실적이 언제 공개됐는지를 추정해 주가와 맞춘 값이다."""
    m = solid()
    m.per_median_5y = 24.0
    assert "per_median_5y" in doubts(m)
    assert "추정" in doubts(m)["per_median_5y"].reason


# --- 희석 --------------------------------------------------------------------
def test_dilution_from_a_short_record_is_not_a_trend():
    m = solid()
    m.share_growth_1y = 0.30
    m.shares_trend = [(date(2026, 1, 1), 100e6), (date(2026, 4, 1), 130e6)]
    assert "share_growth_1y" in doubts(m)


def test_dilution_with_enough_history_is_trusted():
    m = solid()
    m.share_growth_1y = 0.30
    m.shares_trend = [(date(2025, 1 + i, 1), 100e6 + i) for i in range(8)]
    assert "share_growth_1y" not in doubts(m)


# --- 나오는 문장 --------------------------------------------------------------
def test_every_excluded_value_says_why_and_shows_the_number():
    m = solid()
    m.roe = m.roic = 1.2
    m.per_median_5y = 24.0

    lines = notes_from(doubts(m))

    assert len(lines) >= 3
    for line in lines:
        assert "—" in line                          # 값과 이유가 함께
        assert any(ch.isdigit() for ch in line)     # 숫자가 그대로 남아 있다


def test_a_clean_company_has_nothing_to_exclude():
    assert notes_from(doubts(solid())) == []
