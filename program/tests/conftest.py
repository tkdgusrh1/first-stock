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

from stock_analysis.app import Bot  # noqa: E402
from stock_analysis.config import Config, Watch  # noqa: E402

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
}

# SEC 가 따로 내주는 ETF·펀드 티커 목록 (company_tickers_mf.json)
FUND_TICKERS = {
    "fields": ["cik", "seriesId", "classId", "symbol"],
    "data": [
        [884394, "S000005395", "C000014723", "SPY"],
        [1730168, "S000075845", "C000236362", "ETHU"],
        [1689873, "S000058343", "C000191914", "CONL"],
    ],
}

FUND_SUBMISSIONS = {
    "name": "GraniteShares 2x Long COIN Daily ETF",
    "sic": "6726",
    "sicDescription": "Investment offices, NEC",
    "filings": {
        "recent": {
            "accessionNumber": ["0001689873-26-000004"],
            "form": ["497"],
            "filingDate": ["2026-08-05"],
            "acceptanceDateTime": ["2026-08-05T16:00:00.000Z"],
            "reportDate": ["2026-08-05"],
            "primaryDocument": ["conl-497.htm"],
            "items": [""],
        }
    },
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

    def __init__(self, submissions=None, funds=None):
        self.submissions = submissions if submissions is not None else SUBMISSIONS
        self.funds = funds or {}          # {CIK: 펀드 submissions}
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "company_tickers_mf.json" in url:
            return FakeResponse(json.dumps(FUND_TICKERS))
        if "company_tickers.json" in url:
            return FakeResponse(json.dumps(TICKERS))
        if "submissions/CIK" in url:
            for cik, payload in self.funds.items():
                if f"CIK{cik}" in url:
                    return FakeResponse(json.dumps(payload))
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
    # http 를 들고 있는 하위 클라이언트를 전부 가짜로 바꾼다.
    # 하나라도 빠뜨리면 테스트가 실제 네트워크를 타서 느려지고 불안정해진다.
    bot.http = fake
    for client in (bot.edgar, bot.xbrl, bot.prices, bot.estimates, bot.fx, bot.macro):
        client.http = fake
    bot.sent = []
    bot.notifier.send = lambda text, **kw: (bot.sent.append(text), True)[1]
    bot.notifier.reply = lambda chat_id, text: (bot.sent.append(text), True)[1]
    return bot
