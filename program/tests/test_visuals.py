"""종목을 눈으로 알아보게 하는 조각들 — 배지 · 스파크라인 · 등락 색.

표에 글자만 빽빽하면 열네 칸을 하나씩 읽어야 한다. 여기 있는 것들이
지키는 약속은 하나다 — **없는 값으로는 아무것도 그리지 않는다.**
없는 구간을 이어 붙여 매끈한 선을 만들면 그림이 거짓말을 한다.
"""

from stock_analysis import visuals
from stock_analysis.metrics import _thin


# --- 배지 ---------------------------------------------------------------------
def test_the_same_ticker_always_gets_the_same_colour():
    """색이 매번 바뀌면 목록에서 색으로 찾을 수가 없다."""
    assert visuals.badge_color("AAPL") == visuals.badge_color("AAPL")
    assert visuals.badge_color("aapl") == visuals.badge_color("AAPL")


def test_different_tickers_spread_across_the_palette():
    seen = {visuals.badge_color(t) for t in
            ("AAPL", "NVDA", "MU", "TSLA", "MSFT", "AMZN", "GOOG", "META")}
    assert len(seen) >= 4          # 여덟 개가 한 색으로 몰리면 소용이 없다


def test_a_korean_name_puts_its_first_letter_on_the_badge():
    """'삼' 이 '0' 보다 알아보기 쉽다."""
    assert visuals.badge_letter("005930", "삼성전자") == "삼"


def test_an_american_ticker_uses_its_first_letter():
    assert visuals.badge_letter("AAPL", "Apple Inc.") == "A"
    assert visuals.badge_letter("aapl") == "A"


def test_an_empty_ticker_does_not_crash():
    assert visuals.badge_letter("", "") == "?"
    assert visuals.badge_color("") in visuals.BADGE_COLORS


def test_the_badge_escapes_what_it_shows():
    assert "<script>" not in visuals.badge("X", "<script>alert(1)</script>")


def test_without_a_logo_it_is_just_a_letter():
    html = visuals.badge("AAPL", "Apple Inc.")
    assert "img" not in html
    assert ">A<" in html


def test_a_logo_keeps_the_letter_underneath():
    """그림을 못 불러와도 빈 동그라미만 남으면 안 된다."""
    html = visuals.badge("AAPL", "Apple", logo="https://example.com/a.png")
    assert "<img" in html and "onerror" in html
    assert ">A<" in html               # 실패하면 드러날 글자 배지


# --- 등락 색 ------------------------------------------------------------------
def test_up_is_green_and_down_is_red():
    assert 'class="up"' in visuals.move(1.8) and "+1.80%" in visuals.move(1.8)
    assert 'class="down"' in visuals.move(-2.4) and "-2.40%" in visuals.move(-2.4)


def test_no_change_is_neither_colour():
    assert 'class="flat"' in visuals.move(0.0)


def test_a_missing_change_is_not_drawn_as_zero():
    """값이 없는 것과 0% 는 다르다. 같은 그림으로 적으면 안 된다."""
    assert "0" not in visuals.move(None)
    assert "muted" in visuals.move(None)


# --- 스파크라인 ----------------------------------------------------------------
def test_too_few_points_draw_nothing():
    """점 두 개를 이은 직선은 흐름이 아니다."""
    assert visuals.spark([]) == ""
    assert visuals.spark([1.0, 2.0]) == ""


def test_a_rising_line_is_green():
    html = visuals.spark([1, 2, 3, 4, 5])
    assert "sp-up" in html and "sp-down" not in html
    assert "<polyline" in html


def test_a_falling_line_is_red():
    assert "sp-down" in visuals.spark([5, 4, 3, 2, 1])


def test_a_flat_line_stays_inside_the_box():
    """고저가 같으면 0 으로 나누게 된다. 터지지 않아야 한다."""
    html = visuals.spark([3, 3, 3, 3, 3])
    assert html
    ys = [float(pair.split(",")[1]) for pair in
          html.split('class="sp-line" points="')[1].split('"')[0].split()]
    assert all(0 <= y <= visuals.SPARK_H for y in ys)


def test_missing_points_are_dropped_not_guessed():
    """빈 구간을 이어 붙이면 없는 흐름을 그린 것이 된다."""
    assert visuals.spark([1, None, 3, None, 5]) == ""      # 남은 점이 셋뿐


def test_the_line_stays_inside_the_box():
    html = visuals.spark([10, 200, 5, 60, 30])
    coords = html.split('class="sp-line" points="')[1].split('"')[0].split()
    xs = [float(c.split(",")[0]) for c in coords]
    ys = [float(c.split(",")[1]) for c in coords]
    assert min(xs) >= 0 and max(xs) <= visuals.SPARK_W
    assert min(ys) >= 0 and max(ys) <= visuals.SPARK_H


# --- 점 솎기 -------------------------------------------------------------------
def test_thinning_keeps_both_ends():
    """끝값을 잃으면 선의 방향이 바뀐다 — 그러면 그림이 거짓말을 한다."""
    values = [float(n) for n in range(500)]
    picked = _thin(values, 60)

    assert len(picked) <= 60
    assert picked[0] == values[0]
    assert picked[-1] == values[-1]


def test_a_short_series_is_left_alone():
    values = [1.0, 2.0, 3.0]
    assert _thin(values, 60) == values


def test_a_thinned_series_keeps_its_direction():
    rising = [float(n) for n in range(500)]
    falling = list(reversed(rising))

    assert _thin(rising, 60)[-1] > _thin(rising, 60)[0]
    assert _thin(falling, 60)[-1] < _thin(falling, 60)[0]
