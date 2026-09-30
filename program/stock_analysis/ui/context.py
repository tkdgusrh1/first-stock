"""쪽을 그릴 때 한 번 모아두는 값. 쪽마다 따로 모으면 같은 순간의 값이 어긋난다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .. import markets


@dataclass
class Ctx:
    bot: object
    market: str
    here: str                      # 지금 쪽 주소(단추를 누른 뒤 돌아올 곳)
    today: date
    targets: list = field(default_factory=list)       # 감시 중인 전 종목
    metrics: dict = field(default_factory=dict)
    earnings: dict = field(default_factory=dict)

    @classmethod
    def build(cls, bot, market: str, here: str, today: date, targets) -> "Ctx":
        return cls(bot=bot, market=market, here=here, today=today, targets=list(targets),
                   metrics=bot.cached_metrics(), earnings=bot.cached_earnings())

    @property
    def config(self):
        return self.bot.config

    @property
    def mine(self) -> list:
        """보고 있는 시장의 종목만."""
        return [t for t in self.targets if t.market == self.market]

    @property
    def known(self) -> set[str]:
        return {t.ticker.upper() for t in self.targets}

    @property
    def korean(self) -> bool:
        return self.market == markets.KR

    def metric(self, target):
        return self.metrics.get(target.cik)

    def verdict(self, target):
        try:
            return self.bot.assessment_for(target)
        except Exception:
            return None

    def find(self, ticker: str):
        wanted = str(ticker or "").upper()
        for target in self.targets:
            if target.ticker.upper() == wanted or markets.display(target.ticker) == wanted:
                return target
        return None
