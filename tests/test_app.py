"""오케스트레이션 통합 테스트. 네트워크 대신 가짜 EDGAR 응답을 물린다."""

import json
from pathlib import Path

import pytest

from stockbot.app import Bot
from stockbot.config import Config, Watch
from test_edgar import FORM4_XML

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
}

SUBMISSIONS = {
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-26-000010", "0000320193-26-000009", "0000320193-26-000008"],
            "form": ["8-K", "4", "10-Q"],
            "filingDate": ["2026-08-09", "2026-08-08", "2026-08-01"],
            "acceptanceDateTime": [
                "2026-08-09T16:31:05.000Z",
                "2026-08-08T18:02:11.000Z",
                "2026-08-01T16:05:00.000Z",
            ],
            "reportDate": ["2026-08-09", "2026-08-07", "2026-06-30"],
            "primaryDocument": ["aapl-8k.htm", "xslF345X03/wf-form4_1.xml", "aapl-10q.htm"],
            "items": ["2.02,9.01", "", ""],
        }
    },
}


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


class FakeHttp:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "company_tickers.json" in url:
            return FakeResponse(json.dumps(TICKERS))
        if "submissions/CIK" in url:
            return FakeResponse(json.dumps(SUBMISSIONS))
        if url.endswith("wf-form4_1.xml"):
            return FakeResponse(FORM4_XML)
        return FakeResponse("", 404)

    def get_json(self, url, **kwargs):
        return self.get(url, **kwargs).json()

    def get_text(self, url, **kwargs):
        return self.get(url, **kwargs).text


@pytest.fixture
def bot(tmp_path):
    config = Config(
        user_agent="tester test@example.com",
        telegram_token="",
        telegram_chat_id="",
        watchlist=[Watch(ticker="AAPL")],
        forms=["8-K", "4"],
        poll_interval_sec=900,
        lookback_days=3650,          # 고정된 과거 픽스처를 쓰므로 넉넉히
        state_path=tmp_path / "state.json",
        cache_dir=tmp_path / "cache",
        timezone="Asia/Seoul",
        daily_brief_time="08:00",
        econ_lookahead_days=7,
        holiday_lookahead_days=21,
        metrics_in_brief=False,
        raw={},
    )
    bot = Bot(config, dry_run=True)
    fake = FakeHttp()
    bot.http = fake
    bot.edgar.http = fake
    bot.xbrl.http = fake
    bot.prices.http = fake
    bot.sent = []
    bot.notifier.send = lambda text, **kw: (bot.sent.append(text), True)[1]
    return bot


def test_ticker_resolution(bot):
    target = bot.targets()[0]
    assert target.cik == "0000320193"
    assert target.name == "Apple Inc."


def test_ticker_map_failure_does_not_retry_per_ticker(bot):
    class Broken(FakeHttp):
        def get(self, url, **kwargs):
            self.calls.append(url)
            raise RuntimeError("network down")

    broken = Broken()
    bot.edgar.http = broken
    bot.edgar._ticker_map = None
    bot.config.watchlist.append(Watch(ticker="", cik="1045810"))

    targets = bot.targets()
    # 티커 목록 요청은 한 번만, cik 를 직접 준 종목은 그대로 살아남는다
    assert len(broken.calls) == 1
    assert [t.cik for t in targets] == ["0001045810"]


def test_first_run_does_not_spam_old_filings(bot):
    assert bot.check_filings() == []
    assert bot.sent == []
    # 기준선이 저장되어 두 번째 실행에서도 조용해야 한다
    assert bot.check_filings() == []
    assert bot.sent == []


def test_force_sends_existing_filings(bot):
    filings = bot.check_filings(force=True)
    assert [f.form for f in filings] == ["4", "8-K"]   # 오래된 것부터
    assert len(bot.sent) == 2
    assert "실적 발표" in bot.sent[1]
    assert "Hong Gildong" in bot.sent[0]               # Form 4 XML까지 파싱됨


def test_new_filing_after_baseline_is_notified(bot):
    bot.check_filings()                                # 기준선 저장
    SUBMISSIONS["filings"]["recent"]["accessionNumber"].insert(0, "0000320193-26-000011")
    SUBMISSIONS["filings"]["recent"]["form"].insert(0, "8-K")
    SUBMISSIONS["filings"]["recent"]["filingDate"].insert(0, "2026-08-10")
    SUBMISSIONS["filings"]["recent"]["acceptanceDateTime"].insert(0, "2026-08-10T09:00:00.000Z")
    SUBMISSIONS["filings"]["recent"]["reportDate"].insert(0, "2026-08-10")
    SUBMISSIONS["filings"]["recent"]["primaryDocument"].insert(0, "aapl-8k-2.htm")
    SUBMISSIONS["filings"]["recent"]["items"].insert(0, "5.02")
    try:
        new = bot.check_filings()
        assert [f.accession for f in new] == ["0000320193-26-000011"]
        assert "임원·이사 선임/사임" in bot.sent[0]
    finally:
        for key in SUBMISSIONS["filings"]["recent"]:
            SUBMISSIONS["filings"]["recent"][key].pop(0)


def test_10q_is_filtered_out_by_forms(bot):
    filings = bot.check_filings(force=True)
    assert all(f.form != "10-Q" for f in filings)


def test_seen_filings_persist_across_restarts(bot, tmp_path):
    bot.check_filings(force=True)
    assert len(bot.sent) == 2

    restarted = Bot(bot.config, dry_run=True)
    restarted.http = bot.http
    restarted.edgar.http = bot.http
    restarted.sent = []
    restarted.notifier.send = lambda text, **kw: (restarted.sent.append(text), True)[1]
    assert restarted.check_filings() == []
    assert restarted.sent == []


def test_failed_send_leaves_filing_unmarked(bot):
    bot.notifier.send = lambda text, **kw: False
    assert bot.check_filings(force=True) == []

    bot.notifier.send = lambda text, **kw: (bot.sent.append(text), True)[1]
    assert len(bot.check_filings()) == 2   # 다음 실행에서 재시도된다


def test_daily_brief_is_sent_once_per_day(bot):
    text = bot.daily_brief()
    assert text and "데일리 브리핑" in text
    assert "다가오는 휴장·조기폐장" in text
    assert "주요 경제지표 일정" in text
    assert bot.daily_brief() is None          # 같은 날 재전송 안 함
    assert bot.daily_brief(force=True)        # 강제는 가능


def test_state_file_is_written(bot):
    bot.check_filings(force=True)
    saved = json.loads(Path(bot.config.state_path).read_text(encoding="utf-8"))
    assert "0000320193" in saved["seen"]
    assert len(saved["seen"]["0000320193"]) == 2
