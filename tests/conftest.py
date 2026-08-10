import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fixtures import FORM4_XML  # noqa: E402

from stockbot.app import Bot  # noqa: E402
from stockbot.config import Config, Watch  # noqa: E402

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
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
    """네트워크 대신 고정된 EDGAR 응답을 돌려준다."""

    def __init__(self, submissions=None):
        self.submissions = submissions if submissions is not None else SUBMISSIONS
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "company_tickers.json" in url:
            return FakeResponse(json.dumps(TICKERS))
        if "submissions/CIK" in url:
            return FakeResponse(json.dumps(self.submissions))
        if url.endswith("wf-form4_1.xml"):
            return FakeResponse(FORM4_XML)
        return FakeResponse("", 404)

    def get_json(self, url, **kwargs):
        return self.get(url, **kwargs).json()

    def get_text(self, url, **kwargs):
        return self.get(url, **kwargs).text


@pytest.fixture
def submissions():
    """테스트마다 독립된 사본 (한 테스트의 수정이 다른 테스트에 새지 않도록)."""
    return copy.deepcopy(SUBMISSIONS)


@pytest.fixture
def make_config(tmp_path):
    def _make(**overrides):
        kwargs = dict(
            user_agent="tester test@example.com",
            telegram_token="",
            telegram_chat_id="777",
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
            overrides_path=tmp_path / "watchlist.local.yml",
            telegram_commands=True,
            raw={},
        )
        kwargs.update(overrides)
        return Config(**kwargs)

    return _make


@pytest.fixture
def bot(make_config, submissions):
    bot = Bot(make_config(), dry_run=True)
    fake = FakeHttp(submissions)
    bot.http = fake
    bot.edgar.http = fake
    bot.xbrl.http = fake
    bot.prices.http = fake
    bot.sent = []
    bot.notifier.send = lambda text, **kw: (bot.sent.append(text), True)[1]
    bot.notifier.reply = lambda chat_id, text: (bot.sent.append(text), True)[1]
    return bot
