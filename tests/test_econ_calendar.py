from datetime import date

from stockbot.econ_calendar import (
    fomc_coverage_end,
    load_fomc,
    parse_extra_events,
    upcoming_events,
)


def test_fomc_dates_are_loaded_with_decision_day():
    events = {e.day: e for e in load_fomc()}
    # 회의 둘째 날(결정 발표일)만 이벤트가 된다
    assert date(2026, 1, 28) in events
    assert date(2026, 1, 27) not in events
    assert events[date(2026, 1, 28)].time_et == "14:00"
    assert events[date(2026, 1, 28)].importance == 3
    assert "점도표" in events[date(2026, 3, 18)].name


def test_fomc_minutes_three_weeks_after_meeting():
    events = {(e.day, e.name) for e in load_fomc()}
    assert (date(2026, 2, 18), "FOMC 의사록 공개") in events   # 1/28 회의 + 3주


def test_beige_book_lands_on_a_wednesday_before_the_meeting():
    beige = [e for e in load_fomc() if "베이지북" in e.name]
    assert beige
    for event in beige:
        assert event.day.weekday() == 2      # 수요일


def test_fomc_coverage_end_reports_last_meeting():
    assert fomc_coverage_end() == date(2026, 12, 9)


def test_monthly_estimates_include_key_indicators():
    events = upcoming_events(date(2026, 8, 1), days_ahead=31, min_importance=1)
    names = " | ".join(e.name for e in events)
    for keyword in ("고용보고서", "CPI", "PPI", "ISM 제조업", "PCE", "소매판매"):
        assert keyword in names


def test_nfp_is_first_friday():
    events = upcoming_events(date(2026, 8, 1), days_ahead=31, min_importance=1)
    nfp = [e for e in events if "고용보고서" in e.name][0]
    assert nfp.day == date(2026, 8, 7)   # 2026년 8월 첫째 금요일
    assert nfp.time_et == "08:30"
    assert nfp.estimated is True


def test_quad_witching_third_friday_of_quarter_end_month():
    events = upcoming_events(date(2026, 9, 1), days_ahead=30, min_importance=1)
    witching = [e for e in events if "위칭" in e.name]
    assert witching and witching[0].day == date(2026, 9, 18)
    # 분기월엔 월간 만기가 따로 잡히지 않는다
    assert not any("월간 옵션 만기" in e.name for e in events)


def test_monthly_option_expiry_in_non_quarter_months():
    events = upcoming_events(date(2026, 8, 1), days_ahead=31, min_importance=1)
    expiry = [e for e in events if "월간 옵션 만기" in e.name]
    assert expiry and expiry[0].day == date(2026, 8, 21)   # 8월 셋째 금요일


def test_new_indicators_are_present():
    events = upcoming_events(date(2026, 8, 1), days_ahead=31, min_importance=1)
    names = " | ".join(e.name for e in events)
    for keyword in ("ADP", "JOLTS", "미시간대", "잭슨홀"):
        assert keyword in names


def test_jackson_hole_is_late_august_thursday():
    events = upcoming_events(date(2026, 8, 1), days_ahead=31, min_importance=1)
    jackson = [e for e in events if "잭슨홀" in e.name][0]
    assert jackson.day.weekday() == 3 and jackson.day.day >= 22


def test_earnings_season_kickoff_only_in_quarter_start_months():
    august = upcoming_events(date(2026, 8, 1), 31, min_importance=1)
    october = upcoming_events(date(2026, 10, 1), 31, min_importance=1)
    assert not any("어닝시즌" in e.name for e in august)
    assert any("어닝시즌" in e.name for e in october)


def test_user_supplied_event_overrides_estimate():
    extra = parse_extra_events(
        [{"date": "2026-08-12", "name": "소비자물가 CPI", "time_et": "08:30", "importance": 3}]
    )
    events = upcoming_events(date(2026, 8, 1), days_ahead=31, extra=extra)
    cpi = [e for e in events if "CPI" in e.name]
    assert len(cpi) == 1
    assert cpi[0].day == date(2026, 8, 12)
    assert cpi[0].estimated is False


def test_override_matches_even_with_period_suffix():
    """'소비자물가 CPI (7월)' 처럼 괄호가 붙어도 같은 달 추정치를 대체해야 한다."""
    extra = parse_extra_events([{"date": "2026-08-19", "name": "소비자물가 CPI (7월)"}])
    events = upcoming_events(date(2026, 8, 1), days_ahead=31, extra=extra)
    cpi = [e for e in events if "CPI" in e.name]
    assert len(cpi) == 1
    assert cpi[0].day == date(2026, 8, 19)


def test_override_does_not_wipe_other_months():
    extra = parse_extra_events([{"date": "2026-08-12", "name": "소비자물가 CPI (7월)"}])
    events = upcoming_events(date(2026, 8, 1), days_ahead=60, extra=extra)
    cpi_days = sorted(e.day for e in events if "CPI" in e.name)
    assert cpi_days[0] == date(2026, 8, 12)
    assert len(cpi_days) == 2 and cpi_days[1].month == 9   # 9월 추정치는 그대로


def test_min_importance_filter():
    high_only = upcoming_events(date(2026, 8, 1), days_ahead=31, min_importance=3)
    assert all(e.importance == 3 for e in high_only)
    assert not any("ISM" in e.name for e in high_only)


def test_weekly_claims_are_opt_in():
    without = upcoming_events(date(2026, 8, 1), 14, min_importance=1, include_weekly=False)
    with_weekly = upcoming_events(date(2026, 8, 1), 14, min_importance=1, include_weekly=True)
    assert not any("실업수당" in e.name for e in without)
    claims = [e for e in with_weekly if "실업수당" in e.name]
    assert claims and all(e.day.weekday() == 3 for e in claims)


def test_events_are_sorted_and_within_window():
    today = date(2026, 8, 1)
    events = upcoming_events(today, days_ahead=10, min_importance=1)
    days = [e.day for e in events]
    assert days == sorted(days)
    assert all(today <= d <= date(2026, 8, 11) for d in days)
