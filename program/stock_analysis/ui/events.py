"""일정 한 곳 — 실적 발표 · 경제지표 · 휴장일. 홈과 캘린더가 같이 쓴다.

**시각은 화면 시간대(설정의 timezone)로 옮겨 적는다.** 미국 경제지표는 미 동부
08:30 에 나오는데, 한국에서는 같은 날 21:30(서머타임이면 21:30, 아니면 22:30)이다.
옮기면서 날짜가 넘어가면(밤 20시 ET → 다음 날 아침) 옮겨진 날짜에 둔다.

실적 발표와 휴장일은 **미국 날짜 그대로**다. 발표 시각을 SEC 자료로는 알 수
없어서 옮길 수가 없다. 화면에 그렇게 밝힌다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .. import markets
from ..econ_calendar import parse_extra_events, upcoming_events
from ..market_calendar import upcoming_market_days
from ..timeutil import ET, tz

EARN, ECON, HOLIDAY = "earn", "econ", "hol"


@dataclass
class Event:
    day: date
    kind: str                   # earn · econ · hol
    name: str
    time: str = ""              # 화면 시간대의 HH:MM, 모르면 ""
    importance: int = 2
    estimated: bool = False
    note: str = ""
    ticker: str = ""
    market: str = markets.US

    @property
    def sort_key(self):
        return (self.day, self.time or "99:99", -self.importance, self.name)


def _to_local(day: date, time_et: str | None, zone: str) -> tuple[date, str]:
    """미 동부 날짜·시각 → 화면 시간대. 시각이 'HH:MM' 이 아니면 날짜만 둔다."""
    text = str(time_et or "").strip()
    try:
        hour, minute = (int(x) for x in text.split(":"))
    except ValueError:
        return day, ""
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz(ET)).astimezone(tz(zone))
    return moment.date(), moment.strftime("%H:%M")


def collect(bot, start: date, end: date, targets=None) -> list[Event]:
    """start~end(포함) 사이 일정. 받아둔 것만 쓴다(네트워크 없음)."""
    config = bot.config
    zone = config.timezone
    out: list[Event] = []

    # 경제지표 — 앞뒤 하루 넉넉히 받아 옮긴 뒤 범위로 자른다(날짜가 넘어가므로)
    econ = upcoming_events(
        start - timedelta(days=1), (end - start).days + 2,
        min_importance=int(config.raw.get("econ_min_importance", 2)),
        extra=parse_extra_events(config.raw.get("econ_extra_events")),
        include_weekly=bool(config.raw.get("econ_include_weekly", False)),
    )
    for event in econ:
        if "실적" in (event.tags or ()):
            continue
        day, time = _to_local(event.day, event.time_et, zone)
        if start <= day <= end:
            out.append(Event(day=day, kind=ECON, name=event.name, time=time,
                             importance=event.importance, estimated=event.estimated,
                             note=event.note or ""))

    # 실적 발표 — 감시 중인 종목만
    earnings = bot.cached_earnings()
    for target in targets if targets is not None else bot.cached_targets():
        info = earnings.get(target.cik)
        if info and start <= info.day <= end:
            name = target.watch.name or target.name or ""
            out.append(Event(day=info.day, kind=EARN,
                             name=f"{markets.display(target.ticker)} 실적 발표",
                             importance=3, estimated=info.estimated,
                             note=(name + (" · 과거 발표 간격으로 추정" if info.estimated else " · 확정"))
                             .strip(" ·"),
                             ticker=target.ticker, market=target.market))

    # 미국 휴장·조기폐장
    for holiday in upcoming_market_days(start, (end - start).days):
        if start <= holiday.day <= end:
            out.append(Event(day=holiday.day, kind=HOLIDAY, name=f"미국 {holiday.kind}",
                             note=holiday.name, importance=1))

    return sorted(out, key=lambda e: e.sort_key)


__all__ = ["EARN", "ECON", "HOLIDAY", "Event", "collect"]
