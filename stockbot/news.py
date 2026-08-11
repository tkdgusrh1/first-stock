"""속보 감시.

내가 찾아보는 게 아니라 먼저 띄워주는 쪽. API 키 없이 쓸 수 있는
공개 RSS 만 사용한다.

  · 종목별 뉴스  : Yahoo Finance 티커 피드
  · 시장 전체    : 연준 보도자료, Google 뉴스 검색 피드
  · 중요도 판정  : 제목에 담긴 표현으로 3단계 (🚨 속보 / 🟠 주목 / 🟡 참고)

제목을 요약하거나 바꾸지 않는다. 원문 제목 그대로 보여주고 링크를 단다.
중요도는 '왜 그렇게 봤는지' 근거 표현을 함께 남긴다.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)

TICKER_FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
GOOGLE_NEWS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
FED_PRESS = "https://www.federalreserve.gov/feeds/press_all.xml"

# 시장 전체에 영향을 주는 주제 (Google 뉴스 검색어)
DEFAULT_MARKET_QUERIES = [
    "stock market selloff OR rally",
    "Federal Reserve interest rate decision",
    "US inflation CPI report",
    "tariffs trade war stocks",
]

BREAKING, NOTABLE, MINOR = 3, 2, 1
SEVERITY_ICON = {BREAKING: "🚨", NOTABLE: "🟠", MINOR: "🟡"}
SEVERITY_LABEL = {BREAKING: "속보", NOTABLE: "주목", MINOR: "참고"}

# 제목에 이 표현이 있으면 그 등급으로 본다. (표현, 한글 설명)
RULES: list[tuple[int, re.Pattern, str]] = [
    # --- 속보급: 주가가 즉시 크게 움직일 사안 ---------------------------
    (BREAKING, re.compile(r"\b(halt(ed|s)?\s+trading|trading\s+(is\s+|was\s+)?halted|trading halt)\b", re.I), "거래 정지"),
    (BREAKING, re.compile(r"\b(bankrupt|chapter 11|insolven)", re.I), "파산"),
    (BREAKING, re.compile(r"\b(sec (probe|investigation)|fraud|accounting scandal)\b", re.I), "조사·회계 문제"),
    (BREAKING, re.compile(r"\b(delist|going concern)\b", re.I), "상장폐지·존속 우려"),
    (BREAKING, re.compile(r"\b(acquire[sd]?|acquisition|merger|to buy|buyout|takeover)\b", re.I), "인수·합병"),
    (BREAKING, re.compile(r"\b(cuts?|slashe?[sd]?|lowers?|withdraw[sn]?)\s+(\w+[- ]){0,3}?(guidance|outlook|forecast)", re.I), "가이던스 하향"),
    (BREAKING, re.compile(r"\b(raises?|boosts?|lifts?|hikes?)\s+(\w+[- ]){0,3}?(guidance|outlook|forecast)", re.I), "가이던스 상향"),
    (BREAKING, re.compile(r"\b(recall|fda (approval|rejection|reject)|clinical hold)\b", re.I), "규제·리콜"),
    (BREAKING, re.compile(r"\b(short seller|short report)\b", re.I), "공매도 리포트"),
    (BREAKING, re.compile(r"\b(ceo|cfo)\s+(step(s|ped)? down|resign|depart|out)\b", re.I), "핵심 경영진 교체"),
    (BREAKING, re.compile(r"\b(plunge[sd]?|plummet|crash(es|ed)?|soar[sd]?|surge[sd]?)\b", re.I), "급등락"),
    # --- 주목: 방향을 바꿀 수 있는 사안 ---------------------------------
    (NOTABLE, re.compile(r"\b(beats?|misses?|tops?)\s+(estimates|expectations|forecasts)", re.I), "실적 서프라이즈"),
    (NOTABLE, re.compile(r"\b(upgrade[sd]?|downgrade[sd]?|price target)\b", re.I), "투자의견·목표주가"),
    (NOTABLE, re.compile(r"\b(layoff|job cuts|restructur)", re.I), "구조조정"),
    (NOTABLE, re.compile(r"\b(offering|dilut|secondary)\b", re.I), "증자·희석"),
    (NOTABLE, re.compile(r"\b(contract|award|deal|partnership|order)\b", re.I), "계약·수주"),
    (NOTABLE, re.compile(r"\b(lawsuit|sue[sd]?|litigation|settlement)\b", re.I), "소송"),
    (NOTABLE, re.compile(r"\b(earnings|quarterly results|reports q[1-4])\b", re.I), "실적 발표"),
    (NOTABLE, re.compile(r"\b(rate (cut|hike|decision)|fomc|federal reserve|powell)\b", re.I), "통화정책"),
    (NOTABLE, re.compile(r"\b(inflation|cpi|pce|jobs report|payrolls|recession)\b", re.I), "매크로 지표"),
    (NOTABLE, re.compile(r"\b(tariff|trade war|export (ban|control)|sanction)\b", re.I), "무역·규제"),
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

    @property
    def uid(self) -> str:
        base = self.url or self.title
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    @property
    def icon(self) -> str:
        return SEVERITY_ICON.get(self.severity, "🟡")

    @property
    def label(self) -> str:
        return SEVERITY_LABEL.get(self.severity, "참고")


def classify(title: str) -> tuple[int, list[str]]:
    """제목만 보고 중요도를 매긴다. 근거가 된 표현도 돌려준다."""
    severity, reasons = MINOR, []
    for level, pattern, reason in RULES:
        if pattern.search(title):
            if reason not in reasons:
                reasons.append(reason)
            severity = max(severity, level)
    return severity, reasons


def parse_feed(xml_text: str, source: str) -> list[NewsItem]:
    """RSS 2.0 과 Atom 을 모두 받아준다."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        log.debug("피드 해석 실패 (%s): %s", source, exc)
        return []

    items: list[NewsItem] = []
    # RSS 2.0
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title:
            continue
        items.append(
            NewsItem(title=title, url=link, source=source,
                     published=_parse_date(node.findtext("pubDate")))
        )
    if items:
        return items

    # Atom
    ns = "{http://www.w3.org/2005/Atom}"
    for node in root.iter(f"{ns}entry"):
        title = (node.findtext(f"{ns}title") or "").strip()
        link_node = node.find(f"{ns}link")
        link = (link_node.get("href") if link_node is not None else "") or ""
        if not title:
            continue
        items.append(
            NewsItem(title=title, url=link, source=source,
                     published=_parse_date(node.findtext(f"{ns}updated")))
        )
    return items


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class NewsWatcher:
    def __init__(self, http, state, config) -> None:
        self.http = http
        self.state = state
        self.config = config

    # --- 설정 -----------------------------------------------------------
    @property
    def settings(self) -> dict:
        raw = self.config.raw.get("news")
        return raw if isinstance(raw, dict) else {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    @property
    def min_severity(self) -> int:
        return int(self.settings.get("min_severity", NOTABLE))

    @property
    def market_queries(self) -> list[str]:
        queries = self.settings.get("market_queries")
        if isinstance(queries, list) and queries:
            return [str(q) for q in queries]
        return DEFAULT_MARKET_QUERIES

    # --- 수집 -----------------------------------------------------------
    def _fetch(self, url: str, source: str) -> list[NewsItem]:
        try:
            text = self.http.get_text(url, timeout=30)
        except Exception as exc:
            log.debug("뉴스 피드 실패 (%s): %s", source, exc)
            return []
        return parse_feed(text, source)

    def collect(self, tickers: list[str]) -> list[NewsItem]:
        """관심 종목 뉴스 + 시장 전체 뉴스를 모아 중요도를 매긴다."""
        found: list[NewsItem] = []

        for ticker in tickers:
            for item in self._fetch(TICKER_FEED.format(ticker=ticker), f"{ticker} 뉴스"):
                item.tickers = [ticker]
                found.append(item)

        if self.settings.get("market", True):
            for item in self._fetch(FED_PRESS, "연준"):
                found.append(item)
            for query in self.market_queries:
                from urllib.parse import quote_plus

                for item in self._fetch(GOOGLE_NEWS.format(query=quote_plus(query)), "시장"):
                    found.append(item)

        for item in found:
            item.severity, item.reasons = classify(item.title)
            # 관심 종목 뉴스는 한 단계 더 중요하게 본다
            if item.tickers and item.severity < BREAKING:
                item.severity = min(BREAKING, item.severity + 1)

        return _dedupe(found)

    def new_items(self, tickers: list[str]) -> list[NewsItem]:
        """아직 안 보여준 것 중 기준 이상만."""
        if not self.enabled:
            return []
        fresh = []
        for item in self.collect(tickers):
            if item.severity < self.min_severity:
                continue
            if self.state.is_news_seen(item.uid):
                continue
            fresh.append(item)
        fresh.sort(key=lambda i: (-i.severity, i.published or datetime.min.replace(tzinfo=timezone.utc)))
        return fresh[: int(self.settings.get("max_per_check", 12))]

    def mark_sent(self, items: list[NewsItem]) -> None:
        for item in items:
            self.state.mark_news_seen(item.uid)
            self.state.add_news(
                {
                    "title": item.title,
                    "url": item.url,
                    "source": item.source,
                    "severity": item.severity,
                    "reasons": item.reasons,
                    "tickers": item.tickers,
                    "when": (item.published or datetime.now(timezone.utc)).isoformat(timespec="minutes"),
                }
            )


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """같은 기사가 여러 피드에 겹쳐 들어온다. 제목 기준으로 하나만 남긴다."""
    seen: dict[str, NewsItem] = {}
    for item in items:
        key = re.sub(r"[^a-z0-9]+", "", item.title.lower())[:60]
        current = seen.get(key)
        if current is None:
            seen[key] = item
        else:
            # 종목이 붙은 쪽을 남기고 종목 목록은 합친다
            for ticker in item.tickers:
                if ticker not in current.tickers:
                    current.tickers.append(ticker)
    return list(seen.values())
