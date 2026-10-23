"""넓은 바구니용 자료를 한데 — 종목 바구니 · 일봉 저장소 · 미국 재무 · 지수(시장 국면).

'자료 받기' 단추를 누르거나 모의 계좌가 돌 때 받고, 백테스트·모의 계좌는 받아 둔 것으로만 계산한다.
처음 한 번은 오래 걸린다 — 미국은 시세 약 800종목 + 재무 약 800회사(항목 10개쯤씩), 한국은 시세 약 300종목.
두 번째부터는 새로 생긴 날·새 분기만 받는다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .basket import Basket, BasketBuilder
from .engine import from_bars
from .factors import Book, build_company
from .pricedb import PriceDB
from .secfacts import FactsDB

log = logging.getLogger(__name__)

BENCH = {"us": ("SPY", "S&P 500 (SPY)"), "kr": ("069500", "KODEX 200")}
BOOK_TTL = 3600.0            # 재무 재료를 다시 만들기까지(받은 재무는 하루에 몇 번 안 바뀐다)


def _clock(market: str, moment=None):
    from zoneinfo import ZoneInfo

    from .. import markets

    start, end, zone, _label = markets.HOURS.get(market, markets.HOURS[markets.US])
    now = (moment or datetime.now(ZoneInfo(zone))).astimezone(ZoneInfo(zone))
    return now, start, end


def last_session(market: str, moment=None) -> date:
    """끝난 마지막 거래일(어림 — 휴장일은 모른다). 장 마감 20분 뒤부터 오늘을 끝난 날로 본다."""
    now, _start, end = _clock(market, moment)
    minutes = now.hour * 60 + now.minute
    day = now.date()
    if day.weekday() < 5 and minutes >= end[0] * 60 + end[1] + 20:
        return day
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def unfinished_day(market: str, moment=None) -> date | None:
    """장이 열려 있거나 막 끝나 아직 확정되지 않은 날. 그 날 봉은 저장하지 않는다."""
    now, start, end = _clock(market, moment)
    minutes = now.hour * 60 + now.minute
    if now.date().weekday() < 5 and start[0] * 60 + start[1] <= minutes < end[0] * 60 + end[1] + 20:
        return now.date()
    return None


def regime_days(bars) -> dict:
    """{날짜: 그 날 종가가 200일 평균 위인가}. 200일이 안 된 앞쪽은 비운다(판단하지 않는다)."""
    out = {}
    if bars is None:
        return out
    closes, days, total = bars.closes, bars.days, 0.0
    for k in range(len(closes)):
        total += closes[k]
        if k >= 200:
            total -= closes[k - 200]
        if k >= 199:
            out[days[k]] = closes[k] > total / 200
    return out


@dataclass
class Loaded:
    basket: Basket
    prepared: dict
    book: Book | None
    regime: dict
    index: tuple | None          # (이름, 정리된 지수 일봉)

    @property
    def members(self):
        return self.basket.members

    def note(self) -> str:
        years = sorted(y for y, names in self.basket.years.items() if names)
        if not years:
            return ""
        size = self.basket.size
        return (f"{years[0]}~{years[-1]}년 매출 상위 {size} 중 지금 시세가 있는 {len(self.prepared)}종목 — "
                f"회계연도마다 바꿔 끼움")


class QuantData:
    def __init__(self, bot) -> None:
        self.bot = bot
        cache = Path(bot.config.cache_dir)
        self.dir = cache
        self.prices = PriceDB(cache / "quant_prices.sqlite", bot.http)
        self.facts = FactsDB(cache / "quant_facts.sqlite", bot.http)
        self._books: dict = {}
        self._running = threading.Lock()       # 화면 단추와 뒤에서 도는 갱신이 같은 걸 두 번 받지 않게
        self.progress = ""                     # 받는 중이면 사람이 읽을 진행 상황

    def builder(self) -> BasketBuilder:
        return BasketBuilder(self.dir, self.bot.http, self.bot.edgar, getattr(self.bot, "dart", None))

    def basket(self, market: str) -> Basket:
        return self.builder().load(market)

    # ------------------------------------------------------------------ 받기
    def refresh(self, market: str, progress=None, today: date | None = None, prices_only: bool = False) -> str:
        """바구니 → 일봉 → (미국) 재무. 사람이 읽을 한 줄을 돌려준다. 이미 받는 중이면 기다리지 않는다."""
        if not self._running.acquire(blocking=False):
            return f"이미 받는 중입니다 — {self.progress or '잠시 뒤 다시'}"
        try:
            return self._refresh(market, progress, today or date.today(), prices_only)
        finally:
            self.progress = ""
            self._running.release()

    def _refresh(self, market: str, progress, today: date, prices_only: bool) -> str:
        from .. import markets

        place = markets.MARKET_NAME.get(market, market)

        def step(label):
            def report(k, n, what):
                self.progress = f"{place} {label} ({k + 1}/{n}) {what}"
                if progress:
                    progress(k, n, f"{label} {what}")
            return report

        basket = self.basket(market) if prices_only else self.builder().ensure(market, today, step("종목 바구니"))
        if basket.empty:
            return ("종목 바구니를 만들지 못했습니다 — SEC 연락처(설정)를 넣었는지, 인터넷이 되는지 확인하세요."
                    if market == "us" else
                    "종목 바구니를 만들지 못했습니다 — 한국은 DART 인증키가 있어야 합니다(설정 → 열쇠 보관함).")
        bench = BENCH[market][0]
        current = basket.members(today)
        tickers = current + [t for t in basket.all_tickers() if t not in set(current)] + [bench]
        got = self.prices.update(tickers, market, today, step("시세"), unfinished=unfinished_day(market),
                                 expected=last_session(market))
        text = f"시세: {got.text()}"
        if market == "us" and not prices_only:
            ciks = [basket.ciks[t] for t in tickers if t in basket.ciks]
            ok, skipped, bad = self.facts.update(ciks, today, step("재무"))
            self._books.pop(market, None)
            text += f" · 재무: {ok}곳 받음, {skipped}곳은 최근 것" + (f", {bad}곳 못 받음(다음에 다시)" if bad else "")
        return text

    # ------------------------------------------------------------------ 읽기
    def book(self, market: str, basket: Basket, tickers) -> Book:
        cached = self._books.get(market)
        if cached and time.time() - cached[0] < BOOK_TTL and set(tickers) <= cached[2]:
            return cached[1]
        if market == "kr":
            book = Book("kr", kr_fin=basket.fin)
        else:
            companies = {}
            for t in tickers:
                cik = basket.ciks.get(t)
                if cik is None:
                    continue
                data = self.facts.company(cik)
                if data:
                    companies[t] = build_company(data)
            book = Book("us", companies, {t: self.prices.splits(t) for t in companies})
        self._books[market] = (time.time(), book, set(tickers))
        return book

    def load(self, market: str, since: date | None = None, tickers=None) -> Loaded:
        """받아 둔 것만으로 — 바구니 종목(또는 tickers)의 일봉, 재무 재료, 시장 국면, 지수 비교선."""
        basket = self.basket(market)
        wanted = list(tickers) if tickers is not None else basket.all_tickers()
        prepared = from_bars(self.prices.load(wanted, since))
        bench, name = BENCH[market]
        bench_bars = self.prices.load([bench], since).get(bench)
        index = (f"{name} 그냥 보유", from_bars({bench: bench_bars})) if bench_bars else None
        book = self.book(market, basket, prepared) if prepared else None
        return Loaded(basket, prepared, book, regime_days(bench_bars), index)

    def regime(self, market: str, since: date | None = None) -> dict:
        bench = BENCH[market][0]
        return regime_days(self.prices.load([bench], since).get(bench))

    def status(self, market: str, today: date | None = None) -> dict:
        today = today or date.today()
        basket = self.basket(market)
        year = basket.year_for(today)
        members = basket.members(today)
        prices = self.prices.status(members) if members else {"have": 0, "total": 0, "newest": "", "failed": []}
        facts = 0
        if market == "us" and members:
            facts = sum(1 for t in members if t in basket.ciks and self.facts.seen(basket.ciks[t]))
        bench = self.prices.meta(BENCH[market][0])
        return {"year": year, "members": len(members), "prices": prices, "facts": facts,
                "years": sorted(y for y, names in basket.years.items() if names),
                "all": len(basket.all_tickers()), "bench": bool(bench and bench.get("last")),
                "size": basket.size}
