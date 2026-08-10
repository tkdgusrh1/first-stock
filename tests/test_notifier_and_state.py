from datetime import date

from factories import build_facts

from stockbot.econ_calendar import EconEvent
from stockbot.market_calendar import MarketDay
from stockbot.messages import format_daily_brief, format_metrics
from stockbot.metrics import build_metrics
from stockbot.state import State
from stockbot.telegram import MAX_LEN, esc, split_message, strip_tags


def test_split_message_respects_telegram_limit():
    text = "\n".join(f"line {i} " + "x" * 100 for i in range(200))
    chunks = split_message(text)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_LEN for c in chunks)
    assert strip_tags("".join(chunks)).replace("\n", "") == text.replace("\n", "")


def test_split_message_handles_single_long_line():
    chunks = split_message("y" * (MAX_LEN * 2 + 10))
    assert all(len(c) <= MAX_LEN for c in chunks)
    assert "".join(chunks) == "y" * (MAX_LEN * 2 + 10)


def test_short_message_is_not_split():
    assert split_message("안녕하세요") == ["안녕하세요"]


def test_html_escaping():
    assert esc("R&D <b>") == "R&amp;D &lt;b&gt;"
    assert strip_tags("<b>굵게</b> &amp; 기타") == "굵게 & 기타"


def test_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = State(path)
    assert not state.is_seen("0000320193", "acc-1")
    state.mark_seen("0000320193", "acc-1")
    state.mark_bootstrapped("0000320193")
    state.set_last_brief_date("2026-08-10")
    state.save()

    reloaded = State(path)
    assert reloaded.is_seen("0000320193", "acc-1")
    assert reloaded.is_bootstrapped("0000320193")
    assert reloaded.last_brief_date() == "2026-08-10"
    assert not reloaded.is_seen("0000320193", "acc-2")


def test_state_survives_corrupted_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ this is not json", encoding="utf-8")
    state = State(path)
    assert not state.is_seen("x", "y")
    state.mark_seen("x", "y")
    state.save()
    assert State(path).is_seen("x", "y")


def test_state_caps_history(tmp_path):
    state = State(tmp_path / "state.json")
    for i in range(500):
        state.mark_seen("cik", f"acc-{i}")
    stored = state.data["seen"]["cik"]
    assert len(stored) == 400
    assert stored[-1] == "acc-499"
    assert not state.is_seen("cik", "acc-0")


def test_daily_brief_contains_all_sections():
    metrics = build_metrics(
        "TEST",
        build_facts(
            revenue=[100, 110, 120, 130, 140, 150, 160, 170],
            net_income=[10, 11, 12, 13, 15, 16, 17, 18],
            operating_income=[12, 13, 14, 15, 18, 20, 22, 24],
            equity=300.0,
        ),
    )
    text = format_daily_brief(
        date(2026, 8, 10),
        [
            MarketDay(date(2026, 9, 7), "노동절 (Labor Day)"),
            MarketDay(date(2026, 11, 27), "추수감사절 다음 날", early_close=True),
        ],
        [EconEvent(date(2026, 8, 12), "소비자물가 CPI", "08:30", 3, estimated=True)],
        [metrics],
        "Asia/Seoul",
    )
    assert "데일리 브리핑" in text
    assert "2026-08-10(월)" in text
    assert "노동절" in text and "D-28" in text
    assert "조기폐장(13:00 ET)" in text
    assert "소비자물가 CPI" in text and "추정일" in text
    assert "관심 종목 스냅샷" in text
    assert "ROE" in text


def test_brief_marks_today_as_holiday():
    text = format_daily_brief(
        date(2026, 9, 7),
        [MarketDay(date(2026, 9, 7), "노동절 (Labor Day)")],
        [],
        [],
        "Asia/Seoul",
    )
    assert "오늘 미국 증시: 휴장" in text


def test_metrics_report_has_priority_and_checklist():
    metrics = build_metrics(
        "LOSS",
        build_facts(
            revenue=[50, 55, 60, 65, 70, 78, 86, 96],
            net_income=[-30, -30, -30, -30, -25, -25, -25, -25],
            operating_income=[-32, -32, -32, -32, -27, -27, -27, -27],
            ocf=[-25, -25, -25, -25, -20, -20, -20, -20],
            cash=300.0,
            equity=400.0,
        ),
        milestones=["Neutron 첫 발사"],
    )
    text = format_metrics(metrics)
    assert "우선순위 판단" in text
    assert "1순위 · 가이던스" in text
    assert "적자 기업 체크리스트" in text
    assert "Neutron 첫 발사" in text
    assert "투자 판단의 책임" in text
