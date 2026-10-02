"""청산 규칙 묶음 — '어떻게 팔 것인가'. 사는 규칙(전략)과 따로 고른다.

목표는 '잃지 않기' 지만, **손실을 0 으로 만드는 규칙은 없다.** 할 수 있는 건
  · 한 번의 손실 크기를 묶고(손실 상한)
  · 오른 것의 일부를 확정하고(절반 익절)
  · 이익이 난 자리를 손실로 되돌리지 않는 것(본전 손절 · 추적 손절)
이다. 대신 대가가 있다 — 손절선을 가까이 둘수록 흔들림에 자주 털리고, 일찍 팔수록
크게 오를 몫을 놓친다. 그래서 묶음마다 백테스트로 나란히 재서 고르게 한다(청산 규칙 비교).

장중에 저가와 고가 중 무엇이 먼저였는지는 일봉으로 알 수 없다. 그래서 **손절을 먼저**
본다(나쁜 쪽으로 가정). 손절선을 넘는 갭 하락이면 손절선이 아니라 시가에 판다 —
본전 손절이어도 손실이 날 수 있다는 뜻이다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ExitPreset:
    key: str
    name: str
    summary: str
    loss_cap: float = 0.0
    take_half_r: float = 0.0
    breakeven_r: float = 0.0
    trail_atr: float = 0.0


PRESETS = (
    ExitPreset("basic", "전략 그대로", "전략의 손절선(산 날 종가 − 2×ATR)과 전략의 매도 신호만"),
    ExitPreset("cap", "손실 상한 7%", "산 값에서 7% 빠지면 손절 — 전략 손절선보다 가까울 때만", loss_cap=0.07),
    ExitPreset("breakeven", "본전 지키기", "1R 만큼 오른 날부터 손절선을 본전(비용 포함)으로", breakeven_r=1.0),
    ExitPreset("half", "절반 익절 + 본전", "1R 오르면 절반을 팔아 이익 확정, 나머지는 손절선을 본전으로",
               take_half_r=1.0, breakeven_r=1.0),
    ExitPreset("trail", "추적 손절", "산 뒤 최고 종가 − 3×ATR 로 손절선을 따라 올림(내려가지 않음)", trail_atr=3.0),
    ExitPreset("guard", "지키기 세트", "손실 상한 7% + 1R 절반 익절 + 본전 + 추적 손절 3ATR",
               loss_cap=0.07, take_half_r=1.0, breakeven_r=1.0, trail_atr=3.0),
)
BY_KEY = {p.key: p for p in PRESETS}
DEFAULT_EXIT = "basic"


def get(key: str) -> ExitPreset:
    return BY_KEY.get(key, BY_KEY[DEFAULT_EXIT])


def apply(plan, key: str):
    """계획(Plan)에 청산 묶음을 씌운다."""
    p = get(key)
    return replace(plan, loss_cap=p.loss_cap, take_half_r=p.take_half_r, breakeven_r=p.breakeven_r,
                   trail_atr=p.trail_atr, exit_key=p.key)


def describe(plan) -> str:
    """계획에 걸린 청산 규칙을 한 줄로."""
    parts = []
    if plan.loss_cap:
        parts.append(f"손실 상한 {plan.loss_cap:.0%}")
    if plan.take_half_r:
        parts.append(f"+{plan.take_half_r:g}R 절반 익절")
    if plan.breakeven_r:
        parts.append(f"+{plan.breakeven_r:g}R 뒤 본전 손절")
    if plan.trail_atr:
        parts.append(f"추적 손절 {plan.trail_atr:g}ATR")
    return " · ".join(parts) or "전략 그대로"


# 방어형 ETF 바구니 — 주식·채권·금·단기채. 6개월 순위 + 200일선 회전 전략과 함께 쓰면
# '오르는 자산만 들고, 다 빠지면 단기채(현금 대신)' 가 된다(Faber 2007 · Antonacci 의 이중 모멘텀과 같은 생각).
BASKETS = {
    "us": (("SPY", "미국 대형주 S&P 500"), ("QQQ", "미국 기술주 나스닥 100"), ("IEF", "미국 국채 7~10년"),
           ("TLT", "미국 국채 20년+"), ("GLD", "금"), ("SHY", "미국 국채 1~3년(현금 대신)")),
    "kr": (("069500", "KODEX 200"), ("360750", "TIGER 미국S&P500"), ("133690", "TIGER 미국나스닥100"),
           ("148070", "KOSEF 국고채10년"), ("132030", "KODEX 골드선물(H)"), ("153130", "KODEX 단기채권(현금 대신)")),
}
UNIVERSE_NAME = {"watch": "관심 종목", "defense": "방어형 ETF 바구니"}
