"""회사가 과거에 한 약속을 지켰는가.

메모의 1순위는 가이던스다. 그런데 메모에는 이런 단서가 붙어 있다.

    가이던스는 회사가 '관리' 할 수 있다 — 낮게 부르기, 정의 바꾸기,
    강조점 옮기기. 그래서 현금흐름표와 **과거 이행 이력** 을 확인해야 한다.

그 '과거 이행 이력' 을 자동으로 만드는 곳이다. 방법은 단순하다.

    1) 과거 실적 발표(8-K 2.02)에서 회사가 제시한 매출 범위를 찾는다
    2) 그 범위가 가리키는 분기를 정한다
    3) 그 분기의 **SEC 에 제출된 실제 매출** 을 XBRL 에서 꺼내 맞춰본다

지어낸 판정을 하지 않기 위해 다음은 전부 '확인 불가' 로 남긴다.
  · 어느 분기를 말하는지 문장에서 못 읽은 경우
  · 실제 실적이 아직 나오지 않은 경우
  · 조정 EPS 처럼 회계 기준 숫자와 직접 비교할 수 없는 항목
비교한 경우에도 회사가 쓴 문장을 원문 그대로 함께 보여준다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

# 금액 표기는 화면 어디서나 같아야 한다. 한 곳에서 가져다 쓴다.
from .metrics import _money

log = logging.getLogger(__name__)

BEAT, MET, MISS, UNKNOWN = "상회", "충족", "미달", "확인 불가"
VERDICT_ICON = {BEAT: "✅", MET: "🟢", MISS: "❌", UNKNOWN: "❔"}

FULL_YEAR = re.compile(r"\b(full[- ]year|fiscal year|for the year|annual)\b", re.I)

# 매출 가이던스가 이 금액보다 작으면 주당 금액이거나 단위가 다른 것이다.
MIN_REVENUE_SCALE = 1e5


@dataclass
class TrackItem:
    filed: str                       # 가이던스를 발표한 날
    url: str                         # 그 8-K 원문
    sentence: str                    # 회사가 쓴 문장 (원문 그대로)
    metric: str | None = None
    period_text: str | None = None   # 문장에서 읽은 기간 표현
    low: float | None = None
    high: float | None = None
    annual: bool = False
    target_end: date | None = None   # 비교한 분기(또는 연도) 종료일
    actual: float | None = None
    verdict: str = UNKNOWN
    reason: str = ""                 # 왜 이렇게 판정했는지 / 왜 못 했는지

    @property
    def icon(self) -> str:
        return VERDICT_ICON.get(self.verdict, "❔")

    @property
    def promised_text(self) -> str:
        if self.low is None:
            return "-"
        if self.high and self.high != self.low:
            return f"{_money(self.low)} ~ {_money(self.high)}"
        return _money(self.low)

    @property
    def actual_text(self) -> str:
        return _money(self.actual) if self.actual is not None else "-"

    @property
    def gap_pct(self) -> float | None:
        """약속 상단 대비 실제가 몇 % 인지. 미달 폭을 보기 위한 값."""
        if self.actual is None or not self.high:
            return None
        return (self.actual - self.high) / self.high * 100


@dataclass
class TrackRecord:
    ticker: str
    items: list[TrackItem] = field(default_factory=list)

    @property
    def judged(self) -> list[TrackItem]:
        return [i for i in self.items if i.verdict != UNKNOWN]

    @property
    def kept(self) -> int:
        return sum(1 for i in self.judged if i.verdict in (BEAT, MET))

    @property
    def missed(self) -> int:
        return sum(1 for i in self.judged if i.verdict == MISS)

    @property
    def summary(self) -> str:
        total = len(self.judged)
        if not total:
            return "대조할 수 있는 과거 가이던스를 찾지 못했습니다."
        return f"확인된 {total}번 중 {self.kept}번 지켰고 {self.missed}번 못 지켰습니다."

    @property
    def level(self) -> str:
        """good / fair / poor / unknown — 화면 색깔에 쓴다."""
        total = len(self.judged)
        if total == 0:
            return "unknown"
        rate = self.kept / total
        if total >= 2 and rate >= 0.8:
            return "good"
        if rate >= 0.5:
            return "fair"
        return "poor"


def target_period(item_sentence: str, filed: date, annual: bool, ends: list[date]) -> date | None:
    """가이던스가 가리키는 기간의 종료일을 고른다.

    회사는 실적을 발표하면서 '다음 분기' 또는 '올해 전체' 를 이야기한다.
    발표일 이후에 끝나는 첫 기간을 대상으로 본다. 문장에 특정 분기가
    명시돼 있어도 회계연도가 회사마다 달라 오판 위험이 커서, 시간 순서만 쓴다.
    """
    later = [end for end in ends if end > filed]
    if not later:
        return None
    return min(later)


def judge(low: float | None, high: float | None, actual: float | None) -> tuple[str, str]:
    """(판정, 근거 문장)"""
    if actual is None or low is None:
        return UNKNOWN, ""
    top = high if high is not None else low
    if actual >= top:
        return BEAT, f"제시 상단 {_money(top)} 을(를) 넘겼습니다."
    if actual >= low:
        return MET, f"제시 범위 {_money(low)}~{_money(top)} 안에 들어왔습니다."
    return MISS, f"제시 하단 {_money(low)} 에 {_money(low - actual)} 못 미쳤습니다."


def build_track_record(ticker: str, reports: list, facts) -> TrackRecord:
    """가이던스 보고서 여러 개 + XBRL 실적 → 이행 이력.

    reports: guidance.GuidanceReport 목록 (최신순이든 아니든 상관없다)
    facts:   xbrl.CompanyFacts (없으면 판정 없이 문장만 남긴다)
    """
    record = TrackRecord(ticker=ticker.upper())

    quarters = facts.quarterly("revenue", limit=20) if facts else []
    annuals = facts.annual("revenue", limit=8) if facts else []
    quarter_by_end = {f.end: f.val for f in quarters}
    annual_by_end = {f.end: f.val for f in annuals}

    seen: set[str] = set()
    for report in reports:
        if not report:
            continue
        try:
            filed = date.fromisoformat(report.filing_date)
        except (TypeError, ValueError):
            continue

        for guidance in report.items:
            marker = guidance.sentence[:80].lower()
            if marker in seen:
                continue
            seen.add(marker)

            item = TrackItem(
                filed=report.filing_date,
                url=report.url,
                sentence=guidance.sentence,
                metric=guidance.metric,
                period_text=guidance.period,
                low=guidance.low,
                high=guidance.high,
                annual=bool(FULL_YEAR.search(guidance.sentence)),
            )
            _judge_item(item, filed, quarter_by_end, annual_by_end)
            record.items.append(item)

    record.items.sort(key=lambda i: i.filed, reverse=True)
    return record


def _judge_item(item: TrackItem, filed: date, quarters: dict, annuals: dict) -> None:
    if item.metric != "매출":
        item.reason = (
            "매출 가이던스만 자동으로 맞춰봅니다. "
            "조정 EPS·EBITDA 는 회사가 정의를 정하므로 SEC 제출 숫자와 바로 비교할 수 없습니다."
        )
        return
    if item.low is None:
        item.reason = "문장에서 금액 범위를 읽지 못했습니다."
        return
    if item.low < MIN_REVENUE_SCALE:
        item.reason = "금액 단위를 확신할 수 없어 비교하지 않았습니다."
        return

    pool = annuals if item.annual else quarters
    end = target_period(item.sentence, filed, item.annual, sorted(pool))
    if end is None:
        item.reason = "대조할 실적이 아직 SEC 에 제출되지 않았습니다."
        return

    item.target_end = end
    item.actual = pool.get(end)
    item.verdict, item.reason = judge(item.low, item.high, item.actual)
    if item.verdict == UNKNOWN and not item.reason:
        item.reason = "실제 실적을 찾지 못했습니다."

