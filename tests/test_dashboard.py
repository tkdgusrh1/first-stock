"""대시보드 렌더링과 버튼 동작. 실제 포트를 열어 요청까지 보내본다."""

import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from stockbot.dashboard import Dashboard, start_dashboard
from stockbot.edgar import Filing, parse_form4
from stockbot.messages import summarize_filing
from fixtures import FORM4_XML


def wait_idle(dash, timeout: float = 10.0):
    """백그라운드 작업이 끝날 때까지 기다린다."""
    deadline = time.time() + timeout
    while dash.busy and time.time() < deadline:
        time.sleep(0.02)
    assert dash.busy is None, "백그라운드 작업이 끝나지 않았습니다"
    return dash.notice


def sample_metrics(ticker="AAPL"):
    from factories import build_facts

    from stockbot.metrics import build_metrics

    return build_metrics(
        ticker,
        build_facts(
            revenue=[100e6, 110e6, 120e6, 130e6, 140e6, 150e6, 160e6, 170e6],
            net_income=[10e6, 11e6, 12e6, 13e6, 15e6, 16e6, 17e6, 18e6],
            operating_income=[12e6, 13e6, 14e6, 15e6, 18e6, 20e6, 22e6, 24e6],
            ocf=[14e6, 15e6, 16e6, 17e6, 20e6, 22e6, 24e6, 26e6],
            equity=300e6,
            cash=50e6,
            shares=100e6,
        ),
    )


@pytest.fixture
def server(bot):
    srv = start_dashboard(bot, port=8931, open_browser=False, preload=False)
    time.sleep(0.2)
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read().decode("utf-8")


def post(url: str, data: dict):
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


# --- 렌더링 ----------------------------------------------------------------
def test_page_has_all_sections(bot):
    html = Dashboard(bot).render()
    for needle in ("관심 종목 감시", "관심 종목", "최근 공시", "휴장·조기폐장", "경제지표·실적 일정", "AAPL"):
        assert needle in html
    assert html.startswith("<!doctype html>")
    assert "127.0.0.1" in html          # 로컬 전용이라는 안내


def test_page_marks_pending_metrics(bot):
    dash = Dashboard(bot)
    dash.busy = "무언가 하는 중"          # 자동 채움이 끼어들지 않게 고정
    assert "불러오는 중" in dash.render()


def test_adding_a_stock_keeps_other_stocks_data(bot):
    """종목을 추가할 때 기존 종목 지표가 사라지면 안 된다.

    화면이 계속 '불러오는 중' 에 머물던 실제 원인이었다.
    """
    first = bot.targets()[0]
    bot._metrics_cache[first.cik] = sample_metrics(first.ticker)

    bot.commands.handle("/add NVDA")

    assert first.cik in bot.cached_metrics(), "기존 종목 지표가 지워졌습니다"
    assert bot.cached_metrics()[first.cik].revenue_ttm


def test_removing_a_stock_drops_only_its_data(bot):
    bot.commands.handle("/add NVDA")
    for target in bot.targets():
        bot._metrics_cache[target.cik] = sample_metrics(target.ticker)
    kept = [t for t in bot.targets() if t.ticker == "AAPL"][0]

    bot.commands.handle("/remove NVDA")

    cached = bot.cached_metrics()
    assert kept.cik in cached
    assert len(cached) == 1


def test_autofill_starts_for_missing_stocks(bot):
    dash = Dashboard(bot)
    dash.render()
    wait_idle(dash, timeout=15)
    # 자동 채움이 돌았으니 '아직 시도조차 안 한' 종목은 없어야 한다
    assert not bot.missing_metrics()


def test_failed_stock_shows_reason_and_retry(bot):
    target = bot.targets()[0]
    bot._metrics_error[target.cik] = "Timeout: 응답이 없습니다"

    dash = Dashboard(bot)
    dash.busy = "고정"                     # 자동 채움 억제
    html = dash.render()

    assert "불러오기 실패" in html          # 요약 표
    assert "다시 시도" in html              # 상세 카드의 재시도 버튼
    assert "Timeout" in html                # 원인


def test_summary_table_lists_every_stock(bot):
    bot.commands.handle("/add NVDA")
    bot._metrics_cache[bot.targets()[0].cik] = sample_metrics()
    html = Dashboard(bot).render()
    assert "전체 종목 한눈에" in html
    for column in ("매출(TTM)", "영업이익률", "ROE", "ROIC", "PER", "PSR", "런웨이", "실적발표"):
        assert column in html
    # 지표가 아직 없는 종목도 표에는 나온다
    assert ">AAPL<" in html and ">NVDA<" in html


def test_page_shows_metrics_when_available(bot):
    bot._metrics_cache[bot.targets()[0].cik] = sample_metrics()
    html = Dashboard(bot).render()
    assert "22.0%" in html                    # ROE
    assert "① 분기 매출 지속" in html          # 체크리스트 전체가 펼쳐져 있다
    assert "1순위 · 가이던스" in html
    assert "분기 매출" in html                 # 매출 막대
    assert "영업현금흐름" in html              # 상세 숫자
    assert "종목별 상세" in html
    assert "지금 상황" in html                 # 상황 판단
    assert "용어 사전" in html                 # 용어 설명
    assert "이 숫자들의 출처" in html          # 출처 표기


def test_recent_filings_appear(bot):
    bot.check_filings(force=True)
    html = Dashboard(bot).render()
    assert "실적 발표" in html
    assert "내부자" in html
    assert "tone-alert" in html         # 8-K 2.02 는 강조 표시


def test_html_escaping_of_user_values(bot):
    bot._metrics_cache[bot.targets()[0].cik] = sample_metrics()
    bot.targets()[0].watch.milestones = ["<script>alert(1)</script>"]
    html = Dashboard(bot).render()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- 버튼 동작 -------------------------------------------------------------
def test_add_and_remove_through_dashboard(bot):
    dash = Dashboard(bot)
    assert "추가하는 중" in dash.run_action("add", {"ticker": ["NVDA"]})
    assert "추가했습니다" in wait_idle(dash)
    assert [t.ticker for t in bot.targets()] == ["AAPL", "NVDA"]

    dash.run_action("remove", {"ticker": ["NVDA"]})
    assert "뺐습니다" in wait_idle(dash)
    assert [t.ticker for t in bot.targets()] == ["AAPL"]


def test_add_requires_ticker(bot):
    assert "티커를 입력" in Dashboard(bot).run_action("add", {"ticker": [""]})


def test_unknown_action(bot):
    assert "알 수 없는" in Dashboard(bot).run_action("아무거나", {})


def test_background_action_reports_result(bot):
    dash = Dashboard(bot)
    assert "확인하는 중" in dash.run_action("check", {})
    assert wait_idle(dash) is not None


def test_page_stays_responsive_while_working(bot):
    """오래 걸리는 작업이 화면을 잠그면 안 된다 (버튼이 먹통이 되던 버그)."""
    dash = Dashboard(bot)
    dash.render()                       # 직전 화면을 한 번 만들어 둔다

    released = threading.Event()

    def slow():
        released.wait(5)
        return "끝"

    dash._background("느린 작업", slow)
    time.sleep(0.1)

    started = time.time()
    html = dash.render()                # 락이 잡혀 있어도 즉시 돌아와야 한다
    elapsed = time.time() - started

    assert elapsed < 1.5, f"화면이 {elapsed:.1f}초 동안 멈췄습니다"
    assert "느린 작업" in html           # 진행 상황이 보인다
    assert 'content="4"' in html         # 결과가 곧 보이도록 짧게 새로고침
    released.set()
    wait_idle(dash)


def test_first_render_during_work_shows_progress(bot):
    """직전 화면이 없을 때(첫 접속)도 멈추지 않고 안내를 보여준다."""
    dash = Dashboard(bot)
    released = threading.Event()
    dash._background("불러오는 중", lambda: (released.wait(5), "끝")[1])
    time.sleep(0.1)

    html = dash.render()
    assert "불러오는 중" in html
    assert "SEC에서 공시와 재무 데이터를" in html
    released.set()
    wait_idle(dash)


def test_actions_are_not_blocked_by_a_running_job(bot):
    dash = Dashboard(bot)
    released = threading.Event()
    dash._background("느린 작업", lambda: (released.wait(5), "끝")[1])
    time.sleep(0.1)

    started = time.time()
    message = dash.run_action("add", {"ticker": ["NVDA"]})
    assert time.time() - started < 1     # 즉시 응답
    assert "이미 실행 중" in message      # 그리고 이유를 알려준다
    released.set()
    wait_idle(dash)


def test_preload_fills_metrics_without_clicking(bot):
    """버튼을 누르지 않아도 시작하면서 지표가 채워져야 한다."""
    dash = Dashboard(bot)
    dash.load_initial()
    wait_idle(dash, timeout=15)
    assert bot.cached_metrics(), "시작 시 지표가 계산되지 않았습니다"


# --- HTTP ------------------------------------------------------------------
def test_server_serves_page(server):
    _, base = server
    assert "관심 종목 감시" in get(base + "/")
    assert get(base + "/healthz") == "ok"


def test_unknown_path_404(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base + "/nope")
    assert exc.value.code == 404


def test_post_redirects_back(server, bot):
    _, base = server
    assert post(base + "/action", {"action": "add", "ticker": "NVDA"}) in (200, 303)
    assert "NVDA" in get(base + "/")


def test_binds_to_localhost_only(server):
    srv, _ = server
    assert srv.server_address[0] == "127.0.0.1"


def test_port_fallback_when_busy(bot):
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 8945))
    blocker.listen(1)
    try:
        srv = start_dashboard(bot, port=8945, open_browser=False)
        try:
            assert srv.server_address[1] != 8945    # 옆 포트로 비켜서 열린다
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        blocker.close()


# --- 요약 ------------------------------------------------------------------
def _filing(form, items=None, primary="a.htm"):
    return Filing(
        cik="0000320193", ticker="AAPL", company="Apple Inc.", form=form,
        accession="0000320193-26-000010", filing_date="2026-08-09",
        accepted="2026-08-09T16:31:05.000Z", report_date="2026-08-09",
        primary_doc=primary, items=items or [],
    )


def test_summarize_8k_marks_critical_items():
    summary = summarize_filing(_filing("8-K", ["2.02", "9.01"]), "Asia/Seoul")
    assert summary["tone"] == "alert"
    assert "실적 발표" in summary["title"]
    assert summary["when"] == "2026-08-10 05:31"


def test_summarize_8k_plain_item():
    assert summarize_filing(_filing("8-K", ["5.03"]), "Asia/Seoul")["tone"] == "plain"


def test_summarize_form4_direction():
    filing = parse_form4(FORM4_XML, _filing("4", primary="xslF345X03/wf-form4_1.xml"))
    summary = summarize_filing(filing, "Asia/Seoul")
    assert summary["tone"] == "bad"          # 매도
    assert "매도" in summary["title"]
    assert "Hong Gildong" in summary["title"]


@pytest.mark.parametrize(
    "form,expected,tone",
    [
        ("10-Q", "분기보고서", "plain"),
        ("10-K", "연간보고서", "plain"),
        ("SC 13D", "대량보유", "alert"),
        ("S-3", "증자", "alert"),
        ("424B5", "증자", "alert"),
        ("DEF 14A", "DEF 14A 제출", "plain"),
    ],
)
def test_summarize_other_forms(form, expected, tone):
    summary = summarize_filing(_filing(form), "Asia/Seoul")
    assert expected in summary["title"]
    assert summary["tone"] == tone


# --- 환율·지수 스트립 -------------------------------------------------------
def test_market_strip_shows_all_five_rates_per_dollar(bot):
    from datetime import datetime, timezone

    from stockbot.fx import IndexQuote, MarketSnapshot, Rate

    bot.fx._snapshot = MarketSnapshot(
        rates=[
            Rate("원", 1380.5, 0.4, 2, "₩", "Yahoo Finance"),
            Rate("엔", 147.2, -0.3, 2, "¥", "Yahoo Finance"),
            Rate("위안", 7.1234, 0.05, 4, "¥", "Yahoo Finance"),
            Rate("유로", 0.9182, -0.11, 4, "€", "Yahoo Finance"),
        ],
        indexes=[IndexQuote("S&P 500", 5432.1, 0.8, "미국 대형주 500개", "Yahoo Finance"),
                 IndexQuote("VIX", 18.4, -3.2, "공포지수", "Yahoo Finance")],
        fetched_at=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc),
    )
    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "1달러 =" in html
    for label in ("원", "엔", "위안", "유로"):
        assert f'>{label}</span>' in html
    assert "₩1,380.50" in html and "€0.9182" in html
    assert "S&amp;P 500" in html and "VIX" in html    # & 는 HTML 로 이스케이프된다
    assert "08-12 22:00 기준" in html      # 한국시간


def test_market_strip_says_so_while_loading(bot):
    dash = Dashboard(bot)
    dash.busy = "고정"
    assert "환율·지수를 불러오는 중" in dash.render()


# --- 속보 패널 --------------------------------------------------------------
def test_news_panel_shows_korean_time_and_source_rank(bot):
    bot.state.add_news({
        "title": "Oil prices surge 8% after attack on tankers",
        "publisher": "Reuters", "url": "https://news/1", "source": "시장",
        "severity": 3, "reasons": ["유가 급변"], "tickers": [], "macro": True,
        "tier": 3, "when": "2026-08-12T13:00+00:00",
    })
    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "08-12 22:00" in html            # 한국시간으로 변환
    assert 'class="src t3"' in html         # 1차 매체 표시
    assert "Reuters" in html
    assert "시장 전체" in html


# --- ETF ------------------------------------------------------------------
def test_etf_card_replaces_company_metrics_with_etf_view(bot):
    from stockbot.funds import classify_name
    from stockbot.metrics import build_fund_metrics

    target = bot.targets()[0]
    info = classify_name("CONL", "GraniteShares 2x Long COIN Daily ETF")
    bot._metrics_cache[target.cik] = build_fund_metrics(target.ticker, info, bot.prices)
    bot._fund_cache[target.cik] = info

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "이 ETF 는 무엇인가" in html
    assert "2배 레버리지" in html
    assert "변동성 감쇠" in html
    assert "ETF 체크리스트" in html
    # 회사용 항목은 나오지 않는다
    assert "흑자 기업 체크리스트" not in html
    assert "분기보고서 내용" not in html


# --- 가이던스 이행 이력 -----------------------------------------------------
def test_track_record_table_is_rendered(bot):
    from datetime import date

    from stockbot.track_record import TrackItem, TrackRecord

    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = sample_metrics(target.ticker)
    bot._track_cache[target.cik] = TrackRecord(
        ticker=target.ticker,
        items=[TrackItem(
            filed="2026-02-27", url="https://sec/1",
            sentence="We expect revenue of $450 million to $470 million for the first quarter.",
            metric="매출", low=450e6, high=470e6,
            target_end=date(2026, 3, 31), actual=478e6, verdict="상회",
            reason="제시 상단 $470.0M 을(를) 넘겼습니다.",
        )],
    )
    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "과거 가이던스 이행" in html
    assert "1번 지켰고" in html
    assert "$450.0M ~ $470.0M" in html
    assert "$478.0M" in html
    assert "We expect revenue of $450 million" in html   # 원문 그대로


def test_dilution_is_visible(bot):
    target = bot.targets()[0]
    metrics = sample_metrics(target.ticker)
    metrics.share_growth_1y = 0.22
    bot._metrics_cache[target.cik] = metrics

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()
    assert "희석" in html and "+22.0%" in html


def test_market_refresh_thread_starts_once_and_survives_render(bot):
    dash = Dashboard(bot)
    dash.busy = "고정"
    dash.render()
    first = dash._market_thread
    assert first is not None and first.is_alive()
    dash.render()
    assert dash._market_thread is first        # 화면을 볼 때마다 스레드가 늘면 안 된다
