"""애널리스트 예상치(컨센서스) 수집.

SEC 공시에는 컨센서스가 없다. 회사가 아니라 증권사가 만드는 숫자라서다.
그래서 Yahoo Finance 의 공개 엔드포인트를 시도한다. 다만 이 경로는
언제든 막힐 수 있으므로 **실패를 정상 경로로 취급**한다.

  · 성공하면 어디서 온 값인지(제공처·집계 애널리스트 수)를 함께 남긴다
  · 실패하면 직접 입력할 수 있도록 어디서 찾는지 안내한다
  · 추측한 값을 채워 넣지 않는다
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
COOKIE_URL = "https://fc.yahoo.com"
SUMMARY_URL = (
    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    "?modules=earningsTrend,earningsHistory&crumb={crumb}"
)

# 직접 입력할 때 어디를 보면 되는지 (화면에 그대로 띄운다)
WHERE_TO_LOOK = [
    ("Yahoo Finance", "https://finance.yahoo.com/quote/{ticker}/analysis",
     "Earnings Estimate 표의 'Current Qtr' 열 Avg. Estimate"),
    ("Nasdaq", "https://www.nasdaq.com/market-activity/stocks/{ticker}/earnings",
     "Quarterly Earnings Surprise 아래 Consensus EPS Forecast"),
    ("Zacks", "https://www.zacks.com/stock/quote/{ticker}/detailed-estimates",
     "Next Report 의 Consensus Estimate"),
    ("StockAnalysis", "https://stockanalysis.com/stocks/{ticker}/forecast/",
     "EPS Forecast 표"),
]


@dataclass
class Estimate:
    ticker: str
    eps: float | None = None
    revenue: float | None = None
    period: str | None = None            # 예: 0q (이번 분기)
    analysts: int | None = None
    source: str = ""
    history: list[dict] = field(default_factory=list)   # 과거 서프라이즈 이력

    @property
    def found(self) -> bool:
        return self.eps is not None or self.revenue is not None


def links_for(ticker: str) -> list[tuple[str, str, str]]:
    return [(name, url.format(ticker=ticker.upper()), hint) for name, url, hint in WHERE_TO_LOOK]


class EstimateClient:
    """컨센서스 조회. 막히면 조용히 포기하고 안내로 넘긴다."""

    def __init__(self, http) -> None:
        self.http = http
        self._crumb: str | None = None
        self._blocked = False

    def _get_crumb(self) -> str | None:
        if self._crumb or self._blocked:
            return self._crumb
        try:
            # 쿠키를 먼저 받아야 crumb 발급이 된다
            self.http.get(COOKIE_URL, timeout=15, retries=1)
            crumb = self.http.get_text(CRUMB_URL, timeout=15, retries=1).strip()
        except Exception as exc:
            log.info("컨센서스 조회 준비 실패(직접 입력으로 대체): %s", exc)
            self._blocked = True
            return None
        if not crumb or len(crumb) > 32 or "<" in crumb:
            self._blocked = True
            return None
        self._crumb = crumb
        return crumb

    def fetch(self, ticker: str) -> Estimate | None:
        crumb = self._get_crumb()
        if not crumb:
            return None
        try:
            text = self.http.get_text(SUMMARY_URL.format(ticker=ticker.upper(), crumb=crumb), timeout=20, retries=1)
            payload = json.loads(text)
        except Exception as exc:
            log.info("컨센서스 조회 실패 %s (직접 입력으로 대체): %s", ticker, exc)
            return None

        results = ((payload.get("quoteSummary") or {}).get("result")) or []
        if not results:
            return None
        return _parse_summary(ticker, results[0])


def _parse_summary(ticker: str, node: dict) -> Estimate | None:
    estimate = Estimate(ticker=ticker.upper(), source="Yahoo Finance")

    trends = (node.get("earningsTrend") or {}).get("trend") or []
    current = next((t for t in trends if t.get("period") == "0q"), None)
    if current:
        estimate.period = current.get("period")
        eps_node = current.get("earningsEstimate") or {}
        rev_node = current.get("revenueEstimate") or {}
        estimate.eps = _raw(eps_node.get("avg"))
        estimate.revenue = _raw(rev_node.get("avg"))
        estimate.analysts = _raw(eps_node.get("numberOfAnalysts"))

    for item in ((node.get("earningsHistory") or {}).get("history") or [])[-4:]:
        estimate.history.append(
            {
                "quarter": (item.get("quarter") or {}).get("fmt"),
                "actual": _raw((item.get("epsActual") or {})),
                "estimate": _raw((item.get("epsEstimate") or {})),
                "surprise_pct": _raw((item.get("surprisePercent") or {})),
            }
        )

    return estimate if estimate.found or estimate.history else None


def _raw(node):
    if isinstance(node, dict):
        return node.get("raw")
    return node
