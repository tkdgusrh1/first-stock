"""하루씩 넘기며 사고파는 엔진. 백테스트와 모의 계좌가 이 하나를 같이 쓴다.

하루(day)에 일어나는 순서 — 실제 장과 같은 순서다:

1. **시가 체결**: 어제 장 끝나고 낸 주문을 오늘 시가에 체결한다(팔기 먼저, 남은 현금으로 사기).
   시가가 손절선보다 이미 낮게 열리면 사지 않는다.
2. **장중 손절**: 오늘 저가가 손절선을 건드리면 손절선(갭으로 더 낮게 열렸으면 시가)에 판다.
3. **종가 평가**: 계좌 가치를 매기고, 하루 손실·고점 대비 낙폭 규칙을 확인한다.
4. **신호**: 오늘 종가까지만 보고 내일 시가에 낼 주문을 만든다.

미래를 보지 않는 것이 이 순서의 전부다. 4번에서 만든 주문은 1번(다음 날)에서만 체결된다.
"""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field
from datetime import date

from . import indicators as ind
from .costs import CostModel
from .sizing import Plan, RiskRules, round_shares, shares_to_buy
from .strategies import Strategy

MAX_EVENTS = 400
MAX_TRADES = 1000


@dataclass
class Position:
    ticker: str
    shares: float          # 온주면 정수, 소수점 매수면 0.01주 단위
    cost: float            # 1주당 실제로 치른 가격(체결 차이 포함, 수수료 제외)
    entry_day: str
    stop: float | None
    fee: float = 0.0       # 살 때 낸 수수료
    risk: float = 0.0      # 1R = 산 값 − 처음 손절선 (절반 익절·본전 손절의 잣대)
    high: float = 0.0      # 산 뒤 가장 높은 종가 (추적 손절)
    half: bool = False     # 절반 익절을 이미 했나


@dataclass
class Order:
    ticker: str
    side: str              # "buy" | "sell"
    shares: float          # 온주면 정수, 소수점 매수면 0.01주 단위
    reason: str
    made: str              # 주문을 만든 날(그 날 종가 기준)
    stop: float | None = None


@dataclass
class Trade:
    ticker: str
    entry_day: str
    exit_day: str
    shares: float          # 온주면 정수, 소수점 매수면 0.01주 단위
    entry_price: float
    exit_price: float
    pnl: float             # 수수료·세금까지 뺀 손익
    pnl_pct: float
    reason: str


@dataclass
class Engine:
    strategy: Strategy
    rules: RiskRules
    costs: CostModel
    capital: float
    plan: Plan = field(default_factory=Plan)
    cash: float = 0.0
    positions: dict = field(default_factory=dict)
    orders: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    curve: list = field(default_factory=list)          # [(날짜, 계좌 가치)]
    events: list = field(default_factory=list)         # 모의 계좌 일지 [{day, text}]
    last_close: dict = field(default_factory=dict)
    peak: float = 0.0
    prev_equity: float = 0.0
    block_next: bool = False
    halted_on: str | None = None
    skipped: int = 0            # 1주도 못 사서 건너뛴 신호
    chased: int = 0             # 그 날 급등해서 건너뛴 신호(추격 매수 금지)
    blocked: int = 0            # 하루 손실·낙폭 규칙 때문에 막힌 신호
    fees_paid: float = 0.0
    bought: float = 0.0         # 산 금액 합계(회전율 계산)
    invested_days: int = 0
    last_day: str | None = None
    deposited: float = 0.0      # 시작 자본 뒤로 넣은 돈 합계
    flows: dict = field(default_factory=dict)          # {날짜: 그날 넣은 돈} — 수익률에서 빼고 잰다
    rebalanced_week: str | None = None
    growth: dict = field(default_factory=dict)         # {티커: [(알게 된 날, 매출 성장률)]} — 저장하지 않고 매번 만든다
    etfs: set = field(default_factory=set)             # ETF — 매출이 없어 성장 전략에서는 자격만 준다(저장 안 함)

    def __post_init__(self):
        if not self.cash and not self.positions and not self.curve:
            self.cash = self.capital
        if not self.peak:
            self.peak = self.capital
        if not self.prev_equity:
            self.prev_equity = self.capital

    # ------------------------------------------------------------------
    def equity(self) -> float:
        return self.cash + sum(p.shares * self.last_close.get(t, p.cost) for t, p in self.positions.items())

    def drawdown(self) -> float:
        eq = self.equity()
        return 0.0 if self.peak <= 0 else max(0.0, 1 - eq / self.peak)

    def _note(self, day: str, text: str) -> None:
        self.events.append({"day": day, "text": text})
        del self.events[:-MAX_EVENTS]

    def _close_position(self, day: str, pos: Position, raw_price: float, reason: str) -> None:
        self._sell(day, pos, pos.shares, raw_price, reason)

    def _sell(self, day: str, pos: Position, shares: float, raw_price: float, reason: str) -> None:
        """shares 주를 판다. 다 팔면 보유에서 지운다. 살 때 낸 수수료는 판 만큼 나눠 붙인다."""
        shares = min(shares, pos.shares)
        price = self.costs.sell_price(raw_price)
        proceeds = price * shares
        fee = self.costs.sell_fee(proceeds)
        self.cash += proceeds - fee
        self.fees_paid += fee
        buy_fee = pos.fee * shares / pos.shares if pos.shares else 0.0
        paid = pos.cost * shares + buy_fee
        pnl = proceeds - fee - paid
        self.trades.append(Trade(pos.ticker, pos.entry_day, day, shares, round(pos.cost, 6),
                                 round(price, 6), round(pnl, 6), round(pnl / paid, 6) if paid else 0.0, reason))
        del self.trades[:-MAX_TRADES]
        left = round(pos.shares - shares, 6)
        if left <= 0:
            del self.positions[pos.ticker]
        else:
            pos.shares, pos.fee = left, pos.fee - buy_fee
        self._note(day, f"매도 {pos.ticker} {fmt_shares(shares)}주 @ {price:,.2f} — {reason} (손익 {pnl:+,.0f})")

    # ------------------------------------------------------------------
    def on_day(self, day: date, series: dict, execute: bool = True) -> None:
        """series = {티커: (봉 목록, 종가 목록, 날짜 문자열 목록, 오늘 위치 i)} — 오늘 봉이 있는 종목만."""
        iso = day.isoformat()
        # 매달 넣는 돈 — 새 달의 첫 거래일 아침에 들어온다고 본다. 수익이 아니라 '넣은 돈' 이다.
        if (self.plan.monthly_deposit and self.last_day and execute
                and iso[:7] != self.last_day[:7]):
            amount = self.plan.monthly_deposit
            self.cash += amount
            self.deposited += amount
            self.flows[iso] = self.flows.get(iso, 0.0) + amount
            self.prev_equity += amount
            self.peak += amount
            self._note(iso, f"입금 {amount:,.0f} (매달 넣는 돈)")
        if execute:
            self._fill(iso, series)
            self._stops(iso, series)
        for t, (bars, closes, days, i) in series.items():
            self.last_close[t] = closes[i]
        if execute:
            self._raise_stops(iso, series)
        equity = self.equity()
        self.curve.append((iso, round(equity, 6)))
        if self.positions:
            self.invested_days += 1

        # --- 계좌 규칙 ---------------------------------------------------
        r = self.rules
        change = equity / self.prev_equity - 1 if self.prev_equity else 0.0
        self.block_next = bool(r.daily_loss_stop) and change <= -r.daily_loss_stop
        if self.block_next:
            self._note(iso, f"하루 손실 {change:.1%} — 규칙대로 다음 날은 새로 사지 않습니다")
        self.peak = max(self.peak, equity)
        dd = 1 - equity / self.peak if self.peak else 0.0
        multiplier = 0.5 if r.dd_half and dd >= r.dd_half else 1.0
        if r.dd_stop and dd >= r.dd_stop and not self.halted_on:
            self.halted_on = iso
            self._note(iso, f"고점 대비 -{dd:.0%} — 규칙대로 새 매수를 멈춥니다. 규칙을 다시 검토하세요")

        prev = date.fromisoformat(self.last_day) if self.last_day else None
        if execute:
            self._emergency(iso, series)               # 점검일이 아니어도 크게 빠진 종목은 판다
        if day.weekday() in self.plan.check_days:      # 점검 요일에만 새로 사고판다 (손절은 매일)
            self._signals(iso, day, prev, series, equity, multiplier)
        self.prev_equity = equity
        self.last_day = iso

    def _fill(self, iso: str, series: dict) -> None:
        keep = []
        for order in sorted(self.orders, key=lambda o: o.side != "sell"):     # 팔기 먼저
            if order.ticker not in series:
                keep.append(order)            # 오늘 거래가 없는 종목(휴장·정지) — 다음 날로
                continue
            bars, closes, days, i = series[order.ticker]
            bar = bars[i]
            if order.side == "sell":
                pos = self.positions.get(order.ticker)
                if pos:
                    self._close_position(iso, pos, bar.open, order.reason)
                continue
            if order.ticker in self.positions:
                continue
            if order.stop is not None and bar.open <= order.stop:
                self._note(iso, f"매수 취소 {order.ticker} — 시가가 손절선 아래에서 열림")
                continue
            price = self.costs.buy_price(bar.open)
            affordable = round_shares(self.cash / (price * (1 + self.costs.commission)), self.plan.fractional)
            shares = min(order.shares, affordable)
            if shares <= 0:
                self._note(iso, f"매수 취소 {order.ticker} — 현금 부족")
                continue
            amount = price * shares
            fee = self.costs.buy_fee(amount)
            self.cash -= amount + fee
            self.fees_paid += fee
            self.bought += amount
            stop = self._capped(order.stop, price)
            risk = price - stop if stop is not None and price > stop else 0.0
            self.positions[order.ticker] = Position(order.ticker, shares, price, iso, stop, fee,
                                                    risk=risk, high=price)
            stop_text = f", 손절 {stop:,.2f}" if stop is not None else ""
            self._note(iso, f"매수 {order.ticker} {fmt_shares(shares)}주 @ {price:,.2f}{stop_text} — {order.reason}")
        self.orders = keep

    def _emergency(self, iso: str, series: dict) -> None:
        """매일 감시: 하루에 plan.emergency 이상 빠진 보유 종목은 다음 시가에 판다.

        주 2회 점검이어도 그 사이 급락을 그냥 두지 않는다. 최소 보유일과 상관없이 작동한다.
        손절선(장중)과는 따로다 — 손절선은 '산 값 기준', 이건 '하루 낙폭 기준' 이다.
        """
        limit = self.plan.emergency
        if not limit:
            return
        selling = {o.ticker for o in self.orders if o.side == "sell"}
        for t, pos in list(self.positions.items()):
            if t in selling or t not in series:
                continue
            bars, closes, days, i = series[t]
            if i > 0 and closes[i - 1] and closes[i] / closes[i - 1] - 1 <= -limit:
                drop = closes[i] / closes[i - 1] - 1
                self.orders = [o for o in self.orders if o.ticker != t]
                self.orders.append(Order(t, "sell", pos.shares, f"긴급 매도 — 하루 {drop:.1%}", iso))
                self._note(iso, f"긴급: {t} 하루 {drop:.1%} — 다음 시가에 팝니다(점검일과 상관없이)")

    def _capped(self, stop: float | None, price: float) -> float | None:
        """손실 상한이 켜져 있으면 손절선을 산 값의 (1 − 상한) 보다 멀지 않게."""
        cap = self.plan.loss_cap
        if not cap:
            return stop
        floor = price * (1 - cap)
        return floor if stop is None else max(stop, floor)

    def _stops(self, iso: str, series: dict) -> None:
        """장중: 손절선(먼저 — 일봉으로는 순서를 모르니 나쁜 쪽으로) → 절반 익절."""
        plan = self.plan
        for t, pos in list(self.positions.items()):
            if t not in series:
                continue
            bars, closes, days, i = series[t]
            bar = bars[i]
            if pos.stop is not None and bar.low <= pos.stop:
                reason = "손절선 도달" if pos.stop < pos.cost else "이익 보호선 도달"
                self._close_position(iso, pos, min(bar.open, pos.stop), reason)
                self.orders = [o for o in self.orders if o.ticker != t]
                continue
            if plan.take_half_r and not pos.half and pos.risk > 0:
                target = pos.cost + plan.take_half_r * pos.risk
                if bar.high >= target:
                    pos.half = True
                    part = round_shares(pos.shares / 2, plan.fractional)
                    if 0 < part < pos.shares:
                        self._sell(iso, pos, part, max(bar.open, target), f"절반 익절 (+{plan.take_half_r:g}R)")
                    even = self.costs.breakeven(pos.cost)
                    if pos.stop is None or pos.stop < even:
                        pos.stop = even
                        self._note(iso, f"{t} 손절선을 본전 {even:,.2f} 로 올림 — 남은 몫은 잃지 않게")

    def _raise_stops(self, iso: str, series: dict) -> None:
        """종가 뒤: 본전 손절·추적 손절로 손절선을 올린다(내리지 않는다). 내일부터 적용."""
        plan = self.plan
        if not (plan.breakeven_r or plan.trail_atr):
            return
        for t, pos in self.positions.items():
            if t not in series:
                continue
            bars, closes, days, i = series[t]
            close = closes[i]
            pos.high = max(pos.high or pos.cost, close)
            new = pos.stop
            if plan.breakeven_r and pos.risk > 0 and close >= pos.cost + plan.breakeven_r * pos.risk:
                even = self.costs.breakeven(pos.cost)
                if new is None or new < even:
                    new = even
                    self._note(iso, f"{t} +{plan.breakeven_r:g}R 도달 — 손절선을 본전 {even:,.2f} 로 올림")
            if plan.trail_atr:
                a = ind.atr(bars, i, 20)
                if a:
                    trail = pos.high - plan.trail_atr * a
                    if new is None or trail > new:
                        new = trail
            pos.stop = new

    def _signals(self, iso: str, day: date, prev: date | None, series: dict,
                 equity: float, multiplier: float) -> None:
        s = self.strategy
        selling = {o.ticker for o in self.orders if o.side == "sell"}
        pending_buy = {o.ticker for o in self.orders if o.side == "buy"}

        def held_days(t: str, days: list, i: int) -> int:
            return i - bisect.bisect_left(days, self.positions[t].entry_day)

        def g(t: str) -> dict:
            """성장 전략이면 '그 날 알 수 있었던' 성장률을 같이 넘긴다."""
            if not getattr(s, "needs_growth", False):
                return {}
            from .fundamentals import growth_at

            if t in self.etfs:            # ETF 는 매출이 없다 — 성장 자격은 주고 순위는 모멘텀으로
                return {"growth": getattr(s, "MIN_GROWTH", 0.0)}
            return {"growth": growth_at(self.growth.get(t, []), day)}

        def too_young(t: str) -> bool:
            """사자마자 다시 팔지 않는다 — 비용만 나간다. 손절은 이 규칙과 상관없이 매일 작동한다."""
            if t not in series or self.plan.min_hold <= 0:
                return False
            bars, closes, days, i = series[t]
            return held_days(t, days, i) < self.plan.min_hold

        candidates: list[tuple[float, str]] = []
        if s.kind == "rotation":
            week = "%d-%02d" % day.isocalendar()[:2]
            if week == self.rebalanced_week:
                # 같은 주의 두 번째 점검: 순위는 그대로 두고, 200일선 아래로 내려간 것만 판다
                for t in list(self.positions):
                    if t in selling or t not in series or too_young(t):
                        continue
                    bars, closes, days, i = series[t]
                    if not s.keep(bars, closes, i, **g(t)):
                        self.orders.append(Order(t, "sell", self.positions[t].shares, _why_out(s), iso))
                        selling.add(t)
                return
            self.rebalanced_week = week
            ranked = []
            for t, (bars, closes, days, i) in series.items():
                if i + 1 < s.warmup:
                    continue
                score = s.score(bars, closes, i, **g(t))
                if score is not None:
                    ranked.append((score, t))
            ranked.sort(reverse=True)
            n = self.rules.max_positions
            keep_set = {t for _, t in ranked[:2 * n]}
            for t in list(self.positions):
                if t in selling or t not in series or too_young(t):
                    continue
                bars, closes, days, i = series[t]
                if not s.keep(bars, closes, i, **g(t)):
                    self.orders.append(Order(t, "sell", self.positions[t].shares, _why_out(s), iso))
                    selling.add(t)
                elif t not in keep_set:
                    self.orders.append(Order(t, "sell", self.positions[t].shares, f"순위 {2 * n}위 밖으로", iso))
                    selling.add(t)
            candidates = [(score, t) for score, t in ranked[:n]
                          if t not in self.positions and t not in pending_buy]
        else:
            for t, pos in list(self.positions.items()):
                if t in selling or t not in series or too_young(t):
                    continue
                bars, closes, days, i = series[t]
                reason = s.exit(bars, closes, i, held_days(t, days, i))
                if reason:
                    self.orders.append(Order(t, "sell", pos.shares, reason, iso))
                    selling.add(t)
            for t, (bars, closes, days, i) in series.items():
                if t in self.positions or t in pending_buy or i + 1 < s.warmup:
                    continue
                if s.entry(bars, closes, i):
                    candidates.append((s.strength(bars, closes, i), t))
            candidates.sort(reverse=True)

        if not candidates:
            return
        if self.halted_on or self.block_next:
            self.blocked += len(candidates)
            return
        slots = self.rules.max_positions - (len(self.positions) - len(selling)) - len(pending_buy)
        reserved = sum(o.shares * self.last_close.get(o.ticker, 0) for o in self.orders if o.side == "buy")
        freed = sum(self.positions[t].shares * self.last_close.get(t, 0) for t in selling if t in self.positions)
        cash = self.cash + freed - reserved
        from .evidence import CHASE_LIMIT

        for _, t in candidates:
            if slots <= 0:
                break
            bars, closes, days, i = series[t]
            if i > 0 and closes[i - 1] and closes[i] / closes[i - 1] - 1 > CHASE_LIMIT:
                self.chased += 1              # 관심이 몰린 급등일 — 떼 매수 뒤에는 평균적으로 밀렸다
                continue
            price = closes[i]
            stop = self._capped(s.stop(bars, closes, i), price)
            vol = ind.volatility(closes, i, 20)
            unit = self.costs.buy_price(price) * (1 + self.costs.commission)
            shares = shares_to_buy(self.rules, equity, cash, price, stop, vol, multiplier, unit,
                                   self.plan.fractional)
            if shares <= 0:
                self.skipped += 1
                continue
            label = "순위 상위" if s.kind == "rotation" else "신호"
            self.orders.append(Order(t, "buy", shares, f"{s.name} {label}", iso, stop))
            cash -= shares * unit
            slots -= 1

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy.key, "rules": self.rules.to_dict(), "costs": self.costs.to_dict(),
            "plan": self.plan.to_dict(), "deposited": self.deposited, "flows": self.flows,
            "rebalanced_week": self.rebalanced_week,
            "capital": self.capital, "cash": self.cash,
            "positions": [asdict(p) for p in self.positions.values()],
            "orders": [asdict(o) for o in self.orders],
            "trades": [asdict(t) for t in self.trades],
            "curve": [list(c) for c in self.curve[-3000:]],
            "events": self.events, "last_close": self.last_close, "peak": self.peak,
            "prev_equity": self.prev_equity, "block_next": self.block_next, "halted_on": self.halted_on,
            "skipped": self.skipped, "blocked": self.blocked, "chased": self.chased, "fees_paid": self.fees_paid,
            "bought": self.bought, "invested_days": self.invested_days, "last_day": self.last_day,
        }

    @classmethod
    def from_dict(cls, raw: dict, market: str) -> "Engine":
        from . import strategies

        eng = cls(strategies.get(raw.get("strategy", "")), RiskRules.from_dict(raw.get("rules")),
                  CostModel.from_dict(raw.get("costs"), market), float(raw.get("capital") or 0),
                  plan=Plan.from_dict(raw.get("plan")), cash=float(raw.get("cash") or 0))
        if "cash" in raw:               # 다 투자해 현금이 0 인 계좌가 '처음 시작' 으로 되돌아가지 않게
            eng.cash = float(raw.get("cash") or 0)
        eng.deposited = float(raw.get("deposited") or 0)
        eng.flows = {str(k): float(v) for k, v in (raw.get("flows") or {}).items()}
        eng.rebalanced_week = raw.get("rebalanced_week")
        eng.positions = {p["ticker"]: Position(**p) for p in raw.get("positions", [])}
        eng.orders = [Order(**o) for o in raw.get("orders", [])]
        eng.trades = [Trade(**t) for t in raw.get("trades", [])]
        eng.curve = [tuple(c) for c in raw.get("curve", [])]
        eng.events = list(raw.get("events", []))
        eng.last_close = dict(raw.get("last_close", {}))
        for name in ("peak", "prev_equity", "fees_paid", "bought"):
            setattr(eng, name, float(raw.get(name) or 0))
        for name in ("skipped", "blocked", "chased", "invested_days"):
            setattr(eng, name, int(raw.get(name) or 0))
        eng.block_next = bool(raw.get("block_next"))
        eng.halted_on = raw.get("halted_on")
        eng.last_day = raw.get("last_day")
        return eng


def fmt_shares(value: float) -> str:
    """10 → '10', 0.37 → '0.37'. 소수점 매수일 때만 소수가 보인다."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _why_out(s) -> str:
    return "200일선 아래로 또는 매출 성장 멈춤" if getattr(s, "needs_growth", False) else "200일선 아래로"


def prepare(data: dict) -> dict:
    """{티커: [Candle]} → {티커: (봉, 종가, 날짜 문자열, {날짜: 위치})}. 같은 날 봉이 두 개면 뒤엣것."""
    out = {}
    for t, bars in data.items():
        clean = {}
        for b in bars or []:
            if b.open > 0 and b.high > 0 and b.low > 0 and b.close > 0:
                clean[b.day] = b
        ordered = [clean[d] for d in sorted(clean)]
        if not ordered:
            continue
        days = [b.day.isoformat() for b in ordered]
        out[t] = (ordered, [b.close for b in ordered], days, {d: k for k, d in enumerate(days)})
    return out
