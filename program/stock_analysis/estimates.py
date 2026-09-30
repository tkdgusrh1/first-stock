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

    def profile(self, symbol: str) -> "Profile | None":
        """목표가·의견·공매도·회사 개요. 막히면 None (화면은 빈칸으로 둔다)."""
        return _fetch_profile(self, symbol)


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


# --------------------------------------------------------------------------
# 종목 한 장 — 애널리스트 목표가 · 투자의견 · 평가 변경 · 공매도 · 회사 개요
#
# 같은 야후 엔드포인트(quoteSummary)에서 모듈만 바꿔 받는다. **막히면 빈칸이다.**
# 목표가나 의견 수를 지어내서 채우지 않는다. 받은 값에는 어디서 왔는지와
# 언제 받았는지를 붙여 화면에 그대로 적는다.
# --------------------------------------------------------------------------
PROFILE_MODULES = ("financialData,recommendationTrend,upgradeDowngradeHistory,"
                   "defaultKeyStatistics,summaryDetail,assetProfile,price")
PROFILE_URL = (
    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    "?modules={modules}&crumb={crumb}"
)

# 증권사가 쓰는 의견 이름 → 한 줄 한글. 원래 이름도 화면에 같이 둔다.
GRADE_KO = {
    "strong buy": "적극 매수", "buy": "매수", "outperform": "시장수익률 상회",
    "overweight": "비중 확대", "market outperform": "시장수익률 상회",
    "sector outperform": "업종 상회", "accumulate": "매수(분할)", "positive": "긍정",
    "hold": "보유", "neutral": "중립", "equal-weight": "중립", "equal weight": "중립",
    "market perform": "시장수익률", "sector perform": "업종 수준", "peer perform": "업종 수준",
    "in-line": "중립", "mixed": "중립",
    "sell": "매도", "underperform": "시장수익률 하회", "underweight": "비중 축소",
    "reduce": "비중 축소", "negative": "부정", "strong sell": "적극 매도",
}
# 의견이 어느 쪽인지. 색을 칠할 때만 쓴다(초록·회색·빨강).
GRADE_SIDE = {
    "strong buy": "buy", "buy": "buy", "outperform": "buy", "overweight": "buy",
    "market outperform": "buy", "sector outperform": "buy", "accumulate": "buy", "positive": "buy",
    "hold": "hold", "neutral": "hold", "equal-weight": "hold", "equal weight": "hold",
    "market perform": "hold", "sector perform": "hold", "peer perform": "hold", "in-line": "hold",
    "mixed": "hold",
    "sell": "sell", "underperform": "sell", "underweight": "sell", "reduce": "sell",
    "negative": "sell", "strong sell": "sell",
}
ACTION_KO = {"up": "상향", "down": "하향", "main": "유지", "init": "신규",
             "reit": "재확인", "target": "목표가 조정"}
RECOMMENDATION_KO = {"strong_buy": "적극 매수", "buy": "매수", "hold": "보유",
                     "underperform": "비중 축소", "sell": "매도", "none": ""}


@dataclass
class Rating:
    """증권사 한 곳의 의견 변경."""

    day: object                      # date
    firm: str
    to_grade: str
    from_grade: str = ""
    action: str = ""
    target: float | None = None
    prior_target: float | None = None

    @property
    def grade_ko(self) -> str:
        return GRADE_KO.get(self.to_grade.strip().lower(), "")

    @property
    def side(self) -> str:
        return GRADE_SIDE.get(self.to_grade.strip().lower(), "hold")

    @property
    def action_ko(self) -> str:
        return ACTION_KO.get(self.action.strip().lower(), self.action)


@dataclass
class Profile:
    """야후가 집계해 둔 종목 정보. 비어 있는 칸은 None — 추정으로 메우지 않는다."""

    ticker: str
    source: str = "Yahoo Finance"
    fetched_at: object = None        # datetime
    # 애널리스트
    target_mean: float | None = None
    target_median: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    analysts: int | None = None
    recommendation: str = ""         # strong_buy · buy · hold · underperform · sell
    trend: dict = field(default_factory=dict)      # {strongBuy, buy, hold, sell, strongSell}
    ratings: list = field(default_factory=list)    # [Rating] 최근 것부터
    # 공매도
    short_pct_float: float | None = None
    short_ratio: float | None = None               # 소진일(일)
    shares_short: float | None = None
    shares_short_prior: float | None = None
    short_date: object = None
    # 거래·배당
    avg_volume: float | None = None
    avg_volume_10d: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    volume: float | None = None
    dividend_yield: float | None = None
    beta: float | None = None
    # 회사
    exchange: str = ""
    sector: str = ""
    industry: str = ""
    employees: int | None = None
    website: str = ""
    summary: str = ""
    currency: str = ""

    @property
    def has_analysts(self) -> bool:
        return self.target_mean is not None or bool(self.trend) or bool(self.ratings)

    @property
    def opinion_counts(self) -> tuple[int, int, int] | None:
        """(매수, 보유, 매도). 적극 매수·적극 매도를 각 쪽에 합친다."""
        if not self.trend:
            return None
        buy = int(self.trend.get("strongBuy", 0) or 0) + int(self.trend.get("buy", 0) or 0)
        hold = int(self.trend.get("hold", 0) or 0)
        sell = int(self.trend.get("sell", 0) or 0) + int(self.trend.get("strongSell", 0) or 0)
        if buy + hold + sell == 0:
            return None
        return buy, hold, sell


def _fetch_profile(client: "EstimateClient", symbol: str) -> Profile | None:
    crumb = client._get_crumb()
    if not crumb:
        return None
    try:
        text = client.http.get_text(
            PROFILE_URL.format(ticker=symbol.upper(), modules=PROFILE_MODULES, crumb=crumb),
            timeout=20, retries=1)
        payload = json.loads(text)
    except Exception as exc:
        log.info("종목 정보 조회 실패 %s: %s", symbol, exc)
        return None
    results = ((payload.get("quoteSummary") or {}).get("result")) or []
    if not results:
        return None
    return parse_profile(symbol, results[0])


def parse_profile(ticker: str, node: dict) -> Profile | None:
    """quoteSummary 한 건 → Profile. 형식이 어긋난 칸은 조용히 비운다."""
    from datetime import datetime, timezone

    profile = Profile(ticker=ticker.upper(), fetched_at=datetime.now(timezone.utc))

    fin = node.get("financialData") or {}
    profile.target_mean = _num(fin.get("targetMeanPrice"))
    profile.target_median = _num(fin.get("targetMedianPrice"))
    profile.target_high = _num(fin.get("targetHighPrice"))
    profile.target_low = _num(fin.get("targetLowPrice"))
    analysts = _num(fin.get("numberOfAnalystOpinions"))
    profile.analysts = int(analysts) if analysts else None
    profile.recommendation = str(fin.get("recommendationKey") or "").lower()
    if profile.recommendation == "none":
        profile.recommendation = ""

    for row in (node.get("recommendationTrend") or {}).get("trend") or []:
        if row.get("period") == "0m":
            profile.trend = {key: int(_num(row.get(key)) or 0)
                             for key in ("strongBuy", "buy", "hold", "sell", "strongSell")}
            break

    history = (node.get("upgradeDowngradeHistory") or {}).get("history") or []
    ratings = []
    for row in history:
        stamp = _num(row.get("epochGradeDate"))
        if not stamp or not row.get("firm") or not row.get("toGrade"):
            continue
        ratings.append(Rating(
            day=datetime.fromtimestamp(stamp, tz=timezone.utc).date(),
            firm=str(row.get("firm")), to_grade=str(row.get("toGrade")),
            from_grade=str(row.get("fromGrade") or ""), action=str(row.get("action") or ""),
            target=_num(row.get("currentPriceTarget")) or None,
            prior_target=_num(row.get("priorPriceTarget")) or None,
        ))
    ratings.sort(key=lambda r: r.day, reverse=True)
    profile.ratings = ratings[:30]

    stats = node.get("defaultKeyStatistics") or {}
    profile.short_pct_float = _num(stats.get("shortPercentOfFloat"))
    profile.short_ratio = _num(stats.get("shortRatio"))
    profile.shares_short = _num(stats.get("sharesShort"))
    profile.shares_short_prior = _num(stats.get("sharesShortPriorMonth"))
    short_stamp = _num(stats.get("dateShortInterest"))
    if short_stamp:
        profile.short_date = datetime.fromtimestamp(short_stamp, tz=timezone.utc).date()

    detail = node.get("summaryDetail") or {}
    profile.avg_volume = _num(detail.get("averageVolume"))
    profile.avg_volume_10d = _num(detail.get("averageVolume10days"))
    profile.day_high = _num(detail.get("dayHigh"))
    profile.day_low = _num(detail.get("dayLow"))
    profile.volume = _num(detail.get("volume"))
    profile.dividend_yield = _num(detail.get("dividendYield"))
    profile.beta = _num(detail.get("beta"))
    profile.currency = str(detail.get("currency") or "")

    asset = node.get("assetProfile") or {}
    profile.sector = str(asset.get("sector") or "")
    profile.industry = str(asset.get("industry") or "")
    employees = _num(asset.get("fullTimeEmployees"))
    profile.employees = int(employees) if employees else None
    profile.website = str(asset.get("website") or "")
    profile.summary = str(asset.get("longBusinessSummary") or "")

    price = node.get("price") or {}
    profile.exchange = str(price.get("exchangeName") or "")

    return profile


def _num(node) -> float | None:
    """야후 값은 {raw, fmt} 이거나 숫자 그대로다. 숫자가 아니면 None."""
    value = node.get("raw") if isinstance(node, dict) else node
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)

