"""메모의 판단 기준이 코드에 제대로 반영됐는지 확인한다."""

from factories import build_facts

from stockbot.metrics import FAIL, PASS, build_metrics


def _check(metrics, keyword):
    for check in metrics.checks + metrics.priority:
        if keyword in check.label:
            return check
    raise AssertionError(f"체크 항목을 찾지 못함: {keyword}")


def profitable_facts(**overrides):
    kwargs = dict(
        revenue=[100, 110, 120, 130, 140, 150, 160, 170],
        net_income=[10, 11, 12, 13, 15, 16, 17, 18],
        operating_income=[12, 13, 14, 15, 18, 20, 22, 24],
        ocf=[14, 15, 16, 17, 20, 22, 24, 26],
        capex=[2, 2, 2, 2, 3, 3, 3, 3],
        eps=[0.10, 0.11, 0.12, 0.13, 0.15, 0.16, 0.17, 0.18],
        equity=300.0,
        cash=50.0,
        debt=20.0,
        shares=100.0,
    )
    kwargs.update(overrides)
    return build_facts(**kwargs)


def test_profitable_company_uses_profit_checklist():
    m = build_metrics("TEST", profitable_facts())
    assert m.profitable is True
    labels = [c.label for c in m.checks]
    assert labels[0].startswith("①")
    assert "분기 매출 지속" in labels[0]
    assert "자본 효율" in labels[1]
    assert "영업이익률 방향" in labels[2]
    assert "PER" in labels[3]
    assert "영업현금흐름" in labels[4]


def test_ttm_and_growth():
    m = build_metrics("TEST", profitable_facts())
    assert m.revenue_ttm == 140 + 150 + 160 + 170
    assert m.revenue_ttm_prior == 100 + 110 + 120 + 130
    assert round(m.revenue_growth, 4) == round((620 - 460) / 460, 4)


def test_roe_threshold_15_percent():
    m = build_metrics("TEST", profitable_facts())
    # 순이익 TTM 66 / 자기자본 300 = 22%
    assert round(m.roe, 3) == 0.22
    assert _check(m, "자본 효율").status == PASS

    weak = build_metrics("TEST", profitable_facts(equity=2000.0))
    assert weak.roe < 0.15
    assert _check(weak, "자본 효율").status == FAIL


def test_roic_uses_invested_capital():
    m = build_metrics("TEST", profitable_facts())
    # NOPAT = 영업이익 84 * (1-0.21) = 66.36, 투하자본 = 300 + 20 - 50 = 270
    assert round(m.roic, 4) == round(84 * 0.79 / 270, 4)


def test_operating_margin_direction():
    improving = build_metrics("TEST", profitable_facts())
    assert _check(improving, "영업이익률 방향").status == PASS
    assert improving.op_margin > improving.op_margin_prior

    shrinking = build_metrics(
        "TEST", profitable_facts(operating_income=[20, 20, 20, 20, 10, 10, 10, 10])
    )
    assert _check(shrinking, "영업이익률 방향").status == FAIL


def test_ocf_must_exceed_net_income():
    good = build_metrics("TEST", profitable_facts())
    assert _check(good, "영업현금흐름").status == PASS

    bad = build_metrics("TEST", profitable_facts(ocf=[5, 5, 5, 5, 5, 5, 5, 5]))
    check = _check(bad, "영업현금흐름")
    assert check.status == FAIL
    assert "현금이 안 들어옴" in check.detail


def test_priority_order_is_guidance_surprise_margin():
    m = build_metrics("TEST", profitable_facts())
    labels = [c.label for c in m.priority]
    assert labels[0].startswith("1순위 · 가이던스")
    assert labels[1].startswith("2순위 · 어닝 서프라이즈")
    assert labels[2].startswith("3순위 · 마진 방향")


def test_earnings_surprise_against_consensus():
    beat = build_metrics("TEST", profitable_facts(), consensus_eps=0.15)
    assert _check(beat, "어닝 서프라이즈").status == PASS
    assert round(beat.surprise["eps_surprise_pct"], 1) == 20.0  # 0.18 vs 0.15

    miss = build_metrics("TEST", profitable_facts(), consensus_eps=0.25)
    assert _check(miss, "어닝 서프라이즈").status == FAIL


# --------------------------------------------------------------------------
# 적자 기업
# --------------------------------------------------------------------------
def loss_facts(**overrides):
    kwargs = dict(
        revenue=[50, 55, 60, 65, 70, 78, 86, 96],       # YoY +50%
        net_income=[-30, -30, -30, -30, -25, -25, -25, -25],
        operating_income=[-32, -32, -32, -32, -27, -27, -27, -27],
        ocf=[-25, -25, -25, -25, -20, -20, -20, -20],
        capex=[5, 5, 5, 5, 5, 5, 5, 5],
        equity=400.0,
        cash=300.0,
        shares=200.0,
    )
    kwargs.update(overrides)
    return build_facts(**kwargs)


def test_loss_company_uses_loss_checklist():
    m = build_metrics("LOSS", loss_facts())
    assert m.profitable is False
    labels = [c.label for c in m.checks]
    assert "매출 성장률 30%+" in labels[0]
    assert "적자 축소" in labels[1]
    assert "런웨이" in labels[2]
    assert "PSR" in labels[3]
    assert "마일스톤" in labels[4]


def test_growth_threshold_30_percent():
    m = build_metrics("LOSS", loss_facts())
    assert _check(m, "매출 성장률").status == PASS

    slow = build_metrics("LOSS", loss_facts(revenue=[50, 55, 60, 65, 52, 57, 62, 67]))
    assert _check(slow, "매출 성장률").status == FAIL


def test_shrinking_loss_passes():
    m = build_metrics("LOSS", loss_facts())
    check = _check(m, "적자 축소")
    assert check.status == PASS
    assert "적자 축소" in check.detail


def test_growing_revenue_with_growing_loss_is_flagged_worst():
    m = build_metrics("LOSS", loss_facts(net_income=[-20, -20, -20, -20, -40, -40, -40, -40]))
    check = _check(m, "적자 축소")
    assert check.status == FAIL
    assert "최악" in check.detail


def test_runway_under_two_years_is_forbidden():
    # 현금 300, 연간 소진(FCF) = 20*4 + 5*4 = 100 → 3.0년
    ok = build_metrics("LOSS", loss_facts())
    assert round(ok.runway_years, 1) == 3.0
    assert _check(ok, "런웨이").status == PASS

    tight = build_metrics("LOSS", loss_facts(cash=120.0))
    check = _check(tight, "런웨이")
    assert check.status == FAIL
    assert "금지" in check.detail


def test_no_facts_produces_warning_not_crash():
    m = build_metrics("NONE", None)
    assert m.warnings
    assert m.checks == []
