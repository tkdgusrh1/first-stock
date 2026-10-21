"""속보 감시.

가장 중요한 건 '진짜 사건' 과 '조회수용 기사' 를 가르는 일이다.
실제로 화면에 잘못 올라왔던 제목들을 그대로 회귀 테스트로 박아둔다.
"""

from datetime import datetime, timedelta, timezone

import pytest

from stock_analysis.messages import format_news
from stock_analysis.news import (
    BREAKING,
    MINOR,
    NOTABLE,
    NewsItem,
    NewsWatcher,
    classify,
    is_junk,
    parse_feed,
    publisher_tier,
)

def _ago(hours: float, atom: bool = False) -> str:
    """지금으로부터 몇 시간 전. 고정 날짜를 쓰면 48시간 신선도 규칙에 걸려
    어느 날 갑자기 테스트가 깨진다 (실제로 깨졌다)."""
    when = datetime.now(timezone.utc) - timedelta(hours=hours)
    if atom:
        return when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return when.strftime("%a, %d %b %Y %H:%M:%S GMT")


RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Rocket Lab halts trading pending SEC investigation</title>
  <link>https://news/1</link><pubDate>{_ago(2)}</pubDate></item>
<item><title>Nvidia beats estimates and raises guidance for Q4</title>
  <link>https://news/2</link><pubDate>{_ago(3)}</pubDate></item>
<item><title>Company announces new office decoration policy</title>
  <link>https://news/3</link></item>
</channel></rss>"""

ATOM = f"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Federal Reserve announces rate decision</title>
  <link href="https://fed/1"/><updated>{_ago(4, atom=True)}</updated></entry>
</feed>"""


# --- 피드 해석 --------------------------------------------------------------
def test_rss_is_parsed():
    items = parse_feed(RSS, "테스트")
    assert len(items) == 3
    assert items[0].url == "https://news/1"
    assert items[0].published.year == 2026


def test_atom_is_parsed():
    items = parse_feed(ATOM, "연준")
    assert len(items) == 1 and items[0].url == "https://fed/1"


def test_broken_feed_returns_nothing():
    assert parse_feed("<not xml", "x") == []
    assert parse_feed("", "x") == []


def test_publisher_is_split_from_the_headline():
    item = NewsItem(title="Oil prices surge 8% after attack - Reuters", url="", source="시장")
    assert item.publisher == "Reuters"
    assert item.headline == "Oil prices surge 8% after attack"


# --- 가짜 속보 걸러내기 (실제로 잘못 올라왔던 제목들) -------------------------
@pytest.mark.parametrize(
    "title",
    [
        "Tesla Stock's Deepest Fall Was Worse Than Its Worst Market Crash - Trefis",
        "I asked ChatGPT if the AI stock market crash has already started - The Twelfth Magpie",
        "3 Stocks to Buy and Hold Even If There's a Stock Market Sell-Off in August",
        "If a Stock Market Crash Is Coming, History Says Investors Who Do This Will Profit",
        "Waiting for a stock market crash? This FTSE 100 superstar just fell 20%",
        "5 Best Dividend Stocks for Retirement",
        "Should you buy Nvidia stock before earnings?",
        "Here's why Apple could reach $300 by 2027",
    ],
)
def test_clickbait_is_not_breaking_news(title):
    assert is_junk(title), f"걸러지지 않았습니다: {title}"
    severity, reasons, _ = classify(title)
    assert severity == MINOR
    assert reasons == []


# --- 진짜 속보 --------------------------------------------------------------
@pytest.mark.parametrize(
    "title,macro",
    [
        ("Oil prices surge 8% after missile attack on Strait of Hormuz shipping", True),
        ("Fed announces emergency rate cut as credit markets seize", True),
        ("US imposes new tariffs on Chinese semiconductors", True),
        ("Trading halted in shares of XYZ Corp", False),
        ("XYZ files for Chapter 11 bankruptcy", False),
        ("Nvidia agrees to acquire Arm for $40 billion", False),
        ("Boeing cuts its full-year guidance", False),
        ("Apple raises full-year guidance after record quarter", False),
        ("CEO steps down amid SEC investigation", False),
        ("Rocket Lab shares plunge after contract loss", False),
    ],
)
def test_real_events_are_breaking(title, macro):
    severity, reasons, is_macro = classify(title)
    assert severity == BREAKING, f"속보로 잡히지 않았습니다: {title}"
    assert reasons
    assert is_macro == macro


@pytest.mark.parametrize(
    "title",
    [
        "XYZ beats estimates for the third quarter",
        "Analysts upgrade XYZ to overweight",
        "XYZ announces layoffs of 5,000 workers",
        "Rocket Lab wins $500 million launch contract",
        "Markets await CPI report",
    ],
)
def test_notable_events(title):
    severity, reasons, _ = classify(title)
    assert severity == NOTABLE
    assert reasons


def test_reasons_explain_the_rating():
    severity, reasons, _ = classify("XYZ halts trading after SEC investigation")
    assert severity == BREAKING
    assert "거래 정지" in reasons and "조사·회계 문제" in reasons


def test_ordinary_news_has_no_reason():
    severity, reasons, _ = classify("Company publishes annual sustainability brochure")
    assert severity == MINOR and reasons == []


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


def test_ticker_news_is_tagged():
    w = watcher({"headline?s=RKLB": RSS}, {"market": False})
    items = w.collect(["RKLB"])
    assert items and all(i.tickers == ["RKLB"] for i in items)


def test_market_news_without_an_event_is_dropped():
    """관심 종목과 무관하면서 사건도 아닌 기사는 아예 담지 않는다."""
    plain = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Weekly market wrap and commentary</title><link>https://x/1</link></item>
    </channel></rss>"""
    w = watcher({"news.google.com": plain}, {"market": True, "market_queries": ["x"]})
    assert w.collect([]) == []


def test_default_threshold_is_breaking_only():
    w = watcher({}, {})
    assert w.min_severity == BREAKING


def test_only_breaking_reaches_the_user_by_default():
    w = watcher({"headline?s=RKLB": RSS}, {"market": False})
    items = w.new_items(["RKLB"])
    assert items
    assert all(i.severity >= BREAKING for i in items)
    assert not any("decoration policy" in i.title for i in items)


def test_seen_items_are_not_repeated():
    w = watcher({"headline?s=RKLB": RSS}, {"market": False})
    first = w.new_items(["RKLB"])
    assert first
    w.mark_sent(first)
    assert w.new_items(["RKLB"]) == []


def test_macro_items_come_first():
    macro = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Oil prices surge after attack on shipping lanes</title><link>https://x/9</link></item>
    </channel></rss>"""
    w = watcher({"headline?s=RKLB": RSS, "news.google.com": macro},
                {"market": True, "market_queries": ["oil"]})
    items = w.new_items(["RKLB"])
    assert items[0].macro is True


def test_saved_entry_keeps_publisher_and_macro_flag():
    w = watcher({"headline?s=RKLB": RSS}, {"market": False})
    items = w.new_items(["RKLB"])
    w.mark_sent(items)
    saved = w.state.saved[0]
    assert "title" in saved and "reasons" in saved
    assert "macro" in saved and "publisher" in saved


def test_disabled_watcher_returns_nothing():
    assert watcher({"headline?s=RKLB": RSS}, {"enabled": False}).new_items(["RKLB"]) == []


def test_duplicate_headlines_are_merged():
    w = watcher({"headline?s=RKLB": RSS, "headline?s=NVDA": RSS}, {"market": False})
    items = w.collect(["RKLB", "NVDA"])
    assert len({i.headline for i in items}) == len(items)
    merged = next(i for i in items if "halts trading" in i.title)
    assert set(merged.tickers) == {"RKLB", "NVDA"}


def test_results_are_capped():
    w = watcher({"headline?s=RKLB": RSS}, {"market": False, "max_per_check": 1})
    assert len(w.new_items(["RKLB"])) == 1


# --- 매체 신뢰도 ------------------------------------------------------------
@pytest.mark.parametrize(
    "name,tier",
    [
        ("Reuters", 3), ("Bloomberg", 3), ("Investing.com", 3), ("CNBC", 3),
        ("The Wall Street Journal", 3), ("MarketWatch", 3), ("Business Wire", 3),
        ("Yahoo Finance", 2), ("Forbes", 2), ("Business Insider", 2),
        ("The Twelfth Magpie", 1), ("", 1), ("Some Random Blog", 1),
    ],
)
def test_publishers_are_ranked(name, tier):
    assert publisher_tier(name) == tier


def test_publisher_suffixes_still_match():
    assert publisher_tier("Reuters Business") == 3
    assert publisher_tier("CNBC International") == 3


def test_the_trusted_source_survives_deduplication():
    """같은 사건을 두 곳이 쓰면 믿을 만한 쪽을 남긴다."""
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Oil prices surge after attack on tankers - Reuters</title>
      <link>https://a</link></item>
    <item><title>Oil prices surge after attack on tankers - Daily Musings</title>
      <link>https://b</link></item>
    </channel></rss>"""
    w = watcher({"news.google.com": feed}, {"market": True, "market_queries": ["oil"],
                                            "wire_feeds": False})
    items = w.collect([])
    assert len(items) == 1
    assert items[0].publisher == "Reuters"


# --- 시간 -------------------------------------------------------------------
def test_time_is_shown_in_korean_time():
    item = NewsItem(title="x", url="", source="s",
                    published=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc))
    assert item.kst == "08-12 22:00"        # UTC+9


def test_elapsed_time_is_spelled_out():
    now = datetime.now(timezone.utc)
    assert NewsItem(title="x", url="", source="s",
                    published=now - timedelta(minutes=7)).ago == "7분 전"
    assert NewsItem(title="x", url="", source="s",
                    published=now - timedelta(hours=5)).ago == "5시간 전"


def test_stale_articles_are_not_breaking_news():
    old = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    feed = f"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Rocket Lab halts trading pending SEC investigation</title>
      <link>https://old</link><pubDate>{old}</pubDate></item>
    </channel></rss>"""
    w = watcher({"headline?s=RKLB": feed}, {"market": False})
    assert w.new_items(["RKLB"]) == []


def test_fresher_news_comes_first():
    now = datetime.now(timezone.utc)
    def stamp(minutes):
        return (now - timedelta(minutes=minutes)).strftime("%a, %d %b %Y %H:%M:%S GMT")

    feed = f"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>XYZ files for Chapter 11 bankruptcy - Daily Musings</title>
      <link>https://old2</link><pubDate>{stamp(600)}</pubDate></item>
    <item><title>Trading halted in shares of ABC Corp - Daily Musings</title>
      <link>https://new</link><pubDate>{stamp(5)}</pubDate></item>
    </channel></rss>"""
    w = watcher({"headline?s=RKLB": feed}, {"market": False})
    items = w.new_items(["RKLB"])
    assert "Trading halted" in items[0].title


def test_credibility_breaks_ties_within_the_same_freshness():
    now = datetime.now(timezone.utc)
    def stamp(minutes):
        return (now - timedelta(minutes=minutes)).strftime("%a, %d %b %Y %H:%M:%S GMT")

    feed = f"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>XYZ files for Chapter 11 bankruptcy - Daily Musings</title>
      <link>https://x</link><pubDate>{stamp(10)}</pubDate></item>
    <item><title>Trading halted in shares of ABC Corp - Reuters</title>
      <link>https://y</link><pubDate>{stamp(20)}</pubDate></item>
    </channel></rss>"""
    w = watcher({"headline?s=RKLB": feed}, {"market": False})
    items = w.new_items(["RKLB"])
    assert items[0].publisher == "Reuters"      # 둘 다 30분 이내 → 공신력이 가른다


def test_low_tier_sources_can_be_filtered_out():
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>XYZ files for Chapter 11 bankruptcy - Daily Musings</title>
      <link>https://x</link></item>
    </channel></rss>"""
    w = watcher({"headline?s=RKLB": feed}, {"market": False, "min_publisher_tier": 3})
    assert w.new_items(["RKLB"]) == []


def test_wire_feeds_are_consulted():
    w = watcher({"investing.com": RSS}, {"market": True, "market_queries": []})
    w.collect([])
    assert any("investing.com" in url for url in w.http.calls)


def test_wire_feeds_can_be_turned_off():
    w = watcher({}, {"market": True, "market_queries": [], "wire_feeds": False})
    w.collect([])
    assert not any("investing.com" in url for url in w.http.calls)


def test_feed_supplied_publisher_is_used_when_the_title_has_none():
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Oil prices surge after attack on shipping</title>
      <link>https://x</link><source url="https://reuters.com">Reuters</source></item>
    </channel></rss>"""
    items = parse_feed(feed, "시장")
    assert items[0].publisher == "Reuters"
    assert items[0].tier == 3


# --- 알림 형식 --------------------------------------------------------------
def test_news_message_keeps_the_headline_verbatim():
    item = NewsItem(
        title="Rocket Lab halts trading pending SEC investigation",
        url="https://news/1", source="RKLB", tickers=["RKLB"],
        severity=BREAKING, reasons=["거래 정지"],
    )
    text = format_news(item)
    assert "Rocket Lab halts trading pending SEC investigation" in text
    assert "속보" in text and "RKLB" in text and "거래 정지" in text
    assert "https://news/1" in text
    assert "원문 그대로" in text


def test_news_message_shows_korean_time_and_source_quality():
    item = NewsItem(
        title="Oil prices surge 8% after attack - Reuters",
        url="https://news/2", source="시장", severity=BREAKING, reasons=["유가 급변"],
        published=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), macro=True,
    )
    text = format_news(item)
    assert "08-12 22:00 한국시간" in text
    assert "Reuters" in text and "1차 매체" in text


# --- 주가를 움직이는 지표·정책 (예전엔 유가·중동만 속보였다) -----------------------
@pytest.mark.parametrize(
    "title,reason",
    [
        ("Fed holds rates steady, Powell says inflation is easing", "연준 금리 결정"),
        ("US consumer prices rose 0.4% in September, hotter than forecast", "물가 지표"),
        ("Jobs report: payrolls rose more than expected", "고용 지표"),
        ("Treasury yields jump to highest since July", "국채 금리 급변"),
        ("S&P 500 tumbles 2% as tech sells off", "증시 급변"),
        ("US tightens export controls on AI chips to China", "수출 규제"),
    ],
)
def test_market_moving_data_is_breaking(title, reason):
    severity, reasons, macro = classify(title)
    assert severity == BREAKING and reason in reasons and macro


@pytest.mark.parametrize("title", ["Fed expected to cut rates next week", "Fed will hold rates, traders bet"])
def test_forecasts_of_a_decision_are_not_breaking(title):
    assert classify(title)[0] != BREAKING


def test_megacap_results_are_breaking_but_not_marketwide():
    severity, reasons, macro = classify("Nvidia beats estimates, raises outlook")
    assert severity == BREAKING and "대형주 실적" in reasons and not macro


def test_one_topic_cannot_fill_every_slot():
    """유가 기사 여덟 건이 자리를 다 차지하면 금리·지표 소식이 밀려난다."""
    from stock_analysis.news import PER_TOPIC, NewsItem, _spread

    oil = [NewsItem(title=f"Oil prices surge {k}%", url=f"u{k}", source="x", severity=BREAKING,
                    reasons=["유가 급변"], macro=True) for k in range(6)]
    fed = NewsItem(title="Fed cuts rates", url="f", source="x", severity=BREAKING,
                   reasons=["연준 금리 결정"], macro=True)
    picked, held = _spread(oil + [fed], 4)
    assert sum(1 for i in picked if i.reasons == ["유가 급변"]) == PER_TOPIC
    assert fed in picked and len(held) == 4


def test_capped_items_are_recorded_quietly_not_sent_later():
    """묶인 기사를 표시해 두지 않으면 다음 확인 때 새 기사로 다시 올라와 두 건씩 계속 울린다."""
    items = "".join(f"<item><title>Oil prices surge {k}% after attack</title><link>https://oil/{k}</link>"
                    f"<pubDate>{_ago(1)}</pubDate></item>" for k in range(6))
    feed = f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'
    w = watcher({"news.google.com": feed}, {"market": True, "market_queries": ["oil"], "wire_feeds": False})
    first = w.new_items([])
    assert len(first) == 2
    w.mark_sent(first)
    assert w.new_items([]) == []                         # 나머지 4건은 다시 오지 않는다
    assert len(w.state.saved) == 6                       # 화면 목록에는 전부 남는다


@pytest.mark.parametrize(
    "title",
    [
        "Microsoft Outlook outage hits thousands of users",
        "Apple tops list of most valuable brands",
        "Nvidia set to report earnings; investors eye guidance",
        "Tesla stock forecast: analysts raise targets",
        "Google Forecast tool now in Search",
        "Stocks close at record highs",
        "Penny stocks soar",
        "Dow futures point to record high open",
        "Japan inflation slows in September",
        "Mortgage rates fall after Fed cut rates last month",
        "New Zealand Q3 inflation rises 0.7% from previous quarter, lower than forecast",
        "Nasdaq Leads Wall Street Higher as SpaceX Soars Ahead of Meta, Tesla Earnings",
        "Top Democrat says chip restrictions are key lever for US AI edge over China",
        "China revises drone export controls for dual-use items",
        "Trading suspended in shares of XYZ",
    ],
)
def test_lookalike_headlines_do_not_trigger_alerts(title):
    """속보 = 텔레그램 알림이다. 비슷해 보이는 제목이 울리면 알림 전체를 안 보게 된다."""
    assert classify(title)[0] != BREAKING


@pytest.mark.parametrize("title", ["Fed holds rates steady, says it will wait for more data",
                                   "Fed cuts rates, signals more cuts likely",
                                   "May jobs report: payrolls rose by 200,000"])
def test_real_decisions_with_hedged_words_still_count(title):
    assert classify(title)[0] == BREAKING


def test_lower_than_forecast_is_not_a_guidance_cut():
    assert "가이던스 하향" not in classify("Retail sales came in lower than forecast")[1]


def test_unknown_companies_from_market_searches_do_not_ring():
    """관심 종목도 시장 전체도 아닌 회사의 급락은 시장 검색에 섞여 들어와도 알림으로 울리지 않는다."""
    feed = (f'<?xml version="1.0"?><rss version="2.0"><channel><item><title>Tiny Corp shares plunge after audit</title>'
            f'<link>https://t/1</link><pubDate>{_ago(1)}</pubDate></item></channel></rss>')
    w = watcher({"news.google.com": feed}, {"market": True, "market_queries": ["x"], "wire_feeds": False})
    assert w.new_items([]) == []


def test_oil_and_mideast_count_as_one_topic():
    from stock_analysis.news import NewsItem, _spread

    def item(k, reason):
        return NewsItem(title=f"t{k}", url=f"u{k}", source="x", severity=BREAKING, reasons=[reason], macro=True)

    mix = [item(1, "유가 급변"), item(2, "지정학 충격"), item(3, "원유 공급"), item(4, "연준 금리 결정")]
    picked, held = _spread(mix, 8)
    assert len(held) == 1 and mix[3] in picked
