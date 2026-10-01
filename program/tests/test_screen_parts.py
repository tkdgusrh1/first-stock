"""새 화면에 붙은 것들 — 목표가·장중 거래량·종목 뉴스·일정 시각·조각 주소.

여기 있는 값은 전부 **바깥에서 받아야** 한다. 그래서 시험하는 약속도 같다:
받은 것만 보여주고, 못 받으면 못 받았다고 적고, 빈칸을 추정으로 메우지 않는다.
"""

import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest

from stock_analysis.dashboard import Dashboard, _safe_back, start_dashboard
from stock_analysis.estimates import EstimateClient, Profile, Rating, parse_profile
from stock_analysis.news import latest_headlines, short_name, ticker_news
from stock_analysis.prices import Intraday, Session, parse_intraday
from stock_analysis.ui import events as ev
from stock_analysis.ui import frags


# --------------------------------------------------------------------------
# 애널리스트 집계 (야후 quoteSummary)
# --------------------------------------------------------------------------
def _summary_node():
    return {
        "financialData": {"targetMeanPrice": {"raw": 648.07, "fmt": "648.07"},
                          "targetHighPrice": {"raw": 1250.0}, "targetLowPrice": {"raw": 465.0},
                          "targetMedianPrice": {"raw": 640.0},
                          "numberOfAnalystOpinions": {"raw": 36}, "recommendationKey": "buy"},
        "recommendationTrend": {"trend": [
            {"period": "0m", "strongBuy": 12, "buy": 18, "hold": 6, "sell": 0, "strongSell": 0},
            {"period": "-1m", "strongBuy": 1, "buy": 1, "hold": 1, "sell": 1, "strongSell": 1}]},
        "upgradeDowngradeHistory": {"history": [
            {"epochGradeDate": 1790000000, "firm": "Wells Fargo", "toGrade": "Equal-Weight",
             "fromGrade": "Equal-Weight", "action": "main"},
            {"epochGradeDate": 1790500000, "firm": "StoneX", "toGrade": "Buy", "fromGrade": "Hold",
             "action": "up", "currentPriceTarget": 685.0, "priorPriceTarget": 600.0},
            {"epochGradeDate": None, "firm": "망가진 줄", "toGrade": "Buy"}]},
        "defaultKeyStatistics": {"shortPercentOfFloat": {"raw": 0.0246}, "shortRatio": {"raw": 2.13},
                                 "sharesShort": {"raw": 3.4e7}, "sharesShortPriorMonth": {"raw": 3.5e7},
                                 "dateShortInterest": {"raw": 1789000000}},
        "summaryDetail": {"averageVolume": {"raw": 1.5e7}, "dividendYield": {}, "beta": {"raw": 1.9}},
        "assetProfile": {"sector": "Technology", "industry": "Semiconductors",
                         "fullTimeEmployees": 26000, "website": "https://www.amd.com",
                         "longBusinessSummary": "Advanced Micro Devices operates as a semiconductor company."},
        "price": {"exchangeName": "NasdaqGS"},
    }


def test_the_analyst_summary_is_read_as_given():
    profile = parse_profile("AMD", _summary_node())

    assert profile.target_mean == 648.07
    assert (profile.target_low, profile.target_high) == (465.0, 1250.0)
    assert profile.analysts == 36
    assert profile.opinion_counts == (30, 6, 0)            # 적극 매수는 매수 쪽에 합친다
    assert profile.sector == "Technology" and profile.exchange == "NasdaqGS"
    assert profile.short_pct_float == 0.0246 and profile.short_ratio == 2.13


def test_rating_changes_come_newest_first_and_broken_rows_are_dropped():
    profile = parse_profile("AMD", _summary_node())

    assert [r.firm for r in profile.ratings] == ["StoneX", "Wells Fargo"]
    assert profile.ratings[0].target == 685.0
    assert profile.ratings[0].grade_ko == "매수" and profile.ratings[0].action_ko == "상향"
    assert profile.ratings[1].target is None               # 목표가를 안 냈으면 비워 둔다


def test_a_missing_value_stays_missing():
    """야후가 빈 칸({})으로 주면 0 이 아니라 '없음' 이다."""
    profile = parse_profile("AMD", _summary_node())
    assert profile.dividend_yield is None
    assert parse_profile("X", {}).target_mean is None
    assert parse_profile("X", {}).opinion_counts is None


def test_a_blocked_yahoo_gives_nothing_not_a_guess():
    class Blocked:
        def get(self, *a, **k):
            raise OSError("막힘")

        get_text = get

    assert EstimateClient(Blocked()).profile("AMD") is None


# --------------------------------------------------------------------------
# 장중 5분봉
# --------------------------------------------------------------------------
def _intraday_payload():
    """미 동부(UTC-4) 두 거래일, 5분봉 셋씩."""
    base = [datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc), datetime(2026, 9, 30, 13, 30, tzinfo=timezone.utc)]
    stamps, closes, volumes = [], [], []
    for day_start in base:
        for k in range(3):
            stamps.append(int((day_start + timedelta(minutes=5 * k)).timestamp()))
            closes.append(100.0 + k if day_start.day == 29 else 110.0 + k)
            volumes.append(1000 * (k + 1) if day_start.day == 29 else None if k == 1 else 3000)
    return {
        "meta": {"gmtoffset": -4 * 3600, "chartPreviousClose": 1.0,
                 "currentTradingPeriod": {"regular": {
                     "start": int(base[1].timestamp()),
                     "end": int((base[1] + timedelta(hours=6, minutes=30)).timestamp())}}},
        "timestamp": stamps,
        "indicators": {"quote": [{"close": closes, "volume": volumes}]},
    }


def test_intraday_bars_are_split_by_exchange_day():
    data = parse_intraday(_intraday_payload())

    assert [s.day for s in data.sessions] == [date(2026, 9, 29), date(2026, 9, 30)]
    assert data.open_minute == 9 * 60 + 30                    # 거래소 현지 09:30
    assert data.today.minutes == [0, 5, 10]


def test_a_missing_volume_is_not_added_as_zero_or_guessed():
    today = parse_intraday(_intraday_payload()).today
    assert today.total_until(10) == 6000                       # 가운데 빈 칸은 더하지 않는다


def test_the_same_time_average_uses_only_earlier_days():
    data = parse_intraday(_intraday_payload())
    average, days = data.same_time_average(5)
    assert (average, days) == (3000, 1)                        # 어제 09:35 까지 1000+2000


def test_previous_close_is_yesterdays_last_bar_not_a_month_ago():
    """메타의 chartPreviousClose 는 한 달 전 값이라 쓰지 않는다."""
    assert parse_intraday(_intraday_payload()).previous_close == 102.0


# --------------------------------------------------------------------------
# 종목 뉴스 · 헤드라인
# --------------------------------------------------------------------------
RSS = """<rss><channel>
<item><title>Apple to acquire startup in $2 billion deal - Reuters</title><link>https://a/1</link>
<pubDate>Wed, 30 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Apple stock: 3 reasons to buy now</title><link>https://a/2</link>
<pubDate>Wed, 30 Sep 2026 12:00:00 GMT</pubDate></item>
<item><title>Undated story about Apple</title><link>https://a/3</link></item>
</channel></rss>"""


class FeedHttp:
    def __init__(self, text=RSS):
        self.text, self.urls = text, []

    def get_text(self, url, **kw):
        self.urls.append(url)
        return self.text


def test_stock_news_is_newest_first_and_undated_goes_last():
    items = ticker_news(FeedHttp(), "AAPL", "Apple Inc.")
    titles = [i.headline for i in items]

    assert titles[0] == "Apple stock: 3 reasons to buy now"      # 12:00 이 10:00 보다 먼저
    assert titles[-1] == "Undated story about Apple"             # 시각을 지어내지 않고 뒤로
    assert len(titles) == 3                                      # 두 피드가 같은 기사를 주면 하나만


def test_stock_news_keeps_the_original_title_and_marks_importance():
    items = ticker_news(FeedHttp(), "AAPL", "Apple Inc.")
    deal = next(i for i in items if "acquire" in i.title)
    assert deal.headline == "Apple to acquire startup in $2 billion deal"
    assert deal.publisher == "Reuters"
    assert deal.severity == 3                                    # 인수합병은 속보


def test_korean_stock_news_asks_korean_google_news():
    http = FeedHttp()
    ticker_news(http, "005930", "삼성전자", korean=True)
    assert len(http.urls) == 1 and "hl=ko" in http.urls[0]


def test_short_company_names_for_search():
    assert short_name("Rocket Lab USA, Inc.") == "Rocket Lab USA"
    assert short_name("NVIDIA CORP") == "NVIDIA"
    assert short_name("") == ""


def test_a_dead_feed_gives_an_empty_list():
    class Dead:
        def get_text(self, *a, **k):
            raise OSError("막힘")

    assert ticker_news(Dead(), "AAPL") == []
    assert latest_headlines(Dead()) == []


# --------------------------------------------------------------------------
# 일정 시각
# --------------------------------------------------------------------------
def test_a_us_release_time_is_moved_to_the_screen_timezone():
    # 10월(서머타임): 미 동부 08:30 = 서울 21:30
    assert ev._to_local(date(2026, 10, 2), "08:30", "Asia/Seoul") == (date(2026, 10, 2), "21:30")


def test_a_late_release_moves_to_the_next_day():
    # 미 동부 14:00 FOMC = 서울 다음 날 03:00
    assert ev._to_local(date(2026, 10, 28), "14:00", "Asia/Seoul") == (date(2026, 10, 29), "03:00")


def test_an_unknown_time_is_left_blank():
    assert ev._to_local(date(2026, 10, 2), None, "Asia/Seoul") == (date(2026, 10, 2), "")
    assert ev._to_local(date(2026, 10, 2), "장 마감 후", "Asia/Seoul") == (date(2026, 10, 2), "")


def test_an_earnings_date_does_not_invent_a_release_time():
    """SEC 자료에는 장 전·장 후가 없다. '장 마감 후' 라고 박아두면 틀린 날이 생긴다."""
    from stock_analysis.earnings import Earnings

    event = Earnings(ticker="AAPL", day=date(2026, 10, 30), estimated=False, history=[]).to_event()
    assert event.time_et is None


def test_the_calendar_marks_estimates_and_us_only_items(bot):
    from stock_analysis.earnings import Earnings

    target = bot.targets()[0]
    bot._earnings_cache[target.cik] = Earnings(ticker="AAPL", day=date(2026, 10, 30),
                                               estimated=True, history=[])
    html = Dashboard(bot).render_path("/calendar?ym=2026-10&d=2026-10-30")

    assert "2026년 10월" in html
    assert "AAPL 실적 발표" in html and "추정" in html
    assert "실적 발표·휴장일은 미국 날짜입니다" in html


def test_the_calendar_survives_a_bad_month(bot):
    html = Dashboard(bot).render_path("/calendar?ym=엉망&d=없음&k=이상")
    assert "캘린더" in html and "문제가 생겼습니다" not in html


# --------------------------------------------------------------------------
# 조각 (쪽이 뜬 뒤 받아 끼우는 것)
# --------------------------------------------------------------------------
def test_no_analyst_data_says_so_and_shows_no_number(bot):
    target = bot.targets()[0]
    bot.profile_for = lambda t: None
    html = frags.analyst(bot, target, None)
    assert "받지 못했습니다" in html
    assert "추정해서 채우지 않습니다" in html
    assert "$" not in html


def test_analyst_targets_are_compared_with_the_price(bot):
    from stock_analysis.metrics import Metrics

    target = bot.targets()[0]
    profile = parse_profile("AAPL", _summary_node())
    bot.profile_for = lambda t: profile
    m = Metrics(ticker="AAPL")
    m.price = 609.07
    html = frags.analyst(bot, target, m)

    assert "$648.07" in html and "+6.4%" in html            # 평균 목표가 대비
    assert "StoneX" in html and "매수" in html
    assert "투자의견" in html and ">36<" in html              # 도넛 가운데 인원
    assert "Yahoo Finance" in html                           # 어디서 왔는지


def test_no_intraday_bars_draws_no_picture(bot):
    bot.intraday_for = lambda t: None
    html = frags.intraday(bot, bot.targets()[0], None)
    assert "<svg" not in html and "받지 못했습니다" in html


def test_the_intraday_picture_is_drawn_from_real_bars(bot):
    data = parse_intraday(_intraday_payload())
    bot.intraday_for = lambda t: data
    html = frags.intraday(bot, bot.targets()[0], None)
    assert "<svg" in html and "09:30" in html
    assert "같은 시각 1일 평균" in html                   # 몇 날로 낸 평균인지 밝힌다


def test_stock_news_fragment_shows_korean_above_the_original(bot):
    from stock_analysis.news import NewsItem

    target = bot.targets()[0]
    bot.ticker_news = lambda t: [NewsItem(title="Apple beats estimates - Reuters", url="https://x",
                                          source="Yahoo", published=datetime.now(timezone.utc),
                                          tickers=["AAPL"])]
    bot.korean_title = lambda title: ("애플, 예상치 상회", "시험")
    html = frags.stock_news(bot, target, {"AAPL"})

    assert html.index("애플, 예상치 상회") < html.index("Apple beats estimates")   # 한글이 위
    assert "번역</span>" in html                                                  # 자동 번역이라고 밝힌다


def test_korean_text_is_not_sent_to_the_translator(bot):
    calls = []
    bot.translator.translate = lambda text: calls.append(text)
    assert bot.korean_title("삼성전자 HBM 공급 확대") is None
    assert calls == []


def test_no_stock_news_says_why(bot):
    bot.ticker_news = lambda t: []
    html = frags.stock_news(bot, bot.targets()[0], set())
    assert "찾지 못했습니다" in html


# --------------------------------------------------------------------------
# 종목 화면 구성
# --------------------------------------------------------------------------
def _with_metrics(bot):
    from test_dashboard import sample_metrics

    target = bot.targets()[0]
    m = sample_metrics(target.ticker)
    m.price, m.price_change_pct = 150.0, 1.25
    bot._metrics_cache[target.cik] = m
    return target, m


def test_the_stock_page_puts_my_criteria_last_and_folded(bot):
    """이 종목에 대해 가진 것 전부 → 맨 아래 '내 기준' (접었다 펼 수 있게)."""
    _with_metrics(bot)
    html = Dashboard(bot).render_path("/stock/AAPL")

    order = [html.index(f'id="{key}"') for key in
             ("sec-chart", "sec-volume", "sec-news", "sec-filings", "sec-fin", "sec-mine")]
    assert order == sorted(order)
    mine = html[html.index('id="sec-mine"'):]
    assert mine.count('<details class="fold"') >= 5
    assert 'data-lazy="/frag/news?t=AAPL"' in html        # 뉴스는 쪽이 뜬 뒤 받는다
    assert 'data-lazy="/frag/analyst?t=AAPL"' in html


def test_the_stock_page_price_is_live(bot):
    _with_metrics(bot)
    html = Dashboard(bot).render_path("/stock/AAPL")
    assert 'data-live="price" data-t="AAPL">$150.00<' in html
    assert "지연 시세" in html                              # '실시간' 이라고 하지 않는다


def test_an_unknown_stock_offers_to_add_it(bot):
    html = Dashboard(bot).render_path("/stock/ZZZZ")
    assert "감시 목록에 없습니다" in html
    assert 'name="ticker" value="ZZZZ"' in html


def test_search_data_cannot_break_out_of_its_script_tag(bot):
    bot.targets()[0].watch.name = "</script><script>alert(1)</script>"
    html = Dashboard(bot).render()
    data = html[html.index('id="search-data">'):]
    data = data[:data.index("</script>")]
    assert "<" not in data.split(">", 1)[1]                 # 칸 안에 '<' 가 하나도 없다


# --------------------------------------------------------------------------
# 서버
# --------------------------------------------------------------------------
@pytest.fixture
def server(bot):
    srv = start_dashboard(bot, port=8971, open_browser=False, preload=False)
    time.sleep(0.2)
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _post(url, data):
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
    try:
        opener.open(request, timeout=5)
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Location")
    return 200, None


def test_every_page_answers(bot, server):
    for path in ("/", "/news", "/news?f=live", "/filings", "/calendar", "/discover", "/market",
                 "/settings", "/glossary", "/stock/AAPL", "/?m=kr"):
        status, kind, body = _get(server + path)
        assert status == 200 and "html" in kind, path
        assert "문제가 생겼습니다" not in body, path


def test_status_says_whether_work_is_running(bot, server):
    status, kind, body = _get(server + "/status")
    data = json.loads(body)
    assert "busy" in data and "stamp" in data


def test_fragments_answer_even_when_the_outside_is_down(bot, server):
    bot.profile_for = lambda t: None
    bot.intraday_for = lambda t: None
    bot.ticker_news = lambda t: []
    bot.latest_headlines = lambda: []
    for kind in ("news", "analyst", "intraday", "company", "short"):
        status, _, body = _get(server + f"/frag/{kind}?t=AAPL")
        assert status == 200, kind
    status, _, body = _get(server + "/frag/headlines")
    assert "받지 못했습니다" in body


def test_an_unknown_fragment_is_404(bot, server):
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(server + "/frag/nothing?t=AAPL")
    assert err.value.code == 404


def test_a_button_returns_to_the_page_it_was_pressed_on(bot, server):
    code, location = _post(server + "/action", {"action": "memo", "ticker": "AAPL", "memo": "메모",
                                                "back": "/stock/AAPL#sec-mine"})
    assert code == 303 and location == "/stock/AAPL#sec-mine"


def test_a_button_never_sends_you_to_another_site(bot, server):
    for evil in ("https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)"):
        code, location = _post(server + "/action", {"action": "memo", "ticker": "AAPL", "memo": "",
                                                    "back": evil})
        assert code == 303 and location in ("/", "/?m=us"), evil


def test_safe_back_rules():
    assert _safe_back("/news?f=mine") == "/news?f=mine"
    assert _safe_back("//evil") == ""
    assert _safe_back("http://evil") == ""
    assert _safe_back("/a\r\nSet-Cookie: x") == ""


def test_profile_and_rating_types_are_plain_data():
    """화면이 읽는 모양이 바뀌면 여기서 먼저 걸린다."""
    rating = Rating(day=date(2026, 9, 29), firm="X", to_grade="Outperform")
    assert rating.side == "buy" and rating.grade_ko == "시장수익률 상회"
    assert Profile(ticker="X").has_analysts is False
    assert Intraday(sessions=[], open_minute=570, close_minute=960).today is None
    assert Session(day=date(2026, 9, 30), minutes=[0], closes=[1.0], volumes=[None]).total_until(0) == 0


# --------------------------------------------------------------------------
# 홈 맨 위 '주요 속보'
# --------------------------------------------------------------------------
def _story(title, minutes, severity=3, publisher="Reuters", tickers=()):
    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    return {"title": title, "publisher": publisher, "severity": severity, "tickers": list(tickers),
            "when": (now - timedelta(minutes=minutes)).isoformat(timespec="minutes")}


def test_headlines_keep_only_the_last_day_and_put_urgent_first():
    from stock_analysis.ui.home import headline_pick

    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    picked, older = headline_pick([
        _story("Rocket Lab wins launch contract", 5, severity=2),
        _story("Fed holds emergency meeting on rates", 90),
        _story("Minor story nobody needs", 1, severity=1),
        _story("Tariffs announced on chips yesterday", 60 * 30),
    ], now=now)
    assert [p["title"] for p in picked] == ["Fed holds emergency meeting on rates",
                                            "Rocket Lab wins launch contract"]
    # 하루 지난 것은 '속보' 로 띄우지 않고, 마지막 주요 속보로만 알려준다.
    assert older["title"] == "Tariffs announced on chips yesterday"


def test_headlines_merge_the_same_story_from_several_outlets():
    from stock_analysis.ui.home import headline_pick

    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    picked, _ = headline_pick([
        _story("Apple to acquire AI startup in $2 billion deal", 10, tickers=["AAPL"]),
        _story("Apple agrees to acquire AI startup in $2 billion deal", 20, publisher="Bloomberg"),
        _story("Crude oil exports through the Strait of Hormuz hit prewar levels", 30, publisher="CNBC"),
    ], now=now)
    assert len(picked) == 2
    assert picked[0]["also"] == ["Bloomberg"]
    assert picked[1]["also"] == []


def test_home_headlines_show_korean_title_with_original_below(bot):
    bot.state.add_news({"title": "Apple to acquire AI startup", "title_ko": "애플, AI 스타트업 인수",
                        "ko_engine": "구글", "publisher": "Reuters", "severity": 3, "tier": 3,
                        "tickers": [], "url": "https://example.com/a",
                        "when": datetime.now(timezone.utc).isoformat(timespec="minutes")})
    html = Dashboard(bot).render_path("/")
    lead = html.split('class="hb-lead"', 1)[1].split("</article>", 1)[0]
    assert "애플, AI 스타트업 인수" in lead
    assert "Apple to acquire AI startup" in lead      # 원문은 그대로 아래에
    assert "1차 매체" in lead


def test_home_headlines_say_so_when_the_day_was_quiet(bot):
    bot.state.add_news({"title": "Old tariff news", "publisher": "Reuters", "severity": 3, "tickers": [],
                        "when": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="minutes")})
    html = Dashboard(bot).render_path("/")
    assert "최근 24시간 동안 큰 소식" in html
    assert "Old tariff news" in html


def test_news_check_fills_korean_titles_for_older_stored_news(bot, monkeypatch):
    """예전 버전에서 번역 없이 저장된 속보도 다음 확인 때 한글 제목이 붙는다. 안 되는 건 다시 안 두드린다."""
    from stock_analysis.translate import Result

    calls = []

    class HalfTranslator:
        def translate(self, text):
            calls.append(text)
            return Result("유가 급등", "구글") if "oil" in text else Result()

    bot.translator = HalfTranslator()
    monkeypatch.setattr(bot.news, "new_items", lambda tickers: [])
    bot.state.add_news({"title": "Strange headline", "severity": 2, "when": ""})
    bot.state.add_news({"title": "Crude oil prices surge", "severity": 3, "when": ""})
    bot.check_news()
    stored = {n["title"]: n for n in bot.state.news(10)}
    assert stored["Crude oil prices surge"]["title_ko"] == "유가 급등"
    assert "title_ko" not in stored["Strange headline"]
    bot.check_news()
    assert calls.count("Strange headline") == 1
