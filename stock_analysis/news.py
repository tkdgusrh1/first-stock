"""속보 감시.

내가 찾아보는 게 아니라 먼저 띄워주는 쪽. API 키 없이 공개 RSS 만 쓴다.

가장 어려운 부분은 '진짜 속보' 와 '조회수용 기사' 를 가르는 일이다.
"3 Stocks to Buy Even If There's a Sell-Off" 같은 제목은 단어만 보면
매수·급락이 다 들어있지만 속보가 아니다. 그래서 두 단계로 거른다.

  1) 걸러내기 : 리스티클·오피니언·예측 기사는 제목만 보고 먼저 버린다
  2) 골라내기 : 실제로 '일어난 사건' 을 가리키는 표현만 속보로 인정한다

제목은 요약하거나 바꾸지 않는다. 원문 그대로 두고 근거만 덧붙인다.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .timeutil import ago as _ago
from .timeutil import clock as _clock

log = logging.getLogger(__name__)

TICKER_FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
GOOGLE_NEWS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
FED_PRESS = "https://www.federalreserve.gov/feeds/press_all.xml"

# 공신력 있는 매체의 자체 피드. 검색을 거치지 않아 더 빨리 올라온다.
# 한 곳이 막혀도(봇 차단 등) 나머지로 계속 돌아간다.
WIRE_FEEDS = [
    ("Investing.com", "https://www.investing.com/rss/news.rss"),
    ("Investing.com", "https://www.investing.com/rss/news_25.rss"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("CNBC", "https://search.cnbc.com/rss/2.0/id/15839135/device/rss/rss.html"),
]

# 시장 전체를 흔드는 사건만 좁혀서 찾는다.
# when:2d 로 최근 것만, 따옴표로 정확한 표현만 잡는다.
DEFAULT_MARKET_QUERIES = [
    '("oil prices" OR crude) (surge OR spike OR plunge) when:2d',
    '(Middle East OR Israel OR Iran OR Strait of Hormuz) oil OR markets when:2d',
    'Federal Reserve (emergency OR "rate cut" OR "rate hike") decision when:2d',
    '"circuit breaker" OR "trading halted" stocks when:2d',
    'new tariffs announced when:2d',
]

BREAKING, NOTABLE, MINOR = 3, 2, 1
SEVERITY_ICON = {BREAKING: "🚨", NOTABLE: "🟠", MINOR: "🟡"}
SEVERITY_LABEL = {BREAKING: "속보", NOTABLE: "주목", MINOR: "참고"}

# --------------------------------------------------------------------------
# 매체 신뢰도
#   3 = 통신사·1차 매체. 사건을 직접 취재해 가장 먼저 낸다.
#   2 = 종합 매체. 대개 정확하지만 1차 보도를 받아 쓰는 경우가 많다.
#   1 = 그 외. 사실일 수도 있지만 확인 전까지는 참고만.
# --------------------------------------------------------------------------
TIER_NAMES = {3: "1차 매체", 2: "종합 매체", 1: "출처 확인 필요"}

PUBLISHER_TIERS: dict[str, int] = {
    # 통신사·경제 전문지
    "reuters": 3, "bloomberg": 3, "associated press": 3, "ap news": 3, "the ap": 3,
    "wall street journal": 3, "the wall street journal": 3, "wsj": 3,
    "financial times": 3, "ft": 3, "dow jones": 3, "dow jones newswires": 3,
    "cnbc": 3, "investing com": 3, "investingcom": 3, "investing": 3,
    "barrons": 3, "barron s": 3, "marketwatch": 3, "nikkei": 3, "nikkei asia": 3,
    "the economist": 3, "axios": 3, "politico": 3,
    # 기업·규제기관 원문 (가장 정확한 1차 자료)
    "business wire": 3, "businesswire": 3, "pr newswire": 3, "prnewswire": 3,
    "globenewswire": 3, "globe newswire": 3, "accesswire": 3,
    "sec gov": 3, "federal reserve": 3, "연준": 3,
    # 종합 매체
    "yahoo finance": 2, "yahoo": 2, "cnn": 2, "cnn business": 2, "bbc": 2,
    "the new york times": 2, "new york times": 2, "nyt": 2, "the washington post": 2,
    "forbes": 2, "fortune": 2, "business insider": 2, "the guardian": 2,
    "npr": 2, "abc news": 2, "nbc news": 2, "cbs news": 2, "usa today": 2,
    "techcrunch": 2, "the verge": 2, "ars technica": 2, "空": 2,
    "seeking alpha": 2, "benzinga": 2, "quartz": 2, "semafor": 2,
    "the information": 2, "sky news": 2, "al jazeera": 2, "cnbc tv18": 2,
}


def publisher_tier(name: str) -> int:
    """매체 이름 → 신뢰도. 모르는 곳은 1(참고)."""
    key = re.sub(r"[^a-z0-9가-힣 ]+", " ", (name or "").lower())
    key = " ".join(key.split())
    if not key:
        return 1
    if key in PUBLISHER_TIERS:
        return PUBLISHER_TIERS[key]
    # "Reuters Business", "CNBC International" 처럼 뒤에 말이 붙는 경우
    for known, tier in PUBLISHER_TIERS.items():
        if key.startswith(known + " ") or key.endswith(" " + known):
            return tier
    return 1

# --------------------------------------------------------------------------
# 1) 걸러내기 — 사건이 아니라 '의견·목록·예측' 인 기사
# --------------------------------------------------------------------------
JUNK_PATTERNS = [
    re.compile(r"^\s*\d+\s+(best|top|great|stocks?|reasons|things|ways)\b", re.I),
    re.compile(r"\b(\d+\s+stocks?|best stocks?|top stocks?|stocks? to (buy|watch|own|hold))\b", re.I),
    re.compile(r"\b(should you|why you should|here'?s why|here is why|what to know)\b", re.I),
    re.compile(r"\b(prediction|forecast for 20\d\d|could|might|may|would|if you)\b", re.I),
    re.compile(r"\b(history says|analysis|opinion|explained|guide|how to)\b", re.I),
    re.compile(r"\b(i asked|i bought|my \d+|this is what)\b", re.I),
    re.compile(r"\b(motley fool|zacks rank|trefis|simply wall st)\b", re.I),
    re.compile(r"\b(is it too late|worth buying|better buy|vs\.?)\b", re.I),
    re.compile(r"\?", re.I),                       # 질문형 제목은 사건 보도가 아니다
    re.compile(r"\b(waiting for|superstar|hidden gem|no[- ]brainer|screaming buy)\b", re.I),
    re.compile(r"\b(dividend (stock|king|aristocrat)s?|retirement)\b", re.I),
]

# --------------------------------------------------------------------------
# 2) 골라내기 — 실제로 벌어진 사건
# --------------------------------------------------------------------------
# 🚨 시장 전체를 흔드는 사건 (전쟁·유가·긴급 통화정책 등)
MACRO_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(war|invasion|missile strike|attack(s|ed)?)\b.{0,40}\b(oil|market|stocks)\b", re.I), "지정학 충격"),
    (re.compile(r"\b(oil|crude|brent|wti)\b.{0,30}\b(surge[sd]?|spike[sd]?|jump(s|ed)?|plunge[sd]?|soar(s|ed)?)\b", re.I), "유가 급변"),
    (re.compile(r"\bstrait of hormuz\b|\bopec\+?\s+(cut|output)", re.I), "원유 공급"),
    (re.compile(r"\b(emergency (rate|meeting)|unscheduled fomc|intermeeting cut)\b", re.I), "연준 긴급 조치"),
    (re.compile(r"\b(circuit breaker|market[- ]wide halt|trading suspended)\b", re.I), "시장 거래 중단"),
    (re.compile(r"\b(new tariffs?|tariffs? (imposed|announced|raised))\b", re.I), "관세 부과"),
    (re.compile(r"\b(default|debt ceiling breach|credit downgrade)\b.{0,30}\b(us|treasury|sovereign)\b", re.I), "국가 신용"),
    (re.compile(r"\b(bank (failure|collapse|run)|financial crisis)\b", re.I), "금융 시스템"),
]

# 🚨 개별 종목에 즉시 영향을 주는 사건
STOCK_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(halt(ed|s)?\s+trading|trading\s+(is\s+|was\s+)?halted)\b", re.I), "거래 정지"),
    (re.compile(r"\b(files?\s+for\s+)?(bankrupt(cy)?|chapter 11|insolvency)\b", re.I), "파산"),
    (re.compile(r"\b(sec (probe|investigation|charges)|accounting fraud|indicted)\b", re.I), "조사·회계 문제"),
    (re.compile(r"\b(delisted|delisting|going concern doubt)\b", re.I), "상장폐지·존속 우려"),
    (re.compile(r"\b(agrees? to (acquire|buy)|to be acquired|acquisition of|merger with|buyout offer|takeover bid)\b", re.I), "인수·합병"),
    (re.compile(r"\b(cuts?|slashe?[sd]?|lowers?|withdraws?)\s+(\w+[- ]){0,3}?(guidance|outlook|forecast)\b", re.I), "가이던스 하향"),
    (re.compile(r"\b(raises?|boosts?|lifts?)\s+(\w+[- ]){0,3}?(guidance|outlook|forecast)\b", re.I), "가이던스 상향"),
    (re.compile(r"\b(recalls?|fda (approves?|approval|rejects?|rejection)|clinical hold)\b", re.I), "규제·리콜"),
    (re.compile(r"\b(short seller report|accused of fraud)\b", re.I), "공매도 리포트"),
    (re.compile(r"\b(ceo|cfo)\s+(steps? down|resigns?|to depart|ousted|fired)\b", re.I), "핵심 경영진 교체"),
    (re.compile(r"\b(shares?|stock)\s+(plunge[sd]?|plummet(s|ed)?|tumble[sd]?|soar(s|ed)?|surge[sd]?)\b", re.I), "주가 급변"),
]

# 🟠 방향을 바꿀 수 있는 사안
NOTABLE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(beats?|misses?|tops?)\s+(estimates|expectations|forecasts)\b", re.I), "실적 서프라이즈"),
    (re.compile(r"\b(upgrade[sd]?|downgrade[sd]?)\b.{0,25}\b(to|from|by|overweight|underweight|buy|sell|hold)\b|\bprice target (raised|cut|lowered)\b", re.I), "투자의견·목표주가"),
    (re.compile(r"\b(announces? layoffs|job cuts|restructuring plan)\b", re.I), "구조조정"),
    (re.compile(r"\b(public offering|share offering|dilution|secondary offering)\b", re.I), "증자·희석"),
    (re.compile(r"\b(wins?|awarded|signs?|secures?)\b[\w\s$.,\-]{0,40}?\b(contract|deal|order|award)\b", re.I), "계약·수주"),
    (re.compile(r"\b(lawsuit filed|sued by|settles? (lawsuit|charges))\b", re.I), "소송"),
    (re.compile(r"\b(reports? (first|second|third|fourth) quarter|q[1-4] (results|earnings))\b", re.I), "실적 발표"),
    (re.compile(r"\b(fed (holds|cuts|raises)|fomc (decision|minutes)|powell says)\b", re.I), "통화정책"),
    (re.compile(r"\b(cpi|inflation) (rose|fell|came in|report)\b|\bjobs report\b|\bpayrolls (rose|fell)\b", re.I), "매크로 지표"),
]


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: datetime | None = None
    tickers: list[str] = field(default_factory=list)
    severity: int = MINOR
    reasons: list[str] = field(default_factory=list)
    macro: bool = False               # 시장 전체 사안인지
    feed_publisher: str = ""          # 피드가 알려준 매체명 (제목에 안 붙는 경우)

    @property
    def uid(self) -> str:
        return hashlib.sha1((self.url or self.title).encode("utf-8")).hexdigest()[:16]

    @property
    def icon(self) -> str:
        return SEVERITY_ICON.get(self.severity, "🟡")

    @property
    def label(self) -> str:
        return SEVERITY_LABEL.get(self.severity, "참고")

    @property
    def publisher(self) -> str:
        """Google 뉴스 제목 끝에 붙는 ' - 매체명' 을 뽑는다."""
        if " - " in self.title:
            return self.title.rsplit(" - ", 1)[1].strip()
        return self.feed_publisher

    @property
    def headline(self) -> str:
        """매체명을 뗀 제목. 원문 문장은 그대로 둔다."""
        if " - " in self.title:
            return self.title.rsplit(" - ", 1)[0].strip()
        return self.title

    @property
    def tier(self) -> int:
        return publisher_tier(self.publisher)

    @property
    def tier_label(self) -> str:
        return TIER_NAMES.get(self.tier, "출처 확인 필요")

    @property
    def kst(self) -> str:
        """한국시간 표기."""
        return _clock(self.published)

    @property
    def ago(self) -> str:
        return _ago(self.published)

    def age_minutes(self, reference: datetime | None = None) -> float | None:
        if self.published is None:
            return None
        moment = self.published
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        reference = reference or datetime.now(timezone.utc)
        return max(0.0, (reference - moment).total_seconds() / 60)

    def freshness_bucket(self, reference: datetime | None = None) -> int:
        """빠른 기사일수록 작은 값. 정렬에 쓴다.

        분 단위로 줄 세우면 5분 빠른 블로그가 로이터를 밀어낸다.
        그래서 '얼마나 최신인가' 를 구간으로 묶고, 같은 구간 안에서
        공신력으로 순서를 가린다.
        """
        minutes = self.age_minutes(reference)
        if minutes is None:
            return 4          # 시각을 모르는 기사는 뒤로
        if minutes <= 30:
            return 0
        if minutes <= 120:
            return 1
        if minutes <= 12 * 60:
            return 2
        return 3


def is_junk(title: str) -> bool:
    """사건이 아니라 의견·목록·예측 기사인지."""
    return any(pattern.search(title) for pattern in JUNK_PATTERNS)


def classify(title: str) -> tuple[int, list[str], bool]:
    """(중요도, 근거, 시장 전체 사안인지)"""
    if is_junk(title):
        return MINOR, [], False

    reasons: list[str] = []
    macro = False

    for pattern, reason in MACRO_RULES:
        if pattern.search(title):
            reasons.append(reason)
            macro = True
    for pattern, reason in STOCK_RULES:
        if pattern.search(title) and reason not in reasons:
            reasons.append(reason)

    if reasons:
        return BREAKING, reasons, macro

    for pattern, reason in NOTABLE_RULES:
        if pattern.search(title) and reason not in reasons:
            reasons.append(reason)
    if reasons:
        return NOTABLE, reasons, False

    return MINOR, [], False


def parse_feed(xml_text: str, source: str) -> list[NewsItem]:
    """RSS 2.0 과 Atom 을 모두 받아준다."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        log.debug("피드 해석 실패 (%s): %s", source, exc)
        return []

    items: list[NewsItem] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        if title:
            items.append(
                NewsItem(title=title, url=(node.findtext("link") or "").strip(),
                         source=source, published=_parse_date(node.findtext("pubDate")),
                         feed_publisher=(node.findtext("source") or "").strip())
            )
    if items:
        return items

    ns = "{http://www.w3.org/2005/Atom}"
    for node in root.iter(f"{ns}entry"):
        title = (node.findtext(f"{ns}title") or "").strip()
        link_node = node.find(f"{ns}link")
        if title:
            items.append(
                NewsItem(title=title, url=(link_node.get("href") if link_node is not None else "") or "",
                         source=source, published=_parse_date(node.findtext(f"{ns}updated")))
            )
    return items


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


class NewsWatcher:
    def __init__(self, http, state, config) -> None:
        self.http = http
        self.state = state
        self.config = config

    @property
    def settings(self) -> dict:
        raw = self.config.raw.get("news")
        return raw if isinstance(raw, dict) else {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    @property
    def min_severity(self) -> int:
        return int(self.settings.get("min_severity", BREAKING))

    @property
    def market_queries(self) -> list[str]:
        queries = self.settings.get("market_queries")
        if isinstance(queries, list) and queries:
            return [str(q) for q in queries]
        return DEFAULT_MARKET_QUERIES

    @property
    def max_age_hours(self) -> float:
        """이보다 오래된 기사는 속보가 아니다."""
        return float(self.settings.get("max_age_hours", 48))

    @property
    def min_tier(self) -> int:
        """이 신뢰도 미만인 매체는 버린다. 1=전부 허용(기본)."""
        return int(self.settings.get("min_publisher_tier", 1))

    @property
    def use_wires(self) -> bool:
        return bool(self.settings.get("wire_feeds", True))

    def _fetch(self, url: str, source: str) -> list[NewsItem]:
        try:
            text = self.http.get_text(url, timeout=20, retries=1)
        except Exception as exc:
            log.debug("뉴스 피드 실패 (%s): %s", source, exc)
            return []
        return parse_feed(text, source)

    def collect(self, tickers: list[str]) -> list[NewsItem]:
        found: list[NewsItem] = []

        for ticker in tickers:
            for item in self._fetch(TICKER_FEED.format(ticker=ticker), ticker):
                item.tickers = [ticker]
                found.append(item)

        if self.settings.get("market", True):
            found.extend(self._fetch(FED_PRESS, "연준"))
            from urllib.parse import quote_plus

            for query in self.market_queries:
                found.extend(self._fetch(GOOGLE_NEWS.format(query=quote_plus(query)), "시장"))

            # 공신력 있는 매체의 자체 피드. 매체명을 피드에서 바로 알 수 있다.
            if self.use_wires:
                for name, url in WIRE_FEEDS:
                    for item in self._fetch(url, name):
                        if not item.feed_publisher:
                            item.feed_publisher = name
                        found.append(item)

        keep: list[NewsItem] = []
        for item in found:
            severity, reasons, macro = classify(item.title)
            if severity == MINOR and not item.tickers:
                continue           # 시장 뉴스는 사건이 아니면 아예 버린다
            item.severity, item.reasons, item.macro = severity, reasons, macro
            keep.append(item)

        return _dedupe(keep)

    def _is_fresh(self, item: NewsItem, reference: datetime) -> bool:
        minutes = item.age_minutes(reference)
        if minutes is None:
            return True            # 시각이 없는 피드도 있다. 버리지는 않는다
        return minutes <= self.max_age_hours * 60

    def new_items(self, tickers: list[str]) -> list[NewsItem]:
        if not self.enabled:
            return []
        reference = datetime.now(timezone.utc)
        fresh = [
            item for item in self.collect(tickers)
            if item.severity >= self.min_severity
            and item.tier >= self.min_tier
            and self._is_fresh(item, reference)
            and not self.state.is_news_seen(item.uid)
        ]
        # 중요도 → 시장 전체 사안 → 얼마나 최신인지 → 매체 공신력 → 정확한 시각
        fresh.sort(
            key=lambda i: (
                -i.severity,
                not i.macro,
                i.freshness_bucket(reference),
                -i.tier,
                -(i.published or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            )
        )
        return fresh[: int(self.settings.get("max_per_check", 8))]

    def mark_sent(self, items: list[NewsItem]) -> None:
        for item in items:
            self.state.mark_news_seen(item.uid)
            self.state.add_news(
                {
                    "title": item.headline,
                    "publisher": item.publisher,
                    "url": item.url,
                    "source": item.source,
                    "severity": item.severity,
                    "reasons": item.reasons,
                    "tickers": item.tickers,
                    "macro": item.macro,
                    "tier": item.tier,
                    "when": (item.published or datetime.now(timezone.utc)).isoformat(timespec="minutes"),
                }
            )


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """같은 기사가 여러 피드에 겹친다. 제목 기준으로 하나만 남긴다.

    남길 하나는 **더 믿을 만한 매체의 것**으로 고른다. 같은 사건을
    로이터와 이름 모를 블로그가 동시에 쓰면 로이터 쪽을 보여준다.
    """
    seen: dict[str, NewsItem] = {}
    for item in items:
        key = re.sub(r"[^a-z0-9]+", "", item.headline.lower())[:60]
        current = seen.get(key)
        if current is None:
            seen[key] = item
            continue

        if item.tier > current.tier:
            keeper, other = item, current
            seen[key] = item
        else:
            keeper, other = current, item

        keeper.severity = max(keeper.severity, other.severity)
        keeper.macro = keeper.macro or other.macro
        for reason in other.reasons:
            if reason not in keeper.reasons:
                keeper.reasons.append(reason)
        for ticker in other.tickers:
            if ticker not in keeper.tickers:
                keeper.tickers.append(ticker)
    return list(seen.values())
