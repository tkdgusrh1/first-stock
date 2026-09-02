"""시간대 처리. SEC 접수시각(미 동부)을 사용자 시간대로 바꾼다."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

log = logging.getLogger(__name__)

ET = "America/New_York"
_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


_TZ_CACHE: dict[str, object] = {}


def tz(name: str):
    """tzdata 가 없는 환경(주로 윈도우)에서도 죽지 않도록 fallback 을 둔다."""
    if name in _TZ_CACHE:
        return _TZ_CACHE[name]

    zone = None
    if ZoneInfo is not None:
        try:
            zone = ZoneInfo(name)
        except Exception:
            # 매번 경고를 찍으면 로그가 도배된다. 시간대당 한 번만 알린다.
            log.warning(
                "시간대 '%s' 를 찾지 못해 고정 시차로 대체합니다. "
                "정확한 시각을 원하면 `pip install tzdata` 를 실행하세요.",
                name,
            )
    if zone is None:
        fallback = {ET: -4, "America/New_York": -4, "Asia/Seoul": 9, "UTC": 0}.get(name, 0)
        zone = timezone(timedelta(hours=fallback))

    _TZ_CACHE[name] = zone
    return zone


def now(tz_name: str) -> datetime:
    return datetime.now(tz(tz_name))


def parse_sec_datetime(raw: str | None, target_tz: str) -> datetime | None:
    """EDGAR acceptanceDateTime 은 미 동부 기준이다. 사용자 시간대로 변환."""
    if not raw:
        return None
    text = raw.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=tz(ET)).astimezone(tz(target_tz))
    return None


def to_tz(moment: datetime | None, tz_name: str = "Asia/Seoul") -> datetime | None:
    """어느 시간대의 시각이든 원하는 시간대로 바꾼다. (표준시 정보가 없으면 UTC 로 본다)"""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz(tz_name))


def clock(moment: datetime | None, tz_name: str = "Asia/Seoul") -> str:
    """08-12 21:34 형태 (기본 한국시간)."""
    local = to_tz(moment, tz_name)
    return local.strftime("%m-%d %H:%M") if local else ""


def ago(moment: datetime | None, reference: datetime | None = None) -> str:
    """'3분 전' 처럼 얼마나 지났는지. 속보는 시간이 곧 가치라서 크게 보여준다."""
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    reference = reference or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    seconds = (reference - moment).total_seconds()
    if seconds < 0:
        return "방금"
    minutes = int(seconds // 60)
    if minutes < 1:
        return "방금"
    if minutes < 60:
        return f"{minutes}분 전"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}시간 전"
    days = hours // 24
    return f"{days}일 전"


def kdate(day) -> str:
    """2026-08-10(월) 형태."""
    return f"{day.isoformat()}({_WEEKDAYS[day.weekday()]})"


def dday(today, target) -> str:
    delta = (target - today).days
    if delta == 0:
        return "D-DAY"
    return f"D-{delta}" if delta > 0 else f"D+{-delta}"
