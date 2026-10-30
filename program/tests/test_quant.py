"""퀀트 연습장 — 엔진이 미래를 보지 않는지, 규칙대로 사고파는지, 화면이 거짓말을 하지 않는지.

실제 주문 기능은 없다. 그래서 여기서 지키는 약속은 셋이다:
1. 신호는 그 날 종가까지만 보고, 체결은 다음 날 시가에서만 한다.
2. 손절·하루 손실·낙폭 규칙이 적힌 대로 작동한다.
3. 백테스트와 모의 계좌가 같은 규칙으로 돈다.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from stock_analysis.dashboard import Dashboard, quant_settings
from stock_analysis.prices import Candle
from stock_analysis.quant import backtest, paper
from stock_analysis.quant import strategies as strat
from stock_analysis.quant.costs import US_COSTS, CostModel, default_costs
from stock_analysis.quant.engine import Engine, prepare
from stock_analysis.quant.sizing import Plan, RiskRules, shares_to_buy, stage_for
from stock_analysis.quant.store import QuantStore

EVERY_DAY = Plan(check_days=(0, 1, 2, 3, 4), min_hold=0)

FREE = CostModel(commission=0.0, sell_tax=0.0, slippage=0.0)


def days(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def flat_then(closes, start=date(2024, 1, 1), opens=None, lows=None):
    """종가 목록으로 봉을 만든다. 시가는 따로 줄 수 있다(없으면 전날 종가)."""
    bars = []
    for k, (d, c) in enumerate(zip(days(len(closes), start), closes)):
        o = opens[k] if opens else (closes[k - 1] if k else c)
        lo = lows[k] if lows else min(o, c) * 0.999
        bars.append(Candle(d, o, max(o, c) * 1.001, lo, c, 1e6))
    return bars


class OnDay(strat.Strategy):
    """정해진 날(위치)에 사고, 정해진 날에 파는 시험용 전략."""

    def entry(self, bars, closes, i):
        return i == self.warmup + 1

    def exit(self, bars, closes, i, held):
        return "기한" if held >= 3 else None

    def stop(self, bars, closes, i):
        return None


def on_day_strategy(**kw):
    base = dict(key="t", name="시험", kind="signal", buy_rule="", sell_rule="", hold="", evidence="",
                advice="", warmup=5)
    base.update(kw)
    return OnDay(**base)


# --------------------------------------------------------------------------
# 1. 미래를 보지 않는다
# --------------------------------------------------------------------------
def test_signal_at_close_fills_at_next_open_not_same_close():
    closes = [100.0] * 7 + [110, 120, 130, 140, 150]
    opens = [100.0] * 7 + [105, 115, 125, 135, 145]
    bars = flat_then(closes, opens=opens)
    r = backtest.run(on_day_strategy(), RiskRules(risk_per_trade=1, max_weight=1, max_positions=1,
                                                  daily_loss_stop=0, dd_half=0, dd_stop=0),
                     FREE, 10_000, {"A": bars}, plan=EVERY_DAY)
    trade = r["trades"][-1]
    # 신호는 6번째 날(i=6) 종가 100 에서 났고, 체결은 7번째 날 시가 105 — 같은 날 종가 100 이 아니다.
    assert trade["entry_day"] == bars[7].day.isoformat()
    assert trade["entry_price"] == pytest.approx(105)
    # 3일 보유 뒤 그 날 종가에서 신호 → 다음 날 시가에 판다.
    assert trade["exit_day"] == bars[11].day.isoformat()
    assert trade["exit_price"] == pytest.approx(145)


def test_stop_sells_at_the_stop_or_at_a_lower_gap_open():
    closes = [100.0] * 30
    lows = [99.0] * 30
    opens = [100.0] * 30
    lows[26], opens[27], lows[27] = 99, 90, 89          # 27번째 날 갭 하락으로 손절선(95) 아래에서 열림
    bars = flat_then(closes, opens=opens, lows=lows)
    engine = Engine(strat.STRATEGIES["breakout"], RiskRules(daily_loss_stop=0, dd_half=0, dd_stop=0),
                    FREE, 10_000)
    prepared = prepare({"A": bars})
    b, c, d, _ = prepared["A"]
    from stock_analysis.quant.engine import Position
    engine.positions["A"] = Position("A", 10, 100.0, d[20], 95.0)
    for k in range(21, 28):
        engine.on_day(b[k].day, {"A": (b, c, d, k)})
    t = engine.trades[-1]
    assert t.reason == "손절선 도달"
    assert t.exit_price == pytest.approx(90)          # 손절가 95 가 아니라 실제로 열린 90 — 좋게 쳐주지 않는다


def test_buy_is_cancelled_when_open_is_already_below_the_stop():
    engine = Engine(strat.STRATEGIES["breakout"], RiskRules(), FREE, 10_000)
    bars = flat_then([100.0] * 3, opens=[100, 100, 80])
    b, c, d, _ = prepare({"A": bars})["A"]
    from stock_analysis.quant.engine import Order
    engine.orders.append(Order("A", "buy", 5, "신호", d[1], stop=90.0))
    engine.on_day(b[2].day, {"A": (b, c, d, 2)})
    assert "A" not in engine.positions
    assert any("손절선 아래" in e["text"] for e in engine.events)


# --------------------------------------------------------------------------
# 2. 얼마나 사나 · 비용 · 계좌 규칙
# --------------------------------------------------------------------------
def test_position_size_follows_the_report_example():
    # 계좌 100만원, 한 번에 2%, 10,000원에 사서 9,200원 손절 → 25주
    rules = RiskRules(risk_per_trade=0.02, max_weight=0.35)
    assert shares_to_buy(rules, 1_000_000, 1_000_000, 10_000, 9_200, None, 1.0, 10_000) == 25
    # 낙폭 규칙으로 위험이 절반이면 12주
    assert shares_to_buy(rules, 1_000_000, 1_000_000, 10_000, 9_200, None, 0.5, 10_000) == 12
    # 한 종목 최대 비중이 더 작으면 그쪽이 먼저
    assert shares_to_buy(RiskRules(risk_per_trade=0.02, max_weight=0.1), 1_000_000, 1_000_000,
                         10_000, 9_200, None, 1.0, 10_000) == 10
    # 1주 값보다 현금이 적으면 0 — 못 산다고 센다
    assert shares_to_buy(rules, 1_000_000, 5_000, 10_000, 9_200, None, 1.0, 10_000) == 0


def test_us_round_trip_cost_has_no_sell_tax():
    assert US_COSTS.sell_tax == 0 and default_costs() is US_COSTS
    # 수수료 양쪽 + 체결 차이 양쪽
    assert US_COSTS.round_trip == pytest.approx(0.0025 * 2 + 0.001 * 2)


def test_daily_loss_rule_blocks_next_day_entries_and_drawdown_rule_halts():
    rules = RiskRules(daily_loss_stop=0.03, dd_half=0, dd_stop=0.25)
    engine = Engine(strat.STRATEGIES["breakout"], rules, FREE, 1_000)
    engine.on_day(date(2024, 1, 2), {})
    engine.cash = 960                       # 하루 -4%
    engine.on_day(date(2024, 1, 3), {})
    assert engine.block_next
    engine.cash = 700                       # 고점 대비 -30%
    engine.on_day(date(2024, 1, 4), {})
    assert engine.halted_on == "2024-01-04"


class BuyTwice(OnDay):
    """6번째 날과 30번째 날에 사려 하고, 사흘 뒤 판다."""

    def entry(self, bars, closes, i):
        return i in (6, 30)


def test_backtest_keeps_going_after_the_drawdown_limit_but_the_paper_account_stops():
    closes = [100.0] * 7 + [100, 60, 60, 60, 60] + [60.0] * 30         # 사자마자 -40% — 낙폭 한도(-25%)에 걸린다
    bars = flat_then(closes)
    rules = RiskRules(risk_per_trade=1, max_weight=1, max_positions=1, daily_loss_stop=0, dd_half=0, dd_stop=0.25)
    test_strategy = BuyTwice(key="t", name="시험", kind="signal", buy_rule="", sell_rule="", hold="", evidence="",
                             advice="", warmup=5)
    r = backtest.run(test_strategy, rules, FREE, 10_000, {"A": bars}, plan=EVERY_DAY)
    assert r["halted_on"] and len(r["trades"]) == 2                     # 멈춘 날을 적고, 두 번째 신호도 샀다
    assert "모의 계좌라면 거기서 새 매수를 멈추고" in " ".join(r["warnings"])
    engine = Engine(test_strategy, rules, FREE, 10_000, plan=EVERY_DAY)  # 모의 계좌(기본) — 멈춘 뒤엔 사지 않는다
    prepared = prepare({"A": bars})
    b, c, d, _ = prepared["A"]
    for k in range(len(b)):
        engine.on_day(b[k].day, {"A": (b, c, d, k)})
    assert engine.halted_on and len(engine.trades) == 1 and engine.blocked == 1


def test_a_signal_without_cash_left_is_not_blamed_on_a_small_account():
    """한 종목 최대 비중 97% · 최대 2종목 — 첫 종목이 현금을 거의 다 쓴 다음 날의 신호는 '현금 부족' 이다."""
    rules = RiskRules(risk_per_trade=1, max_weight=0.97, max_positions=2, daily_loss_stop=0, dd_half=0, dd_stop=0)
    cheap = flat_then([10.0] * 12)
    pricey = flat_then([50.0] * 11, start=cheap[1].day)                     # 하루 늦게 시작 — 신호도 하루 늦다
    prepared = prepare({"A": cheap, "B": pricey})

    def run(capital, tickers):
        engine = Engine(on_day_strategy(), rules, FREE, capital, plan=EVERY_DAY)
        for bar in cheap:
            iso = bar.day.isoformat()
            engine.on_day(bar.day, {t: (b, c, d, d.index(iso)) for t, (b, c, d, _) in prepared.items()
                                    if t in tickers and iso in d})
        return engine

    full = run(1_000, {"A", "B"})
    assert full.no_cash == 1 and full.skipped == 0
    small = run(40, {"B"})                                                  # 40 의 97% < 1주 50
    assert small.skipped == 1 and small.no_cash == 0


def test_stage_follows_account_size_and_refuses_to_guess():
    assert stage_for(1_000_000).key == "attack"
    assert stage_for(5_000_000).key == "balance"
    assert stage_for(20_000_000).key == "spread"
    assert stage_for(None) is None


# --------------------------------------------------------------------------
# 3. 전략 · 백테스트 결과
# --------------------------------------------------------------------------
def trending(n, up=True, start=100.0, step=0.004):
    closes, p = [], start
    for k in range(n):
        p *= (1 + step) if up else (1 - step)
        closes.append(round(p, 4))
    return flat_then(closes)


def test_rotation_holds_the_stronger_trend_and_skips_the_falling_one():
    data = {"UP": trending(320), "DOWN": trending(320, up=False)}
    r = backtest.run(strat.STRATEGIES["rotation"], RiskRules(max_positions=1, daily_loss_stop=0),
                     FREE, 10_000, data)
    bought = {t["ticker"] for t in r["trades"]} | {p["ticker"] for p in r["open"]}
    assert bought == {"UP"}


def test_backtest_reports_costs_benchmark_and_honest_warnings():
    data = {"UP": trending(320)}
    r = backtest.run(strat.STRATEGIES["breakout"], RiskRules(), US_COSTS, 10_000, data)
    m = r["metrics"]
    assert m["fees"] >= 0 and r["bench"]["cagr"] > 0
    text = " ".join(r["warnings"])
    assert "생존 편향" in text
    assert "종목이 1개뿐" in text
    assert "거래가" in text            # 30번 미만이면 믿기 어렵다고 적는다


def test_split_and_curve_are_kept_small_for_the_screen():
    r = backtest.run(strat.STRATEGIES["ma_cross"], RiskRules(), FREE, 10_000, {"A": trending(900)})
    assert len(r["curve"]) <= backtest.CURVE_POINTS
    assert r["split"]["cut"] > r["start"]


def test_engine_survives_a_save_and_load():
    engine = Engine(strat.STRATEGIES["pullback"], RiskRules(), US_COSTS, 10_000)
    from stock_analysis.quant.engine import Order, Position
    engine.positions["AAPL"] = Position("AAPL", 3, 230.0, "2026-09-01", 215.0, 1.7)
    engine.orders.append(Order("MSFT", "buy", 2, "신호", "2026-09-30", 380.0))
    engine.last_day = "2026-09-30"
    again = Engine.from_dict(engine.to_dict())
    assert again.positions["AAPL"].stop == 215.0
    assert again.orders[0].ticker == "MSFT"
    assert again.strategy.key == "pullback" and again.last_day == "2026-09-30"


# --------------------------------------------------------------------------
# 4. 모의 계좌 — 백테스트와 같은 규칙, 끝난 날만
# --------------------------------------------------------------------------
def fake_bot(bars_by_ticker, market="us", live=False, tmp=None):
    targets, metrics = [], {}
    for k, (t, bars) in enumerate(bars_by_ticker.items()):
        targets.append(SimpleNamespace(ticker=t, market=market, cik=str(k), key=str(k), name=t,
                                       watch=SimpleNamespace(name=t)))
        metrics[str(k)] = SimpleNamespace(bars=bars, market_open=live, revenue_growth=0.3, roic=0.2,
                                          surprise={"eps_surprise_pct": 5.0})
    return SimpleNamespace(cached_targets=lambda: targets, cached_metrics=lambda: metrics,
                           quant=QuantStore(tmp / "quant.json"), market_snapshot=lambda: None)


def test_paper_account_skips_the_unfinished_bar_while_the_market_is_open(tmp_path):
    bars = trending(80)
    bot = fake_bot({"A": bars}, live=True, tmp=tmp_path)
    data = paper.market_data(bot, "us")
    assert data["A"][-1].day == bars[-2].day           # 장중의 오늘 봉은 쓰지 않는다


def test_paper_account_fills_next_open_like_the_backtest(tmp_path):
    bars = trending(120)
    bot = fake_bot({"A": bars[:100]}, tmp=tmp_path)
    store = bot.quant
    message = paper.start(store, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1),
                          plan=EVERY_DAY)
    assert "모의 계좌를 시작" in message
    pending = store.paper("us")["engine"]["orders"]
    assert pending and pending[0]["side"] == "buy"      # 계속 오르니 신고가 돌파
    bot.cached_metrics()["0"].bars = bars[:101]           # 다음 거래일이 끝남
    days_done, events = paper.step(store, bot, "us")
    assert days_done == 1
    pos = store.paper("us")["engine"]["positions"][0]
    assert pos["entry_day"] == bars[100].day.isoformat()
    assert pos["cost"] == pytest.approx(bars[100].open)
    assert paper.step(store, bot, "us") == (0, [])       # 같은 날을 두 번 처리하지 않는다


def _scaled(bars, k):
    return [Candle(b.day, b.open / k, b.high / k, b.low / k, b.close / k, b.volume) for b in bars]


def test_a_split_in_a_held_stock_changes_the_share_count_not_the_account_value(tmp_path):
    """야후는 분할 뒤 지난 값까지 새 기준으로 고친다. 맞추지 않으면 10:1 분할이 -90% 손실·가짜 손절로 기록된다."""
    bars = trending(120)
    bot = fake_bot({"A": bars[:100]}, tmp=tmp_path)
    store = bot.quant
    paper.start(store, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1), plan=EVERY_DAY)
    bot.cached_metrics()["0"].bars = bars[:101]
    paper.step(store, bot, "us")
    held = store.paper("us")["engine"]["positions"][0]
    worth = store.paper("us")["engine"]["curve"][-1][1]
    bot.cached_metrics()["0"].bars = _scaled(bars[:102], 10)            # 다음 날 10:1 분할 — 과거도 새 기준
    days, events = paper.step(store, bot, "us")
    eng = store.paper("us")["engine"]
    pos = eng["positions"][0]
    assert days == 1 and pos["shares"] == pytest.approx(held["shares"] * 10)
    assert pos["cost"] == pytest.approx(held["cost"] / 10) and pos["stop"] == pytest.approx(held["stop"] / 10)
    assert not eng["trades"]                                              # 가짜 손절 없음
    assert eng["last_close"]["A"] == pytest.approx(bars[101].close / 10)
    assert eng["curve"][-1][1] == pytest.approx(eng["cash"] + held["shares"] * bars[101].close)   # 하루 등락만큼만
    assert eng["curve"][-1][1] > worth * 0.95
    assert any(e["text"].startswith("분할·병합 반영 A") for e in events)


def _split_engine(close_now, splits, seen="2026-10-23", fractional=False):
    from stock_analysis.quant.engine import Order, Position

    eng = Engine(strat.STRATEGIES["rotation"], RiskRules(), FREE, 10_000, plan=Plan(fractional=fractional))
    eng.positions["A"] = Position("A", 10, 100.0, "2026-10-01", 90.0, 1.0, risk=10.0, high=110.0)
    eng.orders.append(Order("B", "buy", 4, "신호", "2026-10-23", 45.0))
    eng.last_close, eng.last_day = {"A": 105.0, "B": 50.0}, "2026-10-23"
    if seen:
        eng.last_seen = {"A": seen, "B": "2026-10-23"}
    days = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
    closes = {"A": [104.0, 104.5, 105.0, close_now] if seen != "2026-10-21" else [104.0, close_now, 106.0, 107.0],
              "B": [50.0] * 4}
    prepared = {t: ([], c, days, None) for t, c in closes.items()}
    paper._rebase_splits(eng, prepared, lambda t: splits.get(t, []), "2026-10-24")
    return eng


def test_split_rebase_needs_a_record_unless_the_jump_is_unmistakable():
    same = _split_engine(105.2, {})                                          # 0.2% — 같은 기준
    assert same.positions["A"].shares == 10 and not same.events
    fixed = _split_engine(100.0, {})                                         # 5% 다른데 분할 기록 없음 — 자료 수정
    assert fixed.positions["A"].shares == 10 and fixed.last_close["A"] == 105.0
    big = _split_engine(35.0, {})                                            # 3:1 — 기록 없어도 분할
    a = big.positions["A"]
    assert a.shares == 30 and a.cost == pytest.approx(100 / 3) and a.risk == pytest.approx(10 / 3)
    assert big.last_close["A"] == pytest.approx(35.0) and "기록은 못 받음" in big.events[0]["text"]
    rounded = _split_engine(35.0004, {})                                     # 종가 반올림 — 2.99997 이 아니라 3
    assert rounded.positions["A"].shares == 30 and rounded.cash == 10_000
    old = _split_engine(100.0, {"A": [(date(2026, 9, 1), 1.05)]})            # 이미 반영된 옛 기록은 세지 않는다
    assert old.positions["A"].shares == 10


def test_a_recorded_small_split_uses_the_recorded_ratio_and_pays_the_odd_share_in_cash():
    """한국 무상증자·주식배당처럼 작은 비율 — 온주 계좌의 끝수는 증권사처럼 그 날 값으로 현금."""
    bonus = _split_engine(100.0, {"A": [(date(2026, 10, 24), 1.05)]})        # 기록 1.05, 종가 비율 1.05
    a = bonus.positions["A"]
    assert a.shares == 10 and a.cost == pytest.approx(100 / 1.05)            # 10.5주 → 10주 + 끝수 0.5주
    assert bonus.cash == pytest.approx(10_000 + 0.5 * 100.0)                 # 0.5주 × 새 기준 종가 100
    assert a.fee == pytest.approx(1.0 * 10 / 10.5)                           # 살 때 수수료도 남은 몫만큼
    assert a.stop == pytest.approx(90 / 1.05) and a.high == pytest.approx(110 / 1.05)
    assert bonus.events[0]["day"] == "2026-10-24" and "(2026-10-24)" in bonus.events[0]["text"]
    part = _split_engine(100.0, {"A": [(date(2026, 10, 24), 1.05)]}, fractional=True)
    assert part.positions["A"].shares == pytest.approx(10.5) and part.cash == 10_000


def test_split_rebase_compares_the_day_the_stock_last_closed_not_the_account_day():
    """거래정지로 마지막 처리일에 봉이 없던 종목 — 그 종목이 마지막으로 거래된 날(last_seen)로 견준다."""
    halted = _split_engine(10.45, {}, seen="2026-10-21")                     # 10-21 종가 104.5 → 10.45: 10:1
    assert halted.positions["A"].shares == 100
    moved = _split_engine(80.0, {}, seen="2026-10-22")                       # 같은 날(10-22) 종가가 그대로면
    assert moved.positions["A"].shares == 10                                 # 그 뒤 진짜로 내린 건 분할이 아니다
    legacy = _split_engine(35.0, {}, seen=None)                              # 그 날을 적어 두지 않은 예전 계좌 —
    assert legacy.positions["A"].shares == 10                                # 기록 없이는 바꾸지 않는다
    legacy = _split_engine(35.0, {"A": [(date(2026, 10, 24), 3.0)]}, seen=None)
    assert legacy.positions["A"].shares == 30
    legacy = _split_engine(35.0, {"A": [(date(2026, 10, 23), 3.0)]}, seen=None)   # 처리일(10-23) 당일 분할 기록 —
    assert legacy.positions["A"].shares == 10                                    # 그 날 종가는 이미 새 기준이라 빼야 맞다


def _wait_case(seen_close, jump_day_close, split_day="2026-10-22", ref="2026-10-24"):
    """A 를 10-20 에 마지막으로 기록(종가 100). 새 일봉: 10-20 seen_close, 10-21 100, 10-22 jump_day_close, 10-23 100."""
    eng = Engine(strat.STRATEGIES["rotation"], RiskRules(), FREE, 10_000)
    from stock_analysis.quant.engine import Position

    eng.positions["A"] = Position("A", 10, 100.0, "2026-10-01", 90.0)
    eng.last_close, eng.last_day, eng.last_seen = {"A": 100.0}, "2026-10-20", {"A": "2026-10-20"}
    days = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]
    prepared = {"A": ([], [99.0, seen_close, 100.0, jump_day_close, 100.0], days, None)}
    return paper._split_wait(eng, prepared, lambda t: [(date.fromisoformat(split_day), 1.05)], date.fromisoformat(ref))


def test_split_wait_only_when_the_history_is_still_on_the_old_basis():
    assert _wait_case(100.0 / 1.05, 95.2) == ""          # 과거가 이미 새 기준 — 분할일 근처 진짜 -4.8% 는 그냥 등락
    assert _wait_case(100.0, 100.0 / 1.05) == "A"        # 과거는 옛 기준, 분할일에 1.05 만큼 뚝 — 아직 정리 안 됨
    assert _wait_case(100.0, 100.0 / 1.05, ref="2026-11-05") == ""   # 분할일부터 7일 넘게 지나면 더 기다리지 않는다
    assert _wait_case(100.0, 100.0 / 1.05, split_day="2026-10-20") == ""   # 이미 반영된 날의 기록


def test_a_failed_split_lookup_defers_instead_of_booking_a_fake_loss():
    """기록을 읽지 못한 것과 '기록 없음' 은 다르다 — 작은 분할(1.05)을 자료 수정으로 넘기면 가짜 손실이 영영 남는다."""
    from stock_analysis.quant.engine import Position

    eng = Engine(strat.STRATEGIES["rotation"], RiskRules(), FREE, 10_000)
    eng.positions["A"] = Position("A", 10, 100.0, "2026-10-01", 90.0)
    eng.last_close, eng.last_day, eng.last_seen = {"A": 105.0}, "2026-10-23", {"A": "2026-10-23"}
    prepared = {"A": ([], [100.0], ["2026-10-23"], None)}
    assert paper._rebase_splits(eng, prepared, lambda t: None, "2026-10-24") == "A"
    assert eng.positions["A"].shares == 10
    assert paper._rebase_splits(eng, prepared, lambda t: [], "2026-10-24") == ""     # 기록이 정말 없으면 자료 수정


def test_the_paper_account_waits_while_watchlist_history_is_mixed(tmp_path):
    """분할 뒤 긴 과거를 다시 받지 못해 옛 기준·새 기준이 섞인 감시 종목 일봉으로는 처리하지 않는다."""
    bars = trending(120)
    bot = fake_bot({"A": bars[:100]}, tmp=tmp_path)
    paper.start(bot.quant, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1), plan=EVERY_DAY)
    bot.cached_metrics()["0"].bars = bars[:101]
    paper.step(bot.quant, bot, "us")
    bot.cached_metrics()["0"].bars = bars[:102]
    bot.prices_unsettled = lambda market, t: True
    try:
        assert paper.step(bot.quant, bot, "us") == (0, []) and paper.WAITING["us"] == "A"
        bot.prices_unsettled = lambda market, t: False
        assert paper.step(bot.quant, bot, "us")[0] == 1 and "us" not in paper.WAITING
    finally:
        paper.WAITING.pop("us", None)


def test_orders_follow_the_split_in_whole_shares():
    from stock_analysis.quant.engine import Order

    eng = Engine(strat.STRATEGIES["rotation"], RiskRules(), FREE, 10_000)
    eng.orders.append(Order("B", "buy", 3, "신호", "2026-10-23", 45.0))
    eng.last_close, eng.last_day, eng.last_seen = {"B": 50.0}, "2026-10-23", {"B": "2026-10-23"}
    paper._rebase_splits(eng, {"B": ([], [33.3333], ["2026-10-23"], None)}, lambda t: [], "2026-10-24")
    assert eng.orders[0].shares == 4 and eng.orders[0].stop == pytest.approx(30.0)   # 3 × 1.5 = 4.5 → 4주


def test_the_paper_account_waits_while_the_prices_still_show_the_split_cliff(tmp_path):
    """야후가 과거를 아직 새 기준으로 안 고쳤으면(또는 긴 과거를 못 받아 섞였으면) 그 일봉으로 처리하지 않는다."""
    bars = trending(120)
    bot = fake_bot({"A": bars[:100]}, tmp=tmp_path)
    store = bot.quant
    paper.start(store, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1), plan=EVERY_DAY)
    bot.cached_metrics()["0"].bars = bars[:101]
    paper.step(store, bot, "us")
    held = store.paper("us")["engine"]["positions"][0]
    split_day = bars[101].day
    bot.splits_for = lambda market, t: [(split_day, 10.0)]
    bot.cached_metrics()["0"].bars = bars[:101] + _scaled(bars[101:102], 10)   # 분할 날만 새 기준 — 절벽
    assert paper.step(store, bot, "us") == (0, [])
    assert store.paper("us")["engine"]["positions"][0] == held                  # 아무것도 기록하지 않았다
    bot.cached_metrics()["0"].bars = _scaled(bars[:102], 10)                   # 정리된 뒤
    days, events = paper.step(store, bot, "us")
    assert days == 1 and any(e["text"].startswith("분할·병합 반영 A") for e in events)
    assert not store.paper("us")["engine"]["trades"]


@pytest.mark.parametrize("old_account", [False, True])
def test_after_a_restart_the_paper_account_waits_for_a_ticker_it_has_not_got_yet(tmp_path, monkeypatch, old_account):
    """PC 를 껐다 켰는데 한 종목 일봉을 아직 못 받았으면, 그 종목을 뺀 채로 밀린 날을 기록하지 않는다(4.14.1).
    예전엔 B 없이 기록하고 다시 돌아가지 않아 꺼 둔 사이 B 의 손절이 빠졌다. 받은 뒤 기록한 결과는 켜 둔 채 하루씩 한 것과
    같다. 4.14 까지의 계좌(어제 있던 종목 기록이 없음)도 들고 있는 종목은 기다린다."""
    monkeypatch.setattr(paper, "_looked", {})
    monkeypatch.setattr(paper, "LATE", {})
    a = trending(140)
    b = flat_then([round(100 * 1.004 ** k, 4) for k in range(125)] + [80.0] * 15)      # 꺼 둔 사이 손절선 아래로

    def run(restart):
        (tmp_path / str(restart)).mkdir()
        bot = fake_bot({"A": a[:110], "B": b[:110]}, tmp=tmp_path / str(restart))
        paper.start(bot.quant, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1), plan=EVERY_DAY)
        m = bot.cached_metrics()
        for k in range(111, 121):
            m["0"].bars, m["1"].bars = a[:k], b[:k]
            paper.step(bot.quant, bot, "us")
        if restart:                                   # 15 거래일 뒤 다시 켬 — A 는 받았고 B 는 아직
            if old_account:
                del bot.quant.paper("us")["listed"]
            m["0"].bars, got = a[:135], m.pop("1")
            assert paper.step(bot.quant, bot, "us") == (0, []) and paper.LATE["us"] == ["B"]
            m["1"] = got
        m["0"].bars, m["1"].bars = a[:135], b[:135]
        assert paper.step(bot.quant, bot, "us")[0] == 15 and "us" not in paper.LATE
        return bot.quant.paper("us")["engine"]

    continuous, restarted = run(False), run(True)
    assert [(t["ticker"], t["reason"]) for t in continuous["trades"]] == [("B", "손절선 도달")]
    assert restarted == continuous


class _Baskets:
    """바구니 일봉 저장소 흉내 — 받아 둔 일봉만 돌려준다(뒤에서 받는 중이면 종목마다 받은 데까지)."""

    def __init__(self, data):
        self.data = data

    def basket(self, market):
        return SimpleNamespace(members=lambda day: sorted(self.data))

    def load(self, market, since, tickers):
        return SimpleNamespace(prepared=prepare({t: self.data[t] for t in tickers if t in self.data}),
                               book=None, regime={})


def test_a_week_off_does_not_count_a_ticker_still_being_downloaded_as_halted(tmp_path, monkeypatch):
    """일주일 넘게 꺼 두었다 켜면, 바구니 일봉을 뒤에서 받는 동안 아직 못 받은 종목은 마지막 봉이 일주일 넘게 뒤처져 있다.
    '일주일 넘게 새 봉이 없으면 거래정지' 로 보고 빼고 기록하면 안 된다 — 받을 때까지 기다린다."""
    monkeypatch.setattr(paper, "_looked", {})
    monkeypatch.setattr(paper, "LATE", {})
    a, b = trending(150), trending(150, step=0.003)
    qd = _Baskets({"A": a[:120], "B": b[:120]})
    bot = SimpleNamespace(quant_data=qd, quant=QuantStore(tmp_path / "quant.json"), cached_targets=lambda: [],
                          market_snapshot=lambda: None)
    plan = Plan(universe="us500", check_days=(0, 1, 2, 3, 4), min_hold=0)
    paper.start(bot.quant, bot, "us", 10_000, "rotation", RiskRules(), FREE, date(2024, 7, 1), plan=plan)
    qd.data["A"] = a[:135]                            # 3주 뒤 켬 — A 는 새로 받았고 B 는 아직
    assert paper.settled_day(prepare(qd.data)) == a[134].day.isoformat()       # 예전엔 B 를 거래정지로 봤다
    assert paper.step(bot.quant, bot, "us") == (0, []) and paper.LATE["us"] == ["B"]
    qd.data["B"] = b[:135]
    assert paper.step(bot.quant, bot, "us")[0] == 15


def test_a_ticker_that_never_comes_holds_the_account_only_for_a_while(tmp_path, monkeypatch):
    """정말 거래가 멈춘 종목 때문에 계좌가 영영 서지 않게 — 프로그램이 켜져 있는 시간으로 6시간 넘게 안 오면 빼고 기록한다.
    꺼져 있거나 잠든 시간은 세지 않고(그동안은 받을 수 없었다), 기다린 시간은 껐다 켜도 이어서 센다."""
    clock = [0.0]
    monkeypatch.setattr(paper.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(paper, "_looked", {})
    monkeypatch.setattr(paper, "_counted", {})
    monkeypatch.setattr(paper, "LATE", {})
    a, b = trending(140), trending(140)
    bot = fake_bot({"A": a[:110], "B": b[:110]}, tmp=tmp_path)
    paper.start(bot.quant, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1), plan=EVERY_DAY)
    m = bot.cached_metrics()
    m["0"].bars = a[:115]
    del m["1"]
    assert paper.step(bot.quant, bot, "us") == (0, [])
    clock[0] += 3 * 24 * 3600                         # 사흘 잠듦 — 15분만 센다
    assert paper.step(bot.quant, bot, "us") == (0, []) and bot.quant.paper("us")["late"] == {"B": [paper.LATE_GAP, 1]}
    paper._looked.clear()                             # 껐다 켬 — 기다린 시간은 남아 있다
    paper._counted.clear()
    assert paper.step(bot.quant, bot, "us") == (0, []) and bot.quant.paper("us")["late"] == {"B": [paper.LATE_GAP, 2]}
    waited = paper.LATE_GAP
    while waited + 600 < paper.LATE_GRACE:            # 10분마다 — 6시간이 차기 전까지는 기다린다
        clock[0] += 600
        waited += 600
        assert paper.step(bot.quant, bot, "us") == (0, [])
    clock[0] += 600
    assert paper.step(bot.quant, bot, "us")[0] == 5 and "us" not in paper.LATE
    assert bot.quant.paper("us")["listed"] == ["A"] and bot.quant.paper("us")["late"] == {}


def _two_tickers(tmp_path, monkeypatch, n=110):
    monkeypatch.setattr(paper, "_looked", {})
    monkeypatch.setattr(paper, "_counted", {})
    monkeypatch.setattr(paper, "LATE", {})
    a, b = trending(140), trending(140)
    bot = fake_bot({"A": a[:n], "B": b[:n]}, tmp=tmp_path)
    paper.start(bot.quant, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1), plan=EVERY_DAY)
    return bot, a, b


def test_short_sessions_do_not_hold_the_account_forever(tmp_path, monkeypatch):
    """프로그램을 잠깐씩만 켜면(한 번 처리하고 끔) 기다린 시간이 쌓이지 않는다 — 그 종목을 기다리는 채로 3번째 켜면 빼고 기록한다."""
    bot, a, _ = _two_tickers(tmp_path, monkeypatch)
    m = bot.cached_metrics()
    m["0"].bars = a[:115]
    del m["1"]
    for start in range(1, paper.LATE_STARTS):
        paper._looked.clear()
        paper._counted.clear()
        assert paper.step(bot.quant, bot, "us") == (0, []) and bot.quant.paper("us")["late"]["B"][1] == start
    paper._looked.clear()
    paper._counted.clear()
    assert paper.step(bot.quant, bot, "us")[0] == 5 and "us" not in paper.LATE


def test_a_cut_history_is_waited_for_but_a_gap_inside_it_is_not(tmp_path, monkeypatch):
    """긴 과거를 못 받고 최근 며칠치만 왔으면 기다린다. 과거 가운데 하루만 빠졌으면 기다려도 채워지지 않으니 기다리지 않는다."""
    bot, a, b = _two_tickers(tmp_path, monkeypatch)
    m = bot.cached_metrics()
    m["0"].bars, m["1"].bars = a[:115], b[112:115]
    assert paper.step(bot.quant, bot, "us") == (0, []) and paper.LATE["us"] == ["B"]
    m["1"].bars = b[:109] + b[110:115]                # 마지막으로 기록한 날 하루만 빠졌다
    assert paper.step(bot.quant, bot, "us")[0] == 5 and "us" not in paper.LATE


def test_a_ticker_a_few_days_late_is_left_to_settled_day_and_not_counted(tmp_path, monkeypatch):
    """일주일 안으로 늦은 종목은 원래대로 다 모인 날까지 기다린다(settled_day). 그때는 기다린 시간을 세지도, 기록을 다시
    저장하지도 않는다 — 다음에 껐다 켤 때 쓸 6시간을 깎아 먹지 않게."""
    bot, a, b = _two_tickers(tmp_path, monkeypatch)
    m = bot.cached_metrics()
    m["0"].bars, m["1"].bars = a[:115], b[:112]
    assert paper.step(bot.quant, bot, "us")[0] == 2
    saved = []
    monkeypatch.setattr(bot.quant, "save", lambda: saved.append(1))
    assert paper.step(bot.quant, bot, "us") == (0, []) and bot.quant.paper("us")["late"] == {} and not saved
    assert "us" not in paper.LATE


def test_an_account_from_before_waits_for_a_ticker_it_had_seen(tmp_path, monkeypatch):
    """4.14 까지의 계좌(어제 있던 종목 기록이 없음)도 전에 있었는데 이번에 못 받은 감시 종목은 기다린다."""
    bot, a, _ = _two_tickers(tmp_path, monkeypatch)
    del bot.quant.paper("us")["listed"]
    m = bot.cached_metrics()
    m["0"].bars = a[:115]
    del m["1"]
    assert paper.step(bot.quant, bot, "us") == (0, []) and paper.LATE["us"] == ["B"]
    assert bot.quant.paper("us")["listed"] == ["A", "B"]


def test_a_ticker_taken_off_the_watchlist_is_not_waited_for(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "_looked", {})
    monkeypatch.setattr(paper, "LATE", {})
    a, b = trending(140), trending(140)
    bot = fake_bot({"A": a[:110], "B": b[:110]}, tmp=tmp_path)
    paper.start(bot.quant, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1), plan=EVERY_DAY)
    bot.cached_targets().pop()                        # B 를 감시 목록에서 뺐다
    bot.cached_metrics()["0"].bars = a[:115]
    assert paper.step(bot.quant, bot, "us")[0] == 5


def test_paused_paper_account_does_not_move(tmp_path, monkeypatch):
    bot = fake_bot({"A": trending(100)}, tmp=tmp_path)
    paper.start(bot.quant, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1))
    account = bot.quant.paper("us")
    account["paused"] = True
    bot.cached_metrics()["0"].bars = trending(110)
    monkeypatch.setattr(paper, "LATE", {"us": ["B"]})
    assert paper.step(bot.quant, bot, "us") == (0, [])
    assert "us" not in paper.LATE                     # 멈춘 계좌에 '일봉 기다리는 중' 이 남지 않는다


def test_leveraged_etf_is_left_out_even_under_a_trust_name(tmp_path):
    """SEC 이름은 신탁 이름이다. ETF 판정(펀드 이름으로 읽은 배수)으로 빼야 SOXL 이 퀀트에 섞이지 않는다."""
    from stock_analysis.funds import classify_name

    bot = fake_bot({"SOXL": trending(100), "A": trending(100)}, tmp=tmp_path)
    soxl = bot.cached_targets()[0]
    soxl.name = "Direxion Shares ETF Trust"
    assert "SOXL" in paper.market_data(bot, "us")                     # 예전 모습
    soxl.fund = classify_name("SOXL", "Direxion Daily Semiconductor Bull 3X Shares")
    assert paper.leveraged_tickers(bot, "us") == ["SOXL"]
    assert set(paper.market_data(bot, "us")) == {"A"}


def test_a_leveraged_etf_already_held_keeps_its_prices_but_is_not_bought_again(tmp_path):
    """예전 판으로 산 SOXL 의 일봉을 끊으면 손절·청산이 영영 안 돌고 평가액이 멈춘다."""
    from stock_analysis.funds import classify_name
    from stock_analysis.quant.engine import Engine, Position

    bars = trending(120)
    bot = fake_bot({"SOXL": bars[:100], "A": bars[:100]}, tmp=tmp_path)
    bot.cached_targets()[0].fund = classify_name("SOXL", "Direxion Daily Semiconductor Bull 3X Shares")
    paper.start(bot.quant, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1), plan=EVERY_DAY)
    account = bot.quant.paper("us")
    engine = Engine.from_dict(account["engine"])
    engine.positions["SOXL"] = Position("SOXL", 5, bars[90].close, bars[90].day.isoformat(), bars[90].close * 0.5, 0.0)
    from stock_analysis.quant.engine import Order
    engine.orders.append(Order("SOXL", "buy", 3, "빼기 전에 낸 주문", bars[99].day.isoformat(), None))
    account["engine"] = engine.to_dict()
    for m in bot.cached_metrics().values():
        m.bars = bars[:101]
    days, _ = paper.step(bot.quant, bot, "us")
    assert days == 1
    after = Engine.from_dict(bot.quant.paper("us")["engine"])
    assert after.last_close["SOXL"] == bars[100].close                 # 평가액이 따라 움직인다
    assert not [o for o in after.orders if o.ticker == "SOXL" and o.side == "buy"]
    assert after.positions["SOXL"].shares == 5                          # 빼기 전 주문은 체결하지 않았다
    assert any("매수 주문 취소 SOXL" in e["text"] for e in after.events)
    assert paper.account_view(bot.quant, bot, "us")["orphans"] == []   # '감시 목록에서 빠진 종목' 이 아니다


def test_paper_status_says_when_it_runs_and_why_nothing_was_bought(tmp_path):
    from stock_analysis.ui.quant_page import _status_text

    bot = fake_bot({"A": trending(100)}, tmp=tmp_path)
    paper.start(bot.quant, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1))
    view = paper.account_view(bot.quant, bot, "us")
    text = _status_text(view)
    assert "마지막으로 처리한 거래일" in text and "다음 점검" in text and "새로 살 수 있는 종목 1개" in text
    view["engine"].skipped = 2
    assert "1주도 못 사서 2번" in _status_text(view)


def test_growth_score_is_labelled_and_ranks_within_the_list(tmp_path):
    bot = fake_bot({"A": trending(300), "B": trending(300, up=False)}, tmp=tmp_path)
    rows = paper.signals(bot, "us")
    assert rows[0]["ticker"] == "A"                     # 6개월 수익률이 높은 쪽이 위
    assert rows[0]["score_parts"] == 3


# --------------------------------------------------------------------------
# 5. 화면 · 입력
# --------------------------------------------------------------------------
def test_settings_turn_percent_inputs_into_fractions_and_reject_nonsense():
    got = quant_settings({"strategy": ["breakout"], "capital": ["1,000,000"], "years": ["3"],
                          "risk_per_trade": ["1.5"], "max_weight": ["20"], "max_positions": ["5"],
                          "dd_stop": ["abc"], "commission": ["0.015"], "slippage": ["-3"],
                          "fee": ["custom"], "monthly_deposit": ["200000"], "check_day": ["1", "4"]})
    assert got["strategy"] == "breakout" and got["capital"] == 1_000_000 and got["years"] == 3
    assert got["rules"].risk_per_trade == pytest.approx(0.015)
    assert got["rules"].max_weight == pytest.approx(0.20)
    assert got["rules"].dd_stop == pytest.approx(RiskRules().dd_stop)        # 이상한 값 → 기본값
    assert got["costs"].commission == pytest.approx(0.00015)
    assert got["costs"].slippage == pytest.approx(US_COSTS.slippage)          # 음수 → 기본값
    assert got["plan"].monthly_deposit == 200_000 and got["plan"].check_days == (1, 4)
    plain = quant_settings({})                                                  # 아무것도 안 고르면 기본 수수료
    assert plain["costs"] == US_COSTS and plain["capital"] == 1_000
    weird = quant_settings({"strategy": ["<script>"], "years": ["7"], "profile": ["safe"]})
    assert weird["strategy"] == strat.DEFAULT_STRATEGY and weird["years"] == 0
    assert weird["rules"].max_weight == pytest.approx(0.15)                   # 안전형 묶음


def test_quant_page_draws_without_data_and_has_no_order_button(bot):
    html = Dashboard(bot).render_path("/quant?m=us")
    assert "퀀트 연습장" in html
    assert "실제 주문을 하지 않습니다" in html
    assert "아직 돌린 백테스트가 없습니다" in html
    for word in ("매수 주문", "주문 보내기", "account_no", "계좌번호"):
        assert word not in html


def test_quant_menu_link_and_journal_action(bot):
    dash = Dashboard(bot)
    assert 'href="/quant?m=us"' in dash.render_path("/?m=us")
    assert dash.run_action("journal", {"text": ["  규칙을   어기고 싶었다 "], "back": ["/quant?m=kr"]}) == "일지에 적었습니다."
    entry = bot.quant.journal()[0]
    assert entry["text"] == "규칙을 어기고 싶었다" and entry["market"] == "us"     # 퀀트는 미국만
    assert "규칙을 어기고 싶었다" in dash.render_path("/quant?m=us")
    kr = dash.render_path("/quant?m=kr")                                       # 한국 주소로 와도 미국 퀀트
    assert "규칙을 어기고 싶었다" in kr and 'href="/?m=kr"' in kr


def test_updates_never_overwrite_the_quant_record():
    import bootstrap
    import updater

    assert updater._keep("quant.json")
    assert "quant.json" in bootstrap.MINE


def test_percent_text_never_shows_a_negative_zero():
    from stock_analysis.ui.quant_page import pct

    assert pct(-0.0) == "0.0%"
    assert pct(-0.00001, sign=True) == "+0.0%"
    assert pct(-0.2051) == "-20.5%"


# --------------------------------------------------------------------------
# 6. 운용 계획 — 주 2회 점검 · 최소 보유 · 매달 입금
# --------------------------------------------------------------------------
def test_orders_are_made_only_on_check_days_but_stops_run_daily():
    bars = trending(140)
    r = backtest.run(strat.STRATEGIES["breakout"], RiskRules(daily_loss_stop=0, dd_half=0, dd_stop=0),
                     FREE, 10_000, {"A": bars}, plan=Plan(check_days=(1, 4), min_hold=0))
    made = {date.fromisoformat(t["entry_day"]) for t in r["trades"]} | {
        date.fromisoformat(p["entry_day"]) for p in r["open"]}
    # 화·금 장 마감 뒤 주문 → 다음 거래일(수·월) 시가 체결
    assert made and all(d.weekday() in (2, 0) for d in made)


def test_minimum_hold_stops_flip_flopping_but_not_the_stop_loss():
    closes = [100.0] * 7 + [100, 99, 98, 97, 96, 95, 94, 93]
    bars = flat_then(closes)
    young = on_day_strategy()

    class SellNow(OnDay):
        def exit(self, bars, closes, i, held):
            return "바로 팔기"

    quick = SellNow(**{k: getattr(young, k) for k in ("key", "name", "kind", "buy_rule", "sell_rule",
                                                      "hold", "evidence", "advice", "warmup")})
    rules = RiskRules(risk_per_trade=1, max_weight=1, max_positions=1, daily_loss_stop=0, dd_half=0, dd_stop=0)
    r = backtest.run(quick, rules, FREE, 10_000, {"A": bars}, plan=Plan(check_days=(0, 1, 2, 3, 4), min_hold=5))
    t = r["trades"][-1]
    held = sum(1 for b in bars if t["entry_day"] <= b.day.isoformat() < t["exit_day"])
    assert held >= 5


def test_monthly_deposits_are_money_in_not_profit():
    bars = flat_then([100.0] * 120)                    # 가격이 그대로 — 수익 0
    r = backtest.run(strat.STRATEGIES["breakout"], RiskRules(), FREE, 1_000_000, {"A": bars},
                     plan=Plan(monthly_deposit=200_000))
    m = r["metrics"]
    months = len({b.day.isoformat()[:7] for b in bars}) - 1
    assert r["deposited"] == pytest.approx(200_000 * months)
    assert m["end"] == pytest.approx(1_000_000 + 200_000 * months)
    assert m["cagr"] == pytest.approx(0.0, abs=1e-9)    # 넣은 돈은 수익률에 섞지 않는다
    assert m["profit"] == pytest.approx(0.0, abs=1e-6)
    assert r["bench"]["end"] == pytest.approx(m["end"])  # 그냥 보유에도 같은 돈을 넣는다


def test_plan_survives_bad_input_and_save():
    plan = Plan.from_dict({"monthly_deposit": "-5", "check_days": ["9", "1", "x", 4, 1], "min_hold": "999"})
    assert plan.monthly_deposit == 0 and plan.check_days == (1, 4) and plan.min_hold == 60
    assert plan.days_text == "화·금"
    engine = Engine(strat.STRATEGIES["breakout"], RiskRules(), FREE, 1000, plan=Plan(200, (0,), 3))
    engine.deposited, engine.flows = 400, {"2026-02-02": 200.0}
    again = Engine.from_dict(engine.to_dict())
    assert again.plan == Plan(200, (0,), 3) and again.deposited == 400 and again.flows == {"2026-02-02": 200.0}


def test_fee_presets_are_us_brokers():
    from stock_analysis.quant.costs import DEFAULT_FEE, FEE_PRESETS, fee_preset

    assert fee_preset(DEFAULT_FEE) is US_COSTS
    assert fee_preset("us_event").commission == pytest.approx(0.0007)
    assert fee_preset("kr_min") is None                    # 예전 한국 묶음 이름은 '직접' 으로 본다
    assert all(len(row) == 3 and row[2].sell_tax == 0 for row in FEE_PRESETS)


# --------------------------------------------------------------------------
# 7. 성장 — 그 날 알 수 있었던 매출만
# --------------------------------------------------------------------------
def _facts(quarters):
    """quarters = [(시작, 끝, 값, 제출일)] → CompanyFacts."""
    from stock_analysis.xbrl import CompanyFacts

    units = [{"start": s, "end": e, "val": v, "filed": f, "form": "10-Q"} for s, e, v, f in quarters]
    return CompanyFacts({"facts": {"us-gaap": {"Revenues": {"units": {"USD": units}}}}})


def _quarters(values, first_year=2022, restated=None):
    out = []
    starts = ["01-01", "04-01", "07-01", "10-01"]
    ends = ["03-31", "06-30", "09-30", "12-31"]
    files = ["05-10", "08-10", "11-10", "02-20"]
    for k, v in enumerate(values):
        y = first_year + k // 4
        q = k % 4
        fy = y + 1 if q == 3 else y
        out.append((f"{y}-{starts[q]}", f"{y}-{ends[q]}", v, f"{fy}-{files[q]}"))
    if restated:
        out.extend(restated)
    return out


def test_growth_uses_the_first_reported_number_on_the_day_it_was_filed():
    from stock_analysis.quant.fundamentals import growth_at, revenue_growth_series

    rows = _quarters([100, 100, 100, 100, 120, 120, 120, 120],
                     # 1년 뒤 비교 칸으로 다시 나온 값(정정)은 쓰지 않는다
                     restated=[("2023-10-01", "2023-12-31", 999, "2024-11-10")])
    series = revenue_growth_series(_facts(rows))
    assert series[0][0] == date(2024, 2, 20)              # 8번째 분기 제출일에 처음 알 수 있다
    assert series[0][1] == pytest.approx(0.20)
    assert growth_at(series, date(2024, 2, 19)) is None   # 하루 전에는 몰랐다
    assert growth_at(series, date(2024, 3, 1)) == pytest.approx(0.20)
    assert growth_at(series, date(2024, 9, 30)) is None   # 다음 분기 값 없이 200일이 넘으면 '지금 값' 이 아니다


def test_growth_rotation_only_buys_companies_that_are_growing(tmp_path):
    from stock_analysis.quant.fundamentals import revenue_growth_series

    up_fast, up_flat = trending(320), trending(320, step=0.005)
    first = up_fast[0].day
    growing = [(first + timedelta(days=90 * k - 1), 0.30) for k in range(6)]       # 분기마다 새 성장률
    shrinking = [(first + timedelta(days=90 * k - 1), -0.10) for k in range(6)]
    r = backtest.run(strat.STRATEGIES["growth"], RiskRules(max_positions=1, daily_loss_stop=0), FREE, 10_000,
                     {"GROW": up_fast, "SHRINK": up_flat}, growth={"GROW": growing, "SHRINK": shrinking},
                     plan=EVERY_DAY)
    bought = {t["ticker"] for t in r["trades"]} | {p["ticker"] for p in r["open"]}
    assert bought == {"GROW"}          # SHRINK 이 6개월 수익률은 더 높아도 성장 자격이 없다
    assert "SEC 제출일 기준" in " ".join(r["warnings"])
    assert revenue_growth_series(None) == []


def test_growth_strategy_without_data_buys_nothing_and_says_why():
    r = backtest.run(strat.STRATEGIES["growth"], RiskRules(), FREE, 10_000, {"A": trending(320)})
    assert not r["trades"] and not r["open"]
    assert "과거 시점 매출 자료가 없어" in " ".join(r["warnings"])


# --------------------------------------------------------------------------
# 8. 근거를 규칙으로 — 추격 매수 금지 · 레버리지 제외 · 근거 카드
# --------------------------------------------------------------------------
def test_a_stock_that_jumped_today_is_not_chased():
    closes = [100.0] * 7 + [130, 131, 132, 133]            # 7번째 날 +30% 급등 — 그날 신호는 건너뛴다
    bars = flat_then(closes)
    s = on_day_strategy()
    r = backtest.run(s, RiskRules(risk_per_trade=1, max_weight=1, max_positions=1, daily_loss_stop=0,
                                  dd_half=0, dd_stop=0), FREE, 10_000, {"A": bars}, plan=EVERY_DAY)
    assert r["chased"] == 0                                 # 신호일(i=6)은 급등 전날이라 정상 매수
    jump = flat_then([100.0] * 6 + [130, 131, 132, 133])
    r2 = backtest.run(s, RiskRules(risk_per_trade=1, max_weight=1, max_positions=1, daily_loss_stop=0,
                                   dd_half=0, dd_stop=0), FREE, 10_000, {"A": jump}, plan=EVERY_DAY)
    assert r2["chased"] == 1 and not r2["trades"] and not r2["open"]
    assert "추격 매수 금지" in " ".join(r2["warnings"])


@pytest.mark.parametrize("name,expected", [
    ("KODEX 레버리지", True), ("TIGER 인버스", True), ("ProShares UltraPro QQQ", True),
    ("Direxion Daily Semiconductor Bull 3X", True), ("삼성전자", False), ("NVIDIA CORP", False),
    ("Apple Inc.", False),
])
def test_leveraged_products_are_recognised_by_name(name, expected):
    from stock_analysis.quant.evidence import is_leveraged

    assert is_leveraged(name) is expected


def test_leveraged_products_are_left_out_of_quant_data(tmp_path):
    bot = fake_bot({"TQQQ": trending(80), "AAPL": trending(80)}, tmp=tmp_path)
    bot.cached_targets()[0].watch.name = "ProShares UltraPro QQQ"
    assert paper.leveraged_tickers(bot, "us") == ["TQQQ"]
    assert set(paper.market_data(bot, "us")) == {"AAPL"}


def test_evidence_card_lists_large_studies_and_what_was_applied(bot):
    from stock_analysis.quant.evidence import STUDIES

    html = Dashboard(bot).render_path("/quant?m=us")
    assert "근거" in html and "66,465" in html and "13만 6천" in html
    assert all(st.applied for st in STUDIES) and len(STUDIES) >= 15


# --------------------------------------------------------------------------
# 9. 소수점 매수 · 레버리지 실험
# --------------------------------------------------------------------------
def test_fractional_shares_let_a_small_account_buy_expensive_stocks():
    rules = RiskRules(risk_per_trade=0.02, max_weight=0.35)
    # $750 계좌, 1주 $500 — 온주로는 35% 한도($262)에 못 들어가 0주, 소수점이면 0.52주
    assert shares_to_buy(rules, 750, 750, 500, 450, None, 1.0, 500) == 0
    assert shares_to_buy(rules, 750, 750, 500, 450, None, 1.0, 500, fractional=True) == pytest.approx(0.3)
    bars = flat_then([500.0] * 7 + [505, 510, 515, 520, 525])
    r = backtest.run(on_day_strategy(), RiskRules(risk_per_trade=1, max_weight=0.35, max_positions=1,
                                                  daily_loss_stop=0, dd_half=0, dd_stop=0),
                     FREE, 750, {"A": bars}, plan=Plan(check_days=(0, 1, 2, 3, 4), min_hold=0, fractional=True))
    shares = (r["trades"] or r["open"])[0]["shares"]
    assert 0 < shares < 1 and round(shares, 2) == shares
    assert "소수점 매수" in " ".join(r["warnings"])


def test_leveraged_products_can_be_included_on_purpose(tmp_path):
    bot = fake_bot({"TQQQ": trending(80), "AAPL": trending(80)}, tmp=tmp_path)
    bot.cached_targets()[0].watch.name = "ProShares UltraPro QQQ"
    assert set(paper.market_data(bot, "us")) == {"AAPL"}
    assert set(paper.market_data(bot, "us", include_leveraged=True)) == {"TQQQ", "AAPL"}
    r = backtest.run(strat.STRATEGIES["breakout"], RiskRules(), FREE, 10_000,
                     paper.market_data(bot, "us", include_leveraged=True), leveraged=["TQQQ"])
    assert "변동성 감쇠" in " ".join(r["warnings"])


def test_plan_flags_round_trip_and_fractional():
    plan = Plan.from_dict({"fractional": "1", "include_leveraged": True})
    assert plan.fractional and plan.include_leveraged
    assert Plan.from_dict(plan.to_dict()) == plan
    assert quant_settings({"fractional": ["1"]})["plan"].fractional
    assert Plan.from_dict({"universe": "kr200"}).universe == "watch"           # 예전 한국 바구니 설정은 관심 종목으로


# --------------------------------------------------------------------------
# 10. 투자 성향 · 긴급 매도 · ETF · 설정 초기화
# --------------------------------------------------------------------------
def test_profiles_fill_everything_the_user_does_not_know():
    from stock_analysis.quant import profiles

    safe = quant_settings({"profile": ["safe"], "strategy": ["auto"], "check_day": ["0"]})
    assert safe["rules"].risk_per_trade == pytest.approx(0.005) and safe["rules"].vol_target
    assert safe["plan"].check_days == (4,) and safe["plan"].min_hold == 10      # 칸에 적은 요일보다 성향이 먼저
    assert safe["plan"].emergency == pytest.approx(0.07)
    assert safe["strategy"] == "rotation" and safe["saved"]["strategy"] == "auto"
    bold = quant_settings({"profile": ["aggressive"], "strategy": ["auto"]})
    assert bold["rules"].max_positions == 4 and bold["strategy"] == "growth"
    assert len(profiles.PROFILES) == 3 and all(p.why for p in profiles.PROFILES)
    risks = [p.rules.risk_per_trade for p in profiles.PROFILES]
    assert risks == sorted(risks)                                               # 안전 < 균형 < 공격


def test_emergency_sell_happens_on_a_non_check_day():
    # 화요일에 사고, 목요일에 -15% — 금요일 점검까지 기다리지 않고 다음 시가(금)에 판다
    start = date(2024, 1, 1)                                   # 월요일
    closes = [100.0] * 10 + [100, 100, 85, 84, 83]
    bars = flat_then(closes, start=start, lows=[c * 0.999 for c in closes])
    s = on_day_strategy()
    plan = Plan(check_days=(1, 4), min_hold=5, emergency=0.10)
    rules = RiskRules(risk_per_trade=1, max_weight=1, max_positions=1, daily_loss_stop=0, dd_half=0, dd_stop=0)
    r = backtest.run(s, rules, FREE, 10_000, {"A": bars}, plan=plan)
    sold = [t for t in r["trades"] if t["reason"].startswith("긴급 매도")]
    assert sold
    crash_day = bars[12].day
    assert date.fromisoformat(sold[0]["exit_day"]) > crash_day      # 다음 날 시가에


def test_etfs_can_be_held_by_the_growth_strategy():
    r = backtest.run(strat.STRATEGIES["growth"], RiskRules(max_positions=1, daily_loss_stop=0), FREE, 10_000,
                     {"QQQ": trending(320)}, plan=EVERY_DAY, etfs={"QQQ"})
    held = {t["ticker"] for t in r["trades"]} | {p["ticker"] for p in r["open"]}
    assert held == {"QQQ"}


def test_settings_reset_goes_back_to_defaults(bot):
    dash = Dashboard(bot)
    dash._background = lambda message, func: message
    bot.quant.set_settings("us", {"capital": 5555, "profile": "safe"})
    assert "되돌렸습니다" in dash.run_action("quant_settings_reset", {"back": ["/quant?m=us"]})
    assert bot.quant.settings("us") == {}
    html = dash.render_path("/quant?m=us")
    assert 'value="5555"' not in html and "균형형" in html
