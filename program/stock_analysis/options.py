"""옵션 시장이 지금 무엇을 가격에 넣고 있나 (야후 옵션 체인).

옵션 가격에는 '앞으로 얼마나 움직일 것 같은지' 가 들어 있다. 회사 공시에는
없는, 시장 참여자들이 돈을 걸고 매긴 값이다. 여기서는 체인 원본에서 **계산만**
한다 — 받지 못하면 빈칸이고, 추정으로 채우지 않는다.

  · 콜·풋 거래량과 미결제약정, 그 비율(풋/콜)
  · 현재가에 가장 가까운 행사가(ATM)의 내재변동성
  · ATM 스트래들 가격 = 만기까지 시장이 예상하는 움직임 폭(±)
  · 미결제약정이 가장 많이 쌓인 행사가
  · 오늘 거래량이 미결제약정보다 많은 계약(새 자금이 들어온 자리)

야후 옵션 시세는 보통 15분 늦다. 화면에도 그렇게 적는다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

OPTIONS_URL = "https://query2.finance.yahoo.com/v7/finance/options/{ticker}?crumb={crumb}"
DATED_URL = OPTIONS_URL + "&date={stamp}"
MONTH_DAYS = 30          # 두 번째로 볼 만기: 30일에 가장 가까운 것
NEAR_STRIKES = 5         # 표에 현재가 위아래로 몇 개 행사가를 보일지


@dataclass
class Contract:
    strike: float
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    iv: float | None = None            # 0.85 = 85%

    @property
    def mid(self) -> float | None:
        """호가 가운데. 호가가 없으면 마지막 체결가."""
        if self.bid and self.ask and self.ask >= self.bid:
            return (self.bid + self.ask) / 2
        return self.last if self.last else None


@dataclass
class Expiry:
    day: date
    calls: list = field(default_factory=list)    # [Contract] 행사가 순
    puts: list = field(default_factory=list)

    def days_left(self, today: date) -> int:
        return max(0, (self.day - today).days)

    @staticmethod
    def _sum(rows, key) -> float:
        return sum(getattr(c, key) or 0 for c in rows)

    @property
    def call_volume(self) -> float:
        return self._sum(self.calls, "volume")

    @property
    def put_volume(self) -> float:
        return self._sum(self.puts, "volume")

    @property
    def call_oi(self) -> float:
        return self._sum(self.calls, "open_interest")

    @property
    def put_oi(self) -> float:
        return self._sum(self.puts, "open_interest")

    @property
    def pc_volume(self) -> float | None:
        return self.put_volume / self.call_volume if self.call_volume else None

    @property
    def pc_oi(self) -> float | None:
        return self.put_oi / self.call_oi if self.call_oi else None

    def atm_strike(self, price: float) -> float | None:
        strikes = {c.strike for c in self.calls} & {p.strike for p in self.puts}
        if not strikes or not price:
            return None
        return min(strikes, key=lambda k: (abs(k - price), k))

    def at(self, rows, strike: float) -> Contract | None:
        return next((c for c in rows if c.strike == strike), None)

    def atm_iv(self, price: float) -> float | None:
        strike = self.atm_strike(price)
        if strike is None:
            return None
        values = [c.iv for c in (self.at(self.calls, strike), self.at(self.puts, strike))
                  if c is not None and c.iv and c.iv > 0.01]
        return sum(values) / len(values) if values else None

    def straddle(self, price: float) -> float | None:
        """ATM 콜 + ATM 풋. 만기까지 시장이 매긴 '예상 움직임 폭'."""
        strike = self.atm_strike(price)
        if strike is None:
            return None
        call, put = self.at(self.calls, strike), self.at(self.puts, strike)
        if call is None or put is None or call.mid is None or put.mid is None:
            return None
        return call.mid + put.mid

    def top_oi(self, rows) -> Contract | None:
        found = [c for c in rows if c.open_interest]
        return max(found, key=lambda c: (c.open_interest, -c.strike)) if found else None

    def near(self, price: float, count: int = NEAR_STRIKES) -> list[float]:
        strikes = sorted({c.strike for c in self.calls} | {p.strike for p in self.puts})
        if not strikes or not price:
            return []
        center = min(range(len(strikes)), key=lambda i: abs(strikes[i] - price))
        return strikes[max(0, center - count):center + count + 1]


@dataclass
class Unusual:
    kind: str           # 콜 / 풋
    expiry: date
    contract: Contract

    @property
    def ratio(self) -> float | None:
        c = self.contract
        return c.volume / c.open_interest if c.volume and c.open_interest else None


@dataclass
class OptionsView:
    ticker: str
    price: float | None = None
    expiries: list = field(default_factory=list)       # [Expiry] 받은 것만 (가까운 순)
    all_dates: list = field(default_factory=list)      # [date] 상장된 만기 전부
    fetched_at: datetime | None = None
    source: str = "Yahoo Finance"

    @property
    def empty(self) -> bool:
        return not self.expiries or not any(e.calls or e.puts for e in self.expiries)

    def unusual(self, limit: int = 5) -> list[Unusual]:
        """오늘 거래량이 미결제약정을 넘은 계약. 거래량 큰 순.

        새 포지션이 들어왔다는 뜻일 수 있지만, 사는 쪽인지 파는 쪽인지는 알 수 없다.
        """
        found = []
        for expiry in self.expiries:
            for kind, rows in (("콜", expiry.calls), ("풋", expiry.puts)):
                for c in rows:
                    if c.volume and c.volume >= 500 and c.volume > (c.open_interest or 0):
                        found.append(Unusual(kind, expiry.day, c))
        found.sort(key=lambda u: -(u.contract.volume or 0))
        return found[:limit]


def _f(value) -> float | None:
    value = value.get("raw") if isinstance(value, dict) else value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _day(stamp) -> date | None:
    value = _f(stamp)
    return datetime.fromtimestamp(value, tz=timezone.utc).date() if value else None


def _contracts(rows) -> list[Contract]:
    out = []
    for row in rows or []:
        strike = _f(row.get("strike"))
        if strike is None:
            continue
        out.append(Contract(strike=strike, last=_f(row.get("lastPrice")), bid=_f(row.get("bid")),
                            ask=_f(row.get("ask")), volume=_f(row.get("volume")),
                            open_interest=_f(row.get("openInterest")), iv=_f(row.get("impliedVolatility"))))
    out.sort(key=lambda c: c.strike)
    return out


def parse_chain(payload: dict) -> tuple[float | None, list[date], list[int], Expiry | None]:
    """옵션 응답 하나 → (현재가, 만기 날짜들, 만기 원본 값들, 받은 만기 하나)."""
    results = ((payload.get("optionChain") or {}).get("result")) or []
    if not results:
        return None, [], [], None
    node = results[0]
    quote = node.get("quote") or {}
    price = _f(quote.get("regularMarketPrice"))
    stamps = [int(s) for s in node.get("expirationDates") or [] if isinstance(s, (int, float))]
    days = [d for d in (_day(s) for s in stamps) if d]
    expiry = None
    for block in node.get("options") or []:
        day = _day(block.get("expirationDate"))
        if day is None:
            continue
        expiry = Expiry(day=day, calls=_contracts(block.get("calls")), puts=_contracts(block.get("puts")))
        break
    return price, days, stamps, expiry


def fetch(client, symbol: str, today: date | None = None) -> OptionsView | None:
    """가까운 만기와 30일 근처 만기 두 개를 받는다. 옵션이 없거나 막히면 None."""
    crumb = client._get_crumb()
    if not crumb:
        return None
    today = today or date.today()

    def get(url: str) -> dict | None:
        try:
            return json.loads(client.http.get_text(url, timeout=20, retries=1))
        except Exception as exc:
            log.info("옵션 체인 조회 실패 %s: %s", symbol, exc)
            return None

    payload = get(OPTIONS_URL.format(ticker=symbol.upper(), crumb=crumb))
    if not payload:
        return None
    price, days, stamps, first = parse_chain(payload)
    if first is None:
        return None
    view = OptionsView(ticker=symbol.upper(), price=price, expiries=[first], all_dates=days,
                       fetched_at=datetime.now(timezone.utc))
    month = pick_month(days, stamps, today)
    if month and _day(month) != first.day:
        second = get(DATED_URL.format(ticker=symbol.upper(), crumb=crumb, stamp=month))
        if second:
            found = parse_chain(second)[3]
            if found is not None:
                view.expiries.append(found)
    return view


def pick_month(days: list[date], stamps: list[int], today: date) -> int | None:
    """오늘에서 30일에 가장 가까운 만기(원본 값). 없으면 None."""
    pairs = [(d, s) for d, s in zip(days, stamps) if d > today]
    if not pairs:
        return None
    return min(pairs, key=lambda p: (abs((p[0] - today).days - MONTH_DAYS), p[0]))[1]
