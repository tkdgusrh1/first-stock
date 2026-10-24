"""모의 계좌 · 오늘의 신호 — 받아둔 일봉으로만 계산한다(네트워크 없음).

모의 계좌는 백테스트와 **같은 엔진**을 하루씩 이어서 돌린다. 주문은 실제로 나가지 않고,
다음 날 시가(+체결 차이)에 체결된 것으로 기록한다. 그래서 '백테스트 → 모의' 의 차이는
규칙이 아니라 시기(앞으로 올 시장)에서만 생긴다.

장이 열려 있는 동안의 오늘 봉은 아직 끝나지 않은 봉이라 **쓰지 않는다.**
"""

from __future__ import annotations

import threading

from datetime import date, timedelta

from . import indicators as ind
from . import strategies as strat
from .basket import is_basket
from .costs import CostModel
from .engine import Engine, at_day, prepare
from .sizing import Plan, RiskRules

PAPER_HISTORY = 600       # 바구니 모의 계좌가 읽는 일봉 기간(일) — 1년 모멘텀·200일선을 계산할 만큼
SPLIT_GAP = 0.01          # 같은 날 종가가 이만큼 넘게 달라졌으면 그 사이 가격 기준이 바뀌었다(분할·병합, 자료 수정)
SPLIT_SURE = 0.15         # 분할 기록이 없어도 이만큼 바뀌었으면 분할·병합으로 본다 — 끝난 날 종가가 이만큼 고쳐지는 일은 없다


def leveraged_tickers(bot, market: str) -> list[str]:
    """감시 종목 중 레버리지·인버스 상품. 퀀트 계산에서는 기본으로 뺀다.

    ETF 판정(펀드 이름으로 읽은 배수·인버스)을 먼저 보고, 이름으로 한 번 더 본다.
    예전엔 이름만 봤는데 SEC 이름이 신탁 이름('Direxion Shares ETF Trust')이라 SOXL·SOXS 가 빠지지 않았다.
    """
    from .evidence import is_leveraged

    def risky(t) -> bool:
        fund = getattr(t, "fund", None)
        if fund is not None and (fund.high_risk or not getattr(fund, "name_checked", True)):
            return True               # 펀드 이름을 못 읽어 배수를 모르는 ETF 도 일단 뺀다(안전한 쪽)
        return is_leveraged(f"{t.watch.name or ''} {t.name or ''}")

    return sorted(t.ticker for t in bot.cached_targets() if t.market == market and risky(t))


def etf_tickers(bot, market: str, universe: str = "watch") -> set:
    """감시 종목 중 ETF·펀드(바구니면 바구니 전부). 성장 전략에서 매출 없이도 자격을 준다."""
    if is_basket(universe):
        return set()                  # 매출 순위 바구니에는 ETF 가 없다
    if universe == "defense":
        from .exits import BASKETS

        return {t for t, _ in BASKETS.get(market, ())}
    return {t.ticker for t in bot.cached_targets() if t.market == market and getattr(t, "is_fund", False)}


def basket_data(bot, market: str) -> dict:
    """방어형 ETF 바구니의 일봉(최대 10년). 받지 못한 종목은 빠진다 — 지어내지 않는다.

    장이 열려 있으면 오늘 봉은 끝나지 않은 봉이라 뺀다(시각으로 어림).
    """
    from .. import markets
    from .exits import BASKETS

    data = {}
    for ticker, _name in BASKETS.get(market, ()):
        try:
            bars = bot.prices.candles(markets.price_symbol(ticker))
        except Exception:
            bars = []
        if bars:
            data[ticker] = list(bars)
    data = drop_unfinished(data, market)
    return {t: bars for t, bars in data.items() if bars}


def market_data(bot, market: str, include_leveraged: bool = False, universe: str = "watch",
                keep: set | frozenset = frozenset()) -> dict:
    """{티커: [끝난 날의 Candle]} — 그 시장의 감시 종목 중 봉이 있는 것만.

    레버리지·인버스는 기본으로 뺀다(include_leveraged 로 시험 삼아 넣을 수 있다).
    keep 은 빼더라도 일봉은 주는 종목 — 이미 들고 있거나 주문을 낸 종목이다. 빼 버리면 손절·청산이
    영영 돌지 않고 평가액이 멈춘다. 새로 사지 않게 하는 건 엔진의 exclude 가 맡는다.
    universe="defense" 면 감시 종목 대신 방어형 ETF 바구니를 쓴다.
    """
    if universe == "defense":
        return basket_data(bot, market)
    metrics = bot.cached_metrics()
    data, live = {}, False
    skip = set() if include_leveraged else set(leveraged_tickers(bot, market)) - set(keep)
    for target in bot.cached_targets():
        if target.market != market or target.ticker in skip:
            continue
        m = metrics.get(target.key)
        bars = list(getattr(m, "bars", None) or []) if m else []
        if bars:
            data[target.ticker] = bars
            live = live or bool(getattr(m, "market_open", False))
    if live and data:
        today = max(b.day for bars in data.values() for b in bars)
        data = {t: [b for b in bars if b.day < today] for t, bars in data.items()}
    data = drop_unfinished(data, market)
    return {t: bars for t, bars in data.items() if bars}


def drop_unfinished(data: dict, market: str, moment=None) -> dict:
    """거래소 시각으로 장이 열려 있으면 '오늘(거래소 날짜)' 봉을 뺀다 — 아직 끝나지 않은 봉이다.

    시세 제공처가 장 상태를 안 알려줄 때도 있어서, 시계로 한 번 더 가린다.
    (휴장일에 '열림' 으로 어림해도 그 날 봉이 없으니 빠지는 것이 없다.)
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from .. import markets

    state, _ = markets.state_by_clock(market, moment)
    if state != markets.OPEN or not data:
        return data
    zone = markets.HOURS.get(market, markets.HOURS[markets.US])[2]
    today = (moment or datetime.now(ZoneInfo(zone))).astimezone(ZoneInfo(zone)).date()
    return {t: [b for b in bars if b.day < today] for t, bars in data.items()}


def settled_day(prepared: dict, stale_days: int = 7) -> str:
    """모든 종목의 봉이 도착한 마지막 날.

    한 종목의 봉이 늦게 오면, 그 날을 먼저 처리해버린 뒤에는 다시 돌아가지 않아
    그 종목만 하루를 통째로 건너뛴다. 그래서 다 모인 날까지만 처리한다.
    일주일 넘게 새 봉이 없는 종목(거래정지 등)은 기다리지 않는다.
    """
    lasts = [ds[-1] for (_, _, ds, _) in prepared.values() if ds]
    if not lasts:
        return ""
    newest = date.fromisoformat(max(lasts))
    active = [d for d in lasts if (newest - date.fromisoformat(d)).days <= stale_days]
    return min(active)


def _series_on(prepared: dict, iso: str) -> dict:
    out = {}
    for t, (bars, closes, days, _) in prepared.items():
        i = at_day(days, iso)
        if i is not None:
            out[t] = (bars, closes, days, i)
    return out


def _wire(engine, bot, market: str, today: date, days=(), keep=frozenset()) -> dict:
    """엔진에 종목 바구니·재무 재료·시장 국면을 달고, 쓸 일봉(prepared)을 돌려준다.

    바구니가 아니면 감시 종목(또는 방어형 ETF)의 일봉, 바구니면 일봉 저장소에서 '처리할 날들의 바구니 + 들고 있는 것'.
    시장 국면 필터를 켰으면 지수 일봉을 한 종목만 새로 받는다(가볍다).
    """
    plan = engine.plan
    since = today - timedelta(days=PAPER_HISTORY)
    qd = getattr(bot, "quant_data", None)
    if is_basket(plan.universe):
        if qd is None:
            return {}
        basket = qd.basket(market)
        wanted = set(keep)
        for d in list(days) + [today]:
            wanted |= set(basket.members(d))
        loaded = qd.load(market, since, sorted(wanted))
        engine.book, engine.members, engine.regime = loaded.book, basket.members, loaded.regime
        if getattr(engine.strategy, "needs_growth", False) and loaded.book is not None:
            engine.growth = {t: loaded.book.growth(t) for t in loaded.prepared if loaded.book.growth(t)}
        return loaded.prepared
    prepared = prepare(market_data(bot, market, plan.include_leveraged, plan.universe, keep=keep))
    if plan.market_filter != "off" and qd is not None:
        from .data import BENCH, last_session, unfinished_day

        bench = BENCH[market][0]
        try:
            qd.prices.update([bench], market, today, unfinished=unfinished_day(market),
                             expected=last_session(market))
        except Exception:
            pass                       # 지수를 못 받으면 국면을 모른다 — 필터는 그날 쉰다(엔진이 None 으로 본다)
        engine.regime = qd.regime(market, since)
    return prepared


def start(store, bot, market: str, capital: float, strategy_key: str,
          rules: RiskRules, costs: CostModel, today: date, plan: Plan | None = None) -> str:
    plan = plan or Plan()
    engine = Engine(strat.get(strategy_key), rules, costs, capital, plan=plan)
    prepared = _wire(engine, bot, market, today)
    if not prepared:
        if is_basket(plan.universe):
            return "종목 바구니의 일봉이 아직 없습니다. 위 '자료 받기' 를 먼저 눌러 주세요(처음 한 번은 오래 걸립니다)."
        if plan.universe == "defense":
            return "방어형 ETF 바구니의 일봉을 받지 못했습니다. 인터넷 연결을 확인하고 다시 시도하세요."
        return "모의 계좌를 시작할 일봉이 없습니다. 감시 종목의 지표를 먼저 불러와 주세요."
    last = settled_day(prepared) if is_basket(plan.universe) else max(days[-1] for (_, _, days, _) in prepared.values())
    if not is_basket(plan.universe):
        engine.growth = _growth_for(engine, bot, market)
    engine.etfs = etf_tickers(bot, market, engine.plan.universe)
    deposit = f", 매달 {engine.plan.monthly_deposit:,.0f}" if engine.plan.monthly_deposit else ""
    from .exits import UNIVERSE_NAME, describe

    engine._note(last, f"모의 계좌 시작 — {engine.strategy.name}, 자본 {capital:,.0f}{deposit}, "
                       f"점검 {engine.plan.days_text}, 청산 {describe(engine.plan)}, "
                       f"종목 범위 {UNIVERSE_NAME.get(engine.plan.universe, '')}")
    # 마지막으로 끝난 날의 종가로 신호만 만든다. 체결은 다음 거래일 시가부터.
    engine.on_day(date.fromisoformat(last), _series_on(prepared, last), execute=False)
    from .report import baseline_from, same_settings

    saved = engine.to_dict()
    done = store.backtest(market)
    store.set_paper(market, {"engine": saved, "started": today.isoformat(), "paused": False,
                             "baseline": baseline_from(done) if same_settings(saved, done) else None})
    store.save()
    pending = len(engine.orders)
    return (f"모의 계좌를 시작했습니다({engine.strategy.name}). "
            + (f"다음 거래일 시가에 체결될 주문 {pending}건이 있습니다." if pending
               else f"다음 점검일({engine.plan.days_text}) 장 마감 뒤에 신호를 봅니다."))


_STEP_LOCKS: dict[str, threading.Lock] = {}


def step(store, bot, market: str) -> tuple[int, list[dict]]:
    """끝난 거래일을 이어서 처리한다. (처리한 날 수, 새로 생긴 일지)

    화면 단추와 뒤에서 도는 감시가 같이 부를 수 있다 — 시장마다 한 번에 하나만 돌려, 같은 날을 두 번 처리하거나
    같은 주 성적표를 두 장 만들지 않는다(뒤에 온 쪽은 앞의 결과를 읽고 할 일이 없으면 그냥 끝난다)."""
    with _STEP_LOCKS.setdefault(market, threading.Lock()):
        return _step(store, bot, market)


def _step(store, bot, market: str) -> tuple[int, list[dict]]:
    account = store.paper(market)
    if not account or account.get("paused"):
        return 0, []
    engine = Engine.from_dict(account.get("engine") or {}, market)
    if not engine.last_day:
        return 0, []
    today = date.today()
    pending = [date.fromisoformat(engine.last_day) + timedelta(days=k) for k in range(1, 15)]
    prepared = _wire(engine, bot, market, today, days=[d for d in pending if d <= today], keep=_held(engine))
    if not prepared:
        return 0, []
    engine.exclude = _excluded(bot, market, engine)
    dropped = [o for o in engine.orders if o.side == "buy" and o.ticker in engine.exclude]
    if dropped:
        # 빼기 전에 낸 매수 주문 — 체결하지 않는다
        engine.orders = [o for o in engine.orders if o not in dropped]
        for o in dropped:
            engine._note(engine.last_day, f"매수 주문 취소 {o.ticker} — 레버리지·인버스는 퀀트에서 빼는 종목")
    cutoff = settled_day(prepared)
    days = sorted({d for (_, _, ds, _) in prepared.values() for d in ds if engine.last_day < d <= cutoff})
    if days and not is_basket(engine.plan.universe):   # 재무 파일은 크다 — 처리할 날이 있을 때만 읽는다
        engine.growth = _growth_for(engine, bot, market)
        engine.etfs = etf_tickers(bot, market, engine.plan.universe)
    before = len(engine.events)
    if days:
        _rebase_splits(engine, prepared, lambda t: _recorded_splits(bot, t))
    for iso in days:
        engine.on_day(date.fromisoformat(iso), _series_on(prepared, iso))
    if days:
        account["engine"] = engine.to_dict()
        store.set_paper(market, account)
        make_reports(store, bot, market, engine, account)
        store.save()
    return len(days), engine.events[before:] if days else []


def bench_closes(bot, market: str, since: str) -> dict:
    """{날짜: 지수 종가} — 성적표의 '지수 대비'. 못 받으면 빈 것(지수 칸을 비운다)."""
    qd = getattr(bot, "quant_data", None)
    if qd is None or not since:
        return {}
    from .data import BENCH, last_session, unfinished_day

    bench = BENCH[market][0]
    try:
        qd.prices.update([bench], market, unfinished=unfinished_day(market), expected=last_session(market))
        bars = qd.prices.load([bench], date.fromisoformat(since) - timedelta(days=10)).get(bench)
    except Exception:
        return {}
    return dict(zip(bars.days, bars.closes)) if bars else {}


def make_reports(store, bot, market: str, engine, account: dict) -> list[dict]:
    """다 끝난 주 중 아직 성적표가 없는 주를 만든다. 텔레그램으로 보내는 일은 앱(quant_step)이 한다."""
    from .report import build, complete_weeks

    started = account.get("started")
    have = {r["week"] for r in store.reports(market) if r.get("account") == started}
    todo = [w for w in complete_weeks(engine) if w not in have]
    if not todo:
        return []
    bench = bench_closes(bot, market, engine.curve[0][0] if engine.curve else "")
    made = []
    for week in todo:
        prev = next((r for r in reversed(store.reports(market))
                     if r.get("account") == started and r["week"] < week), None)
        report = build(engine, account, market, week, bench, prev, snapshot=week == todo[-1])
        if report:
            report["sent"] = False
            store.add_report(market, report)
            made.append(report)
    return made


def _recorded_splits(bot, ticker: str) -> list:
    qd = getattr(bot, "quant_data", None)
    try:
        return qd.prices.splits(ticker) if qd is not None else []
    except Exception:
        return []


def _rebase_splits(engine, prepared: dict, recorded) -> None:
    """들고 있거나 주문을 낸 종목이 분할·병합됐으면 주식 수·단가·손절선을 새 기준으로 바꾼다 — 평가액은 그대로.

    야후 일봉은 분할이 생기면 지난 값까지 새 기준으로 고쳐 적는다. 엔진이 기억한 마지막 종가(옛 기준)와 같은 날의
    새 종가를 견주면 비율이 나온다. 맞추지 않으면 10:1 분할이 -90% 손실로, 옛 손절선에 걸려 판 것으로 기록된다.
    분할 기록(recorded(티커) = [(날짜, 비율)])이 마지막 처리일 뒤에 있거나 차이가 아주 클 때만 바꾼다 —
    기록 없이 조금 달라졌으면 자료 수정이라 고친 가격을 그대로 따라간다.
    """
    last = engine.last_day
    for t in dict.fromkeys([*engine.positions, *(o.ticker for o in engine.orders)]):
        old, got = engine.last_close.get(t), prepared.get(t)
        if not old or not got:
            continue
        _, closes, days, _ = got
        i = at_day(days, last)
        if i is None or not closes[i]:
            continue
        ratio = old / closes[i]
        if abs(ratio - 1) <= SPLIT_GAP:
            continue
        after = [d.isoformat() for d, _ in recorded(t) if d.isoformat() > last]
        if not after and max(ratio, 1 / ratio) < 1 + SPLIT_SURE:
            continue
        pos = engine.positions.get(t)
        if pos:
            pos.shares = round(pos.shares * ratio, 6)
            pos.cost /= ratio
            pos.stop = None if pos.stop is None else pos.stop / ratio
            pos.risk /= ratio
            pos.high /= ratio
        for o in engine.orders:
            if o.ticker == t:
                o.shares = round(o.shares * ratio, 6)
                o.stop = None if o.stop is None else o.stop / ratio
        engine.last_close[t] = closes[i]
        engine._note(after[0] if after else last,
                     f"분할·병합 반영 {t} — 주식 수 ×{ratio:.4g}, 단가·손절선 ÷{ratio:.4g} (평가액은 그대로)")


def _held(engine) -> set:
    """들고 있거나 주문을 내 둔 종목."""
    return set(engine.positions) | {o.ticker for o in engine.orders}


def _excluded(bot, market: str, engine) -> set:
    """새로 사지 않을 종목(레버리지·인버스). 들고 있던 것은 일봉을 받아 규칙대로 판다."""
    if engine.plan.include_leveraged or engine.plan.universe == "defense" or is_basket(engine.plan.universe):
        return set()
    return set(leveraged_tickers(bot, market))


def next_check(engine) -> str:
    """다음 점검일(요일 기준 어림 — 휴장일은 모른다). 처리한 날이 없으면 빈 문자열."""
    from datetime import timedelta

    if not engine.last_day or not engine.plan.check_days:
        return ""
    day = date.fromisoformat(engine.last_day)
    for _ in range(14):
        day += timedelta(days=1)
        if day.weekday() in engine.plan.check_days:
            return day.isoformat()
    return ""


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
    if is_basket(engine.plan.universe):
        qd = getattr(bot, "quant_data", None)
        members = set(qd.basket(market).members(date.today())) if qd is not None else set()
        watched = members | set(_held(engine))
    else:
        watched = set(market_data(bot, market, engine.plan.include_leveraged, engine.plan.universe,
                                  keep=_held(engine)))
    equity = engine.equity()
    return {
        "engine": engine, "equity": equity, "started": account.get("started"),
        "paused": bool(account.get("paused")), "universe": sorted(watched),
        "left_out": [] if engine.plan.include_leveraged or engine.plan.universe == "defense"
        or is_basket(engine.plan.universe) else leveraged_tickers(bot, market),
        "return": (equity / (engine.capital + engine.deposited) - 1) if engine.capital else None,
        "drawdown": engine.drawdown(),
        "orphans": [t for t in engine.positions if t not in watched],
    }


# --------------------------------------------------------------------------
# 지금 포지션 — 모의 계좌를 지금 가격으로 평가(보기만, 기록은 그대로)
# --------------------------------------------------------------------------
def live_tickers(store, market: str) -> list[str]:
    """모의 계좌가 들고 있거나 주문을 내 둔 종목 — 감시 목록에 없어도 지금 가격을 받는다."""
    raw = (store.paper(market) or {}).get("engine") or {}
    found = [p.get("ticker") for p in raw.get("positions") or []] + [o.get("ticker") for o in raw.get("orders") or []]
    return [t for t in dict.fromkeys(found) if t]


def _same_basis(q, close: float | None, last_day: str | None) -> bool:
    """지금 가격이 계좌가 아는 마지막 종가와 같은 기준인가.

    분할·병합 당일(계좌는 장이 끝난 뒤에야 새 기준으로 바꾼다)이나 처리하지 않은 날이 쌓였을 때 지금 가격을
    옛 기준 주식 수에 곱하면 없는 손익이 생긴다. 그래서 지금 가격(그 날 것이면 그 값, 다음 날 것이면 직전 종가)이
    계좌가 기억한 마지막 종가와 맞을 때만 쓴다. 못 맞추면 마지막 종가로 평가하고 그렇다고 적는다.
    """
    if q is None or not q.price or not close:
        return False
    if q.day and last_day and q.day < last_day:
        return False                                  # 계좌가 이미 처리한 날보다 오래된 값
    if q.day and q.day == last_day:
        base = q.price
    elif q.change_pct is not None and q.change_pct > -100:
        base = q.price / (1 + q.change_pct / 100)     # 직전 거래일 종가
    else:
        return False
    return abs(close / base - 1) <= SPLIT_GAP


def live_positions(account: dict | None, quotes: dict) -> dict | None:
    """모의 계좌를 지금 가격으로 평가한다 — 보유마다 평가금액·평가손익·하루 손익, 계좌 합계.

    quotes = {티커: Quote}. 지금 가격을 못 받았거나 계좌와 기준이 다르면 마지막으로 처리한 날의 종가로 평가하고
    live=False 로 둔다. 사고파는 판단과 체결 기록은 그대로다(장 마감 뒤 종가로 판단, 다음 장 시가로 기록).
    평가손익은 산 값과 살 때 수수료를 뺀 것 — 팔 때 수수료·세금은 아직 빼지 않았다.
    확정 손익은 거래 내역을 더하지 않고 현금으로 셈한다(현금 + 보유에 치른 돈 − 넣은 돈) — 내역이 잘려도 맞다.
    """
    from .report import EVENT_WORDS

    raw = (account or {}).get("engine") or {}
    if not raw:
        return None
    last, last_day = raw.get("last_close") or {}, raw.get("last_day")
    paused = bool((account or {}).get("paused"))
    rows = []
    for p in raw.get("positions") or []:
        t = p.get("ticker")
        shares, cost, fee = float(p.get("shares") or 0), float(p.get("cost") or 0), float(p.get("fee") or 0)
        q = quotes.get(t)
        live = _same_basis(q, last.get(t), last_day)
        price = q.price if live else (last.get(t) or cost)
        paid, value = shares * cost + fee, shares * price
        change = q.change_pct if live else None
        if live and q.day and q.day == p.get("entry_day"):
            day_pnl = value - paid                    # 그 날 산 것 — 직전 종가가 아니라 치른 돈부터
        elif change is not None:
            day_pnl = shares * (price - price / (1 + change / 100))
        else:
            day_pnl = None
        stop = p.get("stop")
        unprocessed = live and bool(q.day) and (not last_day or q.day > last_day)
        rows.append({"ticker": t, "name": (q.name if q is not None else "") or "", "shares": shares, "cost": cost,
                     "price": price, "live": live, "value": value, "paid": paid, "pnl": value - paid,
                     "pnl_pct": (value - paid) / paid if paid else None, "day_pnl": day_pnl,
                     "day_pct": change / 100 if change is not None else None,
                     "stop": stop, "to_stop": (stop / price - 1) if stop and price else None,
                     "touched": bool(unprocessed and not paused and stop and price <= stop),
                     "since": p.get("entry_day"), "state": q.state_label if live else "",
                     "traded_at": q.traded_at if live else None})
    cash = float(raw.get("cash") or 0)
    equity = cash + sum(r["value"] for r in rows)
    for r in rows:
        r["weight"] = r["value"] / equity if equity else None
    invested = float(raw.get("capital") or 0) + float(raw.get("deposited") or 0)
    days = [r["day_pnl"] for r in rows if r["day_pnl"] is not None]
    orders = []
    for o in raw.get("orders") or []:
        t = o.get("ticker")
        q = quotes.get(t)
        live = _same_basis(q, last.get(t), last_day)
        orders.append({"side": o.get("side"), "ticker": t, "shares": o.get("shares"), "reason": o.get("reason"),
                       "made": o.get("made"), "price": q.price if live else last.get(t), "live": live})
    times = [r["traded_at"] for r in rows if r["traded_at"] is not None]
    return {
        "rows": sorted(rows, key=lambda r: -r["value"]), "cash": cash, "equity": equity, "invested": invested,
        "total": equity - invested, "total_pct": (equity - invested) / invested if invested else None,
        "unrealized": sum(r["pnl"] for r in rows),
        "realized": cash + sum(r["paid"] for r in rows) - invested,
        "day": sum(days) if days else None, "orders": orders, "paused": paused,
        "recent": [e for e in reversed(raw.get("events") or []) if str(e.get("text", "")).startswith(EVENT_WORDS)][:8],
        "as_of": max(times) if times else None, "all_live": all(r["live"] for r in rows),
        "last_day": last_day,
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
        m = metrics.get(target.key) if target else None
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
