from datetime import date

from factories import build_facts

from stock_analysis.econ_calendar import EconEvent
from stock_analysis.market_calendar import MarketDay
from stock_analysis.messages import format_daily_brief, format_metrics
from stock_analysis.metrics import build_metrics
from stock_analysis.state import State
from stock_analysis.telegram import MAX_LEN, esc, split_message, strip_tags


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


def test_brief_shows_what_the_indicators_are_right_now():
    """일정만 있으면 '언제 나오나' 만 알고 '지금 어디쯤인가' 를 모른다."""
    from datetime import datetime, timezone

    from stock_analysis.macro import BY_ID, MacroSnapshot, Reading

    macro = MacroSnapshot(
        readings=[
            Reading(BY_ID["CPIAUCSL"], 2.9, 3.1, date(2026, 7, 1)),
            Reading(BY_ID["DGS10"], 4.28, 4.21, date(2026, 8, 12)),
            Reading(BY_ID["T10Y2Y"], -0.18, -0.05, date(2026, 8, 12)),
            Reading(BY_ID["UNRATE"], 4.3, 4.1, date(2026, 7, 1)),
        ],
        fetched_at=datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc),
    )
    text = format_daily_brief(
        date(2026, 8, 13), [], [], [], "Asia/Seoul", None, macro
    )

    assert "지금 경제 지표" in text
    assert "소비자물가 CPI <b>2.9%</b> (-0.2%p)" in text
    assert "• 금리:" in text and "• 고용:" in text
    assert "금리 역전 — 침체 경고 신호" in text     # 눈에 띄는 것만 한 줄 더


def test_brief_says_nothing_when_there_are_no_numbers():
    text = format_daily_brief(date(2026, 8, 13), [], [], [], "Asia/Seoul")
    assert "지금 경제 지표" not in text


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


# --- 이모지가 알림을 죽이지 않게 -------------------------------------------
def cp949_stream():
    """윈도우에서 출력이 파일로 갈 때의 상황. 한글은 되고 이모지는 안 된다."""
    import io

    return io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="strict", newline="")


def test_an_emoji_does_not_kill_the_alert(monkeypatch):
    """실제로 났던 오류.

    창 없이 돌면 표준 출력은 로그 파일이고, 윈도우에서 그 인코딩은 cp949 다.
    거기에 '🚨' 를 쓰면 UnicodeEncodeError 가 나면서 속보 확인이 통째로 실패했다.
    ('cp949' codec can't encode character '\U0001f6a8')
    """
    from stock_analysis.telegram import TelegramNotifier

    monkeypatch.setattr("sys.stdout", cp949_stream())
    assert TelegramNotifier("", "", dry_run=True).send("🚨 <b>속보</b> 거래 정지")


def test_korean_still_comes_out_readable(monkeypatch):
    """이모지를 살리자고 한글까지 뭉개면 안 된다."""
    from stock_analysis.telegram import show

    stream = cp949_stream()
    monkeypatch.setattr("sys.stdout", stream)
    show("한글은 그대로 🚨")

    stream.flush()
    written = stream.buffer.getvalue().decode("cp949")
    assert "한글은 그대로" in written


def test_output_streams_are_pinned_to_utf8(monkeypatch):
    """근본 대책: 출력 경로 자체를 UTF-8 로 고정한다."""
    import sys

    from main import use_utf8_output

    monkeypatch.setattr("sys.stdout", cp949_stream())
    use_utf8_output()

    print("🚨 이모지도 그대로")          # 예외가 나면 테스트 실패
    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
