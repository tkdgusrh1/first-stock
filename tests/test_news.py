"""속보 감시. 먼저 띄워주는 쪽이라 중요도 판정과 중복 제거가 핵심이다."""

import pytest

from stockbot.messages import format_news
from stockbot.news import (
    BREAKING,
    MINOR,
    NOTABLE,
    NewsItem,
    NewsWatcher,
    classify,
    parse_feed,
)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Rocket Lab halts trading pending SEC investigation</title>
  <link>https://news/1</link><pubDate>Tue, 11 Aug 2026 12:00:00 GMT</pubDate></item>
<item><title>Nvidia beats estimates and raises guidance for Q4</title>
  <link>https://news/2</link><pubDate>Tue, 11 Aug 2026 11:00:00 GMT</pubDate></item>
<item><title>Company announces new office decoration policy</title>
  <link>https://news/3</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Federal Reserve announces rate decision</title>
  <link href="https://fed/1"/><updated>2026-08-11T18:00:00Z</updated></entry>
</feed>"""


# --- 피드 해석 --------------------------------------------------------------
def test_rss_is_parsed():
    items = parse_feed(RSS, "테스트")
    assert len(items) == 3
    assert items[0].title.startswith("Rocket Lab halts")
    assert items[0].url == "https://news/1"
    assert items[0].published.year == 2026


def test_atom_is_parsed():
    items = parse_feed(ATOM, "연준")
    assert len(items) == 1
    assert items[0].url == "https://fed/1"
    assert items[0].published is not None


def test_broken_feed_returns_nothing():
    assert parse_feed("<not xml", "x") == []
    assert parse_feed("", "x") == []


# --- 중요도 ----------------------------------------------------------------
@pytest.mark.parametrize(
    "title,expected",
    [
        ("Trading halted in shares of XYZ", BREAKING),
        ("XYZ files for Chapter 11 bankruptcy", BREAKING),
        ("Acme to acquire Beta Corp for $2 billion", BREAKING),
        ("XYZ cuts its full-year guidance", BREAKING),
        ("XYZ raises guidance after strong quarter", BREAKING),
        ("CEO steps down amid probe", BREAKING),
        ("Analysts upgrade XYZ with higher price target", NOTABLE),
        ("XYZ announces layoffs", NOTABLE),
        ("Fed signals rate cut in September", NOTABLE),
        ("New office decoration policy announced", MINOR),
    ],
)
def test_severity_rules(title, expected):
    severity, _ = classify(title)
    assert severity == expected


def test_reasons_are_reported():
    severity, reasons = classify("XYZ halts trading after SEC investigation")
    assert severity == BREAKING
    assert "거래 정지" in reasons
    assert "조사·회계 문제" in reasons


def test_plain_news_has_no_reason():
    severity, reasons = classify("Company publishes annual sustainability brochure")
    assert severity == MINOR
    assert reasons == []


# --- 수집 ------------------------------------------------------------------
class FakeFeedHttp:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get_text(self, url, **kwargs):
        self.calls.append(url)
        for key, payload in self.mapping.items():
            if key in url:
                return payload
        return "<rss version='2.0'><channel></channel></rss>"


class FakeState:
    def __init__(self):
        self.seen = set()
        self.saved = []

    def is_news_seen(self, uid):
        return uid in self.seen

    def mark_news_seen(self, uid):
        self.seen.add(uid)

    def add_news(self, entry):
        self.saved.append(entry)


class FakeConfig:
    def __init__(self, news=None):
        self.raw = {"news": news if news is not None else {}}


def watcher(mapping, news=None):
    return NewsWatcher(FakeFeedHttp(mapping), FakeState(), FakeConfig(news))


def test_ticker_news_is_tagged_with_the_ticker():
    w = watcher({"headline?s=RKLB": RSS}, {"market": False})
    items = w.collect(["RKLB"])
    assert all(i.tickers == ["RKLB"] for i in items)


def test_watchlist_news_gets_a_severity_boost():
    """관심 종목 뉴스는 한 단계 더 중요하게 본다."""
    headline = "Company announces new office decoration policy"
    assert classify(headline)[0] == MINOR          # 그냥 보면 '참고'

    w = watcher({"headline?s=RKLB": RSS}, {"market": False})
    boosted = {i.title: i.severity for i in w.collect(["RKLB"])}
    assert boosted[headline] == NOTABLE            # 관심 종목이면 한 단계 위


def test_only_new_items_above_threshold_are_returned():
    w = watcher({"headline?s=RKLB": RSS}, {"market": False, "min_severity": BREAKING})
    items = w.new_items(["RKLB"])
    assert items
    assert all(i.severity >= BREAKING for i in items)


def test_seen_items_are_not_repeated():
    w = watcher({"headline?s=RKLB": RSS}, {"market": False})
    first = w.new_items(["RKLB"])
    assert first
    w.mark_sent(first)
    assert w.new_items(["RKLB"]) == []


def test_disabled_watcher_returns_nothing():
    w = watcher({"headline?s=RKLB": RSS}, {"enabled": False})
    assert w.new_items(["RKLB"]) == []


def test_duplicate_headlines_are_merged():
    w = watcher({"headline?s=RKLB": RSS, "headline?s=NVDA": RSS}, {"market": False})
    items = w.collect(["RKLB", "NVDA"])
    titles = [i.title for i in items]
    assert len(titles) == len(set(titles))
    merged = next(i for i in items if "Rocket Lab halts" in i.title)
    assert set(merged.tickers) == {"RKLB", "NVDA"}


def test_market_feeds_are_fetched_when_enabled():
    w = watcher({"federalreserve": ATOM}, {"market": True, "market_queries": ["inflation"]})
    w.collect([])
    urls = " ".join(w.http.calls)
    assert "federalreserve.gov" in urls
    assert "news.google.com" in urls


def test_results_are_capped():
    w = watcher({"headline?s=RKLB": RSS}, {"market": False, "min_severity": MINOR, "max_per_check": 1})
    assert len(w.new_items(["RKLB"])) == 1


# --- 알림 형식 --------------------------------------------------------------
def test_news_message_keeps_the_headline_verbatim():
    item = NewsItem(
        title="Rocket Lab halts trading pending SEC investigation",
        url="https://news/1",
        source="RKLB 뉴스",
        tickers=["RKLB"],
        severity=BREAKING,
        reasons=["거래 정지"],
    )
    text = format_news(item)
    assert "Rocket Lab halts trading pending SEC investigation" in text
    assert "속보" in text
    assert "RKLB" in text
    assert "거래 정지" in text
    assert "https://news/1" in text
    assert "원문 그대로" in text        # 가공하지 않았음을 밝힌다
