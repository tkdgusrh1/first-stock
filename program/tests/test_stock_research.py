"""종목 화면 v4.5 — 실적 기대 한 장 · 통계 · 옵션 · 보유자 · 수익률 비교 · 과거 데이터.

약속은 같다: 야후 응답에서 받은 값만 그대로 읽고, 없는 칸은 비우고, 판정은 숫자에서만 낸다.
"""

import time
from datetime import date, datetime, timedelta, timezone

from stock_analysis import performance
from stock_analysis.estimates import parse_profile
from stock_analysis.guidance import GuidanceItem, GuidanceReport
from stock_analysis.options import Contract, Expiry, OptionsView, parse_chain, pick_month
from stock_analysis.ui import research


def r(value):
    return {"raw": value, "fmt": str(value)}


def epoch(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def node():
    """야후 quoteSummary 한 건 — 실제 응답 모양 그대로(값은 시험용)."""
    return {
        "financialData": {"targetMeanPrice": r(80.0), "targetLowPrice": r(50.0), "targetHighPrice": r(110.0),
                          "numberOfAnalystOpinions": r(14), "recommendationKey": "buy",
                          "grossMargins": r(0.34), "operatingMargins": r(-0.29), "currentRatio": r(3.1),
                          "totalCash": r(1.0e9), "freeCashflow": r(-1.8e8)},
        "recommendationTrend": {"trend": [{"period": "0m", "strongBuy": 4, "buy": 8, "hold": 5, "sell": 0,
                                           "strongSell": 1}]},
        "defaultKeyStatistics": {"enterpriseValue": r(36e9), "forwardPE": r(-250.0), "pegRatio": {},
                                 "52WeekChange": r(1.21), "SandP52WeekChange": r(0.15),
                                 "sharesOutstanding": r(5.1e8), "floatShares": r(4.2e8),
                                 "heldPercentInsiders": r(0.126), "heldPercentInstitutions": r(0.58),
                                 "shortPercentOfFloat": r(0.14)},
        "summaryDetail": {"beta": r(2.15), "fiftyDayAverage": r(66.0), "twoHundredDayAverage": r(52.0)},
        "assetProfile": {"sector": "Industrials", "address1": "3881 McGowen Street", "city": "Long Beach",
                         "country": "United States", "phone": "714 465 5737",
                         "companyOfficers": [{"name": "Jane Roe", "title": "CEO", "totalPay": r(800000),
                                              "yearBorn": 1977},
                                             {"name": "", "title": "빈 이름은 뺀다"}]},
        "price": {"exchangeName": "NasdaqGS"},
        "earningsTrend": {"trend": [
            {"period": "0q", "endDate": "2026-09-30",
             "earningsEstimate": {"avg": r(-0.077), "low": r(-0.10), "high": r(-0.05), "yearAgoEps": r(-0.03),
                                  "numberOfAnalysts": r(16)},
             "revenueEstimate": {"avg": r(257.8e6), "low": r(251.99e6), "high": r(265.57e6),
                                 "yearAgoRevenue": r(155.08e6), "numberOfAnalysts": r(16), "growth": r(0.6624)},
             "epsTrend": {"current": r(-0.077), "90daysAgo": r(-0.09)},
             "epsRevisions": {"upLast30days": r(4), "downLast30days": r(1)}},
            {"period": "0y", "endDate": "2026-12-31", "revenueEstimate": {"avg": r(962.29e6)},
             "earningsEstimate": {}},
            {"period": "-5y", "endDate": "2021-12-31", "revenueEstimate": {"avg": r(1.0)}},       # 모르는 기간은 버린다
            {"period": "+1y", "endDate": "2027-12-31", "earningsEstimate": {}, "revenueEstimate": {}}]},  # 빈 기간도
        "earningsHistory": {"history": [
            {"quarter": r(epoch(date(2026, 6, 30))), "epsActual": r(-0.08), "epsEstimate": r(-0.077),
             "surprisePercent": r(-0.039)},
            {"quarter": r(epoch(date(2026, 3, 31))), "epsActual": r(-0.07), "epsEstimate": r(-0.079)},
            {"quarter": {}, "epsActual": r(1.0)}]},
        "earnings": {"financialsChart": {"yearly": [{"date": 2025, "revenue": r(601.8e6), "earnings": r(-198e6)},
                                                    {"date": 2024, "revenue": r(436.2e6), "earnings": r(-190e6)}]}},
        "calendarEvents": {"earnings": {"earningsDate": [r(epoch(date(2026, 11, 9)))]}},
        "majorHoldersBreakdown": {"institutionsCount": r(712)},
        "institutionOwnership": {"ownershipList": [
            {"organization": "Vanguard Group Inc", "pctHeld": r(0.081), "position": r(4.1e7),
             "value": r(2.9e9), "reportDate": r(epoch(date(2026, 6, 30))), "pctChange": r(0.034)}]},
    }


# --------------------------------------------------------------------------
# 야후 응답 읽기
# --------------------------------------------------------------------------
def test_estimates_by_period_are_read_as_given():
    p = parse_profile("RKLB", node())
    assert [e.period for e in p.estimates] == ["0q", "0y"]          # 빈 기간·모르는 기간은 없다
    q = p.estimate("0q")
    assert q.end == date(2026, 9, 30) and q.label == "이번 분기"
    assert (q.revenue, q.revenue_low, q.revenue_high, q.revenue_analysts) == (257.8e6, 251.99e6, 265.57e6, 16)
    assert q.eps == -0.077 and q.eps_year_ago == -0.03
    assert q.eps_trend == {"90d": -0.09, "now": -0.077}
    assert (q.up_30d, q.down_30d) == (4, 1)
    assert p.estimate("0y").eps is None                              # 비어 있으면 비운다


def test_eps_history_is_oldest_first_and_broken_rows_dropped():
    p = parse_profile("RKLB", node())
    assert [h.quarter for h in p.eps_history] == [date(2026, 3, 31), date(2026, 6, 30)]
    assert p.eps_history[0].beat is True and p.eps_history[1].beat is False


def test_stats_holders_and_officers_are_read():
    p = parse_profile("RKLB", node())
    assert p.enterprise_value == 36e9 and p.peg is None and p.forward_pe == -250.0
    assert (p.week52_change, p.sp52_change) == (1.21, 0.15)
    assert (p.fifty_day, p.two_hundred_day) == (66.0, 52.0)
    assert p.held_institutions == 0.58 and p.institutions_count == 712
    assert p.institutions[0].name == "Vanguard Group Inc" and p.institutions[0].reported == date(2026, 6, 30)
    assert [o.name for o in p.officers] == ["Jane Roe"] and p.officers[0].born == 1977
    assert "Long Beach" in p.address and p.phone == "714 465 5737"
    assert p.yearly == [(2024, 436.2e6, -190e6), (2025, 601.8e6, -198e6)]
    assert p.earnings_dates == [date(2026, 11, 9)]


def test_an_empty_answer_leaves_every_new_field_empty():
    p = parse_profile("X", {})
    assert p.estimates == [] and p.eps_history == [] and p.officers == [] and p.institutions == []
    assert p.enterprise_value is None and p.beta is None and p.earnings_dates == []


# --------------------------------------------------------------------------
# 가이던스 ↔ 컨센서스
# --------------------------------------------------------------------------
def guidance(filed="2026-08-07", period="third quarter 2026", low=250e6, high=260e6, metric="매출"):
    return GuidanceReport(form="8-K", filing_date=filed, url="https://sec/x",
                          items=[GuidanceItem("We expect ...", metric, period, low, high, "$")])


def test_period_words_map_to_quarter_or_year():
    assert research.period_kind("third quarter 2026") == "0q"
    assert research.period_kind("Q4") == "0q"
    assert research.period_kind("full-year 2026") == "0y"
    assert research.period_kind("fiscal year") == "0y"
    assert research.period_kind(None) is None


def test_guidance_is_matched_only_when_it_is_still_about_the_future():
    last = date(2026, 6, 30)
    assert research.guided(guidance(), "매출", "0q", last).low == 250e6
    assert research.guided(guidance(), "매출", "0y", last) is None            # 분기 약속을 연간에 붙이지 않는다
    # 마지막 발표 분기보다 먼저 낸 가이던스는 이미 지난 분기 이야기
    assert research.guided(guidance(filed="2026-05-07"), "매출", "0q", last) is None
    assert research.guided(guidance(low=2.1, high=2.3), "매출", "0q", last) is None   # 주당 값을 매출로 보지 않는다


def test_consensus_is_placed_against_the_guided_range():
    item = guidance().items[0]
    assert research.versus(item, 257.8e6) == ("가이던스 범위 안", "")
    text, cls = research.versus(item, 270e6)
    assert cls == "up" and "+5.9%" in text
    text, cls = research.versus(item, 240e6)
    assert cls == "down" and "-5.9%" in text


def test_eps_growth_is_worded_when_either_side_is_a_loss():
    assert "적자 확대" in research.eps_change(-0.077, -0.03, 1.5)       # '+150%' 로 거꾸로 읽히지 않게
    assert "적자 축소" in research.eps_change(-0.05, -0.09, -0.4)
    assert "흑자 전환" in research.eps_change(0.02, -0.01, None)
    assert "적자 전환" in research.eps_change(-0.02, 0.01, None)
    assert "+50.0%" in research.eps_change(1.5, 1.0, None)


def test_small_eps_keeps_the_third_decimal():
    assert research.eps_text(-0.077, "USD") == "-$0.077"
    assert research.eps_text(-0.05, "USD") == "-$0.05"
    assert research.eps_text(1.234, "USD") == "$1.23"


# --------------------------------------------------------------------------
# 조각 그리기
# --------------------------------------------------------------------------
def _with(bot, profile=None):
    from test_dashboard import sample_metrics

    target = bot.targets()[0]
    m = sample_metrics(target.ticker)
    m.price = 70.0
    bot._metrics_cache[target.cik] = m
    bot.profile_for = lambda t: profile
    return target, m


def test_the_one_page_puts_company_experts_and_results_together(bot):
    profile = parse_profile("AAPL", node())
    target, m = _with(bot, profile)
    bot._guidance_cache[target.cik] = guidance()
    html = research.expect(bot, target, m, date(2026, 10, 2))

    assert "다음 실적 2026-11-09" in html and "D-38" in html
    assert "$250.0M ~ $260.0M" in html                         # 회사가 말한 것
    assert "$257.80M" in html and "16명" in html               # 전문가 평균과 인원
    assert "가이던스 범위 안" in html
    assert "적자 확대" in html                                   # EPS 성장률은 말로
    assert "eps-dots" in html and "하회" in html and "상회" in html
    assert "매수 12·보유 5·매도 1" in html
    assert "예상치 변화" in html and "Vanguard" not in html      # 보유자는 다른 구역
    assert "Yahoo Finance" in html


def test_no_yahoo_answer_shows_no_number(bot):
    target, m = _with(bot, None)
    for html in (research.expect(bot, target, m, date(2026, 10, 2)), research.holders(bot, target, m)):
        assert "받지 못했습니다" in html and "$" not in html


def test_stats_group_values_and_skip_empty_rows(bot):
    target, m = _with(bot, parse_profile("AAPL", node()))
    html = research.stats(bot, target, m)
    assert "가치 평가" in html and "주식 · 공매도" in html
    assert "$36.00B" in html                                    # 기업가치
    assert "PEG" not in html                                     # 빈 값은 줄째 뺀다
    assert "선행 PER" not in html                                # 음수(적자) 선행 PER 은 보이지 않는다
    assert "+121.0%" in html and "S&amp;P 500 +15.0%" in html
    assert "14.00%" in html                                      # 공매도 비중


def test_glance_has_no_link_inside_a_link(bot):
    target, m = _with(bot, parse_profile("AAPL", node()))
    html = research.glance(bot, target, m, date(2026, 10, 2))
    assert html.count('<a class="gl"') >= 3
    for piece in html.split('<a class="gl"')[1:]:
        assert "<a " not in piece.split("</a>")[0]               # 고리 안에 고리가 들어가면 화면이 깨진다
    assert "11/9" in html                                        # 다음 실적


def test_holders_table_lists_institutions(bot):
    target, m = _with(bot, parse_profile("AAPL", node()))
    html = research.holders(bot, target, m)
    assert "Vanguard Group Inc" in html and "8.10%" in html and "712곳" in html
    assert "13F" in html


# --------------------------------------------------------------------------
# 옵션
# --------------------------------------------------------------------------
def chain(price=70.0, expiry=date(2026, 10, 30)):
    rows = lambda kind: [  # noqa: E731
        {"strike": k, "lastPrice": 2.0, "bid": 1.9 if k == 70 else 0.5, "ask": 2.1 if k == 70 else 0.7,
         "volume": 3000 if (kind == "p" and k == 65) else 100, "openInterest": 9000 if k == 75 else 400,
         "impliedVolatility": 0.8}
        for k in (60.0, 65.0, 70.0, 75.0, 80.0)]
    return {"optionChain": {"result": [{"expirationDates": [epoch(expiry), epoch(expiry + timedelta(days=28))],
                                        "quote": {"regularMarketPrice": price},
                                        "options": [{"expirationDate": epoch(expiry), "calls": rows("c"),
                                                     "puts": rows("p")}]}]}}


def test_chain_math_comes_from_the_chain_only():
    price, days, stamps, expiry = parse_chain(chain())
    assert price == 70.0 and days[0] == date(2026, 10, 30) and len(stamps) == 2
    assert expiry.atm_strike(70.0) == 70.0
    assert expiry.straddle(70.0) == 4.0                          # 콜 가운데 2.0 + 풋 가운데 2.0
    assert round(expiry.atm_iv(70.0), 2) == 0.8
    assert expiry.top_oi(expiry.calls).strike == 75.0
    assert round(expiry.pc_volume, 2) == round(3400 / 500, 2)
    view = OptionsView("X", 70.0, [expiry])
    unusual = view.unusual()
    assert len(unusual) == 1 and unusual[0].kind == "풋" and unusual[0].contract.strike == 65.0


def test_a_broken_chain_gives_nothing():
    assert parse_chain({}) == (None, [], [], None)
    e = Expiry(date(2026, 1, 1), calls=[Contract(70.0)], puts=[])
    assert e.straddle(70.0) is None and e.atm_iv(70.0) is None and e.pc_volume is None


def test_the_month_expiry_is_the_one_nearest_thirty_days():
    today = date(2026, 10, 2)
    days = [today + timedelta(days=n) for n in (2, 9, 30, 58)]
    assert pick_month(days, [1, 2, 3, 4], today) == 3


def test_options_card_reads_the_market_and_says_it_is_delayed(bot):
    target, m = _with(bot)
    expiry = parse_chain(chain())[3]
    bot.options_for = lambda t: OptionsView("AAPL", 70.0, [expiry], [expiry.day],
                                            datetime.now(timezone.utc))
    html = research.options_card(bot, target, m, date(2026, 10, 2))
    assert "±$4.00" in html and "(±5.7%)" in html
    assert "80.0%" in html and "15분 지연" in html
    assert "행사가별 표" in html and "오늘 몰린 계약" in html
    bot.options_for = lambda t: None
    assert "받지 못했습니다" in research.options_card(bot, target, m, date(2026, 10, 2))


# --------------------------------------------------------------------------
# 수익률 비교 · 과거 데이터
# --------------------------------------------------------------------------
def test_returns_are_compared_with_the_index_from_real_closes():
    today = date(2026, 10, 2)
    stock = [(today - timedelta(days=400), 50.0), (today - timedelta(days=365), 50.0), (today, 100.0)]
    index = [(today - timedelta(days=400), 1000.0), (today - timedelta(days=365), 1000.0), (today, 1100.0)]
    rows = {r.label: r for r in performance.compare(stock, index, today)}
    assert rows["1년"].stock == 1.0 and round(rows["1년"].index, 2) == 0.10
    assert round(rows["1년"].gap, 2) == 0.90
    assert rows["5년"].stock is None                             # 상장 전 기간은 비운다


def test_the_history_csv_has_every_bar(bot):
    import urllib.request

    from stock_analysis.dashboard import start_dashboard
    from stock_analysis.prices import Candle

    bars = [Candle(date(2026, 9, 1) + timedelta(days=i), 10.0, 11.0, 9.0, 10.5 + i, 1000.0) for i in range(5)]
    bot.targets()
    bot.prices.candles = lambda symbol: bars
    server = start_dashboard(bot, port=8973, open_browser=False, preload=False)
    time.sleep(0.2)
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/download/history?t=AAPL"
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8-sig")
            assert "attachment" in resp.headers["Content-Disposition"]
    finally:
        server.shutdown()
        server.server_close()
    lines = body.strip().splitlines()
    assert lines[0] == "date,open,high,low,close,volume"
    assert len(lines) == 6 and lines[-1].startswith("2026-09-05,10.0000")


def test_a_fund_page_has_no_earnings_or_holder_sections(bot):
    from stock_analysis.dashboard import Dashboard

    target, m = _with(bot)
    m.is_fund = True
    html = Dashboard(bot).render_path("/stock/AAPL")
    assert 'id="sec-expect"' not in html and 'id="sec-holders"' not in html
    assert 'id="sec-options"' in html                            # ETF 도 옵션은 있다
