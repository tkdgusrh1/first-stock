"""경제 지표 — 날짜가 아니라 **숫자**.

일정(econ_calendar.py)은 "언제 나오나" 를 알려주고, 여기는 "지금 얼마인가" 를
알려준다. 물가·금리·고용은 개별 종목의 실적과 별개로 PER 자체를 눌렀다
풀었다 하는 배경이라, 종목 판단 앞에 한 번 보고 들어가는 값이다.

값은 세인트루이스 연준의 FRED 에서 받는다. 열쇠가 필요 없고, 미국 정부
통계기관(BLS·BEA·연준)이 발표한 원본을 그대로 싣는 곳이라 출처가 하나로
정리된다. 화면에는 항상 **기준 시점**을 같이 적는다 — 7월 CPI 를 8월에
보고 있다는 사실이 숫자보다 중요할 때가 있다.

숫자를 어떻게 읽을지는 지표마다 다르다. 그래서 세 가지 규칙만 둔다.
  yoy     지수(CPI·PCE) → 전년 같은 달 대비 몇 % 올랐나
  level   이미 % 인 값(금리·실업률) → 그대로
  change  수준값(고용자 수) → 전월 대비 얼마나 늘었나

받지 못하면 그 지표만 조용히 빠진다. 추정하거나 지어내지 않는다.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"
FRED_HOME = "https://fred.stlouisfed.org/"

# 전년 대비를 계산하려면 최소 13개월치가 필요하다. 넉넉히 3년을 받는다.
HISTORY_YEARS = 3


@dataclass(frozen=True)
class Spec:
    """지표 하나를 어떻게 받아서 어떻게 읽을지."""

    id: str                     # FRED 시리즈 코드
    label: str                  # 화면에 쓰는 이름
    rule: str                   # yoy | level | change
    unit: str                   # 값 뒤에 붙는 단위
    meaning: str                # 이 숫자가 주식에 무슨 뜻인지 (한 줄)
    group: str = "물가"          # 브리핑에서 한 줄로 묶는 단위
    monthly: bool = True        # 월간 지표인가 (아니면 매일 나오는 값)
    digits: int = 1
    scale: float = 1.0          # 원자료 단위를 화면 단위로 바꿀 때
    bands: tuple[tuple[float, str], ...] = ()   # (이 값 미만이면, 이렇게 읽는다)
    lower_is_easier: bool | None = None         # 낮아지는 쪽이 주식에 편한 값인가

    def read(self, value: float) -> str:
        for edge, text in self.bands:
            if value < edge:
                return text
        return ""


# 개인 투자자가 실제로 쓰는 것만 여덟 개. 더 늘리면 화면이 지표판이 된다.
SERIES: list[Spec] = [
    Spec(
        "CPIAUCSL", "소비자물가 CPI", "yoy", "%",
        "물가가 1년 전보다 얼마나 올랐나. 높으면 금리 인하가 멀어진다.",
        bands=((2.0, "연준 목표 2% 아래"), (3.0, "목표에 근접"),
               (4.0, "목표보다 높음"), (float("inf"), "높은 물가")),
        lower_is_easier=True,
    ),
    Spec(
        "CPILFESL", "근원 CPI", "yoy", "%",
        "식품·에너지처럼 출렁이는 항목을 뺀 물가의 몸통. 추세는 이쪽으로 본다.",
        bands=((2.0, "연준 목표 2% 아래"), (3.0, "목표에 근접"),
               (4.0, "목표보다 높음"), (float("inf"), "높은 물가")),
        lower_is_easier=True,
    ),
    Spec(
        "PCEPILFE", "근원 PCE", "yoy", "%",
        "연준이 목표 2% 를 재는 바로 그 지표. CPI 보다 낮게 나오는 편이다.",
        bands=((2.0, "목표 달성 구간"), (2.5, "목표에 근접"),
               (3.0, "목표보다 높음"), (float("inf"), "높은 물가")),
        lower_is_easier=True,
    ),
    Spec(
        "DFEDTARU", "기준금리", "level", "%",
        "연준이 정한 목표 범위의 상단. 돈의 값이 오르면 주식에 요구되는 수익률도 오른다.",
        group="금리",
        monthly=False, digits=2, lower_is_easier=True,
    ),
    Spec(
        "DGS10", "10년물 국채금리", "level", "%",
        "밸류에이션의 기준선. 오르면 먼 미래 이익이 깎여 성장주 PER 이 눌린다.",
        group="금리",
        monthly=False, digits=2,
        bands=((4.0, "부담이 크지 않은 구간"), (4.5, "성장주에 부담되는 구간"),
               (float("inf"), "밸류에이션을 세게 누르는 구간")),
        lower_is_easier=True,
    ),
    Spec(
        "T10Y2Y", "장단기 금리차", "level", "%p",
        "10년물에서 2년물을 뺀 값. 마이너스면 '금리 역전' — 과거 침체 앞에서 반복된 신호다.",
        group="금리",
        monthly=False, digits=2,
        bands=((0.0, "금리 역전 — 침체 경고 신호"), (0.5, "역전에서 막 벗어난 구간"),
               (float("inf"), "정상")),
        lower_is_easier=False,
    ),
    Spec(
        "UNRATE", "실업률", "level", "%",
        "고용이 식으면 소비도 식는다. 반대로 너무 뜨거우면 금리가 안 내려온다.",
        group="고용",
        bands=((4.0, "완전고용에 가까움"), (5.0, "느슨해지는 중"),
               (float("inf"), "고용 둔화")),
    ),
    Spec(
        "PAYEMS", "비농업 고용", "change", "만 명",
        "한 달 동안 늘어난 일자리. 10만 명 언저리를 경기 판단의 눈금으로 본다.",
        group="고용",
        scale=0.1,      # 원자료는 천 명 단위
        bands=((0.0, "일자리가 줄었다"), (10.0, "증가 폭이 약하다"),
               (float("inf"), "견조한 증가")),
    ),
]

BY_ID = {spec.id: spec for spec in SERIES}


@dataclass(frozen=True)
class Reading:
    """지표 하나의 최신 값."""

    spec: Spec
    value: float
    previous: float | None
    as_of: date

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def text(self) -> str:
        sign = "+" if self.spec.rule == "change" and self.value > 0 else ""
        return f"{sign}{self.value:,.{self.spec.digits}f}{self.spec.unit}"

    @property
    def change(self) -> float | None:
        """직전 값과의 차이. 전년비끼리, 레벨끼리 비교한 것이라 단위는 같다."""
        if self.previous is None:
            return None
        return self.value - self.previous

    @property
    def change_text(self) -> str:
        gap = self.change
        if gap is None:
            return ""
        unit = "%p" if self.spec.unit == "%" else self.spec.unit
        return f"{gap:+,.{self.spec.digits}f}{unit}"

    @property
    def direction(self) -> str:
        gap = self.change
        if gap is None or abs(gap) < 10 ** -(self.spec.digits + 1):
            return ""
        return "up" if gap > 0 else "down"

    @property
    def tone(self) -> str:
        """주식 입장에서 편해지는 방향인지. 판단이 갈리는 지표는 비워 둔다."""
        if self.spec.lower_is_easier is None or not self.direction:
            return ""
        easier = (self.direction == "down") == self.spec.lower_is_easier
        return "good" if easier else "bad"

    @property
    def when(self) -> str:
        if self.spec.monthly:
            return f"{self.as_of.year}년 {self.as_of.month}월분"
        return f"{self.as_of.month}월 {self.as_of.day}일 기준"

    @property
    def note(self) -> str:
        return self.spec.read(self.value)


@dataclass
class MacroSnapshot:
    readings: list[Reading]
    fetched_at: datetime

    @property
    def empty(self) -> bool:
        return not self.readings

    def get(self, series_id: str) -> Reading | None:
        return next((r for r in self.readings if r.spec.id == series_id), None)


# --- 받아서 읽기 -----------------------------------------------------------
def parse_csv(text: str) -> list[tuple[date, float]]:
    """FRED CSV → (날짜, 값). 결측치('.')와 머리글은 버린다.

    머리글 이름이 DATE 였다가 observation_date 로 바뀐 적이 있어서
    이름으로 찾지 않고 '첫 칸이 날짜로 읽히는 줄' 만 값으로 본다.
    """
    out: list[tuple[date, float]] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        try:
            day = date.fromisoformat(row[0].strip())
            value = float(row[1].strip())
        except ValueError:
            continue
        out.append((day, value))
    out.sort()
    return out


def _year_earlier(points: list[tuple[date, float]], index: int) -> float | None:
    """1년 전 같은 달의 값. 월간 지표는 매월 1일자로 들어온다."""
    day = points[index][0]
    try:
        target = day.replace(year=day.year - 1)
    except ValueError:                       # 2월 29일
        target = day.replace(year=day.year - 1, day=28)
    for found_day, value in points:
        if found_day == target:
            return value
    if index >= 12:                          # 날짜가 안 맞으면 12개 앞으로
        return points[index - 12][1]
    return None


def to_reading(spec: Spec, points: list[tuple[date, float]]) -> Reading | None:
    """받아온 시계열에서 화면에 쓸 값 하나를 뽑는다."""
    if not points:
        return None
    last = len(points) - 1

    if spec.rule == "yoy":
        def at(index: int) -> float | None:
            base = _year_earlier(points, index)
            if not base:                     # 0 이나 None 이면 나눌 수 없다
                return None
            return (points[index][1] / base - 1) * 100

        value = at(last)
        if value is None:
            return None
        previous = at(last - 1) if last >= 1 else None
    elif spec.rule == "change":
        if last < 1:
            return None
        value = (points[last][1] - points[last - 1][1]) * spec.scale
        previous = (points[last - 1][1] - points[last - 2][1]) * spec.scale if last >= 2 else None
    else:                                    # level
        value = points[last][1] * spec.scale
        previous = points[last - 1][1] * spec.scale if last >= 1 else None

    return Reading(spec=spec, value=value, previous=previous, as_of=points[last][0])


class MacroClient:
    """경제 지표를 느슨한 주기로 받아둔다.

    CPI 는 한 달에 한 번, 금리는 하루에 한 번 바뀐다. 자주 부를 이유가
    없어서 기본 6시간이고, 받은 값은 디스크에도 남겨 프로그램을 다시 켜도
    화면이 비지 않게 한다. 대시보드는 **캐시만 읽는다** — 그리는 중에
    네트워크를 기다리면 화면이 멈춘다.
    """

    def __init__(self, http, cache_dir: str | Path = ".cache", ttl: float = 6 * 3600) -> None:
        self.http = http
        self.ttl = ttl
        self.path = Path(cache_dir) / "macro.json"
        self._lock = threading.Lock()
        self._snapshot: MacroSnapshot | None = self._load()

    # 화면이 부르는 쪽 -------------------------------------------------
    def cached(self) -> MacroSnapshot | None:
        return self._snapshot

    def stale(self) -> bool:
        snap = self._snapshot
        if snap is None:
            return True
        return (datetime.now(timezone.utc) - snap.fetched_at).total_seconds() > self.ttl

    # 백그라운드가 부르는 쪽 ---------------------------------------------
    def refresh(self, force: bool = False) -> MacroSnapshot | None:
        if not force and not self.stale():
            return self._snapshot
        if not self._lock.acquire(blocking=False):
            return self._snapshot            # 이미 다른 스레드가 받는 중
        try:
            start = date(datetime.now(timezone.utc).year - HISTORY_YEARS, 1, 1)
            readings: list[Reading] = []
            for spec in SERIES:
                points = self._series(spec, start)
                if points is None:
                    continue
                reading = to_reading(spec, points)
                if reading:
                    readings.append(reading)

            if readings:
                self._snapshot = MacroSnapshot(
                    readings=readings, fetched_at=datetime.now(timezone.utc)
                )
                self._save()
            elif self._snapshot is None:
                log.info("경제 지표를 한 건도 받지 못했습니다.")
            return self._snapshot
        finally:
            self._lock.release()

    def _series(self, spec: Spec, start: date) -> list[tuple[date, float]] | None:
        url = FRED_CSV.format(series=spec.id, start=start.isoformat())
        try:
            text = self.http.get_text(url, timeout=20, retries=1)
        except Exception as exc:
            log.debug("경제 지표 조회 실패 %s: %s", spec.id, exc)
            return None
        points = parse_csv(text)
        if not points:
            log.debug("경제 지표 응답을 읽지 못했습니다 %s", spec.id)
            return None
        return points

    # 디스크에 남기기 -----------------------------------------------------
    def _save(self) -> None:
        snap = self._snapshot
        if snap is None:
            return
        payload = {
            "fetched_at": snap.fetched_at.isoformat(),
            "readings": [
                {"id": r.spec.id, "value": r.value, "previous": r.previous,
                 "as_of": r.as_of.isoformat()}
                for r in snap.readings
            ],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            log.debug("경제 지표 저장 실패: %s", exc)

    def _load(self) -> MacroSnapshot | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        readings = []
        for item in payload.get("readings") or []:
            spec = BY_ID.get(item.get("id"))
            if not spec:
                continue                     # 목록에서 빠진 지표는 조용히 버린다
            try:
                readings.append(
                    Reading(spec=spec, value=float(item["value"]),
                            previous=_maybe_float(item.get("previous")),
                            as_of=date.fromisoformat(item["as_of"]))
                )
            except (KeyError, TypeError, ValueError):
                continue
        if not readings:
            return None
        try:
            fetched = datetime.fromisoformat(payload["fetched_at"])
        except (KeyError, TypeError, ValueError):
            return None
        return MacroSnapshot(readings=readings, fetched_at=fetched)


def grouped(snapshot: MacroSnapshot) -> list[tuple[str, list[Reading]]]:
    """물가·금리·고용으로 묶는다. 브리핑에서 한 줄씩 쓰려고."""
    out: list[tuple[str, list[Reading]]] = []
    for name in ("물가", "금리", "고용"):
        found = [r for r in snapshot.readings if r.spec.group == name]
        if found:
            out.append((name, found))
    return out


def _maybe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
