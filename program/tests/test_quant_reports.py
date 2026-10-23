"""검증(운이 아닐 확률·무작위 비교)과 주간 성적표.

지키는 약속:
1. '운이 아닐 확률' 은 논문 공식 그대로, 시도 횟수가 늘면 낮아진다.
2. 성적표의 수익률은 넣은 돈을 빼고 잰다. 지수를 못 받으면 지수 칸은 비운다.
3. 뒤늦게 채운 지난 주에는 '지금' 보유 종목을 그 주 것처럼 적지 않는다.
"""

import math
import random
from datetime import date, timedelta
from statistics import NormalDist
from types import SimpleNamespace

import pytest

from stock_analysis.quant import backtest, paper
from stock_analysis.quant import strategies as strat
from stock_analysis.quant.costs import CostModel
from stock_analysis.quant.engine import Engine, Position, Trade
from stock_analysis.quant.report import (baseline_from, build, complete_weeks, expectation, same_settings,
                                         telegram_text, to_csv, week_key)
from stock_analysis.quant.sizing import Plan, RiskRules
from stock_analysis.quant.store import QuantStore
from stock_analysis.quant.validation import (EULER, compare_random, deflated_sharpe, expected_max_sr, moments,
                                             random_twin)

FREE = CostModel(commission=0.0, sell_tax=0.0, slippage=0.0)


# --------------------------------------------------------------------------
# 1. 운이 아닐 확률
# --------------------------------------------------------------------------
def test_moments_of_a_symmetric_series():
    m = moments([0.01, -0.01, 0.02, -0.02, 0.0])
    assert m["skew"] == pytest.approx(0.0, abs=1e-12)
    assert m["sr"] == pytest.approx(0.0)
    assert moments([0.01, 0.01]) == {}


def test_expected_maximum_sharpe_follows_the_formula():
    trials = [1.0, -1.0, 0.5, -0.5, 0.0, 2.0, -2.0, 1.5, -1.5, 0.2]
    n = len(trials)
    mean = sum(trials) / n
    sd = math.sqrt(sum((t - mean) ** 2 for t in trials) / (n - 1))
    z = NormalDist()
    expected = sd * ((1 - EULER) * z.inv_cdf(1 - 1 / n) + EULER * z.inv_cdf(1 - 1 / (n * math.e)))
    assert expected_max_sr(trials) == pytest.approx(expected)
    assert expected_max_sr([0.3]) == 0.0                     # 한 번뿐이면 기준선은 0


def test_more_tries_make_the_same_result_less_believable():
    rng = random.Random(3)
    rets = [0.0006 + rng.gauss(0, 0.01) for _ in range(750)]
    daily = moments(rets)
    alone = deflated_sharpe(daily, [daily["sr"]])
    many = deflated_sharpe(daily, [daily["sr"]] + [rng.gauss(0, 0.03) for _ in range(40)])
    assert alone["p"] > many["p"]
    assert many["trials"] == 41
    assert deflated_sharpe(moments(rets[:30]), []) is None          # 너무 짧으면 확률을 내지 않는다


def test_random_twin_keeps_the_rules_and_only_shuffles_the_ranking():
    def candles(closes):
        from stock_analysis.prices import Candle

        out, d = [], date(2020, 1, 1)
        while len(out) < len(closes):
            if d.weekday() < 5:
                c = closes[len(out)]
                out.append(Candle(d, c, c * 1.01, c * 0.99, c, 1e6))
            d += timedelta(days=1)
        return out

    data = {f"T{k}": candles([100 * (1.002 + 0.0002 * k) ** j for j in range(280)]) for k in range(12)}
    plan = Plan(check_days=(0, 1, 2, 3, 4), min_hold=0)
    picks = []
    for seed in (0, 1, 0):
        r = backtest.run(random_twin("rotation", seed), RiskRules(max_positions=2, daily_loss_stop=0), FREE,
                         10_000, data, plan=plan)
        picks.append(sorted({t["ticker"] for t in r["trades"]} | {p["ticker"] for p in r["open"]}))
    assert picks[0] == picks[2]                                    # 같은 씨앗이면 같은 결과
    assert picks[0] != picks[1]
    # 성장 전략의 무작위 짝도 '매출 성장 10% 이상' 자격은 똑같이 지킨다 — 순위만 무작위
    first = data["T0"][0].day
    growth = {t: [(first + timedelta(days=90 * q - 1), 0.30 if int(t[1:]) < 3 else -0.05) for q in range(6)]
              for t in data}
    for seed in range(4):
        r = backtest.run(random_twin("growth", seed), RiskRules(max_positions=2, daily_loss_stop=0), FREE,
                         10_000, data, plan=plan, growth=growth)
        assert {t["ticker"] for t in r["trades"]} | {p["ticker"] for p in r["open"]} <= {"T0", "T1", "T2"}
    summary = compare_random({"metrics": {"cagr": 0.10, "sharpe": 1.0, "mdd": 0.10}},
                             [{"metrics": {"cagr": c, "sharpe": c * 10, "mdd": 0.2}} for c in (0.0, 0.05, 0.2)])
    assert summary["beat_cagr"] == pytest.approx(2 / 3) and summary["beat_mdd"] == 1.0
    assert summary["median_cagr"] == 0.05


# --------------------------------------------------------------------------
# 2. 주간 성적표
# --------------------------------------------------------------------------
def engine_with(curve, flows=None, trades=(), events=(), positions=None):
    eng = Engine(strat.STRATEGIES["rotation"], RiskRules(), FREE, 1000.0)
    eng.curve = list(curve)
    eng.flows = dict(flows or {})
    eng.trades = list(trades)
    eng.events = list(events)
    eng.positions = dict(positions or {})
    eng.last_day = curve[-1][0]
    eng.last_close = {t: p.cost * 1.1 for t, p in eng.positions.items()}
    return eng


WEEK1 = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
WEEK2 = ["2026-10-19", "2026-10-20"]


def test_week_return_excludes_deposits_and_compares_with_the_index():
    curve = [("2026-10-09", 1000.0)] + [(d, v) for d, v in zip(WEEK1, [1010, 1020, 1530, 1540, 1550])]
    eng = engine_with(curve, flows={"2026-10-14": 500.0},
                      trades=[Trade("A", "2026-10-01", "2026-10-15", 1, 10, 12, 2, 0.2, "순위 밖")],
                      events=[{"day": "2026-10-15", "text": "매도 A 1주 @ 12.00 — 순위 밖 (손익 +2)"},
                              {"day": "2026-10-15", "text": "다른 기록"}],
                      positions={"B": Position("B", 2, 100.0, "2026-10-13", 90.0)})
    bench = {"2026-10-09": 100.0, "2026-10-16": 102.0}
    r = build(eng, {"started": "2026-10-09"}, "us", "2026-W42", bench)
    daily = [1010 / 1000, 1020 / 1010, (1530 - 500) / 1020, 1540 / 1530, 1550 / 1540]
    expected = math.prod(daily) - 1
    assert r["week_return"] == pytest.approx(expected)               # 넣은 500 은 수익이 아니다
    assert r["bench_week"] == pytest.approx(0.02)
    assert r["deposited"] == 1500.0 and r["trades_week"] == 1
    assert r["events"] == ["매도 A 1주 @ 12.00 — 순위 밖 (손익 +2)"]
    assert r["positions"][0]["ticker"] == "B" and r["positions"][0]["gain"] == pytest.approx(0.1)
    assert build(eng, {"started": "x"}, "us", "2026-W43") is None    # 처리한 날이 없는 주
    no_index = build(eng, {"started": "x"}, "us", "2026-W42", {})
    assert no_index["bench_week"] is None                            # 지수를 못 받았으면 비운다


def test_only_finished_weeks_get_a_report_and_old_weeks_hide_todays_holdings():
    curve = [("2026-10-09", 1000.0)] + [(d, 1000.0) for d in WEEK1 + WEEK2]
    eng = engine_with(curve, positions={"B": Position("B", 1, 10.0, "2026-10-19", 9.0)})
    assert complete_weeks(eng) == ["2026-W42"]                         # 이번 주(화요일까지)는 아직
    old = build(eng, {"started": "s"}, "us", "2026-W42", snapshot=False)
    assert old["positions"] is None and old["regime"] is None and old["missed"] is None
    friday = engine_with([("2026-10-09", 1000.0)] + [(d, 1000.0) for d in WEEK1])
    assert complete_weeks(friday) == ["2026-W42"]                      # 금요일을 처리했으면 그 주는 끝
    assert week_key(date(2026, 10, 16)) == "2026-W42"


def test_expectation_needs_enough_days_and_says_how_far_off():
    base = {"mean": 0.001, "sd": 0.01}
    assert expectation([0.001] * 10, base) is None
    same = expectation([0.001] * 40, base)
    assert abs(same["z"]) < 1e-9 and "다르지 않습니다" in same["verdict"]
    worse = expectation([-0.004] * 40, base)
    assert worse["z"] <= -2 and "나쁩니다" in worse["verdict"]


def test_baseline_is_linked_only_for_the_same_rules():
    result = {"strategy": "rotation", "rules": RiskRules().to_dict(), "costs": FREE.to_dict(),
              "plan": Plan(monthly_deposit=100).to_dict(), "start": "2020-01-01", "end": "2025-01-01",
              "metrics": {"cagr": 0.1, "mdd": 0.2, "daily": {"mean": 0.0004, "sd": 0.01}}}
    eng = Engine(strat.STRATEGIES["rotation"], RiskRules(), FREE, 1000.0, plan=Plan())
    assert same_settings(eng.to_dict(), result)                         # 매달 넣는 돈만 다르면 같은 규칙
    other = Engine(strat.STRATEGIES["breakout"], RiskRules(), FREE, 1000.0, plan=Plan())
    assert not same_settings(other.to_dict(), result)
    assert baseline_from(result)["mean"] == 0.0004 and baseline_from({"metrics": {}}) is None


def test_reports_csv_and_telegram_text():
    report = {"week": "2026-W42", "from": "2026-10-12", "to": "2026-10-16", "account": "2026-10-01",
              "strategy": "factor", "universe": "us500", "equity": 1100.0, "deposited": 1000.0,
              "week_return": 0.012, "bench_week": 0.008, "total_return": 0.1, "bench_total": None, "mdd": 0.05,
              "drawdown": 0.01, "trades_week": 1, "trades_total": 5, "win_rate": 0.6, "profit_factor": 1.5,
              "positions": [{"ticker": "NVDA"}], "regime": True, "events": ["매수 NVDA 1주 @ 100.00 — 순위 상위"],
              "expect": None}
    text = to_csv([report])
    head, row = text.splitlines()[:2]
    assert head.startswith("account,week,from,to") and "NVDA" in row and "위" in row
    message = telegram_text(report, "미국", "S&P 500 (SPY)")
    assert "+1.2%" in message and "S&amp;P 500 (SPY) +0.8%" in message and "NVDA" in message   # 텔레그램 HTML
    assert "실제 주문은 없습니다" in message


def test_paper_account_writes_weekly_reports_and_the_app_sends_them_once(tmp_path):
    from stock_analysis.app import Bot
    from stock_analysis.prices import Candle

    def bars(n):
        out, d, p = [], date(2024, 1, 1), 100.0
        while len(out) < n:
            if d.weekday() < 5:
                p *= 1.003
                out.append(Candle(d, p, p * 1.01, p * 0.99, p, 1e6))
            d += timedelta(days=1)
        return out

    full = bars(230)
    target = SimpleNamespace(ticker="A", market="us", cik="1", key="1", name="A", watch=SimpleNamespace(name="A"))
    metrics = {"1": SimpleNamespace(bars=full[:210], market_open=False)}
    store = QuantStore(tmp_path / "quant.json")
    bot = SimpleNamespace(cached_targets=lambda: [target], cached_metrics=lambda: metrics, quant=store,
                          market_snapshot=lambda: None)
    paper.start(store, bot, "us", 10_000, "rotation", RiskRules(), FREE, date(2024, 10, 1),
                plan=Plan(check_days=(0, 1, 2, 3, 4), min_hold=0))
    metrics["1"].bars = full
    days, _ = paper.step(store, bot, "us")
    reports = store.reports("us")
    assert days == 20 and len(reports) >= 3
    assert all(r["sent"] is False for r in reports)
    assert reports[-1]["positions"] is not None and all(r["positions"] is None for r in reports[:-1])
    sent = []
    fake = SimpleNamespace(quant=store, notifier=SimpleNamespace(send=lambda text: sent.append(text) or True))
    Bot._send_reports(fake, "us")
    Bot._send_reports(fake, "us")
    assert len(sent) == 2                                              # 밀린 주가 많아도 최근 두 장만, 한 번만
    assert all(r["sent"] for r in store.reports("us"))


# --------------------------------------------------------------------------
# 3. 화면
# --------------------------------------------------------------------------
def test_quant_page_offers_the_wide_basket_the_filter_and_the_luck_check(bot):
    from stock_analysis.dashboard import Dashboard

    html = Dashboard(bot).render_path("/quant?m=us")
    for word in ("넓은 종목 바구니", "자료 받기", "시장 국면 필터", "운과 비교", "주간 성적표", "미국 큰 회사 500",
                 "Novy-Marx", "Deflated Sharpe"):
        assert word in html
    kr = Dashboard(bot).render_path("/quant?m=kr")
    assert "한국 큰 회사 200" in kr and "미국 큰 회사 500" not in kr


def test_settings_pick_a_basket_only_for_its_own_market():
    from stock_analysis.dashboard import quant_settings

    us = quant_settings({"universe": ["us500"], "market_filter": ["exit"]}, "us")
    assert us["plan"].universe == "us500" and us["plan"].market_filter == "exit"
    kr = quant_settings({"universe": ["us500"], "market_filter": ["weird"]}, "kr")
    assert kr["plan"].universe == "watch" and kr["plan"].market_filter == "off"


def test_backtest_result_shows_the_luck_numbers(bot):
    from stock_analysis.dashboard import Dashboard
    from stock_analysis.prices import Candle

    days, d = [], date(2020, 1, 1)
    while len(days) < 300:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    data = {"A": [Candle(x, 100 + k * 0.1, 101 + k * 0.1, 99 + k * 0.1, 100 + k * 0.1, 1e6) for k, x in enumerate(days)]}
    result = backtest.run(strat.STRATEGIES["rotation"], RiskRules(), FREE, 10_000, data)
    result["dsr"] = {"p": 0.42, "sr0": 1.1, "trials": 12}
    result["random"] = {"runs": 30, "beat_cagr": 0.5, "beat_sharpe": 0.4, "beat_mdd": 0.6, "median_cagr": 0.03,
                        "median_mdd": 0.2, "median_sharpe": 0.3}
    bot.quant.set_backtest("us", result)
    html = Dashboard(bot).render_path("/quant?m=us")
    assert "운이 아닐 확률 42%" in html and "백테스트 12번" in html and "운과 구별하기 어렵습니다" in html
    assert "무작위 순위 30번과 비교" in html and "운보다 낫다고 보기 어렵습니다" in html


def test_weekly_report_card_shows_overall_and_this_week(bot):
    from stock_analysis.dashboard import Dashboard

    bot.quant.set_paper("us", {"engine": Engine(strat.STRATEGIES["rotation"], RiskRules(), FREE, 1000.0).to_dict(),
                               "started": "2026-10-01", "paused": False, "baseline": None})
    bot.quant.add_report("us", {"week": "2026-W42", "from": "2026-10-12", "to": "2026-10-16", "account": "2026-10-01",
                                "week_return": 0.012, "bench_week": 0.008, "total_return": 0.05, "bench_total": 0.03,
                                "mdd": 0.04, "drawdown": 0.01, "trades_week": 1, "trades_total": 3, "win_rate": 2 / 3,
                                "profit_factor": 1.8, "events": ["매수 NVDA 1주 @ 100.00 — 순위 상위"],
                                "positions": [{"ticker": "NVDA", "cost": 100.0, "last": 110.0, "gain": 0.1,
                                               "weight": 0.5, "since": "2026-10-13"}],
                                "regime": True, "missed": {"chased": 2}, "expect": None, "sent": True})
    html = Dashboard(bot).render_path("/quant?m=us")
    assert "전체 평가" in html and "이번 주 세부" in html and "지난 주들" in html
    assert "+1.2%" in html and "S&amp;P 500 (SPY)" in html and "급등일이라 2번" in html
    assert "/download/quant_weekly?m=us" in html
    assert "같은 설정의 백테스트가 없어" in html


def test_getting_basket_data_runs_beside_the_screen_and_shows_progress(bot):
    import threading
    import time

    from stock_analysis.dashboard import Dashboard

    release = threading.Event()

    class FakeData:
        progress = ""

        def refresh(self, market):
            self.progress = "미국 시세 (3/800) AAPL"
            release.wait(5)
            self.progress = ""
            return "시세: 800개 받음"

    bot._quant_data = FakeData()
    dash = Dashboard(bot)
    assert "받기 시작했습니다" in dash.run_action("quant_data", {"back": ["/quant?m=us"]})
    for _ in range(100):
        if bot._quant_data.progress:
            break
        time.sleep(0.01)
    assert "퀀트 바구니 자료 받는 중: 미국 시세 (3/800) AAPL" in (dash.status()["busy"] or "")
    assert dash.busy is None                                      # 화면 작업 잠금은 그대로 비어 있다
    assert "이미 받는 중" in dash.run_action("quant_data", {"back": ["/quant?m=us"]})
    release.set()
    for _ in range(200):
        if dash.notice:
            break
        time.sleep(0.01)
    assert "800개 받음" in dash.notice
