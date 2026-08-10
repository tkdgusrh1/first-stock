"""대시보드 렌더링과 버튼 동작. 실제 포트를 열어 요청까지 보내본다."""

import socket
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


@pytest.fixture
def server(bot):
    srv = start_dashboard(bot, port=8931, open_browser=False)
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


def test_page_marks_uncalculated_metrics(bot):
    assert "지표 미계산" in Dashboard(bot).render()


def test_page_shows_metrics_when_available(bot):
    from factories import build_facts

    from stockbot.metrics import build_metrics

    metrics = build_metrics(
        "AAPL",
        build_facts(
            revenue=[100e6, 110e6, 120e6, 130e6, 140e6, 150e6, 160e6, 170e6],
            net_income=[10e6, 11e6, 12e6, 13e6, 15e6, 16e6, 17e6, 18e6],
            operating_income=[12e6, 13e6, 14e6, 15e6, 18e6, 20e6, 22e6, 24e6],
            equity=300e6,
        ),
    )
    bot._metrics_cache[bot.targets()[0].cik] = metrics
    html = Dashboard(bot).render()
    assert "ROE" in html and "22.0%" in html
    assert "① 분기 매출 지속" in html
    assert "1순위 · 가이던스" in html


def test_recent_filings_appear(bot):
    bot.check_filings(force=True)
    html = Dashboard(bot).render()
    assert "실적 발표" in html
    assert "내부자" in html
    assert "tone-alert" in html         # 8-K 2.02 는 강조 표시


def test_html_escaping_of_user_values(bot):
    bot.targets()[0].watch.milestones = ["<script>alert(1)</script>"]
    html = Dashboard(bot).render()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- 버튼 동작 -------------------------------------------------------------
def test_add_and_remove_through_dashboard(bot):
    dash = Dashboard(bot)
    assert "추가했습니다" in dash.run_action("add", {"ticker": ["NVDA"]})
    assert [t.ticker for t in bot.targets()] == ["AAPL", "NVDA"]

    assert "뺐습니다" in dash.run_action("remove", {"ticker": ["NVDA"]})
    assert [t.ticker for t in bot.targets()] == ["AAPL"]


def test_add_requires_ticker(bot):
    assert "티커를 입력" in Dashboard(bot).run_action("add", {"ticker": [""]})


def test_unknown_action(bot):
    assert "알 수 없는" in Dashboard(bot).run_action("아무거나", {})


def test_background_action_reports_result(bot):
    dash = Dashboard(bot)
    message = dash.run_action("check", {})
    assert "확인하는 중" in message
    for _ in range(50):
        if dash.busy is None:
            break
        time.sleep(0.05)
    assert dash.busy is None
    assert dash.notice is not None


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
