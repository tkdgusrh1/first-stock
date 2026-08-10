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


def tz(name: str):
    """tzdata 가 없는 환경에서도 죽지 않도록 fallback 을 둔다."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            log.warning("시간대 '%s' 를 찾지 못했습니다. tzdata 설치를 권장합니다.", name)
    fallback = {ET: -4, "Asia/Seoul": 9, "UTC": 0}.get(name, 0)
    return timezone(timedelta(hours=fallback))


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


def kdate(day) -> str:
    """2026-08-10(월) 형태."""
    return f"{day.isoformat()}({_WEEKDAYS[day.weekday()]})"


def dday(today, target) -> str:
    delta = (target - today).days
    if delta == 0:
        return "D-DAY"
    return f"D-{delta}" if delta > 0 else f"D+{-delta}"
