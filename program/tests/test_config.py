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


def test_user_agent_must_contain_email(tmp_path):
    with pytest.raises(ConfigError, match="이메일"):
        load_config(write(tmp_path, 'user_agent: "Tester"\nwatchlist: [AAPL]\n'))


def test_missing_user_agent_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="User-Agent"):
        load_config(write(tmp_path, "watchlist: [AAPL]\n"))


def test_empty_watchlist_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="watchlist"):
        load_config(write(tmp_path, 'user_agent: "Tester t@example.com"\nwatchlist: []\n'))


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
