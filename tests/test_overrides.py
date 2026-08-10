from datetime import date

from stockbot.config import Watch
from stockbot.overrides import Overrides


def base_watchlist():
    return [
        Watch(ticker="AAPL", name="애플"),
        Watch(ticker="NVDA", peers=["AMD"]),
    ]


def test_add_appends_new_ticker(tmp_path):
    ov = Overrides(tmp_path / "w.yml")
    ov.add("TSLA", name="테슬라")
    merged = ov.apply(base_watchlist())
    assert [w.ticker for w in merged] == ["AAPL", "NVDA", "TSLA"]
    assert merged[-1].name == "테슬라"
    assert merged[-1].source == "telegram"


def test_add_is_idempotent(tmp_path):
    ov = Overrides(tmp_path / "w.yml")
    ov.add("TSLA")
    ov.add("TSLA", name="테슬라")
    merged = ov.apply(base_watchlist())
    assert [w.ticker for w in merged].count("TSLA") == 1
    assert merged[-1].name == "테슬라"


def test_add_does_not_duplicate_config_entry(tmp_path):
    ov = Overrides(tmp_path / "w.yml")
    ov.add("AAPL")
    merged = ov.apply(base_watchlist())
    assert [w.ticker for w in merged] == ["AAPL", "NVDA"]


def test_remove_hides_config_entry(tmp_path):
    ov = Overrides(tmp_path / "w.yml")
    ov.remove("AAPL")
    merged = ov.apply(base_watchlist())
    assert [w.ticker for w in merged] == ["NVDA"]


def test_remove_then_add_restores(tmp_path):
    ov = Overrides(tmp_path / "w.yml")
    ov.remove("AAPL")
    ov.add("AAPL")
    merged = ov.apply(base_watchlist())
    assert "AAPL" in [w.ticker for w in merged]


def test_remove_drops_bot_added_entry_entirely(tmp_path):
    ov = Overrides(tmp_path / "w.yml")
    ov.add("TSLA")
    ov.remove("TSLA")
    assert ov.data["added"] == []
    assert ov.data["removed"] == []       # config 에 없던 종목이라 끌 필요가 없다
    assert [w.ticker for w in ov.apply(base_watchlist())] == ["AAPL", "NVDA"]


def test_set_field_on_config_entry(tmp_path):
    ov = Overrides(tmp_path / "w.yml")
    ov.set_field("NVDA", "consensus_eps", 1.01)
    ov.set_field("NVDA", "earnings_date", date(2026, 11, 18))
    ov.set_field("NVDA", "peers", ["AMD", "AVGO"])
    nvda = [w for w in ov.apply(base_watchlist()) if w.ticker == "NVDA"][0]
    assert nvda.consensus_eps == 1.01
    assert nvda.earnings_date == date(2026, 11, 18)
    assert nvda.peers == ["AMD", "AVGO"]


def test_set_field_on_bot_added_entry(tmp_path):
    ov = Overrides(tmp_path / "w.yml")
    ov.add("TSLA")
    ov.set_field("TSLA", "milestones", ["로보택시 상용화"])
    tsla = [w for w in ov.apply(base_watchlist()) if w.ticker == "TSLA"][0]
    assert tsla.milestones == ["로보택시 상용화"]
    assert ov.data["fields"] == {}      # 추가 항목에 직접 기록된다


def test_persists_across_reload(tmp_path):
    path = tmp_path / "w.yml"
    ov = Overrides(path)
    ov.add("TSLA", name="테슬라")
    ov.set_field("AAPL", "consensus_eps", 2.5)
    ov.remove("NVDA")
    ov.save()

    reloaded = Overrides(path)
    merged = reloaded.apply(base_watchlist())
    tickers = [w.ticker for w in merged]
    assert tickers == ["AAPL", "TSLA"]
    assert merged[0].consensus_eps == 2.5
    assert "자동으로 씁니다" in path.read_text(encoding="utf-8")


def test_broken_file_is_ignored(tmp_path):
    path = tmp_path / "w.yml"
    path.write_text("added: [ unclosed", encoding="utf-8")
    ov = Overrides(path)
    assert ov.apply(base_watchlist()) == base_watchlist()


def test_unknown_field_is_rejected(tmp_path):
    ov = Overrides(tmp_path / "w.yml")
    try:
        ov.set_field("AAPL", "ticker", "HACK")
    except ValueError as exc:
        assert "수정할 수 없는" in str(exc)
    else:
        raise AssertionError("허용되지 않은 항목이 통과했습니다")
