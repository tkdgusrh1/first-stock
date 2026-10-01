"""모의 계좌 · 오늘의 신호 — 받아둔 일봉으로만 계산한다(네트워크 없음).

모의 계좌는 백테스트와 **같은 엔진**을 하루씩 이어서 돌린다. 주문은 실제로 나가지 않고,
다음 날 시가(+체결 차이)에 체결된 것으로 기록한다. 그래서 '백테스트 → 모의' 의 차이는
규칙이 아니라 시기(앞으로 올 시장)에서만 생긴다.

장이 열려 있는 동안의 오늘 봉은 아직 끝나지 않은 봉이라 **쓰지 않는다.**
"""

from __future__ import annotations

from datetime import date

from . import indicators as ind
from . import strategies as strat
from .costs import CostModel
from .engine import Engine, prepare
from .sizing import Plan, RiskRules


def leveraged_tickers(bot, market: str) -> list[str]:
    """감시 종목 중 레버리지·인버스 상품(이름으로 판단). 퀀트 계산에서는 기본으로 뺀다."""
    from .evidence import is_leveraged

    return sorted(t.ticker for t in bot.cached_targets()
                  if t.market == market and is_leveraged(f"{t.watch.name or ''} {t.name or ''}"))


def market_data(bot, market: str) -> dict:
    """{티커: [끝난 날의 Candle]} — 그 시장의 감시 종목 중 봉이 있는 것만. 레버리지·인버스는 뺀다."""
    metrics = bot.cached_metrics()
    data, live = {}, False
    skip = set(leveraged_tickers(bot, market))
    for target in bot.cached_targets():
        if target.market != market or target.ticker in skip:
            continue
        m = metrics.get(target.cik)
        bars = list(getattr(m, "bars", None) or []) if m else []
        if bars:
            data[target.ticker] = bars
            live = live or bool(getattr(m, "market_open", False))
    if live and data:
        today = max(b.day for bars in data.values() for b in bars)
        data = {t: [b for b in bars if b.day < today] for t, bars in data.items()}
    return {t: bars for t, bars in data.items() if bars}


def _series_on(prepared: dict, iso: str) -> dict:
    out = {}
    for t, (bars, closes, days, index) in prepared.items():
        i = index.get(iso)
        if i is not None:
            out[t] = (bars, closes, days, i)
    return out


def start(store, bot, market: str, capital: float, strategy_key: str,
          rules: RiskRules, costs: CostModel, today: date, plan: Plan | None = None) -> str:
    prepared = prepare(market_data(bot, market))
    if not prepared:
        return "모의 계좌를 시작할 일봉이 없습니다. 감시 종목의 지표를 먼저 불러와 주세요."
    last = max(days[-1] for (_, _, days, _) in prepared.values())
    engine = Engine(strat.get(strategy_key), rules, costs, capital, plan=plan or Plan())
    engine.growth = _growth_for(engine, bot, market)
    deposit = f", 매달 {engine.plan.monthly_deposit:,.0f}" if engine.plan.monthly_deposit else ""
    engine._note(last, f"모의 계좌 시작 — {engine.strategy.name}, 자본 {capital:,.0f}{deposit}, "
                       f"점검 {engine.plan.days_text}")
    # 마지막으로 끝난 날의 종가로 신호만 만든다. 체결은 다음 거래일 시가부터.
    engine.on_day(date.fromisoformat(last), _series_on(prepared, last), execute=False)
    store.set_paper(market, {"engine": engine.to_dict(), "started": today.isoformat(), "paused": False})
    store.save()
    pending = len(engine.orders)
    return (f"모의 계좌를 시작했습니다({engine.strategy.name}). "
            + (f"다음 거래일 시가에 체결될 주문 {pending}건이 있습니다." if pending
               else f"다음 점검일({engine.plan.days_text}) 장 마감 뒤에 신호를 봅니다."))


def step(store, bot, market: str) -> tuple[int, list[dict]]:
    """끝난 거래일을 이어서 처리한다. (처리한 날 수, 새로 생긴 일지)"""
    account = store.paper(market)
    if not account or account.get("paused"):
        return 0, []
    engine = Engine.from_dict(account.get("engine") or {}, market)
    prepared = prepare(market_data(bot, market))
    if not prepared or not engine.last_day:
        return 0, []
    days = sorted({d for (_, _, ds, _) in prepared.values() for d in ds if d > engine.last_day})
    if days:                         # 재무 파일은 크다 — 처리할 날이 있을 때만 읽는다
        engine.growth = _growth_for(engine, bot, market)
    before = len(engine.events)
    for iso in days:
        engine.on_day(date.fromisoformat(iso), _series_on(prepared, iso))
    if days:
        account["engine"] = engine.to_dict()
        store.set_paper(market, account)
        store.save()
    return len(days), engine.events[before:] if days else []


def _growth_for(engine, bot, market: str) -> dict:
    if not getattr(engine.strategy, "needs_growth", False):
        return {}
    from .fundamentals import growth_data

    try:
        return growth_data(bot, market)
    except Exception:
        return {}


def account_view(store, bot, market: str) -> dict | None:
    """화면용 요약. 계좌가 없으면 None."""
    account = store.paper(market)
    if not account:
        return None
    engine = Engine.from_dict(account.get("engine") or {}, market)
    watched = set(market_data(bot, market))
    equity = engine.equity()
    return {
        "engine": engine, "equity": equity, "started": account.get("started"),
        "paused": bool(account.get("paused")),
        "return": (equity / (engine.capital + engine.deposited) - 1) if engine.capital else None,
        "drawdown": engine.drawdown(),
        "orphans": [t for t in engine.positions if t not in watched],
    }


# --------------------------------------------------------------------------
# 오늘의 신호 + 성장 점수
# --------------------------------------------------------------------------
def _rank_pct(values: dict) -> dict:
    """값이 큰 순서의 백분위(0~1). None 은 빼고 매긴다."""
    present = sorted((v, t) for t, v in values.items() if v is not None)
    if len(present) < 2:
        return {t: 0.5 for _, t in present}
    # 같은 값은 같은 순위(평균 순위). 이름순으로 갈리면 점수가 거짓말을 한다.
    out, k = {}, 0
    while k < len(present):
        j = k
        while j + 1 < len(present) and present[j + 1][0] == present[k][0]:
            j += 1
        rank = (k + j) / 2 / (len(present) - 1)
        for _, t in present[k:j + 1]:
            out[t] = rank
        k = j + 1
    return out


def signals(bot, market: str) -> list[dict]:
    """감시 종목마다: 지금 어떤 전략 신호가 켜졌나 + 성장 점수.

    성장 점수 = 매출 성장률·ROIC·6개월 수익률의 순위 평균(이 목록 안에서의 상대 순위).
    **지금 재무로 매기는 점수라 백테스트에는 넣지 않는다**(과거 시점의 재무가 없어 미래 정보가 된다).
    """
    prepared = prepare(market_data(bot, market))
    metrics = bot.cached_metrics()
    targets = {t.ticker: t for t in bot.cached_targets() if t.market == market}
    rows = {}
    for ticker, (bars, closes, days, index) in prepared.items():
        i = len(bars) - 1
        target = targets.get(ticker)
        m = metrics.get(target.cik) if target else None
        high = ind.highest(closes, i, 252, include_today=True) if i >= 251 else None
        surprise = (getattr(m, "surprise", None) or {}).get("eps_surprise_pct") if m else None
        on = []
        for s in strat.STRATEGIES.values():
            if s.kind != "signal" or i + 1 < s.warmup:
                continue
            if s.entry(bars, closes, i):
                on.append(s.name)
        rot = strat.STRATEGIES["rotation"]
        rows[ticker] = {
            "ticker": ticker, "name": (target.watch.name or target.name) if target else ticker,
            "day": days[-1], "close": closes[i],
            "ret6m": ind.change(closes, i, 126),
            "from_high": (closes[i] / high - 1) if high else None,
            "growth": getattr(m, "revenue_growth", None) if m else None,
            "roic": getattr(m, "roic", None) if m else None,
            "surprise": surprise, "signals": on,
            "rot_score": rot.score(bars, closes, i) if i + 1 >= rot.warmup else None,
        }
    parts = [_rank_pct({t: r[key] for t, r in rows.items()}) for key in ("growth", "roic", "ret6m")]
    for t, r in rows.items():
        got = [p[t] for p in parts if t in p]
        r["score"] = sum(got) / len(got) if got else None
        r["score_parts"] = len(got)
    ranked = sorted((r for r in rows.values() if r["rot_score"] is not None),
                    key=lambda r: r["rot_score"], reverse=True)
    for k, r in enumerate(ranked):
        r["rot_rank"] = k + 1
    return sorted(rows.values(), key=lambda r: (r["score"] is None, -(r["score"] or 0)))
