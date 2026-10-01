"""거래 비용. 백테스트가 실제보다 좋게 나오는 가장 흔한 이유가 비용을 빼먹는 것이다.

숫자는 **기본값일 뿐** — 화면에서 내 증권사 값으로 바꿀 수 있다.

- 한국: 온라인 수수료 0.015%(주요 증권사 기본 수준) + 유관기관 제비용 약 0.0036%.
  매도할 때 거래세 0.20%(2026년부터, 코스피 0.05% + 농특세 0.15%, 코스닥 0.20%).
- 미국: 국내 증권사의 해외주식 기본 수수료는 0.25% 안팎이 흔하다(이벤트로 더 낮은 곳도 있음).
  거래세는 없다. 환전 비용은 사고팔 때마다가 아니라 환전할 때 들어서 여기서는 뺐다.
- 체결 차이(슬리피지): 원한 가격과 실제 체결 가격의 차이. 정해진 값이 없어
  보수적으로 한쪽 0.2%(한국 중소형주)·0.1%(미국)로 둔다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CostModel:
    commission: float      # 한쪽(살 때·팔 때 각각) 수수료, 소수 (0.00015 = 0.015%)
    sell_tax: float        # 팔 때만 붙는 세금
    slippage: float        # 한쪽 체결 차이

    def buy_price(self, price: float) -> float:
        """실제로 치르는 1주 가격(체결 차이 포함, 수수료 제외)."""
        return price * (1 + self.slippage)

    def sell_price(self, price: float) -> float:
        return price * (1 - self.slippage)

    def buy_fee(self, amount: float) -> float:
        return amount * self.commission

    def sell_fee(self, amount: float) -> float:
        return amount * (self.commission + self.sell_tax)

    @property
    def round_trip(self) -> float:
        """사고팔 때 한 번에 드는 비용(소수). 화면에 '왕복 몇 %' 로 보여준다."""
        return self.commission * 2 + self.sell_tax + self.slippage * 2

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict | None, market: str) -> "CostModel":
        base = default_costs(market)
        if not isinstance(raw, dict):
            return base
        values = {}
        for name in ("commission", "sell_tax", "slippage"):
            try:
                value = float(raw.get(name, getattr(base, name)))
            except (TypeError, ValueError):
                value = getattr(base, name)
            values[name] = min(max(value, 0.0), 0.05)     # 5% 를 넘는 비용은 입력 실수로 본다
        return cls(**values)


KR_COSTS = CostModel(commission=0.00015 + 0.000036396, sell_tax=0.0020, slippage=0.002)
US_COSTS = CostModel(commission=0.0025, sell_tax=0.0, slippage=0.001)


def default_costs(market: str) -> CostModel:
    return KR_COSTS if market == "kr" else US_COSTS
