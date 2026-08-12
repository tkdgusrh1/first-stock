"""환율과 주요 지수. 화면 맨 위에 한 줄로 띄우는 값들.

환율은 모두 **1달러 기준**이다. "1달러 = 1,380원" 처럼 읽으면 된다.
(유로만 세계 관행상 1유로 = $1.09 로 표기하지만, 여기서는 기준을 하나로
 통일하는 쪽이 헷갈리지 않아서 1달러 = 0.92유로 로 맞춘다.)

값은 Yahoo → Stooq 순서로 시도한다. 한쪽이 막혀도 화면이 비지 않도록.
받은 시각을 함께 남겨서, 언제 기준 값인지 화면에서 알 수 있게 한다.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
STOOQ_QUOTE = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcvn&h&e=csv"

# (표시 이름, Yahoo 심볼, Stooq 심볼, 소수점 자리, 단위 기호)
FX_SPECS = [
    ("원", "KRW=X", "usdkrw", 2, "₩"),
    ("엔", "JPY=X", "usdjpy", 2, "¥"),
    ("위안", "CNY=X", "usdcny", 4, "¥"),
    ("유로", "EUR=X", "usdeur", 4, "€"),
]

# 지수는 '몇 포인트' 보다 '몇 % 움직였나' 가 중요해서 등락률을 크게 보여준다.
INDEX_SPECS = [
    ("S&P 500", "^GSPC", "^spx", "미국 대형주 500개"),
    ("나스닥", "^IXIC", "^ndq", "기술주 중심"),
    ("다우", "^DJI", "^dji", "우량주 30개"),
    ("VIX", "^VIX", "^vix", "공포지수 — 높을수록 불안"),
]


@dataclass
class Rate:
    """1달러당 가격."""

    label: str
    value: float
    change_pct: float | None = None
    digits: int = 2
    symbol: str = ""
    source: str = ""

    @property
    def text(self) -> str:
        return f"{self.symbol}{self.value:,.{self.digits}f}"

    @property
    def direction(self) -> str:
        """달러가 강해졌는지(원화 약세) 약해졌는지."""
        if self.change_pct is None:
            return ""
        return "up" if self.change_pct > 0 else ("down" if self.change_pct < 0 else "")


@dataclass
class IndexQuote:
    label: str
    value: float
    change_pct: float | None = None
    note: str = ""
    source: str = ""

    @property
    def text(self) -> str:
        return f"{self.value:,.2f}"

    @property
    def direction(self) -> str:
        if self.change_pct is None:
            return ""
        return "up" if self.change_pct > 0 else ("down" if self.change_pct < 0 else "")


@dataclass
class MarketSnapshot:
    rates: list[Rate]
    indexes: list[IndexQuote]
    fetched_at: datetime

    @property
    def empty(self) -> bool:
        return not self.rates and not self.indexes


def _yahoo(http, symbol: str) -> tuple[float, float | None] | None:
    """(현재값, 전일대비 %)"""
    try:
        text = http.get_text(YAHOO_CHART.format(symbol=symbol), timeout=15, retries=1)
        data = json.loads(text)
    except Exception as exc:
        log.debug("Yahoo 조회 실패 %s: %s", symbol, exc)
        return None
    results = ((data.get("chart") or {}).get("result")) or []
    if not results:
        return None
    meta = results[0].get("meta") or {}
    value = meta.get("regularMarketPrice")
    if value is None:
        return None
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    change = ((value - previous) / previous * 100) if previous else None
    return float(value), change


def _stooq(http, symbol: str) -> tuple[float, float | None] | None:
    try:
        text = http.get_text(STOOQ_QUOTE.format(symbol=symbol), timeout=15, retries=1)
    except Exception as exc:
        log.debug("Stooq 조회 실패 %s: %s", symbol, exc)
        return None
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return None
    close, open_ = _f(rows[0].get("Close")), _f(rows[0].get("Open"))
    if close is None:
        return None
    change = ((close - open_) / open_ * 100) if open_ else None
    return close, change


def _fetch(http, yahoo_symbol: str, stooq_symbol: str) -> tuple[float, float | None, str] | None:
    found = _yahoo(http, yahoo_symbol)
    if found:
        return found[0], found[1], "Yahoo Finance"
    found = _stooq(http, stooq_symbol)
    if found:
        return found[0], found[1], "Stooq"
    return None


class FxClient:
    """환율·지수를 짧은 주기로 갱신한다.

    대시보드가 그리는 도중에 네트워크를 기다리면 화면이 멈춘다.
    그래서 여기서는 **캐시만 읽어주고**, 갱신은 백그라운드에서 부른다.
    """

    def __init__(self, http, ttl: float = 60.0) -> None:
        self.http = http
        self.ttl = ttl
        self._lock = threading.Lock()
        self._snapshot: MarketSnapshot | None = None

    def cached(self) -> MarketSnapshot | None:
        return self._snapshot

    def stale(self) -> bool:
        snap = self._snapshot
        if snap is None:
            return True
        return (datetime.now(timezone.utc) - snap.fetched_at).total_seconds() > self.ttl

    def refresh(self, force: bool = False) -> MarketSnapshot | None:
        """실제로 값을 받아온다. 오래 걸리므로 백그라운드에서만 부른다."""
        if not force and not self.stale():
            return self._snapshot
        if not self._lock.acquire(blocking=False):
            return self._snapshot        # 이미 다른 스레드가 받는 중
        try:
            rates: list[Rate] = []
            for label, yahoo_symbol, stooq_symbol, digits, unit in FX_SPECS:
                found = _fetch(self.http, yahoo_symbol, stooq_symbol)
                if found:
                    rates.append(
                        Rate(label=label, value=found[0], change_pct=found[1],
                             digits=digits, symbol=unit, source=found[2])
                    )

            indexes: list[IndexQuote] = []
            for label, yahoo_symbol, stooq_symbol, note in INDEX_SPECS:
                found = _fetch(self.http, yahoo_symbol, stooq_symbol)
                if found:
                    indexes.append(
                        IndexQuote(label=label, value=found[0], change_pct=found[1],
                                   note=note, source=found[2])
                    )

            if rates or indexes:
                self._snapshot = MarketSnapshot(
                    rates=rates, indexes=indexes, fetched_at=datetime.now(timezone.utc)
                )
            elif self._snapshot is None:
                log.info("환율·지수를 한 곳에서도 받지 못했습니다.")
            return self._snapshot
        finally:
            self._lock.release()


def _f(value: str | None) -> float | None:
    if value in (None, "", "N/D"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
