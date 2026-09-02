from datetime import date

from stock_analysis.market_calendar import (
    easter,
    is_trading_day,
    nyse_early_closes,
    nyse_holidays,
    upcoming_market_days,
)


def test_easter_known_years():
    assert easter(2024) == date(2024, 3, 31)
    assert easter(2025) == date(2025, 4, 20)
    assert easter(2026) == date(2026, 4, 5)


def test_good_friday_is_a_holiday():
    days = {d.day for d in nyse_holidays(2026)}
    assert date(2026, 4, 3) in days  # 부활절(4/5) 이틀 전


def test_2025_holiday_set():
    days = {d.day for d in nyse_holidays(2025)}
    expected = {
        date(2025, 1, 1),    # 신정
        date(2025, 1, 20),   # MLK
        date(2025, 2, 17),   # 대통령의 날
        date(2025, 4, 18),   # 성금요일
        date(2025, 5, 26),   # 메모리얼 데이
        date(2025, 6, 19),   # 준틴스
        date(2025, 7, 4),    # 독립기념일
        date(2025, 9, 1),    # 노동절
        date(2025, 11, 27),  # 추수감사절
        date(2025, 12, 25),  # 크리스마스
    }
    assert days == expected


def test_2026_holiday_set():
    days = {d.day for d in nyse_holidays(2026)}
    expected = {
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),    # 7/4 토요일 → 금요일 대체휴장
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    }
    assert days == expected


def test_new_year_on_saturday_is_not_observed():
    # 2022-01-01은 토요일이라 NYSE는 대체휴장을 하지 않았다
    assert date(2022, 1, 1) not in {d.day for d in nyse_holidays(2022)}
    assert date(2021, 12, 31) not in {d.day for d in nyse_holidays(2021)}


def test_juneteenth_only_from_2022():
    assert date(2021, 6, 18) not in {d.day for d in nyse_holidays(2021)}
    assert date(2022, 6, 20) in {d.day for d in nyse_holidays(2022)}  # 6/19 일요일 → 월요일


def test_early_closes_2025():
    early = {d.day: d for d in nyse_early_closes(2025)}
    assert date(2025, 7, 3) in early
    assert date(2025, 11, 28) in early   # 추수감사절 다음날
    assert date(2025, 12, 24) in early
    assert all(d.early_close for d in early.values())


def test_july3_not_early_close_when_it_is_the_holiday():
    # 2026년 7/4는 토요일 → 7/3이 휴장일이므로 조기폐장 목록에 있으면 안 된다
    assert date(2026, 7, 3) not in {d.day for d in nyse_early_closes(2026)}


def test_is_trading_day():
    assert not is_trading_day(date(2025, 12, 25))   # 크리스마스
    assert not is_trading_day(date(2025, 12, 27))   # 토요일
    assert is_trading_day(date(2025, 12, 26))       # 평일


def test_upcoming_market_days_window():
    days = upcoming_market_days(date(2025, 12, 20), 10)
    assert [d.day for d in days] == [date(2025, 12, 24), date(2025, 12, 25)]
    assert days[0].early_close and not days[1].early_close
