"""대시보드 렌더링과 버튼 동작. 실제 포트를 열어 요청까지 보내본다."""

import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from conftest import FakeHttp

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
    assert "불러오는 중" in Dashboard(bot).render()


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
    assert "분기 매출 추이" in html            # 매출 막대
    assert "영업현금흐름" in html              # 상세 숫자
    assert "종목별 상세" in html


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


def test_summarize_other_form():
    summary = summarize_filing(_filing("10-Q"), "Asia/Seoul")
    assert summary["title"] == "10-Q 제출"
    assert summary["tone"] == "plain"
