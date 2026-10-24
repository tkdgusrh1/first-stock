"""퀀트용 일봉 저장소 — 넓은 종목 바구니(수백 종목)의 10년치 일봉을 디스크(SQLite)에 쌓는다.

화면용 시세(prices.PriceClient)는 메모리에만 들고 있어서, 수백 종목을 백테스트하려면 켤 때마다
10년치를 다시 받아야 한다. 여기서는 한 번 받은 것을 저장해 두고, 다음부터는 최근 한 달치만 받아 잇는다.

- 야후 일봉은 '받는 날 기준으로 액면분할을 반영한' 가격이다. 그 사이 분할이 생기면 예전 가격이 통째로
  바뀐다. 그래서 새로 받은 구간과 저장해 둔 구간이 겹치는 날의 종가가 어긋나면 10년치를 다시 받는다.
- 분할 기록(언제, 몇 대 몇)도 같이 저장한다 — SEC 의 옛 주식 수를 지금 가격 기준으로 맞출 때 쓴다.
- 장이 열려 있는 동안의 오늘 봉은 끝나지 않은 봉이라 저장하지 않는다.
- 받지 못한 종목은 비워 둔다. 다른 값으로 메우지 않는다.

메모리: 종목 800개 × 10년이면 봉이 200만 개쯤이다. 봉마다 객체를 만들면 수백 MB 가 되어
날짜는 문자열 목록, 값은 array('d') 로 들고 있다가 필요한 봉만 그때그때 만든다(Bars).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from array import array
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

CHART_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
             "?range={span}&interval=1d&events=split")
FULL = "10y"
FRESH_SECONDS = 10 * 3600       # 마지막 거래일을 모를 때: 이만큼 안에 받은 종목은 다시 묻지 않는다
RECHECK = 3 * 3600              # 마지막 거래일 봉이 아직 없으면 세 시간에 한 번만 다시(휴장일에 계속 묻지 않게)
RETRY_FAILED = 24 * 3600        # 받지 못한 종목은 하루 뒤에 다시
OVERLAP_TOLERANCE = 0.005       # 겹치는 날 종가가 0.5% 넘게 다르면 분할·조정이 생긴 것으로 본다
WORKERS = 3
STOP_AFTER_LIMITS = 8           # 야후가 연달아 이만큼 막으면(429) 이번에는 그만 받는다


class Bar(NamedTuple):
    day: date
    open: float
    high: float
    low: float
    close: float


class Bars:
    """한 종목의 일봉을 가볍게 들고 있는 목록. bars[i] 를 꺼낼 때만 봉 하나를 만든다."""

    __slots__ = ("days", "opens", "highs", "lows", "closes")

    def __init__(self, days: list[str], opens: array, highs: array, lows: array, closes: array) -> None:
        self.days, self.opens, self.highs, self.lows, self.closes = days, opens, highs, lows, closes

    def __len__(self) -> int:
        return len(self.closes)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self[k] for k in range(*i.indices(len(self)))]
        return Bar(date.fromisoformat(self.days[i]), self.opens[i], self.highs[i], self.lows[i], self.closes[i])

    def __iter__(self):
        for k in range(len(self)):
            yield self[k]


@dataclass
class UpdateResult:
    asked: int = 0
    fetched: int = 0
    fresh: int = 0
    refetched: int = 0                      # 분할 등으로 10년치를 다시 받은 종목
    failed: list = field(default_factory=list)
    limited: bool = False                   # 야후가 막아서 도중에 멈췄나

    def text(self) -> str:
        bits = [f"{self.fetched}개 받음"]
        if self.fresh:
            bits.append(f"{self.fresh}개는 최근에 받아 건너뜀")
        if self.refetched:
            bits.append(f"{self.refetched}개는 분할 등으로 다시 받음")
        if self.failed:
            bits.append(f"{len(self.failed)}개 못 받음")
        if self.limited:
            bits.append("야후가 잠시 막아 나머지는 다음에 이어서 받습니다")
        return ", ".join(bits)


_ISO: dict[int, str] = {}


def iso_of(ordinal: int) -> str:
    """날짜 서수 → 'YYYY-MM-DD'. 같은 날짜 문자열을 종목마다 따로 만들지 않게 한 번만 만든다."""
    text = _ISO.get(ordinal)
    if text is None:
        text = _ISO[ordinal] = date.fromordinal(ordinal).isoformat()
    return text


def yahoo_symbol(ticker: str) -> str:
    """야후 기호. BRK.B → BRK-B."""
    return str(ticker or "").strip().upper().replace(".", "-")


def parse_chart(payload: dict) -> tuple[list[tuple], list[tuple]]:
    """야후 차트 응답 → ([(날짜, 시, 고, 저, 종, 거래량)], [(날짜, 분할 비율)]).

    넷(시·고·저·종) 중 하나라도 없거나 0 이하인 날은 뺀다. 날짜는 거래소 기준.
    분할 비율 = numerator / denominator (4:1 분할이면 4, 1:10 병합이면 0.1).
    """
    meta = payload.get("meta") or {}
    offset = int(meta.get("gmtoffset") or 0)
    stamps = payload.get("timestamp") or []
    quote = ((payload.get("indicators") or {}).get("quote") or [{}])[0]
    cols = [quote.get(k) or [] for k in ("open", "high", "low", "close", "volume")]

    def at(values, k):
        try:
            v = values[k]
        except (IndexError, TypeError):
            return None
        return None if v is None else float(v)

    def local(stamp) -> date:
        return datetime.fromtimestamp(int(stamp) + offset, tz=timezone.utc).date()

    rows = {}
    for k, stamp in enumerate(stamps):
        o, h, lo, c = (at(cols[j], k) for j in range(4))
        if None in (o, h, lo, c) or min(o, h, lo, c) <= 0:
            continue
        rows[local(stamp)] = (local(stamp), o, h, lo, c, at(cols[4], k))
    splits = []
    for item in ((payload.get("events") or {}).get("splits") or {}).values():
        try:
            num, den = float(item["numerator"]), float(item["denominator"])
            when = local(item["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if num > 0 and den > 0 and num != den:
            splits.append((when, num / den))
    return [rows[d] for d in sorted(rows)], sorted(splits)


class PriceDB:
    """일봉 저장소. 여러 스레드가 같이 써도 되게 연결 하나를 잠금으로 감싼다."""

    def __init__(self, path: str | Path, http=None) -> None:
        self.path = Path(path)
        self.http = http
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(
                "CREATE TABLE IF NOT EXISTS bars (sym TEXT, day INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL,"
                " PRIMARY KEY (sym, day)) WITHOUT ROWID;"
                "CREATE TABLE IF NOT EXISTS splits (sym TEXT, day INTEGER, ratio REAL, PRIMARY KEY (sym, day));"
                "CREATE TABLE IF NOT EXISTS meta (sym TEXT PRIMARY KEY, symbol TEXT, fetched REAL, failed REAL,"
                " first INTEGER, last INTEGER, note TEXT);")
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    # ------------------------------------------------------------------ 읽기
    def meta(self, ticker: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT symbol, fetched, failed, first, last, note FROM meta WHERE sym=?",
                                  (ticker,)).fetchone()
        if not row:
            return None
        return {"symbol": row[0], "fetched": row[1] or 0.0, "failed": row[2] or 0.0,
                "first": row[3], "last": row[4], "note": row[5] or ""}

    def load(self, tickers, since: date | None = None) -> dict[str, Bars]:
        """{티커: Bars} — 저장된 것만. since 를 주면 그 날부터."""
        start = since.toordinal() if since else 0
        out = {}
        with self.lock:
            for t in tickers:
                rows = self.db.execute("SELECT day, o, h, l, c FROM bars WHERE sym=? AND day>=? ORDER BY day",
                                       (t, start)).fetchall()
                if not rows:
                    continue
                out[t] = Bars([iso_of(r[0]) for r in rows], array("d", (r[1] for r in rows)),
                              array("d", (r[2] for r in rows)), array("d", (r[3] for r in rows)),
                              array("d", (r[4] for r in rows)))
        return out

    def splits(self, ticker: str) -> list[tuple[date, float]]:
        with self.lock:
            rows = self.db.execute("SELECT day, ratio FROM splits WHERE sym=? ORDER BY day", (ticker,)).fetchall()
        return [(date.fromordinal(d), r) for d, r in rows]

    def status(self, tickers) -> dict:
        """받아 둔 종목 수, 가장 최근 날, 못 받은 종목."""
        tickers = list(tickers)
        have, newest, failed = 0, 0, []
        for t in tickers:
            m = self.meta(t)
            if m and m["last"]:
                have += 1
                newest = max(newest, m["last"])
            elif m and m["failed"]:
                failed.append(t)
        return {"have": have, "total": len(tickers), "newest": iso_of(newest) if newest else "", "failed": failed}

    # ------------------------------------------------------------------ 쓰기
    def store(self, ticker: str, symbol: str, rows: list[tuple], splits: list[tuple], replace: bool) -> None:
        with self.lock:
            if replace:
                self.db.execute("DELETE FROM bars WHERE sym=?", (ticker,))
            self.db.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                                [(ticker, d.toordinal(), o, h, lo, c, v) for d, o, h, lo, c, v in rows])
            if replace:
                self.db.execute("DELETE FROM splits WHERE sym=?", (ticker,))
            self.db.executemany("INSERT OR REPLACE INTO splits VALUES (?,?,?)",
                                [(ticker, d.toordinal(), r) for d, r in splits])
            first, last = self.db.execute("SELECT MIN(day), MAX(day) FROM bars WHERE sym=?", (ticker,)).fetchone()
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?,?,?,?,?)",
                            (ticker, symbol, time.time(), 0.0, first, last, ""))
            self.db.commit()

    def mark_failed(self, ticker: str, note: str) -> None:
        with self.lock:
            old = self.meta(ticker) or {}
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?,?,?,?,?)",
                            (ticker, old.get("symbol") or "", old.get("fetched") or 0.0, time.time(),
                             old.get("first"), old.get("last"), note[:200]))
            self.db.commit()

    def _closes(self, ticker: str, start: int) -> dict[int, float]:
        with self.lock:
            return dict(self.db.execute("SELECT day, c FROM bars WHERE sym=? AND day>=?", (ticker, start)))

    # ------------------------------------------------------------------ 받기
    def update(self, tickers, market: str, today: date | None = None, progress=None,
               unfinished: date | None = None, expected: date | None = None) -> UpdateResult:
        """tickers 의 일봉을 최신으로.

        unfinished = 아직 끝나지 않은 날(장중 오늘) — 그 날 봉은 저장하지 않는다.
        expected = 끝난 마지막 거래일(어림) — 그 날 봉까지 이미 있으면 묻지 않는다.
        """
        tickers = list(dict.fromkeys(str(t).upper() for t in tickers if t))
        result = UpdateResult(asked=len(tickers))
        today = today or date.today()
        todo = []
        for t in tickers:
            m = self.meta(t)
            if m and m["last"]:
                caught_up = expected is not None and date.fromordinal(m["last"]) >= expected
                recent = time.time() - m["fetched"] < (RECHECK if expected is not None else FRESH_SECONDS)
                if caught_up or recent:
                    result.fresh += 1
                    continue
            elif m and m["failed"] and time.time() - m["failed"] < RETRY_FAILED:
                result.fresh += 1           # 못 받은 종목은 하루에 한 번만 다시 묻는다
                continue
            todo.append((t, m))
        if not todo or self.http is None:
            return result
        limits = 0
        done = 0
        lock = threading.Lock()

        def one(item):
            nonlocal limits, done
            t, m = item
            if limits >= STOP_AFTER_LIMITS:
                return
            outcome = self._update_one(t, m, market, today, unfinished)
            with lock:
                done += 1
                if outcome == "limited":
                    limits += 1
                    result.failed.append(t)
                elif outcome == "failed":
                    result.failed.append(t)
                else:
                    limits = 0
                    result.fetched += 1
                    if outcome == "refetched":
                        result.refetched += 1
                if progress:
                    progress(done - 1, len(todo), t)       # 화면은 '몇 번째(1부터)/전체' 로 보여준다

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(one, todo))
        result.limited = limits >= STOP_AFTER_LIMITS
        return result

    def _chart(self, symbol: str, span: str) -> dict | None | str:
        try:
            resp = self.http.get(CHART_URL.format(symbol=symbol, span=span), retries=2, timeout=30)
        except Exception as exc:                 # 네트워크·429 반복 등
            text = str(exc)
            log.debug("일봉 받기 실패 %s: %s", symbol, text)
            cause = getattr(exc, "__cause__", None)
            status = getattr(getattr(cause, "response", None), "status_code", None)
            return "limited" if status == 429 else None
        if resp.status_code == 429:
            return "limited"
        if resp.status_code != 200:
            return None
        try:
            results = ((resp.json().get("chart") or {}).get("result")) or []
        except ValueError:
            return None
        return results[0] if results else None

    def _symbols(self, ticker: str, market: str, m: dict | None) -> list[str]:
        if m and m.get("symbol"):
            return [m["symbol"]]
        return [yahoo_symbol(ticker)]

    def _update_one(self, ticker: str, m: dict | None, market: str, today: date,
                    unfinished: date | None) -> str:
        last = date.fromordinal(m["last"]) if m and m.get("last") else None
        gap = (today - last).days if last else None
        span = FULL if gap is None else ("1mo" if gap <= 25 else ("1y" if gap <= 330 else FULL))
        for symbol in self._symbols(ticker, market, m):
            payload = self._chart(symbol, span)
            if payload == "limited":
                return "limited"
            if not payload:
                continue
            rows, splits = parse_chart(payload)
            if unfinished:
                rows = [r for r in rows if r[0] < unfinished]
            if not rows:
                continue
            if span != FULL and last:
                # 겹치는 날의 종가가 다르면 그 사이 분할·조정이 생겼다 — 10년치를 다시 받는다
                saved = self._closes(ticker, rows[0][0].toordinal())
                moved = [r for r in rows if r[0].toordinal() in saved
                         and abs(r[4] / saved[r[0].toordinal()] - 1) > OVERLAP_TOLERANCE]
                if moved or not any(r[0].toordinal() in saved for r in rows):
                    full = self._chart(symbol, FULL)
                    if full == "limited":
                        return "limited"
                    if not full:
                        continue
                    rows, splits = parse_chart(full)
                    if unfinished:
                        rows = [r for r in rows if r[0] < unfinished]
                    if not rows:
                        continue
                    self.store(ticker, symbol, rows, splits, replace=True)
                    return "refetched"
            self.store(ticker, symbol, rows, splits, replace=span == FULL)
            return "ok"
        self.mark_failed(ticker, "야후에서 일봉을 받지 못함")
        return "failed"


def split_factor_after(splits: list[tuple[date, float]], when: date) -> float:
    """when 뒤에 있었던 분할 비율을 모두 곱한 값. 그 날 기준 주식 수 × 이 값 = 지금 가격 기준 주식 수."""
    factor = 1.0
    for day, ratio in splits:
        if day > when:
            factor *= ratio
    return factor
