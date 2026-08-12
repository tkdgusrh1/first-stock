"""내부자가 자기 돈으로 샀는가.

Form 4 는 한 건씩 보면 뜻을 알기 어렵다. 임원이 RSU 를 받아도, 세금 내려고
주식을 반납해도 전부 Form 4 로 올라오기 때문이다. 그래서 묶어서 본다.

세는 것은 **공개시장 거래 두 가지뿐**이다.
  P — 자기 돈으로 시장에서 산 것
  S — 시장에 내다 판 것

A(무상 취득)·F(세금 납부용 반납)·M(옵션 행사)·G(증여)는 매매 의사와
관계가 없어서 합계에서 뺀다. 이걸 섞으면 "임원이 100만 달러어치 취득" 같은
숫자가 나오는데, 실제로는 보상으로 받은 것이라 아무 뜻이 없다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

# 금액 표기는 화면 어디서나 같아야 한다. 한 곳에서 가져다 쓴다.
from .metrics import _money

log = logging.getLogger(__name__)

BUY, SELL = "P", "S"
DEFAULT_DAYS = 90


@dataclass
class InsiderTrade:
    person: str
    title: str
    day: str
    code: str
    shares: float
    price: float | None
    value: float | None
    url: str

    @property
    def is_buy(self) -> bool:
        return self.code == BUY


@dataclass
class InsiderSummary:
    ticker: str
    days: int = DEFAULT_DAYS
    trades: list[InsiderTrade] = field(default_factory=list)
    other_filings: int = 0          # 보상·세금 등 합계에서 뺀 건수

    @property
    def buys(self) -> list[InsiderTrade]:
        return [t for t in self.trades if t.is_buy]

    @property
    def sells(self) -> list[InsiderTrade]:
        return [t for t in self.trades if not t.is_buy]

    @property
    def buy_value(self) -> float:
        return sum(t.value or 0 for t in self.buys)

    @property
    def sell_value(self) -> float:
        return sum(t.value or 0 for t in self.sells)

    @property
    def net_value(self) -> float:
        return self.buy_value - self.sell_value

    @property
    def buyers(self) -> list[str]:
        return sorted({t.person for t in self.buys if t.person})

    @property
    def sellers(self) -> list[str]:
        return sorted({t.person for t in self.sells if t.person})

    @property
    def verdict(self) -> str:
        if not self.trades:
            return "거래 없음"
        if self.net_value > 0:
            return "순매수"
        if self.net_value < 0:
            return "순매도"
        return "중립"

    @property
    def level(self) -> str:
        """화면 색깔. 매수는 드물어서 신호가 되고, 매도는 흔해서 신호가 약하다."""
        if not self.trades:
            return "unknown"
        if self.buy_value > 0 and self.net_value > 0:
            return "good"
        if self.sell_value > 0 and not self.buys:
            return "fair"
        return "fair"

    @property
    def summary(self) -> str:
        if not self.trades:
            base = f"최근 {self.days}일 공개시장 매매가 없었습니다."
            if self.other_filings:
                base += f" (보상·세금 목적 신고는 {self.other_filings}건)"
            return base

        parts = []
        if self.buys:
            parts.append(f"{len(self.buyers)}명이 {_money(self.buy_value)} 매수")
        if self.sells:
            parts.append(f"{len(self.sellers)}명이 {_money(self.sell_value)} 매도")
        return f"최근 {self.days}일 " + ", ".join(parts) + f" → {self.verdict} {_money(abs(self.net_value))}"

    @property
    def note(self) -> str:
        """숫자를 어떻게 읽어야 하는지. 과장하지 않기 위한 문장."""
        if self.buys and len(self.buyers) >= 2:
            return ("임원 여러 명이 같은 기간에 자기 돈으로 샀습니다. "
                    "드문 일이라 눈여겨볼 만합니다.")
        if self.buys:
            return "자기 돈으로 산 기록입니다. 보상으로 받은 주식과는 다릅니다."
        if self.sells:
            return ("매도는 분산 투자·세금·사전계획(10b5-1) 때문일 수 있어 "
                    "그 자체로 악재는 아닙니다. 규모와 빈도를 보세요.")
        return ""


def summarize(ticker: str, filings, days: int = DEFAULT_DAYS) -> InsiderSummary:
    """Form 4 목록(edgar.Filing, 거래 내역이 채워진 것) → 기간 집계."""
    summary = InsiderSummary(ticker=ticker.upper(), days=days)

    for filing in filings:
        counted = False
        for tx in filing.transactions or []:
            if tx.get("derivative"):
                continue                      # 옵션·워런트는 주식 매매가 아니다
            code = (tx.get("code") or "").upper()
            if code not in (BUY, SELL):
                continue
            shares = tx.get("shares")
            if not shares:
                continue
            summary.trades.append(
                InsiderTrade(
                    person=filing.insider or "",
                    title=filing.insider_title or "",
                    day=tx.get("date") or filing.filing_date,
                    code=code,
                    shares=float(shares),
                    price=tx.get("price"),
                    value=tx.get("value"),
                    url=filing.index_url,
                )
            )
            counted = True
        if not counted:
            summary.other_filings += 1

    summary.trades.sort(key=lambda t: t.day, reverse=True)
    return summary


def since_day(today: date, days: int = DEFAULT_DAYS) -> date:
    from datetime import timedelta

    return today - timedelta(days=days)
