from datetime import date, timedelta

from stock_analysis.earnings import Earnings, due_reminders, estimate_next


def quarterly_history(start: date, count: int, gap: int = 91) -> list[date]:
    return [start + timedelta(days=gap * i) for i in range(count)]


def test_estimates_next_quarter_from_history():
    history = quarterly_history(date(2025, 1, 29), 6)   # 91일 간격 6회
    nxt = estimate_next(history, today=history[-1] + timedelta(days=10))
    assert nxt == history[-1] + timedelta(days=91)


def test_needs_enough_history():
    assert estimate_next([date(2026, 1, 5)], today=date(2026, 2, 1)) is None
    assert estimate_next([], today=date(2026, 2, 1)) is None


def test_ignores_out_of_range_gaps():
    # 8-K 2.02 가 실적 외 사유로도 올라올 수 있어 분기 간격만 인정한다
    history = [date(2025, 1, 29), date(2025, 2, 3), date(2025, 4, 30), date(2025, 7, 30), date(2025, 10, 29)]
    nxt = estimate_next(history, today=date(2025, 11, 5))
    assert nxt is not None
    assert 60 <= (nxt - history[-1]).days <= 120


def test_rolls_forward_when_history_is_stale():
    history = quarterly_history(date(2024, 1, 31), 5)
    nxt = estimate_next(history, today=date(2026, 8, 10))
    assert nxt >= date(2026, 8, 10)


def test_gives_up_when_history_is_far_too_old():
    history = quarterly_history(date(2015, 1, 30), 4)
    assert estimate_next(history, today=date(2026, 8, 10)) is None


def test_estimate_lands_on_a_weekday():
    history = quarterly_history(date(2025, 1, 4), 5, gap=91)   # 토요일 기준
    nxt = estimate_next(history, today=date(2026, 1, 1))
    assert nxt is None or nxt.weekday() < 5


def test_due_reminders_matches_configured_offsets():
    earnings = Earnings(ticker="TSLA", day=date(2026, 10, 22), estimated=False, history=[])
    assert due_reminders(earnings, date(2026, 10, 15), [7, 1, 0]) == 7
    assert due_reminders(earnings, date(2026, 10, 21), [7, 1, 0]) == 1
    assert due_reminders(earnings, date(2026, 10, 22), [7, 1, 0]) == 0
    assert due_reminders(earnings, date(2026, 10, 20), [7, 1, 0]) is None
    assert due_reminders(earnings, date(2026, 10, 23), [7, 1, 0]) is None


def test_to_event_marks_estimate_and_priority():
    earnings = Earnings(ticker="TSLA", day=date(2026, 10, 22), estimated=True, history=[])
    event = earnings.to_event("테슬라")
    assert "TSLA 실적 발표" in event.name
    assert "테슬라" in event.name
    assert event.importance == 3
    assert event.estimated is True
    assert "실적" in event.tags
