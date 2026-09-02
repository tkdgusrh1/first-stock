"""주가 조회. 무료·무인증 제공처 두 곳을 순서대로 시도한다.

한 곳이 특정 티커를 모르는 경우가 잦아서(특히 최근 상장·소형주) 이중화했다.
어디서 온 값인지 함께 남겨 화면에 출처를 표시한다.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

STOOQ_QUOTE = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
STOOQ_HISTORY = "https://stooq.com/q/d/l/?s={symbol}&i=d"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range={range}&interval=1d"


MARKET_STATE = {
    "PRE": "장전",
    "PREPRE": "장전",
    "REGULAR": "정규장",
    "POST": "장후",
    "POSTPOST": "장마감 후",
    "CLOSED": "폐장",
}


@dataclass
class Quote:
    symbol: str
    price: float                       # 정규장 종가(또는 현재가)
    day: str | None = None
    change_pct: float | None = None
    source: str = ""
    # 장외 거래 (프리마켓 / 애프터마켓)
    extended_price: float | None = None
    extended_change_pct: float | None = None
    market_state: str | None = None    # PRE / REGULAR / POST / CLOSED

    @property
    def state_label(self) -> str:
        return MARKET_STATE.get(self.market_state or "", self.market_state or "")

    @property
    def extended_label(self) -> str:
        """장외 가격에 붙일 이름."""
        if self.market_state in ("PRE", "PREPRE"):
            return "프리마켓"
        if self.market_state in ("POST", "POSTPOST", "CLOSED"):
            return "애프터마켓"
        return "장외"


class PriceClient:
    def __init__(self, http) -> None:
        self.http = http
        self._history_cache: dict[str, list[tuple[date, float]]] = {}
        self._quote_cache: dict[str, Quote | None] = {}
        self.last_source: str = ""

    # --- 현재가 ----------------------------------------------------------
    def quote(self, ticker: str) -> Quote | None:
        key = ticker.upper()
        if key in self._quote_cache:
            return self._quote_cache[key]

        # Yahoo 를 먼저 쓴다. 등락률·장외 가격·장 상태까지 한 번에 오기 때문.
        # 실패하면 Stooq 종가로 물러난다.
        result = self._yahoo_quote(key) or self._stooq_quote(key)
        if result is None:
            log.info("시세를 찾지 못했습니다: %s (제공처 2곳 모두 실패)", ticker)
        else:
            self.last_source = result.source
        self._quote_cache[key] = result
        return result

    def _stooq_quote(self, ticker: str) -> Quote | None:
        try:
            text = self.http.get_text(STOOQ_QUOTE.format(symbol=f"{ticker.lower()}.us"), retries=1)
        except Exception as exc:
            log.debug("Stooq 시세 실패 %s: %s", ticker, exc)
            return None

        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            return None
        close = _f(rows[0].get("Close"))
        if close is None:
            return None
        return Quote(symbol=ticker, price=close, day=rows[0].get("Date"), source="Stooq")

    def _yahoo_quote(self, ticker: str) -> Quote | None:
        payload = self._yahoo_chart(ticker, "5d")
        if not payload:
            return None
        return _quote_from_meta(ticker, payload.get("meta") or {})

    def extended(self, ticker: str) -> Quote | None:
        """장외(프리·애프터마켓) 가격까지 담긴 시세.

        Stooq 는 정규장 종가만 주므로 이 정보는 Yahoo 에서만 얻는다.
        """
        payload = self._yahoo_chart(ticker, "1d")
        if not payload:
            return None
        return _quote_from_meta(ticker, payload.get("meta") or {})

    def _yahoo_chart(self, ticker: str, span: str) -> dict | None:
        try:
            text = self.http.get_text(YAHOO_CHART.format(ticker=ticker.upper(), range=span), retries=1)
            data = json.loads(text)
        except Exception as exc:
            log.debug("Yahoo 시세 실패 %s: %s", ticker, exc)
            return None
        results = ((data.get("chart") or {}).get("result")) or []
        return results[0] if results else None

    # --- 일봉 ------------------------------------------------------------
    def history(self, ticker: str) -> list[tuple[date, float]]:
        """일봉 종가(오래된 순). 과거 PER 밴드 계산에 쓴다."""
        key = ticker.upper()
        if key in self._history_cache:
            return self._history_cache[key]

        rows = self._stooq_history(key) or self._yahoo_history(key) or []
        self._history_cache[key] = rows
        return rows

    def _stooq_history(self, ticker: str) -> list[tuple[date, float]] | None:
        try:
            text = self.http.get_text(STOOQ_HISTORY.format(symbol=f"{ticker.lower()}.us"), retries=1)
        except Exception:
            return None
        rows: list[tuple[date, float]] = []
        for row in csv.DictReader(io.StringIO(text)):
            close = _f(row.get("Close"))
            try:
                day = date.fromisoformat(row.get("Date", ""))
            except ValueError:
                continue
            if close is not None:
                rows.append((day, close))
        rows.sort()
        return rows or None

    def _yahoo_history(self, ticker: str) -> list[tuple[date, float]] | None:
        payload = self._yahoo_chart(ticker, "10y")
        if not payload:
            return None
        stamps = payload.get("timestamp") or []
        quotes = ((payload.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quotes.get("close") or []
        rows: list[tuple[date, float]] = []
        for stamp, close in zip(stamps, closes):
            if close is None:
                continue
            rows.append((datetime.fromtimestamp(stamp, tz=timezone.utc).date(), float(close)))
        rows.sort()
        return rows or None

    def close_on_or_before(self, ticker: str, target: date) -> float | None:
        chosen = None
        for day, close in self.history(ticker):
            if day <= target:
                chosen = close
            else:
                break
        return chosen

    def prev_close_change(self, ticker: str) -> float | None:
        """직전 거래일 대비 등락률(%)."""
        quote = self.quote(ticker)
        if quote and quote.change_pct is not None:
            return quote.change_pct
        rows = self.history(ticker)
        if len(rows) < 2 or not rows[-2][1]:
            return None
        return (rows[-1][1] - rows[-2][1]) / rows[-2][1] * 100


def _quote_from_meta(ticker: str, meta: dict) -> Quote | None:
    """Yahoo 차트 메타에서 정규장·장외 가격을 뽑는다."""
    price = meta.get("regularMarketPrice")
    if price is None:
        return None
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    change = ((price - previous) / previous * 100) if previous else None

    state = meta.get("marketState")
    # 장전이면 프리마켓, 장마감 뒤면 애프터마켓 값을 쓴다
    extended = meta.get("preMarketPrice") if state in ("PRE", "PREPRE") else meta.get("postMarketPrice")
    extended_change = None
    if extended is not None and price:
        extended_change = (extended - price) / price * 100

    return Quote(
        symbol=ticker.upper(),
        price=float(price),
        day=datetime.now(timezone.utc).date().isoformat(),
        change_pct=change,
        source="Yahoo Finance",
        extended_price=float(extended) if extended is not None else None,
        extended_change_pct=extended_change,
        market_state=state,
    )


def _f(value: str | None) -> float | None:
    if value in (None, "", "N/D"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
