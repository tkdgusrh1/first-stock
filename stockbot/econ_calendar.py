"""미국 주요 경제지표 일정.

무료·무인증으로 쓸 수 있는 확정 일정 API가 없어서 두 가지를 합친다.
  1) 확정 일정: data/fomc.yml (연준 공식 일정) + config 의 econ_extra_events
  2) 규칙 기반 추정: 발표 기관들이 지키는 관례(첫째 금요일 고용보고서 등)

추정치는 estimated=True 로 표시되고 알림에도 '추정'이라고 붙는다.
정확한 시각이 필요하면 config 에 직접 이벤트를 넣으면 그 값이 우선한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 중요도: 3=최상(시장 전체를 움직임), 2=상, 1=중
@dataclass(frozen=True)
class EconEvent:
    day: date
    name: str
    time_et: str | None = None
    importance: int = 2
    estimated: bool = False
    note: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


def _business_days(year: int, month: int) -> list[date]:
    day = date(year, month, 1)
    out = []
    while day.month == month:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _nearest_business_day(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _month_range(start: date, end: date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        month += 1
        if month > 12:
            year, month = year + 1, 1


def load_fomc(path: Path | None = None) -> list[EconEvent]:
    """FOMC 회의 + 그로부터 파생되는 의사록·베이지북 일정."""
    path = path or (DATA_DIR / "fomc.yml")
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    events: list[EconEvent] = []
    for meetings in raw.values():
        for meeting in meetings or []:
            days = [d for d in meeting if isinstance(d, date)]
            if not days:
                continue
            decision_day = max(days)
            has_sep = any(str(x).lower() == "sep" for x in meeting)
            events.append(
                EconEvent(
                    day=decision_day,
                    name="FOMC 금리 결정" + (" + 경제전망(점도표)" if has_sep else ""),
                    time_et="14:00",
                    importance=3,
                    note="발표 30분 뒤 의장 기자회견",
                    tags=("금리", "매크로"),
                )
            )
            # 의사록은 회의 3주 뒤 14:00 ET 공개 (연준 관례)
            events.append(
                EconEvent(
                    day=decision_day + timedelta(days=21),
                    name="FOMC 의사록 공개",
                    time_et="14:00",
                    importance=2,
                    estimated=True,
                    note="회의 3주 뒤 관례",
                    tags=("금리",),
                )
            )
            # 베이지북은 회의 2주 전 수요일 14:00 ET
            beige = decision_day - timedelta(days=14)
            beige -= timedelta(days=(beige.weekday() - 2) % 7)
            events.append(
                EconEvent(
                    day=beige,
                    name="베이지북 (연준 경기평가)",
                    time_et="14:00",
                    importance=1,
                    estimated=True,
                    tags=("금리",),
                )
            )
    return events


def fomc_coverage_end(path: Path | None = None) -> date | None:
    """FOMC 데이터가 어디까지 채워져 있는지. 만료되면 브리핑에서 알려준다."""
    events = [e for e in load_fomc(path) if e.name.startswith("FOMC 금리")]
    return max((e.day for e in events), default=None)


def _monthly_estimates(year: int, month: int) -> list[EconEvent]:
    """관례에 기반한 월간 지표 추정 일정."""
    bdays = _business_days(year, month)
    out: list[EconEvent] = []

    # 고용보고서(비농업 고용): 매월 첫째 금요일 08:30 ET
    out.append(
        EconEvent(
            _nth_weekday(year, month, 4, 1),
            "고용보고서 (비농업 고용·실업률·시간당 임금)",
            "08:30",
            importance=3,
            estimated=True,
            tags=("고용", "매크로"),
        )
    )
    # ISM 제조업 PMI: 첫 영업일 / 서비스업 PMI: 셋째 영업일 10:00 ET
    if bdays:
        out.append(EconEvent(bdays[0], "ISM 제조업 PMI", "10:00", 2, True, tags=("경기",)))
    if len(bdays) >= 3:
        out.append(EconEvent(bdays[2], "ISM 서비스업 PMI", "10:00", 2, True, tags=("경기",)))

    # CPI: 통상 10~15일 사이 평일 / PPI: 그 전후
    cpi = _nearest_business_day(date(year, month, 12))
    out.append(EconEvent(cpi, "소비자물가 CPI", "08:30", 3, True, tags=("물가", "매크로")))
    out.append(
        EconEvent(_nearest_business_day(cpi + timedelta(days=1)), "생산자물가 PPI", "08:30", 2, True, tags=("물가",))
    )
    # 소매판매: 15일 전후
    out.append(
        EconEvent(_nearest_business_day(date(year, month, 15)), "소매판매", "08:30", 2, True, tags=("소비",))
    )
    # PCE 물가(연준이 보는 지표): 월말 근처
    pce_day = bdays[-3] if len(bdays) >= 3 else bdays[-1]
    out.append(EconEvent(pce_day, "PCE 물가지수 (연준 선호 지표)", "08:30", 3, True, tags=("물가", "매크로")))

    # ADP 민간고용: 고용보고서 이틀 전 수요일
    nfp = _nth_weekday(year, month, 4, 1)
    out.append(EconEvent(nfp - timedelta(days=2), "ADP 민간 고용", "08:15", 1, True, tags=("고용",)))

    # JOLTS 구인·이직 보고서: 대략 7번째 영업일
    if len(bdays) >= 7:
        out.append(EconEvent(bdays[6], "JOLTS 구인건수", "10:00", 1, True, tags=("고용",)))

    # 미시간대 소비자심리 예비치: 둘째 금요일
    out.append(
        EconEvent(_nth_weekday(year, month, 4, 2), "미시간대 소비자심리 (예비치)", "10:00", 1, True, tags=("소비",))
    )

    # 분기 GDP: 1·4·7·10월 말
    if month in (1, 4, 7, 10) and len(bdays) >= 2:
        out.append(EconEvent(bdays[-2], "분기 GDP 속보치", "08:30", 2, True, tags=("성장",)))

    # 어닝시즌 개막(대형은행 실적): 1·4·7·10월 둘째 금요일 무렵
    if month in (1, 4, 7, 10):
        out.append(
            EconEvent(
                _nth_weekday(year, month, 4, 2),
                "어닝시즌 개막 (대형은행 실적)",
                "07:00",
                1,
                True,
                note="이후 3~4주가 실적 발표 집중 구간",
                tags=("실적",),
            )
        )

    third_friday = _nth_weekday(year, month, 4, 3)
    if month in (3, 6, 9, 12):
        # 쿼드러플 위칭: 선물·옵션 동시 만기
        out.append(
            EconEvent(
                third_friday,
                "쿼드러플 위칭 (선물·옵션 동시 만기)",
                "16:00",
                2,
                note="변동성·거래량 급증 구간",
                tags=("수급",),
            )
        )
    else:
        out.append(EconEvent(third_friday, "월간 옵션 만기", "16:00", 1, tags=("수급",)))

    # 잭슨홀 심포지엄: 8월 마지막 주 목~토 (의장 연설이 시장을 흔든다)
    if month == 8:
        out.append(
            EconEvent(
                _last_weekday_of_month(year, 8, 3),
                "잭슨홀 심포지엄 (연준 의장 연설)",
                "10:00",
                2,
                True,
                note="통화정책 방향 힌트가 나오는 자리",
                tags=("금리", "매크로"),
            )
        )
    return out


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _weekly_estimates(start: date, end: date) -> list[EconEvent]:
    """주간 신규 실업수당 청구건수: 매주 목요일 08:30 ET."""
    out = []
    day = start + timedelta(days=(3 - start.weekday()) % 7)
    while day <= end:
        out.append(EconEvent(day, "주간 신규 실업수당 청구건수", "08:30", 1, True, tags=("고용",)))
        day += timedelta(days=7)
    return out


def parse_extra_events(items: list[dict] | None) -> list[EconEvent]:
    """config 의 econ_extra_events 를 파싱. 같은 날 같은 이름이면 추정치를 덮어쓴다."""
    out: list[EconEvent] = []
    for item in items or []:
        day = item.get("date")
        if isinstance(day, str):
            day = date.fromisoformat(day)
        if not isinstance(day, date):
            continue
        out.append(
            EconEvent(
                day=day,
                name=str(item.get("name", "사용자 지정 일정")),
                time_et=item.get("time_et"),
                importance=int(item.get("importance", 2)),
                estimated=False,
                note=item.get("note"),
                tags=tuple(item.get("tags") or ()),
            )
        )
    return out


def _event_key(name: str) -> str:
    """'소비자물가 CPI (7월)' → '소비자물가 cpi' 처럼 비교용 키로 정규화."""
    return name.split("(")[0].strip().lower()


def upcoming_events(
    today: date,
    days_ahead: int = 7,
    min_importance: int = 1,
    extra: list[EconEvent] | None = None,
    include_weekly: bool = True,
) -> list[EconEvent]:
    end = today + timedelta(days=days_ahead)

    events: list[EconEvent] = list(load_fomc())
    for year, month in _month_range(today, end):
        events.extend(_monthly_estimates(year, month))
    if include_weekly:
        events.extend(_weekly_estimates(today, end))
    events.extend(extra or [])

    # 사용자가 확정 일정을 넣었으면 같은 달의 같은 지표 추정치는 버린다.
    # ('소비자물가 CPI (7월)' 처럼 괄호로 기간을 덧붙여도 같은 지표로 인식)
    confirmed = {(e.day.year, e.day.month, _event_key(e.name)) for e in events if not e.estimated}
    deduped: dict[tuple[date, str], EconEvent] = {}
    for event in events:
        if event.estimated and (event.day.year, event.day.month, _event_key(event.name)) in confirmed:
            continue
        if not (today <= event.day <= end) or event.importance < min_importance:
            continue
        deduped[(event.day, event.name)] = event

    return sorted(deduped.values(), key=lambda e: (e.day, -e.importance, e.name))
