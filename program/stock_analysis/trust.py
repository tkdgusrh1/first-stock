"""**믿고 판단에 쓸 값**과 **참고로만 볼 값**을 가른다.

같은 SEC 공시에서 나온 숫자라도 다 같은 무게가 아니다. 매출이나 순이익은
회사가 그대로 적어 낸 값이지만, 어떤 값은 그 위에 나눗셈을 얹은 것이라
분모가 이상해지면 숫자만 멀쩡하고 뜻은 사라진다.

가장 흔한 예가 자본 효율(ROE·ROIC)이다. 애플처럼 자사주를 오래 사들인
회사는 자기자본이 매출에 비해 아주 작아져서 ROE 가 100% 를 넘는다. 계산은
맞지만 '끌어다 쓴 돈에 비해 잘 번다' 는 말과는 다른 이야기다. 이런 값을
근거로 '수익성 양호' 를 주면, 맞는 숫자로 틀린 판단을 하게 된다.

그래서 여기서 하는 일은 두 가지다.
  1) 못 미더운 값을 **점수에서 뺀다**
  2) 그래도 **화면에서 지우지는 않는다** — 왜 뺐는지와 함께 참고로 남긴다

지우지 않는 이유는, 값이 없는 것과 못 미더운 것은 다르기 때문이다.
ROE 100% 는 그 자체로 '이 회사는 자기자본이 거의 없다' 는 정보다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import Metrics, _money, _pct

# 자본 효율이 이 위로 올라가면 분모(자기자본·투하자본)가 무너진 것으로 본다.
# 정상적으로 이만큼 버는 회사는 거의 없다 — 대개 자사주 매입으로 자본이
# 줄었거나, 자본이 워낙 작아서 나온 값이다.
EFFICIENCY_CEILING = 0.60

# 직전 매출이 이보다 작으면 성장률이 크게 흔들린다.
# ($1M → $3M 도 +200% 다. 회사의 성장 속도라고 부르기 어렵다)
GROWTH_BASE_FLOOR = 20e6

# 이보다 긴 런웨이는 '돈이 거의 안 줄고 있다' 는 뜻이지 실제 햇수가 아니다.
RUNWAY_CEILING = 20.0

# 순이익이 겨우 흑자면 PER 이 수백 배로 튄다. 비싸다는 뜻이 아니라
# 분모가 0 에 가깝다는 뜻이다.
PER_CEILING = 200.0


@dataclass
class Doubt:
    """판단에서 뺀 값 하나. 왜 뺐는지를 항상 함께 들고 다닌다."""

    field: str          # Metrics 의 필드 이름
    label: str          # 화면에 쓸 이름
    shown: str          # 값 자체 (참고로 보여줄 때 쓴다)
    reason: str         # 왜 판단에 쓰지 않았는지

    @property
    def text(self) -> str:
        return f"{self.label} {self.shown} — {self.reason}"


def doubts(m: Metrics) -> dict[str, Doubt]:
    """이 종목에서 판단에 쓰기엔 못 미더운 값들. {필드이름: Doubt}"""
    found: dict[str, Doubt] = {}

    _capital_efficiency(m, found)
    _growth_base(m, found)
    _runway(m, found)
    _valuation(m, found)
    _dilution(m, found)
    return found


def _capital_efficiency(m: Metrics, found: dict) -> None:
    """ROE·ROIC — 분모가 무너지면 숫자만 커진다."""
    if m.equity is not None and m.equity <= 0:
        reason = ("자기자본이 0 이하입니다. 이 상태에서는 자본 효율을 나눗셈으로 "
                  "구할 수 없어 판단에 쓰지 않았습니다.")
        for field, label, value in (("roe", "ROE", m.roe), ("roic", "ROIC", m.roic)):
            if value is not None:
                found[field] = Doubt(field, label, _pct(value), reason)
        return

    for field, label, value in (("roe", "ROE", m.roe), ("roic", "ROIC", m.roic)):
        if value is not None and value >= EFFICIENCY_CEILING:
            found[field] = Doubt(
                field, label, _pct(value),
                "자사주를 오래 사들인 회사는 자기자본이 크게 줄어서 이 비율이 사업 성과와 "
                "상관없이 치솟습니다. 숫자는 맞지만 '잘 번다' 는 뜻으로 읽으면 안 되어 "
                "판단에서 뺐습니다.",
            )


def _growth_base(m: Metrics, found: dict) -> None:
    """성장률 — 밑이 작으면 몇 %든 나온다."""
    base = m.revenue_ttm_prior
    if m.revenue_growth is None or base is None:
        return
    if abs(base) < GROWTH_BASE_FLOOR:
        found["revenue_growth"] = Doubt(
            "revenue_growth", "매출 성장률", _pct(m.revenue_growth),
            f"직전 1년 매출이 {_money(base)} 로 너무 작습니다. 이럴 때 성장률은 "
            "몇 백 %도 쉽게 나와서 회사가 커지는 속도라고 보기 어렵습니다.",
        )


def _runway(m: Metrics, found: dict) -> None:
    """런웨이 — 흑자 기업에는 뜻이 없고, 너무 길면 계산의 부산물이다."""
    if m.runway_years is None:
        return
    if m.profitable:
        found["runway_years"] = Doubt(
            "runway_years", "현금 런웨이", f"{m.runway_years:.1f}년",
            "흑자 기업이라 '돈이 언제 떨어지는가' 라는 질문 자체가 맞지 않습니다.",
        )
    elif m.runway_years > RUNWAY_CEILING:
        found["runway_years"] = Doubt(
            "runway_years", "현금 런웨이", f"{m.runway_years:.1f}년",
            "현금이 거의 줄지 않아 나온 값입니다. 실제로 그만큼 버틴다는 뜻이 아니라 "
            "지금은 돈이 새지 않고 있다는 뜻으로 읽으세요.",
        )


def _valuation(m: Metrics, found: dict) -> None:
    """PER — 순이익이 0 에 가까우면 분모가 무너진다."""
    if m.per is not None and m.per > PER_CEILING:
        found["per"] = Doubt(
            "per", "PER", f"{m.per:,.1f}배",
            "순이익이 겨우 흑자라 분모가 0 에 가까워서 나온 값입니다. "
            "비싸다는 뜻으로 읽으면 안 됩니다.",
        )

    # 과거 PER 중앙값은 '실적이 공개된 시점' 을 추정해 주가와 맞춘 값이다.
    # 계산에 가정이 하나 들어가 있어서, 판단의 근거로 쓰기에는 한 걸음 약하다.
    if m.per_median_5y is not None:
        found["per_median_5y"] = Doubt(
            "per_median_5y", "과거 PER 중앙값", f"{m.per_median_5y:,.1f}배",
            "실적이 언제 공개됐는지를 분기말 기준으로 추정해 그때 주가와 맞춘 값입니다. "
            "추정이 들어가 있어 참고로만 봅니다.",
        )


def _dilution(m: Metrics, found: dict) -> None:
    """희석 — 분기가 몇 개 없으면 한 번의 증자가 추세처럼 보인다."""
    if m.share_growth_1y is None:
        return
    if len(m.shares_trend or []) < 5:
        found["share_growth_1y"] = Doubt(
            "share_growth_1y", "발행주식수 증가율", _pct(m.share_growth_1y),
            f"발행주식수 기록이 {len(m.shares_trend or [])}개뿐이라 한 번의 증자가 "
            "추세처럼 보일 수 있습니다.",
        )


def notes_from(found: dict[str, Doubt]) -> list[str]:
    """화면·알림에 그대로 쓸 '참고' 문장들."""
    return [doubt.text for doubt in found.values()]


__all__ = [
    "Doubt", "doubts", "notes_from",
    "EFFICIENCY_CEILING", "GROWTH_BASE_FLOOR", "RUNWAY_CEILING", "PER_CEILING",
]
