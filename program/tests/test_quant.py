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
from stock_analysis.quant.costs import KR_COSTS, CostModel, default_costs
from stock_analysis.quant.engine import Engine, prepare
from stock_analysis.quant.sizing import RiskRules, shares_to_buy, stage_for
from stock_analysis.quant.store import QuantStore

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
                     FREE, 10_000, {"A": bars})
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


def test_korean_round_trip_cost_includes_the_2026_sell_tax():
    assert KR_COSTS.sell_tax == pytest.approx(0.002)
    # 수수료 양쪽 + 거래세 + 체결 차이 양쪽
    assert KR_COSTS.round_trip == pytest.approx(0.000186396 * 2 + 0.002 + 0.004)
    assert default_costs("us").sell_tax == 0


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
    r = backtest.run(strat.STRATEGIES["breakout"], RiskRules(), KR_COSTS, 1_000_000, data)
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
    engine = Engine(strat.STRATEGIES["pullback"], RiskRules(), KR_COSTS, 1_000_000)
    from stock_analysis.quant.engine import Order, Position
    engine.positions["005930"] = Position("005930", 3, 70000.0, "2026-09-01", 65000.0, 31.5)
    engine.orders.append(Order("000660", "buy", 2, "신호", "2026-09-30", 150000.0))
    engine.last_day = "2026-09-30"
    again = Engine.from_dict(engine.to_dict(), "kr")
    assert again.positions["005930"].stop == 65000.0
    assert again.orders[0].ticker == "000660"
    assert again.strategy.key == "pullback" and again.last_day == "2026-09-30"


# --------------------------------------------------------------------------
# 4. 모의 계좌 — 백테스트와 같은 규칙, 끝난 날만
# --------------------------------------------------------------------------
def fake_bot(bars_by_ticker, market="us", live=False, tmp=None):
    targets, metrics = [], {}
    for k, (t, bars) in enumerate(bars_by_ticker.items()):
        targets.append(SimpleNamespace(ticker=t, market=market, cik=str(k), name=t,
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
    message = paper.start(store, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1))
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


def test_paused_paper_account_does_not_move(tmp_path):
    bot = fake_bot({"A": trending(100)}, tmp=tmp_path)
    paper.start(bot.quant, bot, "us", 10_000, "breakout", RiskRules(), FREE, date(2024, 6, 1))
    account = bot.quant.paper("us")
    account["paused"] = True
    bot.cached_metrics()["0"].bars = trending(110)
    assert paper.step(bot.quant, bot, "us") == (0, [])


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
                          "dd_stop": ["abc"], "commission": ["0.015"], "slippage": ["-3"]}, "kr")
    assert got["strategy"] == "breakout" and got["capital"] == 1_000_000 and got["years"] == 3
    assert got["rules"].risk_per_trade == pytest.approx(0.015)
    assert got["rules"].max_weight == pytest.approx(0.20)
    assert got["rules"].dd_stop == pytest.approx(RiskRules().dd_stop)        # 이상한 값 → 기본값
    assert got["costs"].commission == pytest.approx(0.00015)
    assert got["costs"].slippage == pytest.approx(KR_COSTS.slippage)          # 음수 → 기본값
    weird = quant_settings({"strategy": ["<script>"], "years": ["7"], "preset": ["spread"]}, "us")
    assert weird["strategy"] == strat.DEFAULT_STRATEGY and weird["years"] == 0
    assert weird["rules"].max_weight == pytest.approx(0.10)                   # 3단계 묶음


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
    assert entry["text"] == "규칙을 어기고 싶었다" and entry["market"] == "kr"
    assert "규칙을 어기고 싶었다" in dash.render_path("/quant?m=kr")
    assert "규칙을 어기고 싶었다" not in dash.render_path("/quant?m=us")


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
