"""주가 조회. 무료·무인증 제공처 두 곳을 순서대로 시도한다.

한 곳이 특정 티커를 모르는 경우가 잦아서(특히 최근 상장·소형주) 이중화했다.
어디서 온 값인지 함께 남겨 화면에 출처를 표시한다.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

STOOQ_QUOTE = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
# 시세를 얼마나 들고 있을지.
#   긴 과거(10년치 일봉)는 하루에 한 줄 늘 뿐이라 자주 받을 이유가 없다.
#   바뀌는 건 **오늘 봉 하나**다. 그래서 최근 며칠만 따로, 자주 받아 덧붙인다.
#   10년치를 5분마다 통째로 받으면 종목당 2,500줄을 되풀이해서 받는 셈이고,
#   그러다 야후가 막으면 주가 자체가 안 나온다.
HISTORY_TTL = 6 * 3600.0
LIVE_TTL = 60.0
QUOTE_TTL = 60.0
LIVE_RANGE = "5d"

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
class Candle:
    """하루치 봉 하나. 넷이 다 있을 때만 만든다 — 없는 값을 종가로 메우면
    있지도 않은 몸통을 그리게 된다."""

    day: date
    open: float
    high: float
    low: float
    close: float

    @property
    def rising(self) -> bool:
        return self.close >= self.open


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
        self._candle_cache: dict[str, list[Candle]] = {}
        # 언제 받았는지. 이게 없으면 한 번 받은 값을 프로그램이 도는 내내
        # 그대로 쓴다 — 며칠 켜두면 차트가 켠 날에 멈춰 있게 된다.
        self._fetched_at: dict[str, float] = {}
        self._live_cache: dict[str, list[Candle]] = {}      # 최근 며칠 (자주 받는다)
        self._board: dict[str, str] = {}    # 005930.KS → 005930.KQ 처럼 알아낸 것
        self._quote_cache: dict[str, Quote | None] = {}
        self.last_source: str = ""

    # --- 현재가 ----------------------------------------------------------
    def _symbol(self, ticker: str) -> str:
        """이미 알아낸 기호가 있으면 그걸 쓴다 (코스피↔코스닥)."""
        key = ticker.upper()
        return self._board.get(key, key)

    def _other_board(self, key: str) -> str:
        """005930.KS ↔ 005930.KQ. 한국 종목이 아니면 빈 문자열.

        종목 코드만으로는 코스피인지 코스닥인지 알 수 없다. 한쪽에서 못
        찾으면 다른 쪽을 본다 — 이게 없으면 코스닥 종목은 주가가 영영 안 뜬다.
        """
        found = _KR_SYMBOL.match(key)
        if not found:
            return ""
        return f"{found.group(1)}.{'KQ' if found.group(2) == 'KS' else 'KS'}"

    def _remember_board(self, asked: str, works: str) -> None:
        if asked != works:
            self._board[asked] = works
            log.info("%s 는 %s 로 찾았습니다.", asked, works)

    def quote(self, ticker: str) -> Quote | None:
        key = self._symbol(ticker)
        if key in self._quote_cache and not self._stale(f"q:{key}", QUOTE_TTL):
            return self._quote_cache[key]

        # Yahoo 를 먼저 쓴다. 등락률·장외 가격·장 상태까지 한 번에 오기 때문.
        # 실패하면 Stooq 종가로 물러난다.
        result = self._yahoo_quote(key) or self._stooq_quote(key)
        other = self._other_board(key) if result is None else ""
        if other:
            result = self._yahoo_quote(other)
            if result is not None:
                self._remember_board(key, other)
                key = other
        if result is None:
            log.info("시세를 찾지 못했습니다: %s (제공처 2곳 모두 실패)", ticker)
        else:
            self.last_source = result.source
        self._quote_cache[key] = result
        self._fetched_at[f"q:{key}"] = time.time()
        return result

    def _stooq_quote(self, ticker: str) -> Quote | None:
        # Stooq 의 '.us' 는 미국 종목 표기다. 005930.KS 에 붙이면
        # '005930.ks.us' 라는 없는 종목을 물어보는 셈이라, 실패하는 데 시간만 쓴다.
        if "." in ticker:
            return None
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
    def _stale(self, key: str, ttl: float) -> bool:
        return time.time() - self._fetched_at.get(key, 0.0) > ttl

    def forget_prices(self, ticker: str = "") -> None:
        """지금 값(현재가·오늘 봉)만 버린다. 긴 과거는 그대로 둔다.

        긴 과거까지 버리면 다음에 10년치를 통째로 다시 받는다.
        """
        keys = [ticker.upper()] if ticker else list(self._quote_cache)
        for key in keys:
            self._quote_cache.pop(key, None)
            self._live_cache.pop(key, None)
            self._fetched_at.pop(f"q:{key}", None)
            self._fetched_at.pop(f"live:{key}", None)

    def history(self, ticker: str) -> list[tuple[date, float]]:
        """일봉 종가(오래된 순). 과거 PER 밴드 계산에 쓴다."""
        key = self._symbol(ticker)
        self._ensure_long(key)
        if not self._history_cache.get(key):
            other = self._other_board(key)
            if other:
                self._ensure_long(other)
                if self._history_cache.get(other):
                    self._remember_board(key, other)
                    key = other
        rows = list(self._history_cache.get(key, []))
        live = self._live(key)
        if not live:
            return rows
        # 오늘 몫으로 끝을 갈아끼운다. 같은 날이 있으면 새 값이 이긴다.
        fresh = {bar.day: bar.close for bar in live}
        merged = [(day, close) for day, close in rows if day not in fresh]
        merged.extend(sorted(fresh.items()))
        return merged

    def _ensure_long(self, key: str) -> None:
        """긴 과거(10년치). 여섯 시간에 한 번만 받는다."""
        if key in self._history_cache and not self._stale(key, HISTORY_TTL):
            return
        # **야후를 먼저 본다.** Stooq 는 장 마감 뒤에야 그날 것을 내주므로,
        # 장중에는 오늘 봉이 아예 없다. 캔들에 오늘이 안 보이던 이유가 이것이다.
        rows = self._yahoo_history(key) or self._stooq_history(key) or []
        self._history_cache[key] = rows
        self._fetched_at[key] = time.time()

    def _live(self, key: str) -> list[Candle]:
        """최근 며칠치 봉. 1분에 한 번까지만 받는다.

        장중이면 마지막 봉이 **아직 끝나지 않은 오늘 봉**이다. 고가·저가·종가가
        계속 바뀐다. 이게 실시간 반영의 전부다 — 과거 봉은 이미 확정됐다.
        """
        if key in self._live_cache and not self._stale(f"live:{key}", LIVE_TTL):
            return self._live_cache[key]
        payload = self._yahoo_chart(key, LIVE_RANGE)
        bars = _candles_from(payload) if payload else []
        self._live_cache[key] = bars
        self._fetched_at[f"live:{key}"] = time.time()
        return bars

    def candles(self, ticker: str) -> list[Candle]:
        """일봉 하나하나(시가·고가·저가·종가). 캔들 차트에 쓴다.

        종가만 있는 history 와 같은 응답에서 뽑는다 — 추가 요청이 없다.
        넷 중 하나라도 없는 날은 **그 날을 통째로 건너뛴다.** 없는 값을
        종가로 메우면 있지도 않은 몸통을 그리게 된다.
        """
        self.history(ticker)                   # 코스피·코스닥을 여기서 가린다
        key = self._symbol(ticker)
        self._ensure_long(key)                 # 같은 응답에서 캔들도 채워진다
        bars = list(self._candle_cache.get(key, []))
        live = self._live(key)
        if not live:
            return bars
        fresh = {bar.day for bar in live}
        return [b for b in bars if b.day not in fresh] + list(live)

    def _stooq_history(self, ticker: str) -> list[tuple[date, float]] | None:
        try:
            text = self.http.get_text(STOOQ_HISTORY.format(symbol=f"{ticker.lower()}.us"), retries=1)
        except Exception:
            return None
        candles: list[Candle] = []
        rows: list[tuple[date, float]] = []
        for row in csv.DictReader(io.StringIO(text)):
            close = _f(row.get("Close"))
            opened, high, low = (_f(row.get(k)) for k in ("Open", "High", "Low"))
            try:
                day = date.fromisoformat(row.get("Date", ""))
            except ValueError:
                continue
            if close is not None:
                rows.append((day, close))
            if None not in (opened, high, low, close):
                candles.append(Candle(day, opened, high, low, close))
        rows.sort()
        candles.sort(key=lambda c: c.day)
        self._candle_cache[ticker.upper()] = candles
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
        self._candle_cache[ticker.upper()] = _candles_from(payload)
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


_KR_SYMBOL = re.compile(r"^(\d{6})\.(KS|KQ)$")


def _candles_from(payload: dict) -> list[Candle]:
    """야후 차트 응답 → 봉 목록. 넷 중 하나라도 없는 날은 뺀다."""
    stamps = payload.get("timestamp") or []
    quotes = ((payload.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows, closes = (quotes.get(k) or [] for k in ("open", "high", "low", "close"))
    out: list[Candle] = []
    for i, stamp in enumerate(stamps):
        four = [_at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i)]
        if None in four:
            continue
        day = datetime.fromtimestamp(stamp, tz=timezone.utc).date()
        out.append(Candle(day, *(float(v) for v in four)))
    out.sort(key=lambda c: c.day)
    return out


def _at(values, index):
    """목록에서 한 칸. 없거나 짧으면 None — 옆 값으로 메우지 않는다."""
    try:
        return values[index]
    except (IndexError, TypeError):
        return None


def _f(value: str | None) -> float | None:
    if value in (None, "", "N/D"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
