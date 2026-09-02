"""미국 증시(NYSE/나스닥) 휴장일·조기폐장 계산. 외부 API 없이 규칙으로 산출한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

__all__ = [
    "MarketDay",
    "easter",
    "nyse_holidays",
    "nyse_early_closes",
    "upcoming_market_days",
    "is_trading_day",
]


@dataclass(frozen=True)
class MarketDay:
    day: date
    name: str
    early_close: bool = False   # True면 13:00 ET 조기 폐장

    @property
    def kind(self) -> str:
        return "조기폐장(13:00 ET)" if self.early_close else "휴장"


def easter(year: int) -> date:
    """그레고리력 부활절(Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1)


def good_friday(year: int) -> date:
    return easter(year) - timedelta(days=2)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """그 달의 n번째 특정 요일. weekday: 월=0 … 일=6."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date | None:
    """토요일이면 전날 금요일, 일요일이면 다음 월요일로 대체 휴장."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def nyse_holidays(year: int) -> list[MarketDay]:
    """해당 연도의 정규 휴장일."""
    out: list[MarketDay] = []

    # 신정: 토요일이면 (전년 12/31로 당기지 않고) 휴장 없음 — NYSE 규칙
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:
        out.append(MarketDay(_observed(new_year), "신정 (New Year's Day)"))

    out.append(MarketDay(_nth_weekday(year, 1, 0, 3), "마틴 루터 킹 데이"))
    out.append(MarketDay(_nth_weekday(year, 2, 0, 3), "대통령의 날 (Presidents' Day)"))
    out.append(MarketDay(good_friday(year), "성금요일 (Good Friday)"))
    out.append(MarketDay(_last_weekday(year, 5, 0), "메모리얼 데이"))

    if year >= 2022:  # 준틴스는 2022년부터 NYSE 휴장일
        out.append(MarketDay(_observed(date(year, 6, 19)), "준틴스 (Juneteenth)"))

    out.append(MarketDay(_observed(date(year, 7, 4)), "독립기념일"))
    out.append(MarketDay(_nth_weekday(year, 9, 0, 1), "노동절 (Labor Day)"))
    out.append(MarketDay(_nth_weekday(year, 11, 3, 4), "추수감사절"))
    out.append(MarketDay(_observed(date(year, 12, 25)), "크리스마스"))

    return sorted((d for d in out if d.day.year == year), key=lambda d: d.day)


def nyse_early_closes(year: int) -> list[MarketDay]:
    """13:00 ET 조기 폐장일."""
    out: list[MarketDay] = []
    holidays = {d.day for d in nyse_holidays(year)}

    # 독립기념일 전날(평일이고 그 자체가 휴장일이 아닐 때)
    july3 = date(year, 7, 3)
    if july3.weekday() < 5 and july3 not in holidays:
        out.append(MarketDay(july3, "독립기념일 전날", early_close=True))

    # 추수감사절 다음 날(블랙프라이데이)
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    out.append(MarketDay(thanksgiving + timedelta(days=1), "추수감사절 다음 날", early_close=True))

    # 크리스마스 이브(평일이고 휴장일이 아닐 때)
    dec24 = date(year, 12, 24)
    if dec24.weekday() < 5 and dec24 not in holidays:
        out.append(MarketDay(dec24, "크리스마스 이브", early_close=True))

    return sorted(out, key=lambda d: d.day)


def _calendar_for(years: set[int]) -> list[MarketDay]:
    days: list[MarketDay] = []
    for year in sorted(years):
        days.extend(nyse_holidays(year))
        days.extend(nyse_early_closes(year))
    return sorted(days, key=lambda d: d.day)


def is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in {d.day for d in nyse_holidays(day.year)}


def upcoming_market_days(today: date, days_ahead: int = 14) -> list[MarketDay]:
    """오늘부터 days_ahead일 안의 휴장/조기폐장 일정."""
    end = today + timedelta(days=days_ahead)
    years = {today.year, end.year}
    return [d for d in _calendar_for(years) if today <= d.day <= end]
