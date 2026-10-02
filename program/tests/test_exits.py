"""청산 규칙 — 손실 상한 · 절반 익절 · 본전 손절 · 추적 손절 · 청산 규칙 비교 · 방어형 ETF 바구니.

약속:
1. 손절선은 올라가기만 한다(내려가지 않는다).
2. 장중 순서를 모르니 손절을 먼저 본다(나쁜 쪽 가정).
3. 갭 하락이면 손절선이 아니라 시가에 판다 — 본전 손절도 손실이 날 수 있다.
4. 비교 표는 비용을 빼고 번 것들 중에서만 '가장 덜 흔들린 것' 을 짚는다.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from stock_analysis.dashboard import Dashboard, quant_settings
from stock_analysis.prices import Candle
from stock_analysis.quant import backtest, exits, paper
from stock_analysis.quant.costs import CostModel
from stock_analysis.quant.engine import Engine, Position, prepare
from stock_analysis.quant.sizing import Plan, RiskRules

FREE = CostModel(commission=0.0, sell_tax=0.0, slippage=0.0)
QUIET = Plan(check_days=(), min_hold=0)        # 새 신호 없이 청산만 본다


class Never(SimpleNamespace):
    key, name, kind, warmup = "never", "시험", "signal", 10_000

    def stop(self, *a):
        return None


def engine(plan=QUIET, costs=FREE):
    eng = Engine(Never(), RiskRules(daily_loss_stop=0, dd_half=0, dd_stop=0), costs, 10_000, plan=plan)
    return eng


def bars_from(rows, start=date(2024, 1, 2)):
    """(시가, 고가, 저가, 종가) 목록 → 평일 봉."""
    out, d = [], start
    for o, h, lo, c in rows:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(Candle(d, o, h, lo, c, 1e6))
        d += timedelta(days=1)
    return out


def hold(eng, shares=10, cost=100.0, stop=90.0):
    eng.positions["A"] = Position("A", shares, cost, "2024-01-01", stop, 0.0, risk=cost - stop, high=cost)
    eng.cash -= shares * cost


def run(eng, bars):
    b, c, d, _ = prepare({"A": bars})["A"]
    for i, day in enumerate(d):
        eng.on_day(date.fromisoformat(day), {"A": (b, c, d, i)})


# --------------------------------------------------------------------------
# 규칙 하나씩
# --------------------------------------------------------------------------
def test_half_is_taken_at_one_r_and_the_rest_is_protected_at_breakeven():
    eng = engine(exits.apply(QUIET, "half"))
    hold(eng)
    run(eng, bars_from([(100, 111, 99, 108),        # +1R(110) 에 닿음 → 5주 110 에 팔고 손절선 본전
                        (107, 108, 99.5, 101)]))     # 본전(100) 을 건드림 → 나머지 5주 100
    sold = [(t.shares, t.exit_price, t.reason) for t in eng.trades]
    assert sold[0] == (5, 110.0, "절반 익절 (+1R)")
    assert sold[1][:2] == (5, 100.0) and sold[1][2] == "이익 보호선 도달"
    assert "A" not in eng.positions
    assert sum(t.pnl for t in eng.trades) == pytest.approx(50.0)     # 50 벌고, 나머지는 본전


def test_the_stop_is_checked_before_the_target_on_the_same_bar():
    """하루에 손절선과 목표가를 둘 다 건드리면 손절로 본다 — 좋은 쪽으로 가정하지 않는다."""
    eng = engine(exits.apply(QUIET, "half"))
    hold(eng)
    run(eng, bars_from([(100, 112, 89, 100)]))
    assert [t.reason for t in eng.trades] == ["손절선 도달"]
    assert eng.trades[0].exit_price == 90.0


def test_a_gap_below_breakeven_still_loses():
    eng = engine(exits.apply(QUIET, "breakeven"))
    hold(eng)
    run(eng, bars_from([(100, 111, 100, 110.5),     # 종가가 +1R 넘음 → 내일부터 손절선 본전
                        (95, 96, 94, 95)]))          # 본전 아래로 갭 → 시가 95 에 팔림
    assert eng.trades[-1].exit_price == 95.0 and eng.trades[-1].pnl < 0


def test_breakeven_includes_fees_and_tax():
    costs = CostModel(commission=0.001, sell_tax=0.002, slippage=0.001)
    even = costs.breakeven(100.0)
    paid = 100.0 * (1 + costs.commission)
    got = costs.sell_price(even) * (1 - costs.commission - costs.sell_tax)
    assert got == pytest.approx(paid)                   # 그 값에 팔면 딱 본전
    assert even > 100.3


def test_trailing_stop_only_goes_up():
    eng = engine(exits.apply(QUIET, "trail"))
    # 앞쪽 20일로 ATR 을 만든다(하루 폭 2)
    warm = [(100, 101, 99, 100)] * 21
    hold(eng, stop=80.0)
    run(eng, bars_from(warm + [(100, 121, 99, 120), (118, 119, 115, 116)]))
    stop_after = eng.positions["A"].stop
    assert stop_after > 100                             # 최고 종가 120 − 3×ATR 쪽으로 올라감
    run(eng, bars_from([(116, 117, 112, 113)], start=date(2024, 3, 1)))
    assert eng.positions["A"].stop >= stop_after        # 값이 내려도 손절선은 내려가지 않음


def test_loss_cap_tightens_a_far_stop_but_not_a_near_one():
    eng = engine(exits.apply(QUIET, "cap"))
    assert eng._capped(80.0, 100.0) == pytest.approx(93.0)       # 20% 멀리 → 7% 로
    assert eng._capped(95.0, 100.0) == 95.0                      # 이미 더 가까우면 그대로
    assert eng._capped(None, 100.0) == pytest.approx(93.0)
    assert engine()._capped(80.0, 100.0) == 80.0                 # 끄면 손대지 않음


def test_old_saved_positions_and_plans_still_load():
    eng = engine()
    hold(eng)
    raw = eng.to_dict()
    for p in raw["positions"]:
        for k in ("risk", "high", "half"):
            p.pop(k)
    for k in ("loss_cap", "take_half_r", "breakeven_r", "trail_atr", "exit_key", "universe"):
        raw["plan"].pop(k)
    back = Engine.from_dict(raw, "us")
    assert back.positions["A"].risk == 0.0 and back.plan.trail_atr == 0.0 and back.plan.universe == "watch"


def test_plan_round_trips_the_exit_settings():
    plan = exits.apply(Plan(), "guard")
    again = Plan.from_dict(plan.to_dict())
    assert (again.loss_cap, again.take_half_r, again.breakeven_r, again.trail_atr) == (0.07, 1.0, 1.0, 3.0)
    assert again.exit_key == "guard" and exits.describe(again).startswith("손실 상한 7%")
    assert Plan.from_dict({"loss_cap": 9, "universe": "엉뚱"}).loss_cap == 0.5          # 터무니없는 값은 묶음
    assert Plan.from_dict({"universe": "엉뚱"}).universe == "watch"


# --------------------------------------------------------------------------
# 비교 표
# --------------------------------------------------------------------------
def row(key, total, mdd, cagr):
    return {"exit": key, "metrics": {"total": total, "mdd": mdd, "cagr": cagr}}


def test_the_safest_pick_is_only_among_rules_that_made_money():
    rows = [row("basic", 0.5, 0.30, 0.10), row("cap", -0.05, 0.05, -0.01), row("guard", 0.2, 0.12, 0.05),
            row("trail", 0.4, 0.15, 0.09)]
    picks = backtest.pick_exits(rows)
    assert picks["safest"] == "guard"                    # 잃은 'cap' 은 낙폭이 작아도 짚지 않는다
    assert picks["balanced"] == "trail"                   # 0.09/0.15 = 0.6 이 가장 큼
    assert backtest.pick_exits([row("cap", -0.1, 0.05, -0.02)]) == {"safest": None, "balanced": None}


def test_trade_stats_count_losses_honestly():
    T = SimpleNamespace
    trades = [T(pnl=10, pnl_pct=0.1, exit_day="2024-01-02", entry_day="1"),
              T(pnl=-5, pnl_pct=-0.05, exit_day="2024-01-03", entry_day="1"),
              T(pnl=-7, pnl_pct=-0.07, exit_day="2024-01-04", entry_day="1"),
              T(pnl=4, pnl_pct=0.04, exit_day="2024-01-05", entry_day="1")]
    s = backtest.trade_stats(trades)
    assert s["worst"] == -0.07 and s["loss_streak"] == 2 and s["loss_share"] == 0.5
    assert s["profit_factor"] == pytest.approx(14 / 12)


# --------------------------------------------------------------------------
# 설정 · 바구니 · 화면
# --------------------------------------------------------------------------
def params(**kw):
    return {k: [v] for k, v in kw.items()}


def test_profiles_bring_their_exit_rule_and_custom_picks_are_kept():
    chosen = quant_settings(params(profile="safe", strategy="auto", exit="auto"), "us")
    assert chosen["plan"].exit_key == "guard" and chosen["saved"]["exit"] == "auto"
    chosen = quant_settings(params(profile="safe", strategy="auto", exit="trail", universe="defense"), "us")
    assert chosen["plan"].exit_key == "trail" and chosen["plan"].universe == "defense"
    chosen = quant_settings(params(profile="custom", strategy="rotation", exit="이상한값"), "us")
    assert chosen["plan"].exit_key == "basic"


def test_the_defense_basket_uses_only_the_basket(bot):
    asked = []
    bars = bars_from([(10, 11, 9, 10)] * 3)

    def candles(symbol):
        asked.append(symbol)
        return [] if symbol == "GLD" else bars

    bot.prices.candles = candles
    data = paper.market_data(bot, "us", universe="defense")
    assert set(asked) == {t for t, _ in exits.BASKETS["us"]}
    assert "GLD" not in data and set(data) == {"SPY", "QQQ", "IEF", "TLT", "SHY"}   # 못 받은 건 빠진다
    assert "AAPL" not in data


def test_the_exit_comparison_runs_every_rule_and_is_shown(bot):
    from test_quant import flat_then


    up = [100 + k * 0.5 + (3 if k % 7 == 0 else 0) for k in range(400)]
    bot.prices.candles = lambda symbol: flat_then(up)
    dash = Dashboard(bot)
    dash._background = lambda label, job: job()
    dash.run_action("backtest", {**params(back="/quant?m=us", mode="exits", profile="safe", strategy="rotation",
                                         exit="auto", universe="defense", years="0", capital="1000")})
    found = bot.quant.exits("us")
    assert [r["exit"] for r in found["rows"]] == [p.key for p in exits.PRESETS]
    assert found["universe"] == "defense"
    html = Dashboard(bot).render_path("/quant?m=us")
    assert "청산 규칙 비교" in html and "지키기 세트" in html and "최악 거래" in html
    assert "청산 규칙 6개 비교" in html


def test_the_evidence_card_lists_the_stop_loss_studies():
    from stock_analysis.quant.evidence import STUDIES

    names = " ".join(s.name for s in STUDIES)
    for key in ("Han·Zhou·Zhu", "Kaminski·Lo", "Faber", "Odean", "152년"):
        assert key in names
