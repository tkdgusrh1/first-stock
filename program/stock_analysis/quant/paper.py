"""모의 계좌 · 오늘의 신호 — 받아둔 일봉으로만 계산한다(네트워크 없음).

모의 계좌는 백테스트와 **같은 엔진**을 하루씩 이어서 돌린다. 주문은 실제로 나가지 않고,
다음 날 시가(+체결 차이)에 체결된 것으로 기록한다. 그래서 '백테스트 → 모의' 의 차이는
규칙이 아니라 시기(앞으로 올 시장)에서만 생긴다.

장이 열려 있는 동안의 오늘 봉은 아직 끝나지 않은 봉이라 **쓰지 않는다.**
"""

from __future__ import annotations

import logging
import math
import threading
import time
from bisect import bisect_left, bisect_right
from datetime import date, timedelta

from . import indicators as ind
from . import strategies as strat
from .basket import is_basket
from .costs import CostModel
from .engine import Engine, at_day, prepare
from .sizing import Plan, RiskRules, round_shares

log = logging.getLogger(__name__)

PAPER_HISTORY = 600       # 바구니 모의 계좌가 읽는 일봉 기간(일) — 1년 모멘텀·200일선을 계산할 만큼
SPLIT_GAP = 0.01          # 같은 날 종가가 이만큼 넘게 달라졌으면 그 사이 가격 기준이 바뀌었다(분할·병합, 자료 수정)
SPLIT_SURE = 0.15         # 분할 기록이 없어도 같은 날 종가가 이만큼 다르면 분할·병합으로 본다 — 끝난 날 종가가 이만큼 고쳐지지 않는다
SPLIT_MATCH = 0.02        # 종가로 잰 비율과 분할 기록의 비율이 이만큼 안에서 맞으면 기록의 비율을 그대로 쓴다
SPLIT_WAIT_DAYS = 7       # 분할 뒤 일봉이 정리되기를 기다리는 건 분할일부터 이만큼(달력 일)까지 — 계좌 전체가 멈추지 않게

WAITING: dict = {}        # {시장: 티커} — 분할 뒤 일봉 정리를 기다리느라 처리를 미룬 종목(화면에 보여 준다)
_warned: dict = {}        # {(시장, 티커): 마지막으로 경고한 때} — 5분마다 같은 경고를 되풀이하지 않게


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
    engine = Engine.from_dict(account.get("engine") or {})
    if not engine.last_day:
        return 0, []
    today = date.today()
    pending = [date.fromisoformat(engine.last_day) + timedelta(days=k) for k in range(1, 15)]
    prepared = _wire(engine, bot, market, today, days=[d for d in pending if d <= today], keep=_held(engine))
    if not prepared:
        return 0, []
    engine.exclude = _excluded(bot, market, engine)
    _drop_excluded(engine, engine.last_day)
    cutoff = settled_day(prepared)
    days = sorted({d for (_, _, ds, _) in prepared.values() for d in ds if engine.last_day < d <= cutoff})
    if days and not is_basket(engine.plan.universe):   # 재무 파일은 크다 — 처리할 날이 있을 때만 읽는다
        engine.growth = _growth_for(engine, bot, market)
        engine.etfs = etf_tickers(bot, market, engine.plan.universe)
    before = len(engine.events)
    if days:
        def recorded(t):
            return _recorded_splits(bot, market, t)

        mixed = getattr(bot, "prices_unsettled", None)
        waiting = (_split_wait(engine, prepared, recorded, date.fromisoformat(days[-1]),
                               None if mixed is None or is_basket(engine.plan.universe) else lambda t: mixed(market, t))
                   or _rebase_splits(engine, prepared, recorded, days[0]))
        if waiting:
            WAITING[market] = waiting
            if time.monotonic() - _warned.get((market, waiting), -1e9) > 3600:
                _warned[(market, waiting)] = time.monotonic()
                log.warning("모의 계좌(%s): %s 분할 뒤 일봉·분할 기록이 정리되지 않아 처리를 미룹니다.", market, waiting)
            return 0, []
        WAITING.pop(market, None)
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


def _drop_excluded(engine, day: str) -> None:
    """빼기 전에 낸 매수 주문(레버리지·인버스) — 체결하지 않는다."""
    dropped = [o for o in engine.orders if o.side == "buy" and o.ticker in engine.exclude]
    if dropped:
        engine.orders = [o for o in engine.orders if o not in dropped]
        for o in dropped:
            engine._note(day, f"매수 주문 취소 {o.ticker} — 레버리지·인버스는 퀀트에서 빼는 종목")


def _recorded_splits(bot, market: str, ticker: str) -> list | None:
    """분할 기록 [(날짜, 비율)]. 읽다가 실패하면 None — '기록 없음' 과 구분한다(작은 분할을 놓치지 않게)."""
    find = getattr(bot, "splits_for", None)
    try:
        return find(market, ticker) if find else []
    except Exception as exc:
        log.debug("분할 기록 조회 실패 %s: %s", ticker, exc)
        return None


def _nice_ratio(ratio: float) -> float:
    """종가끼리 나눈 비율을 분할 비율로 — 3:1 이 2.9999 로 나와 300주가 299주 + 끝수가 되지 않게. 맞는 게 없으면 그대로."""
    for den in range(1, 11):
        num = round(ratio * den)
        if num >= 1 and abs(num / den / ratio - 1) <= 0.005:
            return num / den
    return ratio


def _split_wait(engine, prepared: dict, recorded, ref: date, mixed=None) -> str:
    """분할 뒤 일봉이 아직 정리되지 않은 보유·주문 종목(없으면 "") — 그 일봉으로 처리하면 가짜 손절·가짜 신호가 난다.

    - 감시 종목: 분할 뒤 긴 과거를 다시 받지 못해 옛 기준 과거와 새 기준 최근 봉이 섞여 있다(mixed(티커)).
    - 그 종목이 마지막으로 거래된 날(last_seen) 뒤에 분할 기록이 있는데, 그 날 종가는 옛 값 그대로이고(과거가 안 고쳐짐)
      분할일 첫 봉이 그 비율만큼 뛰었다 — 야후가 과거를 늦게 고치는 중.
    분할일부터 SPLIT_WAIT_DAYS 가 지나면(ref = 처리할 마지막 날) 더 기다리지 않는다 — 계좌 전체가 멈추지 않게.
    """
    for t in dict.fromkeys([*engine.positions, *(o.ticker for o in engine.orders)]):
        got = prepared.get(t)
        if not got:
            continue
        if mixed is not None and mixed(t):
            return t
        _, closes, days, _ = got
        old, seen = engine.last_close.get(t), engine.last_seen.get(t) or engine.last_day
        k = at_day(days, seen)
        if not old or k is None or not closes[k] or abs(old / closes[k] - 1) > SPLIT_GAP:
            continue                                  # 과거가 이미 새 기준이거나 견줄 수 없다
        for d, r in recorded(t) or []:
            j = bisect_left(days, d.isoformat())
            if d.isoformat() <= seen or (ref - d).days > SPLIT_WAIT_DAYS or not 0 < j < len(days):
                continue
            if closes[j - 1] and abs(closes[j] / closes[j - 1] * r - 1) <= SPLIT_MATCH:
                return t
    return ""


def _rebase_splits(engine, prepared: dict, recorded, first_day: str) -> str:
    """들고 있거나 주문을 낸 종목이 분할·병합됐으면 주식 수·단가·손절선을 새 기준으로 바꾼다 — 평가액은 그대로.

    야후 일봉은 분할이 생기면 지난 값까지 새 기준으로 고쳐 적는다. 엔진이 기억한 그 종목의 마지막 종가(옛 기준,
    last_seen 날)와 새 일봉의 **같은 날** 종가를 견주면 비율이 나온다. 맞추지 않으면 10:1 분할이 -90% 손실로,
    옛 손절선에 걸려 판 것으로 기록된다. 그 날 뒤로 분할 기록이 있고 비율이 맞으면 기록의 비율을 쓰고, 기록을 못 받았으면
    같은 날 종가가 15% 넘게 다를 때만 분할로 본다 — 그보다 작은 차이는 자료 수정이라 고친 가격을 그대로 따라간다.
    예전 계좌(그 날을 적어 두지 않은 때)는 처리일 이전 마지막 봉으로 어림하고, 기록과 맞을 때만 바꾼다.
    기준이 달라졌는데 분할 기록을 읽지 못했으면 그 종목을 돌려준다 — 이번엔 처리하지 않고 다음에 다시 본다.
    """
    for t in dict.fromkeys([*engine.positions, *(o.ticker for o in engine.orders)]):
        old, got = engine.last_close.get(t), prepared.get(t)
        if not old or not got:
            continue
        _, closes, days, _ = got
        seen = engine.last_seen.get(t)
        k = at_day(days, seen) if seen else bisect_right(days, engine.last_day) - 1
        if k is None or k < 0 or not closes[k]:
            continue                                  # 그 날 봉이 새 일봉에 없다 — 견줄 수 없다
        ratio = old / closes[k]
        if abs(ratio - 1) <= SPLIT_GAP:
            continue
        recs = recorded(t)
        if recs is None:
            return t
        recs = [(d, r) for d, r in recs if d.isoformat() > (seen or days[k])]
        expected = math.prod(r for _, r in recs) if recs else None
        if expected is not None and abs(ratio / expected - 1) <= SPLIT_MATCH:
            ratio, when = expected, f" ({recs[0][0].isoformat()})"
        elif expected is None and seen and max(ratio, 1 / ratio) >= 1 + SPLIT_SURE:
            ratio, when = _nice_ratio(ratio), " (분할 기록은 못 받음 — 같은 날 종가로 잼)"
        else:
            continue
        _apply_split(engine, t, ratio, old, first_day, when)
    return ""


def _apply_split(engine, t: str, ratio: float, old_close: float, day: str, when: str) -> None:
    """주식 수 × 비율, 단가·손절선 ÷ 비율. 온주 계좌에서 생긴 끝수는 증권사처럼 현금으로(마지막 종가를 새 기준으로 고친 값)."""
    fractional = engine.plan.fractional
    price = old_close / ratio
    text = f"분할·병합 반영 {t}{when} — 1주가 {ratio:.4g}주로, 단가·손절선 ÷{ratio:.4g}(평가액은 그대로)"
    pos = engine.positions.get(t)
    if pos:
        held = pos.shares * ratio
        whole = round_shares(held, fractional)
        part = held - whole
        if part > 1e-6:
            fee_part = pos.fee * part / held
            engine.cash += part * price
            pos.fee -= fee_part
            text += f", 끝수 {part:.4g}주는 현금 {part * price:,.2f}"
        if whole <= 0:
            del engine.positions[t]
        else:
            pos.shares, pos.cost = whole, pos.cost / ratio
            pos.stop = None if pos.stop is None else pos.stop / ratio
            pos.risk /= ratio
            pos.high /= ratio
    for o in list(engine.orders):
        if o.ticker != t:
            continue
        o.shares = round_shares(o.shares * ratio, fractional)
        o.stop = None if o.stop is None else o.stop / ratio
        if o.side == "sell":
            o.shares = min(o.shares, engine.positions[t].shares) if t in engine.positions else 0
        if o.shares <= 0:
            engine.orders.remove(o)
            text += f", {'매수' if o.side == 'buy' else '매도'} 주문은 0주가 되어 취소"
    engine.last_close[t] = price
    engine._note(day, text)


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
    engine = Engine.from_dict(account.get("engine") or {})
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


def live_view(store, bot, market: str) -> dict | None:
    """화면용 — 시세 루프가 받아 둔 지금 가격·최근 5일 봉으로 live_positions 를 부른다(새로 묻지 않는다)."""
    view = live_positions(store.paper(market), bot.paper_quotes(market), market, bot.paper_recent(market),
                          exclude_of=lambda eng: _excluded(bot, market, eng))
    if view is not None:
        view["waiting"] = WAITING.get(market, "")
    return view


def _same_basis(eng, t: str, q, recent: dict) -> bool:
    """지금 가격이 계좌가 기억한 그 종목 종가와 같은 기준인가 — 최근 5일 봉에서 **그 날** 종가를 찾아 견준다.

    분할·병합 당일(계좌는 장이 끝난 뒤에야 새 기준으로 바꾼다)이나 처리하지 않은 날이 쌓였을 때 지금 가격을 옛 기준
    주식 수에 곱하면 없는 손익이 생긴다. 5일 봉에 그 날이 없거나 그 뒤로 분할 기록이 있으면 쓰지 않는다.
    """
    close = eng.last_close.get(t)
    seen = eng.last_seen.get(t) or eng.last_day
    if q is None or not q.price or not close or not seen or (q.day and q.day < seen):
        return False
    bars, split_days = recent.get(t) or ([], [])
    if any(seen < d for d in split_days):
        return False
    closes = {b.day.isoformat(): b.close for b in bars}
    base = closes.get(seen) if closes else (q.price if q.day == seen else None)
    return base is not None and abs(close / base - 1) <= SPLIT_GAP


def _today_bars(eng, quotes: dict, recent: dict, live: set) -> tuple[str | None, dict]:
    """마지막 처리일 바로 다음 거래일의 봉이 생겼으면 (그 날, 엔진에 줄 그 날 봉) — 장중이면 진행 중인 봉.

    장전(정규장 거래가 아직 없음)이거나 처리하지 않은 날이 둘 넘게 쌓였으면 (None, {}).
    """
    found = {}
    for t in live:
        bars, q = (recent.get(t) or ([], []))[0], quotes.get(t)
        if len(bars) < 2 or q is None:
            continue
        day = bars[-1].day.isoformat()
        if day > eng.last_day and bars[-2].day.isoformat() == eng.last_day and q.day == day:
            found[t] = (day, bars[-1])
    if not found:
        return None, {}
    today = max(d for d, _ in found.values())
    return today, {t: ([bar], [bar.close], [d], 0) for t, (d, bar) in found.items() if d == today}


def live_positions(account: dict | None, quotes: dict, market: str, recent: dict | None = None,
                   exclude_of=None) -> dict | None:
    """모의 계좌를 지금 가격으로 평가한다 — 보유마다 평가금액·평가손익, 계좌 합계·하루 손익.

    quotes = {티커: Quote}, recent = {티커: (최근 5일 봉, [분할 날짜])} — 시세 루프가 받아 둔 것.
    마지막 처리일 다음 거래일의 장이 열렸으면, 엔진 규칙(입금 → 시가 체결 → 장중 손절·절반 익절)을 계좌 **사본**에
    그 날 봉으로 돌려 지금 상태를 보여 준다. 장이 끝난 뒤 처리할 때 같은 규칙·같은 봉으로 기록된다
    (장 마감 판단·새 주문은 그때). 지금 가격은 계좌와 기준이 맞을 때만 쓰고, 아니면 기록한 종가로 평가하고 live=False.
    확정 손익은 현금으로 셈한다(현금 + 보유에 치른 돈 − 넣은 돈) — 거래 내역 1000개 상한과 끝수 현금까지 맞다.
    """
    from .report import EVENT_WORDS

    raw = (account or {}).get("engine") or {}
    if not raw:
        return None
    eng = Engine.from_dict(raw)
    recent = recent or {}
    paused = bool((account or {}).get("paused"))
    held = dict.fromkeys([*eng.positions, *(o.ticker for o in eng.orders)])
    live = {t for t in held if _same_basis(eng, t, quotes.get(t), recent)}
    recorded = [e for e in reversed(eng.events) if str(e.get("text", "")).startswith(EVENT_WORDS)][:8]
    today, series = (None, {}) if paused or not eng.last_day else _today_bars(eng, quotes, recent, live)
    # 주문 종목 중 오늘 봉이 없는 게 있으면 체결 순서(먼저 판 돈으로 사기 등)를 맞출 수 없다 — 미리 반영하지 않는다
    held_back = bool(series) and any(o.ticker not in series for o in eng.orders)
    if held_back:
        today, series = None, {}
    eng.events = []                                   # 미리보기에서 생긴 일만 모은다(일지는 400개에서 잘린다)
    if series:
        eng.exclude = set(exclude_of(eng)) if exclude_of else set()
        _drop_excluded(eng, today)
        eng._deposit(today)
        eng._fill(today, series)
        eng._stops(today, series)
    rows = []
    for t, p in eng.positions.items():
        q, ok = quotes.get(t), t in live
        price = q.price if ok else (eng.last_close.get(t) or p.cost)
        paid, value = p.shares * p.cost + p.fee, p.shares * price
        rows.append({"ticker": t, "name": (q.name if q is not None else "") or "", "shares": p.shares, "cost": p.cost,
                     "price": price, "live": ok, "fallback": "" if ok else ("종가" if eng.last_close.get(t) else "산 값"),
                     "value": value, "paid": paid, "pnl": value - paid, "pnl_pct": (value - paid) / paid if paid else None,
                     "day_pct": q.change_pct / 100 if ok and q.change_pct is not None else None,
                     "stop": p.stop, "to_stop": (p.stop / price - 1) if p.stop and price else None,
                     "since": p.entry_day, "today": p.entry_day == today,
                     "state": q.state_label if ok else "", "traded_at": q.traded_at if ok else None})
    equity = eng.cash + sum(r["value"] for r in rows)
    for r in rows:
        r["weight"] = r["value"] / equity if equity else None
    invested = eng.capital + eng.deposited
    later = any(quotes[t].day and eng.last_day and quotes[t].day > eng.last_day for t in live)
    curve, day, day_from = eng.curve, None, None
    if today and curve and curve[-1][0] == eng.last_day:              # 오늘 장 — 마지막 기록일 종가 대비
        day, day_from = equity - curve[-1][1] - eng.flows.get(today, 0.0), eng.last_day
    elif not today and not later and len(curve) >= 2 and curve[-1][0] == eng.last_day:   # 마지막 기록일 하루
        day, day_from = equity - curve[-2][1] - eng.flows.get(eng.last_day, 0.0), curve[-2][0]
    orders = [{"side": o.side, "ticker": o.ticker, "shares": o.shares, "reason": o.reason, "made": o.made,
               "price": quotes[o.ticker].price if o.ticker in live else eng.last_close.get(o.ticker),
               "live": o.ticker in live} for o in eng.orders]
    times = sorted(r["traded_at"] for r in rows if r["traded_at"] is not None)
    return {
        "rows": sorted(rows, key=lambda r: -r["value"]), "cash": eng.cash, "equity": equity, "invested": invested,
        "total": equity - invested, "total_pct": (equity - invested) / invested if invested else None,
        "unrealized": sum(r["pnl"] for r in rows), "realized": eng.cash + sum(r["paid"] for r in rows) - invested,
        "day": day, "day_from": day_from, "today": today, "today_events": eng.events, "orders": orders,
        "paused": paused, "held_back": held_back, "behind": bool(later and not today and not paused and not held_back),
        "recent": recorded,
        "times": (times[0], times[-1]) if times else None, "all_live": all(r["live"] for r in rows),
        "last_day": eng.last_day,
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
