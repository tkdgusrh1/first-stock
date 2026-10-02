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
                   "defaultKeyStatistics,summaryDetail,assetProfile,price,"
                   "earningsTrend,earningsHistory,earnings,calendarEvents,"
                   "majorHoldersBreakdown,institutionOwnership")
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
class PeriodEstimate:
    """한 기간(이번 분기·다음 분기·올해·내년)의 애널리스트 예상. 비어 있으면 None."""

    period: str                      # 0q · +1q · 0y · +1y
    end: object = None               # date — 그 기간이 끝나는 날
    eps: float | None = None
    eps_low: float | None = None
    eps_high: float | None = None
    eps_year_ago: float | None = None
    eps_analysts: int | None = None
    eps_growth: float | None = None
    revenue: float | None = None
    revenue_low: float | None = None
    revenue_high: float | None = None
    revenue_year_ago: float | None = None
    revenue_analysts: int | None = None
    revenue_growth: float | None = None
    eps_trend: dict = field(default_factory=dict)     # {"90d": .., "60d": .., "30d": .., "7d": .., "now": ..}
    up_30d: int | None = None
    down_30d: int | None = None

    @property
    def label(self) -> str:
        return PERIOD_LABEL.get(self.period, self.period)

    @property
    def quarterly(self) -> bool:
        return self.period.endswith("q")


PERIOD_LABEL = {"0q": "이번 분기", "+1q": "다음 분기", "0y": "올해", "+1y": "내년"}


@dataclass
class EpsResult:
    """지난 분기 하나의 EPS 실제 vs 예상."""

    quarter: object                  # date (분기 말)
    actual: float | None = None
    estimate: float | None = None
    surprise_pct: float | None = None     # 0.05 = +5%

    @property
    def beat(self) -> bool | None:
        if self.actual is None or self.estimate is None:
            return None
        return self.actual >= self.estimate


@dataclass
class Officer:
    name: str
    title: str = ""
    pay: float | None = None
    exercised: float | None = None
    born: int | None = None


@dataclass
class Holder:
    name: str
    pct: float | None = None         # 0.05 = 5%
    shares: float | None = None
    value: float | None = None
    reported: object = None          # date
    change: float | None = None      # 직전 보고 대비 보유 주식 변화율


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
    address: str = ""
    phone: str = ""
    officers: list = field(default_factory=list)   # [Officer]
    # 가치 평가 · 주식 구조 (야후 집계)
    market_cap: float | None = None
    enterprise_value: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    price_to_sales: float | None = None
    price_to_book: float | None = None
    ev_revenue: float | None = None
    ev_ebitda: float | None = None
    week52_change: float | None = None
    sp52_change: float | None = None
    fifty_day: float | None = None
    two_hundred_day: float | None = None
    shares_out: float | None = None
    float_shares: float | None = None
    held_insiders: float | None = None
    held_institutions: float | None = None
    institutions_count: int | None = None
    institutions: list = field(default_factory=list)   # [Holder]
    # 수익성·재무 (야후 집계, 최근 12개월)
    gross_margin: float | None = None
    operating_margin: float | None = None
    profit_margin: float | None = None
    roe: float | None = None
    roa: float | None = None
    current_ratio: float | None = None
    debt_to_equity: float | None = None
    total_cash: float | None = None
    total_debt: float | None = None
    free_cashflow: float | None = None
    revenue_growth: float | None = None
    # 실적 기대
    estimates: list = field(default_factory=list)      # [PeriodEstimate] 0q, +1q, 0y, +1y
    eps_history: list = field(default_factory=list)    # [EpsResult] 오래된 것부터
    yearly: list = field(default_factory=list)         # [(연도, 매출, 순이익)]
    earnings_dates: list = field(default_factory=list)  # [date] 다음 실적 발표(범위면 둘)

    def estimate(self, period: str) -> "PeriodEstimate | None":
        return next((e for e in self.estimates if e.period == period), None)

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

    _parse_stats(profile, stats, detail, fin, price)
    _parse_company(profile, asset)
    _parse_expectations(profile, node)
    _parse_holders(profile, node)
    return profile


def _int(node) -> int | None:
    value = _num(node)
    return int(value) if value is not None else None


def _day(node):
    """유닉스 초 → 날짜. 'YYYY-MM-DD' 문자열도 받는다. 아니면 None."""
    from datetime import date, datetime, timezone

    stamp = _num(node)
    if stamp:
        return datetime.fromtimestamp(stamp, tz=timezone.utc).date()
    text = node.get("fmt") if isinstance(node, dict) else node
    if isinstance(text, str):
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _parse_stats(profile: Profile, stats: dict, detail: dict, fin: dict, price: dict) -> None:
    profile.market_cap = _num(price.get("marketCap")) or _num(detail.get("marketCap"))
    profile.enterprise_value = _num(stats.get("enterpriseValue"))
    profile.trailing_pe = _num(detail.get("trailingPE"))
    profile.forward_pe = _num(stats.get("forwardPE")) or _num(detail.get("forwardPE"))
    profile.peg = _num(stats.get("pegRatio"))
    profile.price_to_sales = _num(detail.get("priceToSalesTrailing12Months"))
    profile.price_to_book = _num(stats.get("priceToBook"))
    profile.ev_revenue = _num(stats.get("enterpriseToRevenue"))
    profile.ev_ebitda = _num(stats.get("enterpriseToEbitda"))
    profile.week52_change = _num(stats.get("52WeekChange"))
    profile.sp52_change = _num(stats.get("SandP52WeekChange"))
    profile.fifty_day = _num(detail.get("fiftyDayAverage"))
    profile.two_hundred_day = _num(detail.get("twoHundredDayAverage"))
    profile.shares_out = _num(stats.get("sharesOutstanding"))
    profile.float_shares = _num(stats.get("floatShares"))
    profile.held_insiders = _num(stats.get("heldPercentInsiders"))
    profile.held_institutions = _num(stats.get("heldPercentInstitutions"))
    if profile.beta is None:
        profile.beta = _num(stats.get("beta"))
    profile.gross_margin = _num(fin.get("grossMargins"))
    profile.operating_margin = _num(fin.get("operatingMargins"))
    profile.profit_margin = _num(fin.get("profitMargins"))
    profile.roe = _num(fin.get("returnOnEquity"))
    profile.roa = _num(fin.get("returnOnAssets"))
    profile.current_ratio = _num(fin.get("currentRatio"))
    profile.debt_to_equity = _num(fin.get("debtToEquity"))
    profile.total_cash = _num(fin.get("totalCash"))
    profile.total_debt = _num(fin.get("totalDebt"))
    profile.free_cashflow = _num(fin.get("freeCashflow"))
    profile.revenue_growth = _num(fin.get("revenueGrowth"))


def _parse_company(profile: Profile, asset: dict) -> None:
    parts = [asset.get(k) for k in ("address1", "city", "state", "zip", "country")]
    profile.address = ", ".join(str(p) for p in parts if p)
    profile.phone = str(asset.get("phone") or "")
    officers = []
    for row in asset.get("companyOfficers") or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        officers.append(Officer(name=name, title=str(row.get("title") or ""),
                                pay=_num(row.get("totalPay")) or None,
                                exercised=_num(row.get("exercisedValue")) or None,
                                born=_int(row.get("yearBorn"))))
    profile.officers = officers[:12]


def _parse_expectations(profile: Profile, node: dict) -> None:
    for row in (node.get("earningsTrend") or {}).get("trend") or []:
        period = str(row.get("period") or "")
        if period not in PERIOD_LABEL:
            continue
        eps = row.get("earningsEstimate") or {}
        rev = row.get("revenueEstimate") or {}
        trend = row.get("epsTrend") or {}
        revisions = row.get("epsRevisions") or {}
        item = PeriodEstimate(
            period=period, end=_day(row.get("endDate")),
            eps=_num(eps.get("avg")), eps_low=_num(eps.get("low")), eps_high=_num(eps.get("high")),
            eps_year_ago=_num(eps.get("yearAgoEps")), eps_analysts=_int(eps.get("numberOfAnalysts")),
            eps_growth=_num(eps.get("growth")),
            revenue=_num(rev.get("avg")), revenue_low=_num(rev.get("low")), revenue_high=_num(rev.get("high")),
            revenue_year_ago=_num(rev.get("yearAgoRevenue")), revenue_analysts=_int(rev.get("numberOfAnalysts")),
            revenue_growth=_num(rev.get("growth")),
            up_30d=_int(revisions.get("upLast30days")), down_30d=_int(revisions.get("downLast30days")),
        )
        for key, label in (("90daysAgo", "90d"), ("60daysAgo", "60d"), ("30daysAgo", "30d"),
                           ("7daysAgo", "7d"), ("current", "now")):
            value = _num(trend.get(key))
            if value is not None:
                item.eps_trend[label] = value
        if item.eps is not None or item.revenue is not None:
            profile.estimates.append(item)

    history = []
    for row in (node.get("earningsHistory") or {}).get("history") or []:
        quarter = _day(row.get("quarter"))
        if quarter is None:
            continue
        history.append(EpsResult(quarter=quarter, actual=_num(row.get("epsActual")),
                                 estimate=_num(row.get("epsEstimate")),
                                 surprise_pct=_num(row.get("surprisePercent"))))
    history.sort(key=lambda r: r.quarter)
    profile.eps_history = history[-4:]

    chart = (node.get("earnings") or {}).get("financialsChart") or {}
    for row in chart.get("yearly") or []:
        year = _int(row.get("date"))
        if year:
            profile.yearly.append((year, _num(row.get("revenue")), _num(row.get("earnings"))))
    profile.yearly.sort()

    dates = ((node.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or []
    profile.earnings_dates = [d for d in (_day(x) for x in dates) if d][:2]


def _parse_holders(profile: Profile, node: dict) -> None:
    major = node.get("majorHoldersBreakdown") or {}
    if profile.held_insiders is None:
        profile.held_insiders = _num(major.get("insidersPercentHeld"))
    if profile.held_institutions is None:
        profile.held_institutions = _num(major.get("institutionsPercentHeld"))
    profile.institutions_count = _int(major.get("institutionsCount"))
    holders = []
    for row in (node.get("institutionOwnership") or {}).get("ownershipList") or []:
        name = str(row.get("organization") or "").strip()
        if not name:
            continue
        holders.append(Holder(name=name, pct=_num(row.get("pctHeld")), shares=_num(row.get("position")),
                              value=_num(row.get("value")), reported=_day(row.get("reportDate")),
                              change=_num(row.get("pctChange"))))
    profile.institutions = holders[:10]


def _num(node) -> float | None:
    """야후 값은 {raw, fmt} 이거나 숫자 그대로다. 숫자가 아니면 None."""
    value = node.get("raw") if isinstance(node, dict) else node
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)

