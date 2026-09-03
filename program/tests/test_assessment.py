"""상황 판단. 모든 결론이 계산된 숫자에서 나와야 한다."""

from factories import build_facts

from stock_analysis.assessment import GOOD, POOR, UNKNOWN, assess
from stock_analysis.metrics import build_metrics


def axis(verdict, key):
    return next(a for a in verdict.axes if a.key == key)


def healthy():
    return build_metrics("GOOD", build_facts(
        revenue=[80e9, 85e9, 90e9, 95e9, 100e9, 110e9, 120e9, 130e9],
        net_income=[18e9, 19e9, 20e9, 21e9, 23e9, 25e9, 27e9, 29e9],
        operating_income=[22e9, 23e9, 25e9, 26e9, 30e9, 33e9, 36e9, 40e9],
        ocf=[26e9, 28e9, 30e9, 32e9, 36e9, 40e9, 44e9, 48e9],
        # 자기자본은 순이익에 견줘 현실적인 크기여야 한다. 너무 작게 잡으면
        # ROE 가 100% 를 넘어가는데, 그런 회사는 실제로 거의 없다.
        capex=[3e9] * 8, equity=400e9, cash=60e9, debt=20e9, shares=15e9,
    ))


def struggling():
    return build_metrics("LOSS", build_facts(
        revenue=[100e6, 100e6, 100e6, 100e6, 95e6, 92e6, 90e6, 88e6],
        net_income=[-20e6] * 4 + [-40e6] * 4,
        operating_income=[-22e6] * 4 + [-44e6] * 4,
        ocf=[-18e6] * 4 + [-38e6] * 4,
        capex=[5e6] * 8, cash=100e6, equity=120e6, shares=200e6,
    ))


def test_all_five_axes_are_reported():
    verdict = assess(healthy())
    assert [a.key for a in verdict.axes] == [
        "growth", "profit", "stability", "cash", "valuation"
    ]


def test_healthy_company_is_rated_well():
    verdict = assess(healthy())
    assert verdict.level == GOOD
    assert axis(verdict, "growth").level == GOOD
    assert axis(verdict, "profit").level == GOOD
    assert axis(verdict, "cash").level == GOOD


def test_struggling_company_is_flagged():
    verdict = assess(struggling())
    assert verdict.level == POOR
    assert axis(verdict, "growth").level == POOR      # 역성장
    assert axis(verdict, "profit").level == POOR      # 적자 확대
    assert axis(verdict, "cash").level == POOR        # 현금 유출


def test_every_axis_carries_evidence():
    """근거 없는 판정은 없어야 한다."""
    verdict = assess(healthy())
    for ax in verdict.axes:
        if ax.level != UNKNOWN:
            assert ax.evidence, f"{ax.name} 에 근거가 없습니다"
            assert ax.headline


def test_evidence_contains_actual_numbers():
    verdict = assess(healthy())
    growth = axis(verdict, "growth")
    joined = " ".join(growth.evidence)
    assert "TTM 매출" in joined
    assert "%" in joined


def test_missing_data_is_unknown_not_guessed():
    """데이터가 없으면 추측하지 말고 '판단 불가' 여야 한다."""
    thin = build_metrics("THIN", build_facts(revenue=[1e6] * 2, net_income=[1e5] * 2))
    verdict = assess(thin)
    unknown = [a for a in verdict.axes if a.level == UNKNOWN]
    assert unknown
    for ax in unknown:
        assert "없" in ax.headline or "부족" in ax.headline or "못" in ax.headline


def test_no_facts_at_all():
    verdict = assess(build_metrics("NONE", None))
    assert verdict.level == UNKNOWN
    assert "판단할 수 없" in verdict.headline


def test_runway_under_two_years_is_poor_stability():
    tight = build_metrics("TIGHT", build_facts(
        revenue=[50e6] * 8, net_income=[-30e6] * 8, operating_income=[-32e6] * 8,
        ocf=[-25e6] * 8, capex=[5e6] * 8, cash=60e6, equity=80e6, shares=100e6,
    ))
    verdict = assess(tight)
    stability = axis(verdict, "stability")
    assert stability.level == POOR
    assert "증자" in stability.headline or "미달" in stability.headline


def test_debt_is_judged_by_ability_to_repay_not_by_equity_ratio():
    """자사주 매입이 많은 우량기업은 부채 > 자기자본이 정상이다.

    자본 대비만 보면 애플 같은 회사가 '주의' 로 나온다. 영업이익으로
    갚을 수 있는지로 판단해야 한다.
    """
    verdict = assess(healthy())          # 부채 20B, 현금 60B → 순현금
    assert axis(verdict, "stability").level == GOOD

    leveraged = build_metrics("LEV", build_facts(
        revenue=[100e9] * 8, net_income=[20e9] * 8, operating_income=[30e9] * 8,
        ocf=[35e9] * 8, capex=[3e9] * 8, equity=50e9, cash=20e9, debt=60e9, shares=10e9,
    ))
    stability = axis(assess(leveraged), "stability")
    # 순부채 40B ÷ 영업이익 120B = 0.3년치 → 감당 가능
    assert stability.level == GOOD
    assert "년치" in " ".join(stability.evidence)


def test_heavy_debt_with_thin_profit_is_flagged():
    heavy = build_metrics("HEAVY", build_facts(
        revenue=[100e9] * 8, net_income=[1e9] * 8, operating_income=[2e9] * 8,
        ocf=[3e9] * 8, capex=[1e9] * 8, equity=10e9, cash=5e9, debt=60e9, shares=10e9,
    ))
    assert axis(assess(heavy), "stability").level == POOR


def test_watch_points_reference_the_memo_priorities():
    verdict = assess(healthy())
    joined = " ".join(verdict.watch_points)
    assert "가이던스" in joined
    assert "컨센서스" in joined or "서프라이즈" in joined


def test_growing_revenue_with_growing_loss_is_called_out():
    bad_mix = build_metrics("MIX", build_facts(
        revenue=[50e6, 55e6, 60e6, 65e6, 80e6, 90e6, 100e6, 110e6],
        net_income=[-10e6] * 4 + [-30e6] * 4,
        operating_income=[-11e6] * 4 + [-33e6] * 4,
        ocf=[-9e6] * 4 + [-28e6] * 4, cash=500e6, equity=600e6, shares=100e6,
    ))
    profit = axis(assess(bad_mix), "profit")
    assert profit.level == POOR
    assert "가장 나쁜" in profit.headline


def test_headline_summarises_strong_and_weak_axes():
    verdict = assess(struggling())
    assert "적자 기업" in verdict.headline
    assert "주의" in verdict.headline


# --- 가이던스가 실제로 채워졌을 때 -------------------------------------------
def test_watch_points_change_once_guidance_is_found():
    from stock_analysis.metrics import apply_guidance
    from stock_analysis.track_record import TrackItem, TrackRecord

    m = healthy()
    before = assess(m)
    assert any("아직 찾지 못했습니다" in p for p in before.watch_points)

    class G:
        found = True
        form, filing_date = "8-K", "2026-05-08"
        items = [type("I", (), {"range_text": "$230.0M ~ $240.0M",
                                "period": "2분기", "metric": "매출"})()]

    record = TrackRecord(ticker=m.ticker, items=[
        TrackItem(filed="2026-02-27", url="u", sentence="s", metric="매출",
                  low=1.0, high=2.0, actual=3.0, verdict="상회"),
    ])
    apply_guidance(m, G(), record)
    after = assess(m)

    assert any("과거 이행 이력이 있습니다" in p for p in after.watch_points)
    assert m.priority[0].status != "na"
    assert "1번 지켰고" in m.priority[0].detail


def test_heavy_dilution_becomes_a_watch_point():
    m = healthy()
    m.share_growth_1y = 0.24
    verdict = assess(m)
    assert any("희석" in p for p in verdict.watch_points)
    assert any("발행주식수" in e for a in verdict.axes for e in a.evidence)
