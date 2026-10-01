"""얼마나 살지 — '틀렸을 때 얼마를 잃을 것인가' 로 정한다.

    살 주식 수 = 계좌 × 한 번에 잃어도 되는 비율 ÷ (산 가격 − 손절 가격)

여기에 한 종목 최대 비중과 남은 현금으로 한 번 더 자른다. 1주도 못 사면
**건너뛰고 그렇다고 센다** — 소액 계좌에서 실제로 자주 일어나는 일이라 숨기지 않는다.

계좌 단계(공격 → 균형 → 분산)는 원화 기준 계좌 크기로 정한다. 숫자는 연구로
정해진 값이 아니라 출발점이고, 화면에서 바꿀 수 있다.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RiskRules:
    risk_per_trade: float = 0.02     # 한 번 틀릴 때 잃어도 되는 비율
    max_weight: float = 0.35         # 한 종목 최대 비중
    max_positions: int = 4           # 동시에 들고 있을 최대 종목 수
    vol_target: bool = False         # 변동성이 크면 비중을 줄일지
    target_vol: float = 0.30         # vol_target 일 때 기준(연율)
    daily_loss_stop: float = 0.03    # 하루에 이만큼 잃으면 다음 날 새로 사지 않음 (0 = 끔)
    dd_half: float = 0.15            # 고점 대비 이만큼 빠지면 위험 비율 절반 (0 = 끔)
    dd_stop: float = 0.25            # 고점 대비 이만큼 빠지면 새로 사지 않고 멈춤 (0 = 끔)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict | None) -> "RiskRules":
        base = cls()
        if not isinstance(raw, dict):
            return base
        out = {}
        for name, default in asdict(base).items():
            value = raw.get(name, default)
            try:
                if isinstance(default, bool):
                    value = value in (True, "1", "on", "true", 1)
                elif isinstance(default, int):
                    value = int(float(value))
                else:
                    value = float(value)
            except (TypeError, ValueError):
                value = default
            out[name] = value
        out["risk_per_trade"] = min(max(out["risk_per_trade"], 0.001), 0.10)
        out["max_weight"] = min(max(out["max_weight"], 0.01), 1.0)
        out["max_positions"] = min(max(out["max_positions"], 1), 30)
        for name in ("daily_loss_stop", "dd_half", "dd_stop"):
            out[name] = min(max(out[name], 0.0), 0.9)
        out["target_vol"] = min(max(out["target_vol"], 0.05), 2.0)
        return cls(**out)


@dataclass(frozen=True)
class Stage:
    key: str
    name: str
    upto_krw: float | None      # 이 금액 미만이면 이 단계 (None = 끝까지)
    rules: RiskRules
    note: str


STAGES = (
    Stage("attack", "1단계 공격", 3_000_000,
          RiskRules(risk_per_trade=0.02, max_weight=0.35, max_positions=4),
          "3~5종목에 집중, 한 번 틀릴 때 2%만 잃게"),
    Stage("balance", "2단계 균형", 10_000_000,
          RiskRules(risk_per_trade=0.015, max_weight=0.20, max_positions=8, dd_half=0.12, dd_stop=0.20),
          "5~10종목, 성격이 다른 두 번째 전략 추가"),
    Stage("spread", "3단계 분산", None,
          RiskRules(risk_per_trade=0.01, max_weight=0.10, max_positions=15, vol_target=True,
                    dd_half=0.10, dd_stop=0.15),
          "10~20종목 + 지수 ETF 핵심, 위성 전략은 절반 이하"),
)


WEEKDAYS = ("월", "화", "수", "목", "금")


@dataclass(frozen=True)
class Plan:
    """운용 계획 — 매달 넣는 돈, 점검 요일, 최소 보유일.

    점검 요일에만 새 신호를 보고 사고판다(기본 화·금, 주 2회). 손절은 증권사의
    자동 감시 주문(스탑로스)에 걸어둔다고 보고 **매일** 확인한다 — 손실을 막는 장치를
    점검일까지 미루면 그 사이 손실이 커진다.
    최소 보유일은 사자마자 신호가 바뀌어 다시 파는 일(비용만 나가는 매매)을 막는다.
    """

    monthly_deposit: float = 0.0
    check_days: tuple = (1, 4)          # 0=월 … 4=금
    min_hold: int = 5                   # 거래일
    fractional: bool = False            # 소수점 매수(미국 소액 계좌용, 0.01주 단위)
    include_leveraged: bool = False     # 레버리지·인버스 상품도 넣어 시험(기본은 뺌)

    def to_dict(self) -> dict:
        return {"monthly_deposit": self.monthly_deposit, "check_days": list(self.check_days),
                "min_hold": self.min_hold, "fractional": self.fractional,
                "include_leveraged": self.include_leveraged}

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Plan":
        base = cls()
        if not isinstance(raw, dict):
            return base
        try:
            deposit = max(0.0, min(float(raw.get("monthly_deposit", 0) or 0), 1e12))
        except (TypeError, ValueError):
            deposit = 0.0
        days = []
        for d in raw.get("check_days") or base.check_days:
            try:
                d = int(d)
            except (TypeError, ValueError):
                continue
            if 0 <= d <= 4 and d not in days:
                days.append(d)
        try:
            hold = max(0, min(int(raw.get("min_hold", base.min_hold)), 60))
        except (TypeError, ValueError):
            hold = base.min_hold
        flag = lambda name: raw.get(name) in (True, 1, "1", "on", "true")  # noqa: E731
        return cls(deposit, tuple(sorted(days)) or base.check_days, hold,
                   flag("fractional"), flag("include_leveraged"))

    @property
    def days_text(self) -> str:
        return "·".join(WEEKDAYS[d] for d in self.check_days)


def stage_for(equity_krw: float | None) -> Stage | None:
    """원화 계좌 크기 → 단계. 금액을 모르면(환율을 못 받음 등) None — 추측하지 않는다."""
    if equity_krw is None:
        return None
    for stage in STAGES:
        if stage.upto_krw is None or equity_krw < stage.upto_krw:
            return stage
    return STAGES[-1]


LOT = 0.01      # 소수점 매수 최소 단위(주)


def round_shares(value: float, fractional: bool) -> float:
    """주식 수를 내림한다. 온주는 1주, 소수점은 0.01주 단위."""
    if fractional:
        return max(0.0, math.floor(value / LOT + 1e-9) * LOT)
    return max(0, math.floor(value + 1e-9))


def shares_to_buy(rules: RiskRules, equity: float, cash: float, price: float,
                  stop: float | None, vol: float | None, multiplier: float,
                  unit_cost: float, fractional: bool = False) -> float:
    """살 주식 수(0 이면 못 삼).

    unit_cost = 1주를 사는 데 실제로 드는 돈(체결 차이·수수료 포함).
    fractional 이면 0.01주 단위까지 산다(미국 소수점 거래).
    """
    if price <= 0 or equity <= 0 or unit_cost <= 0:
        return 0
    limits = []
    if stop is not None and price > stop:
        limits.append(equity * rules.risk_per_trade * multiplier / (price - stop))
    cap = rules.max_weight
    if rules.vol_target and vol:
        cap *= min(1.0, rules.target_vol / vol)
    limits.append(equity * cap / price)
    limits.append(cash / unit_cost)
    return round_shares(min(limits), fractional)
