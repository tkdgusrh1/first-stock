"""주가 조회. 무료·무인증인 Stooq CSV를 쓴다 (PER/PSR 계산용)."""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date

from .http import HttpClient

log = logging.getLogger(__name__)

QUOTE_URL = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
HISTORY_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


@dataclass
class Quote:
    symbol: str
    price: float
    day: str | None = None
    change_pct: float | None = None


class PriceClient:
    """Stooq 기반 시세 조회. 실패하면 None을 돌려주고 계산은 그 값 없이 진행한다."""

    def __init__(self, http: HttpClient) -> None:
        # Stooq는 SEC와 무관하므로 별도 리미터를 쓰는 게 맞지만,
        # 동일 클라이언트를 재사용해도 문제는 없다(오히려 더 보수적).
        self.http = http
        self._history_cache: dict[str, list[tuple[date, float]]] = {}

    def quote(self, ticker: str) -> Quote | None:
        symbol = f"{ticker.lower()}.us"
        try:
            text = self.http.get_text(QUOTE_URL.format(symbol=symbol))
        except Exception as exc:
            log.warning("시세 조회 실패 %s: %s", ticker, exc)
            return None

        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            return None
        row = rows[0]
        close = _f(row.get("Close"))
        if close is None:
            log.info("시세 없음(상장폐지/티커 불일치 가능): %s", ticker)
            return None

        open_px = _f(row.get("Open"))
        change = ((close - open_px) / open_px * 100) if open_px else None
        return Quote(symbol=ticker.upper(), price=close, day=row.get("Date"), change_pct=change)

    def history(self, ticker: str) -> list[tuple[date, float]]:
        """일봉 종가 전체(오래된 순). 과거 PER 밴드 계산에 쓴다."""
        if ticker.upper() in self._history_cache:
            return self._history_cache[ticker.upper()]
        symbol = f"{ticker.lower()}.us"
        rows: list[tuple[date, float]] = []
        try:
            text = self.http.get_text(HISTORY_URL.format(symbol=symbol))
        except Exception as exc:
            log.warning("일봉 조회 실패 %s: %s", ticker, exc)
            self._history_cache[ticker.upper()] = rows
            return rows
        for row in csv.DictReader(io.StringIO(text)):
            close = _f(row.get("Close"))
            try:
                day = date.fromisoformat(row.get("Date", ""))
            except ValueError:
                continue
            if close is not None:
                rows.append((day, close))
        rows.sort()
        self._history_cache[ticker.upper()] = rows
        return rows

    def close_on_or_before(self, ticker: str, target: date) -> float | None:
        rows = self.history(ticker)
        chosen = None
        for day, close in rows:
            if day <= target:
                chosen = close
            else:
                break
        return chosen

    def prev_close_change(self, ticker: str) -> float | None:
        """직전 거래일 대비 등락률(%)."""
        rows = self.history(ticker)
        if len(rows) < 2:
            return None
        last, prev = rows[-1][1], rows[-2][1]
        if not prev:
            return None
        return (last - prev) / prev * 100


def _f(value: str | None) -> float | None:
    if value in (None, "", "N/D"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
