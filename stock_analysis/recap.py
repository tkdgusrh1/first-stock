"""실적 발표 3자 대조.

실적이 나온 순간 봐야 하는 건 숫자 하나가 아니라 **세 숫자의 관계**다.

    회사가 약속한 값(가이던스)  ·  시장이 기대한 값(컨센서스)  ·  실제 값

메모의 우선순위가 여기서 한 화면에 모인다.
  1순위 가이던스 — 회사가 자기 말을 지켰나
  2순위 어닝 서프라이즈 — 시장 기대를 넘었나

셋 중 없는 것이 있으면 없는 대로 둔다. 두 개만 있어도 비교는 성립한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 금액 표기는 화면 어디서나 같아야 한다. 한 곳에서 가져다 쓴다.
from .metrics import _money

BEAT, MET, MISS, UNKNOWN = "상회", "부합", "미달", "확인 불가"
ICON = {BEAT: "✅", MET: "🟢", MISS: "❌", UNKNOWN: "❔"}


@dataclass
class Line:
    """한 줄 비교. '무엇을' '무엇과' 견줬는지 항상 밝힌다."""

    label: str
    actual: float | None = None
    expected: float | None = None
    expected_high: float | None = None
    verdict: str = UNKNOWN
    detail: str = ""
    money: bool = True

    @property
    def icon(self) -> str:
        return ICON.get(self.verdict, "❔")

    def _fmt(self, value: float | None) -> str:
        if value is None:
            return "-"
        return _money(value) if self.money else f"{value:,.2f}"

    @property
    def actual_text(self) -> str:
        return self._fmt(self.actual)

    @property
    def expected_text(self) -> str:
        if self.expected is None:
            return "-"
        if self.expected_high and self.expected_high != self.expected:
            return f"{self._fmt(self.expected)} ~ {self._fmt(self.expected_high)}"
        return self._fmt(self.expected)

    @property
    def gap_pct(self) -> float | None:
        base = self.expected_high or self.expected
        if self.actual is None or not base:
            return None
        return (self.actual - base) / abs(base) * 100


@dataclass
class Recap:
    ticker: str
    period: str = ""
    lines: list[Line] = field(default_factory=list)
    guidance_url: str = ""
    guidance_date: str = ""
    consensus_source: str = ""

    @property
    def known(self) -> list[Line]:
        return [line for line in self.lines if line.verdict != UNKNOWN]

    @property
    def empty(self) -> bool:
        return not self.known

    @property
    def level(self) -> str:
        if not self.known:
            return "unknown"
        if any(line.verdict == MISS for line in self.known):
            return "poor"
        if all(line.verdict == BEAT for line in self.known):
            return "good"
        return "fair"

    @property
    def summary(self) -> str:
        if not self.known:
            return "대조할 값이 아직 없습니다."
        wins = [line.label for line in self.known if line.verdict in (BEAT, MET)]
        losses = [line.label for line in self.known if line.verdict == MISS]
        parts = []
        if wins:
            parts.append(f"{' · '.join(wins)} 충족")
        if losses:
            parts.append(f"{' · '.join(losses)} 미달")
        return ", ".join(parts)


def judge(actual: float | None, low: float | None, high: float | None = None) -> str:
    if actual is None or low is None:
        return UNKNOWN
    top = high if high is not None else low
    if actual >= top:
        return BEAT
    if actual >= low:
        return MET
    return MISS


def build_recap(ticker: str, metrics, guidance=None) -> Recap:
    """지표(실제·컨센서스)와 가이던스 → 3자 대조.

    실제값과 컨센서스는 이미 metrics.surprise 안에 있다. 여기서는 거기에
    '회사가 직전에 약속한 값' 을 한 줄 더 얹는다.
    """
    recap = Recap(ticker=ticker.upper())
    surprise = getattr(metrics, "surprise", None) or {}
    recap.period = str(surprise.get("period") or "")

    # --- 시장 기대(컨센서스) 대비 ---
    if surprise.get("consensus_revenue") is not None:
        actual, expected = surprise.get("actual_revenue"), surprise.get("consensus_revenue")
        recap.lines.append(
            Line(label="매출 vs 컨센서스", actual=actual, expected=expected,
                 verdict=judge(actual, expected),
                 detail="증권사 예상치와의 비교입니다.")
        )
    if surprise.get("consensus_eps") is not None:
        actual, expected = surprise.get("actual_eps"), surprise.get("consensus_eps")
        recap.lines.append(
            Line(label="EPS vs 컨센서스", actual=actual, expected=expected,
                 verdict=judge(actual, expected), money=False,
                 detail="증권사 예상치와의 비교입니다.")
        )

    # --- 회사가 약속한 값(가이던스) 대비 ---
    guided = _revenue_guidance(guidance)
    if guided:
        low, high = guided
        actual = surprise.get("actual_revenue")
        if actual is None:
            actual = _latest_quarter_revenue(metrics)
        recap.lines.append(
            Line(label="매출 vs 가이던스", actual=actual, expected=low, expected_high=high,
                 verdict=judge(actual, low, high),
                 detail="회사가 직전 실적 발표에서 제시한 범위입니다.")
        )
        recap.guidance_url = getattr(guidance, "url", "") or ""
        recap.guidance_date = getattr(guidance, "filing_date", "") or ""

    return recap


def _revenue_guidance(guidance) -> tuple[float, float | None] | None:
    if guidance is None:
        return None
    for item in getattr(guidance, "items", []) or []:
        if item.metric == "매출" and item.low is not None and item.low >= 1e5:
            return item.low, item.high
    return None


def _latest_quarter_revenue(metrics) -> float | None:
    quarters = getattr(metrics, "quarterly_revenue", None) or []
    return quarters[-1][1] if quarters else None

