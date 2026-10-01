import pytest

from stock_analysis.config import ConfigError, load_config

MINIMAL = """
user_agent: "Tester tester@example.com"
watchlist:
  - AAPL
"""


def write(tmp_path, text):
    path = tmp_path / "config.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_config_gets_defaults(tmp_path):
    config = load_config(write(tmp_path, MINIMAL))
    assert [w.ticker for w in config.watchlist] == ["AAPL"]
    assert config.forms[:2] == ["8-K", "4"]
    assert "10-Q" in config.forms and "S-3" in config.forms   # 정기보고서·증자까지 감시
    assert config.poll_interval_sec == 300
    assert config.timezone == "Asia/Seoul"


def test_full_watch_entry(tmp_path):
    config = load_config(
        write(
            tmp_path,
            """
user_agent: "Tester tester@example.com"
forms: ["8-K"]
poll_interval_sec: 300
watchlist:
  - ticker: nvda
    name: 엔비디아
    forms: ["4", "10-q"]
    peers: [amd]
    consensus_eps: 1.01
    milestones: ["Neutron 첫 발사"]
""",
        )
    )
    watch = config.watchlist[0]
    assert watch.ticker == "NVDA"
    assert watch.forms == ["4", "10-Q"]
    assert watch.peers == ["AMD"]
    assert watch.consensus_eps == 1.01
    assert watch.milestones == ["Neutron 첫 발사"]
    assert config.poll_interval_sec == 300


def test_env_overrides_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    config = load_config(write(tmp_path, MINIMAL + '\ntelegram_token: "file-token"\n'))
    assert config.telegram_token == "env-token"
    assert config.telegram_chat_id == "12345"


def test_a_contact_without_email_does_not_stop_the_program(tmp_path):
    """예전엔 여기서 멈추고 터미널에서 물었다. 이제는 뜨고, 화면에서 이메일을 받는다."""
    config = load_config(write(tmp_path, 'user_agent: "Tester"\nwatchlist: [AAPL]\n'))
    assert config.user_agent == "" and not config.sec_ready


def test_a_missing_contact_does_not_stop_the_program(tmp_path):
    config = load_config(write(tmp_path, "watchlist: [AAPL]\n"))
    assert not config.sec_ready


def test_an_empty_watchlist_is_fine(tmp_path):
    config = load_config(write(tmp_path, 'user_agent: "Tester t@example.com"\nwatchlist: []\n'))
    assert config.watchlist == [] and config.sec_ready


def test_missing_file_message(tmp_path):
    with pytest.raises(ConfigError, match="config.example.yml"):
        load_config(tmp_path / "nope.yml")


def test_example_config_is_valid(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    config = load_config("config.example.yml")
    tickers = [w.ticker for w in config.watchlist]
    assert tickers == ["AAPL", "NVDA", "RKLB", "SPY", "QQQ"]   # 기업 3 + ETF 2
    assert config.raw["econ_extra_events"]
