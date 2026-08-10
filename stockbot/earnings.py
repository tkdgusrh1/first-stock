"""실적 발표일 추적.

메모의 1·2순위(가이던스·어닝 서프라이즈)가 실제로 결정되는 날이라
따로 챙긴다. 확정일을 모르면 과거 실적 발표(8-K 항목 2.02) 간격으로 추정한다.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from .econ_calendar import EconEvent
from .edgar import EdgarClient

log = logging.getLogger(__name__)

EARNINGS_ITEM = "2.02"
_MIN_HISTORY = 3            # 추정하려면 과거 발표가 최소 3번은 있어야 한다
_MIN_GAP, _MAX_GAP = 60, 120  # 분기 실적으로 인정할 간격(일)


@dataclass
class Earnings:
    ticker: str
    day: date
    estimated: bool
    history: list[date]
    note: str | None = None

    def to_event(self, name: str | None = None) -> EconEvent:
        label = f"{self.ticker} 실적 발표"
        if name:
            label += f" ({name})"
        return EconEvent(
            day=self.day,
            name=label,
            time_et=None if self.estimated else "장 마감 후",
            importance=3,
            estimated=self.estimated,
            note=self.note or ("과거 발표 간격으로 추정" if self.estimated else None),
            tags=("실적", self.ticker),
        )


def past_earnings_dates(edgar: EdgarClient, cik: str, ticker: str, years: int = 3) -> list[date]:
    """과거 실적 발표일(8-K 2.02 제출일)을 오래된 순으로."""
    since = date.today() - timedelta(days=365 * years)
    try:
        filings = edgar.recent_filings(cik, ticker, ["8-K"], since, limit=300)
    except Exception as exc:
        log.warning("과거 실적 공시 조회 실패 %s: %s", ticker, exc)
        return []

    days: set[date] = set()
    for filing in filings:
        if EARNINGS_ITEM not in filing.items:
            continue
        try:
            days.add(date.fromisoformat(filing.filing_date))
        except ValueError:
            continue
    return sorted(days)


def estimate_next(history: list[date], today: date | None = None) -> date | None:
    """과거 발표 간격의 중앙값으로 다음 발표일을 추정한다."""
    today = today or date.today()
    if len(history) < _MIN_HISTORY:
        return None

    gaps = [
        (b - a).days
        for a, b in zip(history, history[1:])
        if _MIN_GAP <= (b - a).days <= _MAX_GAP
    ]
    if len(gaps) < _MIN_HISTORY - 1:
        return None

    gap = int(statistics.median(gaps))
    nxt = history[-1] + timedelta(days=gap)
    # 마지막 발표가 오래됐으면 오늘 이후가 될 때까지 한 분기씩 민다
    guard = 0
    while nxt < today and guard < 8:
        nxt += timedelta(days=gap)
        guard += 1
    if nxt < today:
        return None
    # 실적은 보통 평일에 발표한다
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def next_earnings(
    edgar: EdgarClient,
    cik: str,
    ticker: str,
    explicit: date | None = None,
    today: date | None = None,
) -> Earnings | None:
    """확정일이 있으면 그것을, 없으면 과거 간격으로 추정한 날짜를 돌려준다."""
    today = today or date.today()
    if explicit and explicit >= today:
        return Earnings(ticker=ticker, day=explicit, estimated=False, history=[], note="직접 지정")

    history = past_earnings_dates(edgar, cik, ticker)
    estimated = estimate_next(history, today)
    if estimated is None:
        return None
    return Earnings(ticker=ticker, day=estimated, estimated=True, history=history)


def due_reminders(earnings: Earnings, today: date, offsets: list[int]) -> int | None:
    """오늘이 알림 보낼 날(D-7, D-1, D-DAY 등)이면 그 offset 을 돌려준다."""
    delta = (earnings.day - today).days
    return delta if delta in set(offsets) else None
