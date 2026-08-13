"""경제 지표 — 날짜 말고 숫자.

여기서 지켜야 할 것.
  · CPI 같은 지수는 '전년 같은 달 대비 %' 로만 읽는다 (지수 자체는 의미가 없다)
  · 금리·실업률처럼 이미 % 인 값은 손대지 않는다
  · 기준 시점을 반드시 같이 들고 다닌다 (7월분을 8월에 보는 일이 흔하다)
  · 한 지표를 못 받아도 나머지는 나온다. 없는 값을 지어내지 않는다
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from stock_analysis.macro import (
    BY_ID,
    SERIES,
    MacroClient,
    Reading,
    parse_csv,
    to_reading,
)


def monthly_csv(start: date, values: list[float], name: str = "CPIAUCSL") -> str:
    """FRED 월간 CSV 한 장. 관측치는 매월 1일자로 들어온다."""
    lines = [f"observation_date,{name}"]
    year, month = start.year, start.month
    for value in values:
        lines.append(f"{date(year, month, 1).isoformat()},{value}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return "\n".join(lines) + "\n"


def daily_csv(end: date, values: list[float], name: str = "DGS10") -> str:
    lines = [f"observation_date,{name}"]
    start = end - timedelta(days=len(values) - 1)
    for offset, value in enumerate(values):
        lines.append(f"{(start + timedelta(days=offset)).isoformat()},{value}")
    return "\n".join(lines) + "\n"


class FakeHttp:
    def __init__(self, mapping=None, fail=()):
        self.mapping = mapping or {}
        self.fail = set(fail)
        self.calls = []

    def get_text(self, url, **kwargs):
        self.calls.append(url)
        for key in self.fail:
            if key in url:
                raise RuntimeError("막힘")
        for key, payload in self.mapping.items():
            if f"id={key}" in url:
                return payload
        raise RuntimeError("모르는 주소")


# --- 응답 읽기 --------------------------------------------------------------
def test_header_and_missing_values_are_skipped():
    text = "observation_date,DGS10\n2026-08-10,4.21\n2026-08-11,.\n2026-08-12,4.28\n"
    assert parse_csv(text) == [(date(2026, 8, 10), 4.21), (date(2026, 8, 12), 4.28)]


def test_the_old_header_name_still_works():
    """FRED 가 DATE → observation_date 로 머리글을 바꾼 적이 있다."""
    assert parse_csv("DATE,UNRATE\n2026-07-01,4.2\n") == [(date(2026, 7, 1), 4.2)]


def test_garbage_does_not_raise():
    assert parse_csv("<html>error</html>") == []
    assert parse_csv("") == []


# --- 숫자로 바꾸기 ----------------------------------------------------------
def test_price_index_is_read_as_a_year_over_year_percent():
    """CPI 지수 310.0 은 아무 뜻이 없다. '1년 전보다 3.3%' 여야 뜻이 생긴다."""
    values = [300.0] * 12 + [309.0]          # 13개월: 마지막이 정확히 +3%
    points = parse_csv(monthly_csv(date(2025, 7, 1), values))
    reading = to_reading(BY_ID["CPIAUCSL"], points)

    assert reading.value == pytest.approx(3.0)
    assert reading.text == "3.0%"
    assert reading.as_of == date(2026, 7, 1)


def test_the_reference_month_is_kept_not_the_release_date():
    points = parse_csv(monthly_csv(date(2025, 7, 1), [300.0] * 12 + [309.0]))
    reading = to_reading(BY_ID["CPIAUCSL"], points)
    assert reading.when == "2026년 7월분"


def test_previous_reading_is_the_month_before_not_the_index():
    """비교 대상도 전년비여야 한다. 지수끼리 빼면 물가가 급락한 것처럼 보인다."""
    values = [100.0] * 12 + [104.0, 103.0]   # 전년비 4% → 3%
    points = parse_csv(monthly_csv(date(2025, 6, 1), values))
    reading = to_reading(BY_ID["CPIAUCSL"], points)

    assert reading.value == pytest.approx(3.0)
    assert reading.previous == pytest.approx(4.0)
    assert reading.change_text == "-1.0%p"
    assert reading.direction == "down"


def test_percent_series_are_shown_as_they_are():
    points = parse_csv(daily_csv(date(2026, 8, 12), [4.21, 4.28]))
    reading = to_reading(BY_ID["DGS10"], points)

    assert reading.text == "4.28%"
    assert reading.previous == pytest.approx(4.21)
    assert reading.when == "8월 12일 기준"


def test_payroll_is_shown_as_the_monthly_gain():
    """비농업 고용은 총 인원(1억 6천만 명)이 아니라 '이번 달에 몇 명 늘었나'."""
    points = parse_csv(monthly_csv(date(2026, 6, 1), [159000.0, 159147.0], "PAYEMS"))
    reading = to_reading(BY_ID["PAYEMS"], points)

    assert reading.value == pytest.approx(14.7)      # 147천 명 → 14.7만 명
    assert reading.text == "+14.7만 명"


def test_a_single_data_point_is_not_enough():
    points = parse_csv(monthly_csv(date(2026, 7, 1), [300.0]))
    assert to_reading(BY_ID["CPIAUCSL"], points) is None
    assert to_reading(BY_ID["CPIAUCSL"], []) is None


# --- 읽는 법 ---------------------------------------------------------------
@pytest.mark.parametrize(
    "series_id,value,expected",
    [
        ("CPIAUCSL", 1.9, "연준 목표 2% 아래"),
        ("CPIAUCSL", 3.4, "목표보다 높음"),
        ("CPIAUCSL", 6.0, "높은 물가"),
        ("T10Y2Y", -0.35, "금리 역전 — 침체 경고 신호"),
        ("T10Y2Y", 1.2, "정상"),
        ("PAYEMS", -30.0, "일자리가 줄었다"),
    ],
)
def test_levels_come_with_a_plain_reading(series_id, value, expected):
    reading = Reading(BY_ID[series_id], value, None, date(2026, 7, 1))
    assert reading.note == expected


def test_falling_inflation_reads_as_easier_for_stocks():
    easing = Reading(BY_ID["CPIAUCSL"], 3.0, 4.0, date(2026, 7, 1))
    heating = Reading(BY_ID["CPIAUCSL"], 4.0, 3.0, date(2026, 7, 1))
    assert easing.tone == "good" and heating.tone == "bad"


def test_an_inverted_curve_getting_deeper_reads_as_worse():
    reading = Reading(BY_ID["T10Y2Y"], -0.40, -0.10, date(2026, 8, 12))
    assert reading.tone == "bad"


def test_two_sided_indicators_are_left_uncoloured():
    """실업률·고용은 오르는 게 좋은지 나쁜지 한마디로 못 한다. 색을 칠하지 않는다."""
    for series_id in ("UNRATE", "PAYEMS"):
        assert Reading(BY_ID[series_id], 4.3, 4.1, date(2026, 7, 1)).tone == ""


def test_an_unchanged_value_has_no_direction():
    reading = Reading(BY_ID["DGS10"], 4.28, 4.28, date(2026, 8, 12))
    assert reading.direction == "" and reading.tone == ""


def test_every_series_explains_itself():
    """숫자만 있는 지표는 초보자에게 아무 도움이 안 된다."""
    for spec in SERIES:
        assert spec.meaning and spec.rule in {"yoy", "level", "change"}


# --- 받아오기 --------------------------------------------------------------
def test_a_snapshot_holds_every_series_it_could_get(tmp_path):
    http = FakeHttp({
        "CPIAUCSL": monthly_csv(date(2025, 7, 1), [300.0] * 12 + [309.0]),
        "DGS10": daily_csv(date(2026, 8, 12), [4.21, 4.28]),
    })
    snapshot = MacroClient(http, tmp_path).refresh(force=True)

    assert snapshot.get("CPIAUCSL").text == "3.0%"
    assert snapshot.get("DGS10").text == "4.28%"
    assert snapshot.get("UNRATE") is None       # 못 받은 것은 그냥 없다


def test_one_blocked_series_does_not_take_down_the_rest(tmp_path):
    http = FakeHttp({"DGS10": daily_csv(date(2026, 8, 12), [4.21, 4.28])},
                    fail=["CPIAUCSL"])
    snapshot = MacroClient(http, tmp_path).refresh(force=True)
    assert snapshot.get("DGS10") is not None


def test_a_dead_network_does_not_crash_the_page(tmp_path):
    client = MacroClient(FakeHttp(fail=["fred"]), tmp_path)
    assert client.refresh(force=True) is None
    assert client.cached() is None


def test_previous_values_survive_a_failed_refresh(tmp_path):
    http = FakeHttp({"DGS10": daily_csv(date(2026, 8, 12), [4.21, 4.28])})
    client = MacroClient(http, tmp_path)
    client.refresh(force=True)

    http.fail.add("fred")
    client.refresh(force=True)
    assert client.cached().get("DGS10").text == "4.28%"


def test_cache_is_reused_until_it_goes_stale(tmp_path):
    http = FakeHttp({"DGS10": daily_csv(date(2026, 8, 12), [4.21, 4.28])})
    client = MacroClient(http, tmp_path, ttl=3600)
    client.refresh(force=True)
    calls = len(http.calls)
    client.refresh()
    assert len(http.calls) == calls


def test_values_survive_a_restart(tmp_path):
    """물가는 한 달에 한 번 바뀐다. 껐다 켰다고 화면이 비면 안 된다."""
    http = FakeHttp({"CPIAUCSL": monthly_csv(date(2025, 7, 1), [300.0] * 12 + [309.0])})
    MacroClient(http, tmp_path).refresh(force=True)

    fresh = MacroClient(FakeHttp(fail=["fred"]), tmp_path)
    reading = fresh.cached().get("CPIAUCSL")
    assert reading.text == "3.0%" and reading.as_of == date(2026, 7, 1)


def test_a_broken_cache_file_is_ignored(tmp_path):
    (tmp_path / "macro.json").write_text("{ not json", encoding="utf-8")
    assert MacroClient(FakeHttp(fail=["fred"]), tmp_path).cached() is None


def test_a_series_dropped_from_the_list_is_forgotten(tmp_path):
    (tmp_path / "macro.json").write_text(
        json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "readings": [{"id": "GONE", "value": 1.0, "previous": None,
                          "as_of": "2026-07-01"}],
        }),
        encoding="utf-8",
    )
    assert MacroClient(FakeHttp(fail=["fred"]), tmp_path).cached() is None


def test_only_a_few_years_are_requested(tmp_path):
    """CPI 전체 이력은 1947년부터다. 매번 다 받을 이유가 없다."""
    http = FakeHttp({"DGS10": daily_csv(date(2026, 8, 12), [4.21, 4.28])})
    MacroClient(http, tmp_path).refresh(force=True)
    assert any("cosd=" in url for url in http.calls)
