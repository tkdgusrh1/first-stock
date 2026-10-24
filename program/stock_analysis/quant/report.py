"""주간 성적표 — 모의 계좌를 매주 한 장씩 남긴다. 실전 전에 쌓는 '데이터' 가 이것이다.

한 장에 두 가지:
- 전체 평가(시작 뒤 지금까지): 수익률(넣은 돈 제외) · 지수 대비 · 최대 낙폭 · 거래 수·승률·이익÷손실 합 ·
  같은 설정 백테스트가 있으면 '기대한 만큼 가고 있나'(하루 평균 수익의 차이를 표준오차로 나눈 값).
- 그 주 세부: 그 주 수익률과 지수 · 산 것·판 것(일지 그대로) · 들고 있는 종목 · 시장 국면 ·
  신호가 났는데 안 산 횟수(그 주에 늘어난 만큼).

숫자는 모의 계좌 기록(엔진)과 지수 일봉으로만 계산한다. 지수를 못 받았으면 지수 칸은 비운다.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import date

from .. import __version__
from .backtest import trade_stats

MIN_DAYS_FOR_EXPECT = 20          # 기대치 비교는 처리한 날이 이만큼 쌓인 뒤부터
EVENT_WORDS = ("매수", "매도", "긴급", "시장(지수)", "손절선", "입금", "매수 취소", "매수 주문 취소")


def week_key(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def _days(curve) -> list[date]:
    return [date.fromisoformat(d) for d, _ in curve]


def complete_weeks(engine) -> list[str]:
    """다 끝난 주 — 그 뒤 주의 날을 처리했거나, 그 주 금요일을 처리했다. 시작한 날(체결 없음)은 뺀다."""
    days = _days(engine.curve[1:])
    if not days:
        return []
    weeks = sorted({week_key(d) for d in days})
    last = days[-1]
    return [w for w in weeks if w != week_key(last) or last.weekday() == 4]


def _twr(values: list[float], days: list[str], flows: dict) -> list[float]:
    """하루 수익률(넣은 돈은 빼고)."""
    out = []
    for k in range(1, len(values)):
        prev = values[k - 1]
        out.append((values[k] - flows.get(days[k], 0.0)) / prev - 1 if prev else 0.0)
    return out


def _chain(rets) -> float:
    total = 1.0
    for r in rets:
        total *= 1 + r
    return total - 1


def _close_at(bench: dict, day: str) -> float | None:
    """그 날 또는 그 전 마지막 지수 종가."""
    keys = [d for d in bench if d <= day]
    return bench[max(keys)] if keys else None


def build(engine, account: dict, market: str, week: str, bench: dict | None = None,
          prev: dict | None = None, snapshot: bool = True) -> dict | None:
    """그 주의 성적표. 그 주에 처리한 날이 없으면 None.

    snapshot=False 는 지난 주를 뒤늦게 채울 때 — 보유 종목·국면·놓친 신호는 '지금' 값이라 그 주 값이 아니므로 비운다.
    """
    curve = engine.curve
    days = [d for d, _ in curve]
    values = [v for _, v in curve]
    in_week = [k for k, d in enumerate(days) if k > 0 and week_key(date.fromisoformat(d)) == week]
    if not in_week:
        return None
    first, last = in_week[0], in_week[-1]
    rets = _twr(values, days, engine.flows)              # rets[k-1] = days[k] 의 수익률
    week_ret = _chain(rets[first - 1:last])
    total_ret = _chain(rets[:last])
    index, peak, mdd = 1.0, 1.0, 0.0
    for r in rets[:last]:
        index *= 1 + r
        peak = max(peak, index)
        mdd = max(mdd, 1 - index / peak)

    bench_week = bench_total = None
    if bench:
        before, end, start = _close_at(bench, days[first - 1]), _close_at(bench, days[last]), _close_at(bench, days[0])
        if before and end:
            bench_week = end / before - 1
        if start and end:
            bench_total = end / start - 1

    lo, hi = days[first], days[last]
    closed = [t for t in engine.trades if t.exit_day <= hi]
    stats = trade_stats(closed)
    wins = sum(1 for t in closed if t.pnl > 0)
    equity = values[last]
    positions = []
    for t, p in engine.positions.items():
        last_close = engine.last_close.get(t)
        positions.append({"ticker": t, "shares": p.shares, "cost": p.cost, "last": last_close,
                          "gain": (last_close / p.cost - 1) if last_close and p.cost else None,
                          "stop": p.stop, "since": p.entry_day,
                          "weight": (p.shares * last_close / equity) if last_close and equity else None})
    counters = {"skipped": engine.skipped, "no_cash": engine.no_cash, "chased": engine.chased, "blocked": engine.blocked,
                "regime_blocked": engine.regime_blocked}
    before = (prev or {}).get("counters") or {}
    report = {
        "week": week, "from": lo, "to": hi, "account": account.get("started"), "market": market,
        "strategy": engine.strategy.key, "strategy_name": engine.strategy.name, "universe": engine.plan.universe,
        "equity": equity, "deposited": engine.capital + sum(v for d, v in engine.flows.items() if d <= hi),
        "week_return": week_ret, "bench_week": bench_week, "total_return": total_ret, "bench_total": bench_total,
        "mdd": mdd, "drawdown": 1 - index / peak if peak else 0.0, "cash": engine.cash,
        "trades_week": sum(1 for t in engine.trades if lo <= t.exit_day <= hi),
        "trades_total": len(closed), "win_rate": wins / len(closed) if closed else None,
        "profit_factor": stats.get("profit_factor"), "worst": stats.get("worst"),
        "events": [e["text"] for e in engine.events if lo <= e["day"] <= hi and e["text"].startswith(EVENT_WORDS)],
        "positions": positions if snapshot else None, "regime": engine.regime_on if snapshot else None,
        "counters": counters if snapshot else None,
        # 놓친 신호는 '그 주에 늘어난 만큼'. 첫 성적표면 시작 뒤 전부, 앞 장이 뒤늦게 채운 장이면 비교할 수 없어 비운다.
        "missed": ({k: v - int(before.get(k) or 0) for k, v in counters.items()}
                   if snapshot and (prev is None or (prev or {}).get("counters")) else None),
        "as_of": engine.last_day if snapshot else None, "days": last, "version": __version__,
    }
    report["expect"] = expectation(rets[:last], account.get("baseline"))
    return report


def expectation(rets: list[float], baseline: dict | None) -> dict | None:
    """모의 계좌의 하루 평균 수익이 같은 설정 백테스트의 평균과 얼마나 다른가(표준오차 단위, z)."""
    if not baseline or len(rets) < MIN_DAYS_FOR_EXPECT:
        return None
    mean_b, sd_b = baseline.get("mean"), baseline.get("sd")
    if mean_b is None or not sd_b:
        return None
    mean_p = sum(rets) / len(rets)
    z = (mean_p - mean_b) / (sd_b / math.sqrt(len(rets)))
    if z <= -2:
        verdict = "백테스트 기대보다 뚜렷이 나쁩니다 — 규칙이 지금 시장에 안 맞거나 백테스트가 과최적화였을 수 있습니다"
    elif z >= 2:
        verdict = "백테스트 기대보다 뚜렷이 좋습니다 — 운일 수 있으니 기대치를 올리지 마세요"
    else:
        verdict = "백테스트 기대와 통계적으로 다르지 않습니다"
    return {"z": z, "days": len(rets), "paper_year": mean_p * 252, "backtest_year": mean_b * 252, "verdict": verdict}


def baseline_from(result: dict | None) -> dict | None:
    """백테스트 결과에서 기대치(하루 평균·표준편차·연수익률·최대 낙폭)만."""
    m = (result or {}).get("metrics") or {}
    daily = m.get("daily") or {}
    if "mean" not in daily or "sd" not in daily:
        return None
    return {"mean": daily["mean"], "sd": daily["sd"], "cagr": m.get("cagr"), "mdd": m.get("mdd"),
            "start": result.get("start"), "end": result.get("end")}


def same_settings(engine_dict: dict, result: dict | None) -> bool:
    """모의 계좌와 백테스트가 같은 규칙인가(매달 넣는 돈은 수익률 계산에서 빠지므로 달라도 같다고 본다)."""
    if not result:
        return False

    def plan(raw):
        return {k: v for k, v in (raw or {}).items() if k != "monthly_deposit"}

    return (engine_dict.get("strategy") == result.get("strategy") and engine_dict.get("rules") == result.get("rules")
            and engine_dict.get("costs") == result.get("costs") and plan(engine_dict.get("plan")) == plan(result.get("plan")))


def telegram_text(report: dict, market_name: str, bench_name: str) -> str:
    from ..telegram import esc

    def pct(v):
        return "-" if v is None else f"{v * 100:+.1f}%"

    lines = [f"📊 <b>퀀트 모의 계좌 주간 성적표</b> — {esc(market_name)} · {esc(report['week'])} "
             f"({esc(report['from'])}~{esc(report['to'])})",
             f"이번 주 {pct(report['week_return'])} ({esc(bench_name)} {pct(report['bench_week'])}) · "
             f"시작 뒤 {pct(report['total_return'])} (지수 {pct(report['bench_total'])})",
             f"최대 낙폭 {pct(-report['mdd'])} · 거래 이번 주 {report['trades_week']}건 / 누적 {report['trades_total']}건"
             + (f" (승률 {report['win_rate'] * 100:.0f}%)" if report.get("win_rate") is not None else "")]
    for text in report.get("events", [])[:8]:
        lines.append(f"· {esc(text)}")
    if report.get("positions") is not None:
        held = ", ".join(p["ticker"] for p in report["positions"])
        lines.append(f"들고 있는 것: {esc(held) if held else '없음(현금)'}")
    if report.get("regime") is not None:
        lines.append("시장 국면: 지수 200일선 " + ("위" if report["regime"] else "아래"))
    expect = report.get("expect")
    if expect:
        lines.append(f"백테스트 기대와 비교: {esc(expect['verdict'])} (z = {expect['z']:+.1f})")
    lines.append("실제 주문은 없습니다 — 연습 기록입니다.")
    return "\n".join(lines)


CSV_COLUMNS = ("account", "week", "from", "to", "strategy", "universe", "equity", "deposited", "week_return",
               "bench_week", "total_return", "bench_total", "mdd", "drawdown", "trades_week", "trades_total",
               "win_rate", "profit_factor", "positions", "regime", "version")


def to_csv(reports: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for r in reports:
        row = []
        for col in CSV_COLUMNS:
            value = r.get(col)
            if col == "positions":
                value = " ".join(p["ticker"] for p in value or [])
            elif col == "regime":
                value = "" if value is None else ("위" if value else "아래")
            row.append("" if value is None else value)
        writer.writerow(row)
    return out.getvalue()
