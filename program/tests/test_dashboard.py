"""대시보드 렌더링과 버튼 동작. 실제 포트를 열어 요청까지 보내본다."""

import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from stock_analysis.dashboard import Dashboard, start_dashboard
from stock_analysis.edgar import Filing, parse_form4
from stock_analysis.messages import summarize_filing
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

    from stock_analysis.metrics import build_metrics

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


# --- 경제 지표 -------------------------------------------------------------
def macro_snapshot():
    from datetime import date, datetime, timezone

    from stock_analysis.macro import BY_ID, MacroSnapshot, Reading

    return MacroSnapshot(
        readings=[
            Reading(BY_ID["CPIAUCSL"], 3.0, 3.4, date(2026, 7, 1)),
            Reading(BY_ID["T10Y2Y"], -0.35, -0.10, date(2026, 8, 12)),
            Reading(BY_ID["PAYEMS"], 14.7, 9.0, date(2026, 7, 1)),
        ],
        fetched_at=datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc),
    )


def test_macro_numbers_appear_with_their_reference_month(bot):
    bot.macro._snapshot = macro_snapshot()
    html = Dashboard(bot).render()

    assert "소비자물가 CPI" in html
    assert "3.0%" in html                     # 지수가 아니라 전년 대비
    assert "2026년 7월분" in html              # 언제 기준인지 반드시 함께
    assert "-0.4%p" in html                   # 직전 발표 대비
    assert "금리 역전 — 침체 경고 신호" in html   # 숫자를 말로 풀어준다
    assert "FRED" in html                     # 출처


def test_macro_colour_follows_the_meaning_not_the_arrow(bot):
    """물가가 내려가면 화살표는 아래지만 주식에는 좋다. 색은 뜻을 따라간다."""
    bot.macro._snapshot = macro_snapshot()
    html = Dashboard(bot).render()

    assert '<span class="mi-move good" title="직전 발표 대비">▼ -0.4%p' in html
    assert '<span class="mi-move bad" title="직전 발표 대비">▼ -0.25%p' in html   # 금리차 역전 심화
    assert '<span class="mi-move flat" title="직전 발표 대비">▲ +5.7만 명' in html  # 해석이 갈리는 값


def test_macro_section_waits_quietly_when_there_is_nothing(bot):
    bot.macro._snapshot = None
    html = Dashboard(bot).render()
    assert "추정치를 대신 넣지 않습니다" in html


def test_html_escaping_of_user_values(bot):
    bot._metrics_cache[bot.targets()[0].cik] = sample_metrics()
    bot.targets()[0].watch.milestones = ["<script>alert(1)</script>"]
    html = Dashboard(bot).render()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- 지표 새로고침 ----------------------------------------------------------
def test_the_metrics_button_actually_recalculates(bot):
    """'지표' 를 눌렀는데 저장해둔 값을 그대로 돌려주면 눌러도 아무 일이 없다.

    실제로 그랬다. force=True 가 캐시 검사만 건너뛰고, 정작 계산하는 쪽은
    캐시를 그대로 반환하고 있었다.
    """
    target = bot.targets()[0]
    stale = sample_metrics()
    stale.revenue_ttm = 1                      # 눈에 띄는 가짜 값
    bot._metrics_cache[target.cik] = stale

    dash = Dashboard(bot)
    dash.run_action("metrics", {})
    assert "계산했습니다" in wait_idle(dash, timeout=20)

    assert bot.cached_metrics()[target.cik].revenue_ttm != 1, "옛 값이 그대로 남았습니다"


def test_refreshing_metrics_does_not_leave_stale_side_data(bot):
    """지표를 다시 계산하면 그 종목의 판정·실적일도 같이 다시 만든다."""
    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = sample_metrics()
    bot._assessment_cache[target.cik] = "옛 판정"
    bot._earnings_cache[target.cik] = "옛 실적일"

    bot.ensure_all_metrics(force=True)

    assert bot._assessment_cache.get(target.cik) != "옛 판정"
    assert bot._earnings_cache.get(target.cik) != "옛 실적일"


def test_progress_is_reported_while_it_runs(bot):
    """종목당 10초씩 걸린다. 어디까지 왔는지 안 보이면 멈춘 걸로 보인다."""
    seen = []
    dash = Dashboard(bot)
    report = dash._progress("지표를 계산하는 중")
    report(0, 3, "AAPL")
    seen.append(dash.busy)
    report(2, 3, "NVDA")
    seen.append(dash.busy)

    assert seen == ["지표를 계산하는 중 (1/3) AAPL…", "지표를 계산하는 중 (3/3) NVDA…"]


def test_a_failed_refresh_keeps_the_numbers_i_already_had(bot, monkeypatch):
    """새로 받다 실패했다고 멀쩡하던 숫자까지 지우면 화면이 더 나빠진다."""
    target = bot.targets()[0]
    good = sample_metrics()
    bot._metrics_cache[target.cik] = good

    def explode(*args, **kwargs):
        raise RuntimeError("SEC 접속 실패")

    monkeypatch.setattr(bot.xbrl, "company_facts", explode)
    done, failed = bot.ensure_all_metrics(force=True)

    assert failed == ["AAPL"]
    assert bot.cached_metrics()[target.cik] is good      # 옛 숫자는 남는다


def test_the_page_stops_retrying_instead_of_spinning_forever(bot, monkeypatch):
    """계속 실패하는 종목 때문에 '불러오는 중' 이 무한 반복되면 안 된다."""
    target = bot.targets()[0]
    monkeypatch.setattr(bot, "missing_metrics", lambda: [target])
    monkeypatch.setattr(bot, "ensure_all_metrics",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("계속 실패")))

    dash = Dashboard(bot)
    for _ in range(10):
        dash.autofill_if_needed()
        wait_idle(dash, timeout=10)

    assert dash._autofill_tries == 3, "정해둔 횟수를 넘겨 계속 재시도했습니다"

    dash.run_action("check", {})     # 사람이 누르면 다시 시작한다
    wait_idle(dash, timeout=10)
    assert dash._autofill_tries == 0


def test_pressing_a_button_while_busy_says_so(bot):
    """작업 중에 버튼을 누르면 아무 반응이 없어서 '먹통' 으로 보였다.

    작업 중에는 notice 자리를 진행 표시가 차지해서 답이 화면에 안 나왔다.
    """
    dash = Dashboard(bot)
    dash.busy = "무언가 하는 중"

    dash.run_action("metrics", {})
    block = dash._notice_block()

    assert "무언가 하는 중" in block
    assert "지표를 계산하는 중 — 지금 작업이 끝난 뒤에 다시 눌러주세요." in block
    assert "🕐" in block


def test_the_waiting_note_is_shown_once(bot):
    dash = Dashboard(bot)
    dash.busy = "무언가 하는 중"
    dash.run_action("metrics", {})

    assert "다시 눌러주세요" in dash._notice_block()
    assert "다시 눌러주세요" not in dash._notice_block()


def test_metrics_button_explains_an_empty_watchlist(bot, monkeypatch):
    """SEC 가 막혀 종목을 못 찾은 것과 '종목이 없는 것' 은 다른 말이다."""
    monkeypatch.setattr(bot, "targets", lambda: [])
    monkeypatch.setattr(bot, "unresolved_tickers", lambda: ["AAPL"])

    dash = Dashboard(bot)
    dash.run_action("metrics", {})
    notice = wait_idle(dash, timeout=10)
    assert "SEC 에서 AAPL 를 찾지 못했습니다" in notice
    assert "실행기록.log" in notice


# --- 종료 --------------------------------------------------------------------
def test_the_quit_button_stops_the_program(bot, monkeypatch):
    """창이 없으니 끄는 방법은 화면에 있어야 한다.

    안 그러면 프로그램이 폴더를 붙잡고 있어서 폴더를 지우지도 옮기지도 못한다.
    (실제로 '사용 중인 폴더' 라며 삭제가 안 되는 일이 있었다)
    """
    stopped = []
    monkeypatch.setattr("stock_analysis.dashboard.stop_process", lambda: stopped.append(True))

    dash = Dashboard(bot)
    assert "멈췄습니다" in dash.run_action("quit", {})

    time.sleep(1.0)                     # 답을 보낸 뒤에 끄도록 잠깐 미뤄져 있다
    assert stopped == [True]


def test_the_quit_button_is_on_the_screen(bot):
    html = Dashboard(bot).render()
    assert 'value="quit"' in html
    assert "⏻ 종료" in html
    assert "confirm(" in html            # 실수로 눌러도 한 번 물어본다


def test_the_last_screen_explains_what_to_do_next(bot):
    page = Dashboard(bot).render_goodbye()
    assert "감시를 멈췄습니다" in page
    assert "시작하기" in page
    assert "사용 중인 폴더" in page       # 폴더가 안 지워지던 이유까지 알려준다


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

    from stock_analysis.fx import IndexQuote, MarketSnapshot, Rate

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
    from stock_analysis.funds import classify_name
    from stock_analysis.metrics import build_fund_metrics

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

    from stock_analysis.track_record import TrackItem, TrackRecord

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
    assert "$450.00M ~ $470.00M" in html
    assert "$478.00M" in html
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


# --- 카드 구획 접기 ---------------------------------------------------------
def test_card_groups_carry_their_conclusion_when_collapsed(bot):
    """접힌 줄만 보고도 무엇을 펼칠지 고를 수 있어야 한다."""
    from stock_analysis.insiders import summarize
    from stock_analysis.risk_watch import build_risk_change
    from stock_analysis.filing_text import FilingText, Section

    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = sample_metrics(target.ticker)

    old = ["We depend on a limited number of suppliers for key components, and losing any of them "
           "would delay production and raise our costs for an extended period of time."]
    new = old + ["We may need to raise additional capital to fund our operations, and such capital "
                 "may not be available on acceptable terms when we need it."]
    bot._risk_cache[target.cik] = build_risk_change(
        target.ticker,
        FilingText("10-Q", "2026-08-05", None, "https://sec/a", [Section("risk", "위험", new)]),
        FilingText("10-Q", "2026-05-06", None, "https://sec/b", [Section("risk", "위험", old)]),
    )
    bot._insider_cache[target.cik] = summarize(target.ticker, [])

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "details class=\"grp\"" in html
    assert "🎯 메모 기준 판단" in html
    assert "⚠️ 위험 요인 변화" in html
    assert "추가 자금 필요" in html            # 접힌 줄에 결론이 보인다
    assert "👤 내부자 거래" in html
    assert "공개시장 매매가 없었습니다" in html


def test_risk_paragraph_and_flag_are_rendered(bot):
    from stock_analysis.risk_watch import build_risk_change
    from stock_analysis.filing_text import FilingText, Section

    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = sample_metrics(target.ticker)
    text = ("Our independent auditors have expressed substantial doubt about our ability to continue "
            "as a going concern given our recurring losses from operations.")
    bot._risk_cache[target.cik] = build_risk_change(
        target.ticker,
        FilingText("10-K", "2026-08-05", None, "https://sec/a", [Section("risk", "위험", [text])]),
    )
    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "존속 의문" in html
    assert "going concern" in html            # 회사가 쓴 문장 그대로
    assert "감사인이 이 문구를 붙이면" in html   # 무슨 뜻인지 설명


def test_insider_table_shows_who_bought(bot):
    from stock_analysis.insiders import summarize
    from stock_analysis.edgar import Filing

    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = sample_metrics(target.ticker)
    filing = Filing(cik=target.cik, ticker="AAPL", company="Apple", form="4",
                    accession="0000320193-26-000099", filing_date="2026-07-28",
                    accepted=None, report_date="2026-07-28", primary_doc="")
    filing.insider, filing.insider_title = "Beck Peter", "이사, CEO"
    filing.transactions = [{"code": "P", "date": "2026-07-28", "shares": 50000,
                            "price": 41.20, "value": 2_060_000, "derivative": False}]
    bot._insider_cache[target.cik] = summarize(target.ticker, [filing])

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "Beck Peter" in html and "이사, CEO" in html
    assert "$2.06M" in html
    assert "순매수" in html
    assert "RSU 수령·세금 납부용 반납" in html   # 무엇을 뺐는지 밝힌다


# --- 내 보유 ----------------------------------------------------------------
def test_position_shows_dollar_and_won(bot):
    from datetime import datetime, timezone
    from stock_analysis.fx import MarketSnapshot, Rate

    target = bot.targets()[0]
    metrics = sample_metrics(target.ticker)
    metrics.price = 48.20
    bot._metrics_cache[target.cik] = metrics
    target.watch.buy_price, target.watch.buy_shares = 38.40, 120
    bot.fx._snapshot = MarketSnapshot(
        rates=[Rate("원", 1382.40, 0.4, 2, "₩")], indexes=[],
        fetched_at=datetime.now(timezone.utc))

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "내 보유" in html
    assert "+$1,176.00" in html         # 달러 손익
    assert "+25.52%" in html
    assert "163만원" in html            # 원화 환산
    assert "지금 환율" in html          # 어느 환율로 바꿨는지 밝힌다


def test_position_input_is_saved(bot):
    dash = Dashboard(bot)
    assert "저장했습니다" in dash.run_action(
        "position", {"ticker": ["AAPL"], "price": ["38.40"], "shares": ["120"]})
    watch = bot.targets()[0].watch
    assert watch.buy_price == 38.40 and watch.buy_shares == 120


def test_position_input_rejects_words(bot):
    dash = Dashboard(bot)
    assert "숫자로" in dash.run_action(
        "position", {"ticker": ["AAPL"], "price": ["비싸게"], "shares": ["열개"]})


# --- 실적 3자 대조 ----------------------------------------------------------
def test_recap_table_appears(bot):
    from datetime import date

    from stock_analysis.guidance import GuidanceItem, GuidanceReport

    target = bot.targets()[0]
    metrics = sample_metrics(target.ticker)
    metrics.surprise = {"actual_revenue": 246e6, "consensus_revenue": 240e6,
                        "period": "2026-06-30"}
    metrics.quarterly_revenue = [(date(2026, 6, 30), 246e6)]
    bot._metrics_cache[target.cik] = metrics
    bot._guidance_cache[target.cik] = GuidanceReport(
        form="8-K", filing_date="2026-05-08", url="https://sec/1",
        items=[GuidanceItem(sentence="We expect revenue of $230 million to $240 million.",
                            metric="매출", period="second quarter",
                            low=230e6, high=240e6, unit="$")])

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "실적 3자 대조" in html
    assert "매출 vs 컨센서스" in html
    assert "매출 vs 가이던스" in html
    assert "$246.00M" in html


# --- 화면 밝기 --------------------------------------------------------------
def test_theme_toggle_is_present_and_self_contained(bot):
    html = Dashboard(bot).render()
    assert 'id="themebtn"' in html
    assert "cycleTheme()" in html
    assert "localStorage" in html
    # 그려지기 전에 적용해야 새로고침마다 흰 화면이 번쩍이지 않는다
    assert html.index("localStorage") < html.index("<style>")


def test_all_three_theme_states_are_styled(bot):
    html = Dashboard(bot).render()
    assert "@media (prefers-color-scheme: dark)" in html
    assert ':root:not([data-theme="light"])' in html   # 시스템 어두움 + 밝게 선택 안 함
    assert ':root[data-theme="dark"]' in html          # 사람이 어둡게 고름


def test_market_strip_sits_in_the_header(bot):
    """헤더 왼쪽이 비어 보이지 않도록 환율 줄을 그 안에 넣었다."""
    html = Dashboard(bot).render()
    header = html[html.index("<header>"):html.index("</header>")]
    assert "1달러" in header or "환율·지수를 불러오는 중" in header


# --- 영어 원문에 한글 얹기 --------------------------------------------------
def _report_with(sentences, risk_paragraphs=None):
    from stock_analysis.filing_text import FilingText, Section

    sections = [Section("mdna", "경영진 논의 (MD&A)", list(sentences))]
    if risk_paragraphs:
        sections.append(Section("risk", "위험 요인", list(risk_paragraphs)))
    report = FilingText("10-Q", "2026-08-05", "2026-06-30", "https://sec/a", sections)
    report.company_words = list(sentences)
    return report


def test_korean_summary_appears_above_the_english(bot):
    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = sample_metrics(target.ticker)
    bot._report_cache[target.cik] = _report_with(
        ["Revenue increased 78% year over year to $213.0 million."]
    )

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "매출 $213.0M — 전년 대비 78% 증가" in html      # 한글이 위
    assert "영어 원문" in html                              # 원문은 접어서 아래
    assert "Revenue increased 78%" in html                  # 원문을 지우지는 않는다


def test_machine_translation_is_labelled_as_such(bot):
    target = bot.targets()[0]
    sentence = "The board appointed a new advisory committee to oversee the transition."
    bot._metrics_cache[target.cik] = sample_metrics(target.ticker)
    bot._report_cache[target.cik] = _report_with([sentence])
    bot._korean_cache[target.cik] = {
        sentence: __import__("stock_analysis.korean", fromlist=["KoreanNote"]).KoreanNote(
            machine="이사회가 전환을 감독할 새 자문위원회를 임명했습니다."
        )
    }

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "이사회가 전환을 감독할" in html
    assert "기계 번역" in html            # 자동 번역임을 반드시 밝힌다


def test_risk_paragraph_gets_a_korean_topic(bot):
    from stock_analysis.filing_text import FilingText, Section
    from stock_analysis.risk_watch import build_risk_change

    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = sample_metrics(target.ticker)
    old = ["We face intense competition from companies with greater resources than ours, "
           "which could reduce our share of the market over an extended period of time."]
    new = old + ["We depend on a limited number of suppliers for certain critical components, "
                 "and losing any of them would delay production and raise our costs."]
    bot._risk_cache[target.cik] = build_risk_change(
        target.ticker,
        FilingText("10-Q", "2026-08-05", None, "https://sec/a", [Section("risk", "위험", new)]),
        FilingText("10-Q", "2026-05-06", None, "https://sec/b", [Section("risk", "위험", old)]),
    )

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "공급망" in html                                   # 무엇에 관한 위험인지
    assert "부품·원자재를 특정 업체에 의존" in html            # 그게 무슨 뜻인지
    assert "We depend on a limited number of suppliers" in html   # 원문도 그대로


def test_guidance_sentence_gets_a_korean_headline(bot):
    from stock_analysis.guidance import GuidanceItem, GuidanceReport

    target = bot.targets()[0]
    bot._metrics_cache[target.cik] = sample_metrics(target.ticker)
    bot._guidance_cache[target.cik] = GuidanceReport(
        form="8-K", filing_date="2026-05-08", url="https://sec/1",
        items=[GuidanceItem(
            sentence="We expect revenue in the second quarter of 2026 to be in the range of "
                     "$230 million to $240 million.",
            metric="매출", period="second quarter", low=230e6, high=240e6, unit="$")])

    dash = Dashboard(bot)
    dash.busy = "고정"
    html = dash.render()

    assert "2분기 매출 전망" in html
    assert "We expect revenue in the second quarter" in html


def test_sentences_without_a_rule_still_show_the_original(bot):
    """옮길 말이 없다고 문장을 숨기면 정보가 사라진다."""
    target = bot.targets()[0]
    odd = "The Company relocated its administrative office to a new building this quarter."
    bot._metrics_cache[target.cik] = sample_metrics(target.ticker)
    bot._report_cache[target.cik] = _report_with([odd])

    dash = Dashboard(bot)
    dash.busy = "고정"
    assert odd in dash.render()


# --- 번역 설정 (화면에서 열쇠 넣기) -----------------------------------------
def test_translate_panel_says_it_already_works(bot):
    """열쇠 없이도 번역이 된다는 걸 먼저 알려야 한다."""
    html = Dashboard(bot).render()
    assert "번역 설정" in html
    assert "아무것도 안 하셔도 됩니다" in html
    assert "지금 쓰는 번역기: 무료 번역" in html      # 접힌 줄에 지금 상태가 보인다
    assert "눌러서 펼치기" in html
    assert "DeepL" in html and "deepl.com" in html      # 어디서 받는지


def test_saving_a_key_switches_the_engine(bot):
    dash = Dashboard(bot)
    reply = dash.run_action("translator", {"provider": ["deepl"], "key": ["abc:fx"]})
    assert "DeepL 열쇠를 저장했습니다" in reply

    # 저장한 열쇠가 번역기에 반영된다
    assert bot.translator.available()[0] == "deepl"
    assert bot.translate_settings()["deepl_key"] == "abc:fx"

    html = dash.render()
    assert "지금 쓰는 번역기: DeepL" in html
    assert "안 되면 무료 번역" in html                 # 실패 시 넘어갈 곳


def test_the_key_is_stored_locally_not_in_config(bot):
    dash = Dashboard(bot)
    dash.run_action("translator", {"provider": ["deepl"], "key": ["abc:fx"]})

    saved = bot.overrides.settings("translate")
    assert saved["deepl_key"] == "abc:fx"
    assert "translate" not in bot.config.raw          # config.yml 은 건드리지 않는다


def test_an_empty_key_clears_it(bot):
    dash = Dashboard(bot)
    dash.run_action("translator", {"provider": ["deepl"], "key": ["abc:fx"]})
    reply = dash.run_action("translator", {"provider": ["deepl"], "key": [""]})

    assert "무료 번역으로 돌아갑니다" in reply
    assert "deepl_key" not in bot.overrides.settings("translate")


def test_the_key_never_appears_in_the_page(bot):
    """비밀번호 칸이라도 값이 HTML 로 새어나가면 안 된다."""
    dash = Dashboard(bot)
    dash.run_action("translator", {"provider": ["deepl"], "key": ["super-secret-key"]})
    assert "super-secret-key" not in dash.render()


def test_translate_test_button_reports_what_happened(bot):
    class FakeTranslator:
        def available(self):
            return ["free"]

        def translate(self, text):
            from stock_analysis.translate import Result

            return Result("매출 $213.0M — 전년 대비 78% 증가", "free")

    bot.translator = FakeTranslator()
    dash = Dashboard(bot)
    assert "시험하는 중" in dash.run_action("translate_test", {})
    assert "무료 번역 로 번역했습니다" in wait_idle(dash)


def test_translate_test_explains_a_failure(bot):
    class DeadTranslator:
        def available(self):
            return []

        def translate(self, text):
            from stock_analysis.translate import Result

            return Result()

    bot.translator = DeadTranslator()
    dash = Dashboard(bot)
    dash.run_action("translate_test", {})
    assert "쓸 수 있는 번역기가 없습니다" in wait_idle(dash)


# --- 눈여겨볼 종목 ----------------------------------------------------------


def picks_for(bot, *entries):
    from stock_analysis.screener import Pick

    for pick in entries:
        bot._picks.remember(pick.ticker, pick, "2026-09-02")
    return Pick


def test_a_recommendation_always_shows_why_and_how_many_we_looked_at(bot):
    """근거 없는 추천은 하지 않고, 몇 개 중에서 골랐는지도 숨기지 않는다."""
    from stock_analysis.screener import Pick

    picks_for(bot, Pick(
        ticker="COST", name="COSTCO WHOLESALE CORP", score=19.5,
        headline="흑자 기업, 재무 안정성은 양호.",
        reasons=["성장: 매출이 +43% 성장 중입니다. (TTM 매출 $130.50B)"],
        cautions=["확인 못 한 항목: 밸류에이션"],
    ))
    html = Dashboard(bot).render()

    assert "눈여겨볼 종목" in html
    assert "COST" in html
    assert "+43% 성장" in html                    # 이유는 숫자로
    assert "확인 못 한 항목: 밸류에이션" in html      # 모르는 것도 그대로
    assert "사라는 뜻이 아닙니다" in html
    assert "후보" in html and "확인" in html        # 몇 개 중에서 골랐는지


def test_the_candidate_list_says_where_it_came_from(bot):
    """무엇이 후보에 들어가느냐가 곧 무엇을 추천받느냐다. 출처를 숨기면 안 된다."""
    from stock_analysis.screener import Pick

    picks_for(bot, Pick(ticker="COST", name="코스트코", score=19.5, reasons=["성장 좋음"]))
    bot.universe_builder._cached = None
    (bot.universe_builder.path).parent.mkdir(parents=True, exist_ok=True)
    bot.universe_builder.path.write_text(
        '{"tickers": ["COST", "AAPL"], "period": "2025년", "fetched": "2026-09-02",'
        ' "total_filers": 4821, "source": "SEC"}', encoding="utf-8")

    html = Dashboard(bot).render()

    assert "4,821" in html and "2025년" in html
    assert "손으로 적은 목록이 아니라" in html


def test_no_candidate_list_means_an_empty_space_not_a_made_up_one(bot):
    """SEC 에서 못 받았으면 비워 둔다. 대신 쓸 목록을 지어내지 않는다."""
    html = Dashboard(bot).render()

    assert "후보 목록을 SEC 에서 받지 못했습니다" in html
    assert "지어내지 않습니다" in html


def test_a_stock_i_already_watch_gets_no_add_button(bot):
    from stock_analysis.screener import Pick

    ticker = bot.targets()[0].ticker
    picks_for(bot, Pick(ticker=ticker, name="애플", score=20.0, reasons=["좋음"]))
    html = Dashboard(bot).render()

    assert "이미 감시 중" in html


def test_a_new_stock_can_be_added_straight_from_the_recommendation(bot):
    from stock_analysis.screener import Pick

    picks_for(bot, Pick(ticker="COST", name="코스트코", score=20.0, reasons=["좋음"]))
    html = Dashboard(bot).render()

    assert '<form method="post" action="/action" class="pk-add">' in html
    assert 'value="COST"' in html
    assert "감시 목록에 추가" in html


def test_nothing_found_yet_says_so_instead_of_going_blank(bot):
    """빈 자리를 그냥 두면 고장 난 것처럼 보인다."""
    html = Dashboard(bot).render()
    assert "아직 추천할 만한 종목을 찾지 못했습니다" in html


def test_turning_recommendations_off_removes_the_section(bot):
    from stock_analysis.screener import Pick

    picks_for(bot, Pick(ticker="COST", score=20.0, reasons=["좋음"]))
    bot.config.raw["recommend"] = {"enabled": False}
    html = Dashboard(bot).render()

    assert "눈여겨볼 종목" not in html


def test_a_recommendation_cannot_inject_html(bot):
    """후보 이름은 SEC 에서 온 남의 글자다. 그대로 화면에 심으면 안 된다."""
    from stock_analysis.screener import Pick

    picks_for(bot, Pick(ticker="EVIL", name="<script>alert(1)</script>",
                        score=20.0, reasons=["<img onerror=x>"]))
    html = Dashboard(bot).render()

    assert "<script>alert(1)</script>" not in html
    assert "<img onerror=x>" not in html
    assert "&lt;script&gt;" in html


def test_each_category_shows_its_own_warning(bot):
    """숫자만 보여주고 한계를 빼면 그게 제일 위험하다."""
    from stock_analysis.screener import BLUE, GROWTH, MOMENTUM, Pick

    picks_for(
        bot,
        Pick(ticker="COST", name="코스트코", category=BLUE, score=20, reasons=["탄탄"]),
        Pick(ticker="RKLB", name="로켓랩", category=GROWTH, score=30, reasons=["매출 +78%"]),
        Pick(ticker="PLTR", name="팔란티어", category=MOMENTUM, score=21, reasons=["시장보다 +24%p"]),
    )
    html = Dashboard(bot).render()

    assert "탄탄한 회사 1개" in html
    assert "성장 가능성 1개" in html
    assert "시장 흐름 1개" in html
    assert "증자" in html                                  # 성장 갈래의 위험
    assert "앞으로 오른다는 뜻이 전혀 아닙니다" in html      # 시장 흐름의 한계
    assert "갈래끼리는 점수를 견주지 않습니다" in html


def test_a_recommendation_starts_folded_and_can_be_opened(bot):
    """다섯 개의 근거를 한꺼번에 늘어놓으면 화면 한 판을 다 먹는다."""
    from stock_analysis.screener import BLUE, Pick

    picks_for(bot, Pick(ticker="COST", name="코스트코", category=BLUE, score=20,
                        reasons=["성장: 매출이 +43% 성장 중입니다."]))
    html = Dashboard(bot).render()

    assert '<details class="pk-d" data-keep="pick-blue-COST">' in html   # 접힌 채로 시작
    assert 'class="fold" data-keep="picks" open' in html                 # 섹션은 펼친 채로
    assert "성장: 매출이 +43% 성장 중입니다." in html                     # 내용은 안에 들어 있다


def test_what_i_unfolded_is_remembered_across_refreshes(bot):
    """화면이 90초마다 스스로 새로고침된다. 그때 도로 닫히면 읽던 자리를 잃는다."""
    html = Dashboard(bot).render()
    assert "restoreFolds" in html and "localStorage" in html


def test_a_value_left_out_of_the_judgment_is_still_shown(bot):
    """값이 없는 것과 못 미더운 것은 다르다. 지우지 말고 이유와 함께 남긴다."""
    from stock_analysis.screener import BLUE, Pick

    picks_for(bot, Pick(
        ticker="AAPL", name="Apple Inc.", category=BLUE, score=20, reasons=["마진 개선 중"],
        notes=["ROE 100.5% — 자사주를 오래 사들인 회사는 자기자본이 크게 줄어서 "
               "이 비율이 사업 성과와 상관없이 치솟습니다."],
    ))
    html = Dashboard(bot).render()

    assert "참고 — 판단에는 넣지 않은 값" in html
    assert "100.5%" in html
    assert "자사주" in html


# --- 미국 / 한국 나누기 -----------------------------------------------------
#
# 두 시장은 보는 자료가 아예 다르다. 미국은 SEC 가 전부 무료로 주지만
# 한국은 DART 열쇠가 필요하고 받아올 수 있는 항목도 다르다. 한 화면에
# 섞으면 어떤 숫자가 어느 기준으로 나온 것인지 알 수 없게 된다.


def watch_korean(bot, ticker="005930", name="삼성전자"):
    from stock_analysis.config import Watch

    bot.config.watchlist = list(bot.config.watchlist) + [Watch(ticker=ticker, name=name)]
    bot._targets = None
    return bot


def test_both_markets_get_a_button(bot):
    html = Dashboard(bot).render()
    assert 'href="/?m=us"' in html and 'href="/?m=kr"' in html
    assert "미국" in html and "한국" in html


def test_the_button_shows_how_many_i_watch_in_each(bot):
    watch_korean(bot)
    html = Dashboard(bot).render()
    assert '<span class="tab-n">1</span>' in html      # 각 시장에 하나씩


def test_a_korean_stock_does_not_show_up_on_the_us_page(bot):
    watch_korean(bot)
    html = Dashboard(bot).render(market="us")
    assert "005930" not in html.split('<nav class="tabs">')[-1].split("</nav>")[0]


def test_the_korean_page_shows_korean_stocks(bot):
    watch_korean(bot)
    html = Dashboard(bot).render(market="kr")
    assert "005930" in html


def test_the_korean_tab_warns_when_the_key_is_missing(bot):
    """열쇠가 없으면 재무제표가 빈다. 화면이 왜 빈지 말해줘야 한다."""
    watch_korean(bot)
    html = Dashboard(bot).render()
    assert "열쇠 필요" in html


def test_no_warning_once_the_key_is_there(bot):
    watch_korean(bot)
    bot.dart.api_key = "있는열쇠"
    html = Dashboard(bot).render()
    assert "열쇠 필요" not in html


def test_us_only_sections_stay_off_the_korean_page(bot):
    """미국 기준 자료를 한국 화면에 띄우면 한국 증시 이야기로 읽힌다."""
    watch_korean(bot)
    html = Dashboard(bot).render(market="kr")

    assert "<h2>눈여겨볼 종목" not in html      # 설명문에는 나와도 되지만 섹션은 없어야
    assert "<h2>휴장·조기폐장" not in html
    assert "<h2>경제 지표" not in html
    assert "사라는 뜻이 아닙니다" not in html


def test_the_korean_page_says_what_is_not_there_yet(bot):
    """아직 안 만든 것과 고장 난 것은 다르다. 그 차이를 말해줘야 한다."""
    watch_korean(bot)
    html = Dashboard(bot).render(market="kr")

    assert "한국 화면에서 아직 안 되는 것" in html
    assert "opendart.fss.or.kr" in html          # 열쇠 받는 곳
    assert "지어내지 않습니다" in html            # PER 을 비우는 이유
    assert "지금 한국 화면에서 되는 것" in html   # 되는 것도 함께


def test_with_a_key_the_page_stops_asking_for_one(bot):
    watch_korean(bot)
    bot.dart.api_key = "있는열쇠"
    html = Dashboard(bot).render(market="kr")

    assert "인증키가 없어 비어 있습니다" not in html
    assert "연간 확정치" in html                  # 대신 무엇을 쓰는지 밝힌다


def test_the_us_page_is_unchanged(bot):
    watch_korean(bot)
    html = Dashboard(bot).render(market="us")

    assert "눈여겨볼 종목" in html
    assert "한국 화면에서 아직 안 되는 것" not in html


# --- 공시를 시장별로 나누기 -------------------------------------------------
def add_filing(bot, market, ticker, title, **extra):
    entry = {"market": market, "ticker": ticker, "company": "", "form": "DART" if market == "kr" else "8-K",
             "title": title, "tone": "alert", "items": [], "date": "2026-09-02",
             "when": "2026-09-02", "url": "https://example.test/1", "index_url": ""}
    entry.update(extra)
    bot.state.add_recent(entry)


def test_korean_filings_do_not_show_on_the_us_page(bot):
    """한국 화면에 미국 공시가 섞이면 어느 쪽 이야기인지 알 수 없다."""
    watch_korean(bot)
    add_filing(bot, "kr", "005930", "유상증자 결정")
    add_filing(bot, "us", "AAPL", "실적 발표")

    assert "유상증자 결정" not in Dashboard(bot).render(market="us")
    assert "유상증자 결정" in Dashboard(bot).render(market="kr")


def test_old_filings_without_a_market_are_treated_as_us(bot):
    """예전에 쌓인 공시에는 시장 표시가 없다. 그게 사라지면 안 된다."""
    entry = {"ticker": "AAPL", "form": "8-K", "title": "예전 공시", "tone": "plain",
             "when": "2026-08-01", "url": "#"}
    bot.state.add_recent(entry)

    assert "예전 공시" in Dashboard(bot).render(market="us")


def test_a_korean_filing_says_what_to_look_at(bot):
    """'유상증자결정' 다섯 글자만으로는 좋은 일인지 나쁜 일인지 알 수 없다."""
    watch_korean(bot)
    add_filing(bot, "kr", "005930", "유상증자 결정",
               why="새 주식을 찍어 파는 것입니다. 발행주식수가 늘어 내 몫이 줄어듭니다.",
               report="주요사항보고서(유상증자결정)")
    html = Dashboard(bot).render(market="kr")

    assert "내 몫이 줄어듭니다" in html                 # 판단 기준
    assert "주요사항보고서(유상증자결정)" in html        # DART 원래 이름도 함께


def test_each_page_names_its_source(bot):
    watch_korean(bot)
    assert "SEC EDGAR" in Dashboard(bot).render(market="us")
    assert "금융감독원 DART" in Dashboard(bot).render(market="kr")


# --- 장 시작 / 마감 ---------------------------------------------------------
def test_each_market_shows_whether_it_is_open(bot):
    html = Dashboard(bot).render()
    assert "tab-open" in html
    assert any(word in html for word in ("장중", "장 마감", "장 시작 전"))


def test_a_guessed_state_is_marked_as_a_guess(bot):
    """시세를 못 받으면 시각으로 어림한다. 어림을 사실처럼 보여주면 안 된다."""
    html = Dashboard(bot).render()
    assert "~" in html
    assert "시각으로 어림한 값입니다" in html


def test_the_exchange_state_wins_over_the_clock(bot):
    """거래소가 알려준 값이 있으면 그걸 쓴다. 휴장일도 자동으로 맞는다."""
    from stock_analysis.metrics import Metrics

    target = bot.targets()[0]
    live = Metrics(ticker=target.ticker)
    live.market_state = "REGULAR"
    bot._metrics_cache[target.cik] = live

    state, _shown, guessed = bot.market_state("us")

    assert state == "open" and guessed is False
