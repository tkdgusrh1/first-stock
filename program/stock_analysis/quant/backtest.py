"""백테스트 — 규칙을 과거에 그대로 적용했다면 어땠을지.

결과에는 **좋게 나오게 만드는 함정**을 같이 적는다. 경고 없이 숫자만 보여주면
백테스트는 거의 항상 실제보다 좋아 보인다.
"""

from __future__ import annotations

from datetime import date

from .costs import CostModel
from .engine import Engine, prepare
from .sizing import RiskRules
from .strategies import Strategy

SPLIT = 0.7            # 앞 70% 기간 / 뒤 30% 기간으로 나눠 따로 잰다
CURVE_POINTS = 400     # 화면에 보낼 점 수
MIN_TRADES = 30


def run(strategy: Strategy, rules: RiskRules, costs: CostModel, capital: float,
        data: dict, start: date | None = None, end: date | None = None) -> dict:
    """data = {티커: [Candle]}. 결과는 그대로 JSON 으로 저장할 수 있는 dict."""
    prepared = prepare(data)
    engine = Engine(strategy, rules, costs, capital)
    all_days = sorted({d for (_, _, days, _) in prepared.values() for d in days})
    if start:
        all_days = [d for d in all_days if d >= start.isoformat()]
    if end:
        all_days = [d for d in all_days if d <= end.isoformat()]
    for iso in all_days:
        series = {}
        for t, (bars, closes, days, index) in prepared.items():
            i = index.get(iso)
            if i is not None:
                series[t] = (bars, closes, days, i)
        engine.on_day(date.fromisoformat(iso), series)

    curve = engine.curve
    bench = benchmark(prepared, [d for d, _ in curve], capital)
    result = {
        "strategy": strategy.key, "strategy_name": strategy.name,
        "rules": rules.to_dict(), "costs": costs.to_dict(), "capital": capital,
        "tickers": sorted(prepared), "start": curve[0][0] if curve else None,
        "end": curve[-1][0] if curve else None,
        "metrics": summarize(curve, engine.trades, capital, engine),
        "bench": summarize(bench, [], capital) if bench else None,
        "split": split(curve),
        "curve": thin([(d, v, bv) for (d, v), (_, bv) in zip(curve, bench)] if bench
                      else [(d, v, None) for d, v in curve]),
        "trades": [t.__dict__ for t in reversed(engine.trades[-300:])],
        "open": [{"ticker": p.ticker, "shares": p.shares, "cost": p.cost, "entry_day": p.entry_day,
                  "last": engine.last_close.get(p.ticker)} for p in engine.positions.values()],
        "skipped": engine.skipped, "blocked": engine.blocked, "halted_on": engine.halted_on,
    }
    result["warnings"] = warnings(result)
    return result


def benchmark(prepared: dict, days: list[str], capital: float) -> list[tuple[str, float]]:
    """같은 종목들을 첫날 똑같이 나눠 사서 그냥 들고 있었다면(비용 빼기 전).

    첫날 이미 상장돼 있던 종목만 넣는다. 그 뒤에 생긴 종목을 끼워 넣으면 비교가 흐려진다.
    """
    if not days:
        return []
    first = days[0]
    starts = {}
    for t, (bars, closes, tdays, index) in prepared.items():
        if t and tdays and tdays[0] <= first:
            k = max(0, _at_or_before(tdays, first))
            if closes[k] > 0:
                starts[t] = closes[k]
    if not starts:
        return []
    out = []
    last = dict(starts)
    for d in days:
        for t in starts:
            bars, closes, tdays, index = prepared[t]
            i = index.get(d)
            if i is not None:
                last[t] = closes[i]
        value = capital * sum(last[t] / starts[t] for t in starts) / len(starts)
        out.append((d, round(value, 6)))
    return out


def _at_or_before(days: list[str], day: str) -> int:
    import bisect

    return bisect.bisect_right(days, day) - 1


def summarize(curve, trades, capital: float, engine: Engine | None = None) -> dict:
    if not curve:
        return {}
    values = [v for _, v in curve]
    first_day, last_day = date.fromisoformat(curve[0][0]), date.fromisoformat(curve[-1][0])
    years = (last_day - first_day).days / 365.25
    end = values[-1]
    total = end / capital - 1 if capital else 0.0
    cagr = (end / capital) ** (1 / years) - 1 if years > 0.0 and end > 0 and capital else None

    peak, peak_day, mdd, mdd_peak, mdd_day = values[0], curve[0][0], 0.0, curve[0][0], curve[0][0]
    under_start, longest = None, 0
    for (d, v) in curve:
        if v >= peak:
            if under_start is not None:
                longest = max(longest, (date.fromisoformat(d) - date.fromisoformat(under_start)).days)
                under_start = None
            peak, peak_day = v, d
        else:
            if under_start is None:
                under_start = peak_day
            dd = 1 - v / peak
            if dd > mdd:
                mdd, mdd_peak, mdd_day = dd, peak_day, d
    if under_start is not None:
        longest = max(longest, (last_day - date.fromisoformat(under_start)).days)

    rets = [values[k] / values[k - 1] - 1 for k in range(1, len(values)) if values[k - 1]]
    vol = sharpe = None
    if len(rets) > 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = var ** 0.5
        vol = std * 252 ** 0.5
        sharpe = mean / std * 252 ** 0.5 if std > 0 else None

    out = {"total": total, "cagr": cagr, "mdd": mdd, "mdd_from": mdd_peak, "mdd_to": mdd_day,
           "underwater_days": longest, "vol": vol, "sharpe": sharpe, "years": years, "end": end}
    if engine is not None:
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else None
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else None
        mean_equity = sum(values) / len(values)
        out.update({
            "trades": len(trades), "win_rate": len(wins) / len(trades) if trades else None,
            "avg_win": avg_win, "avg_loss": avg_loss,
            "payoff": (avg_win / abs(avg_loss)) if avg_win is not None and avg_loss else None,
            "fees": engine.fees_paid,
            "turnover": (engine.bought / mean_equity / years) if years > 0 and mean_equity else None,
            "exposure": engine.invested_days / len(curve) if curve else None,
        })
    return out


def split(curve) -> dict | None:
    """앞 70% / 뒤 30%. 뒤 기간이 크게 나쁘면 과최적화나 운을 의심한다."""
    if len(curve) < 60:
        return None
    cut = int(len(curve) * SPLIT)
    head, tail = curve[:cut + 1], curve[cut:]
    return {"cut": curve[cut][0],
            "head": summarize(head, [], head[0][1]),
            "tail": summarize(tail, [], tail[0][1])}


def thin(points: list, keep: int = CURVE_POINTS) -> list:
    if len(points) <= keep:
        return [list(p) for p in points]
    step = (len(points) - 1) / (keep - 1)
    picked = [points[round(k * step)] for k in range(keep)]
    picked[-1] = points[-1]
    return [list(p) for p in picked]


def warnings(result: dict) -> list[str]:
    m = result.get("metrics") or {}
    out = ["지금 감시 목록에 있는 종목만으로 돌렸습니다. 그사이 상장폐지·급락으로 빠진 종목이 없어서 "
           "결과가 실제보다 좋게 나옵니다(생존 편향). 특히 '지금 잘나가는 종목'을 골라 넣었다면 더 그렇습니다.",
           "가격은 Yahoo 일봉입니다(액면분할 반영, 배당 미포함). 체결은 신호 다음 날 시가 + 체결 차이로 가정했습니다."]
    n = len(result.get("tickers") or [])
    if n < 5:
        out.append(f"종목이 {n}개뿐이라 결과가 몇 종목의 운에 크게 좌우됩니다. 10개 이상을 권합니다.")
    trades = m.get("trades") or 0
    if trades < MIN_TRADES:
        out.append(f"거래가 {trades}번뿐이라 통계로 믿기 어렵습니다(최소 {MIN_TRADES}번 이상 권장).")
    if (m.get("years") or 0) < 3:
        out.append("기간이 3년보다 짧습니다. 오르는 장·내리는 장을 모두 거친 기간이 필요합니다.")
    if result.get("skipped"):
        out.append(f"1주도 살 수 없어 건너뛴 신호가 {result['skipped']}번 있었습니다 — 계좌가 작아서 생기는 일입니다.")
    if result.get("halted_on"):
        out.append(f"규칙대로라면 {result['halted_on']}에 새 매수를 멈췄어야 합니다(고점 대비 낙폭 한도). "
                   "그 뒤의 결과는 '멈추지 않고 보유만 했을 때' 입니다.")
    sp = result.get("split") or {}
    head, tail = (sp.get("head") or {}).get("cagr"), (sp.get("tail") or {}).get("cagr")
    if head is not None and tail is not None and head > 0 and tail < head / 2:
        out.append("뒤 30% 기간의 연수익률이 앞 기간의 절반도 안 됩니다. 과최적화이거나 앞 기간의 운일 수 있습니다.")
    return out
