"""종목을 눈으로 알아보게 하는 조각들 — 배지 · 스파크라인 · 등락 표시.

표에 글자만 빽빽하면 열네 칸을 하나씩 읽어야 한다. 사람이 목록에서 찾는
것은 대개 셋이다 — **어느 회사인가, 올랐나 내렸나, 얼마나 움직였나.**
그 셋을 글자보다 빨리 알아볼 수 있게 만드는 것이 여기 있는 것들이다.

지어내지 않는다는 규칙은 그림에도 똑같이 적용된다. 값이 없으면 선을
그리지 않고 빈칸을 둔다. 없는 구간을 이어 붙여 매끈한 선을 만들면
그 자체가 거짓말이 된다.
"""

from __future__ import annotations

import html
import re

# 배지 색. 티커에서 정해지므로 같은 종목은 늘 같은 색이다 — 목록에서
# 위치가 바뀌어도 색으로 찾을 수 있다. 밝은 바탕·어두운 바탕 모두에서
# 흰 글씨가 읽히도록 채도를 낮춘 여덟 가지만 쓴다.
BADGE_COLORS = (
    "#2563eb", "#7c3aed", "#db2777", "#dc2626",
    "#ea580c", "#0d9488", "#4f46e5", "#0891b2",
)

_HANGUL = re.compile(r"[가-힣]")


def badge_color(ticker: str) -> str:
    """티커 → 늘 같은 색. 글자 코드 합을 색 개수로 나눈 나머지."""
    key = str(ticker or "").upper()
    if not key:
        return BADGE_COLORS[0]
    return BADGE_COLORS[sum(ord(c) for c in key) % len(BADGE_COLORS)]


def badge_letter(ticker: str, name: str = "") -> str:
    """배지에 넣을 한 글자.

    한글 이름이 있으면 그 첫 글자를 쓴다. '삼' 이 '0' 보다 알아보기 쉽다.
    """
    text = str(name or "").strip()
    if text and _HANGUL.match(text[0]):
        return text[0]
    key = str(ticker or "").strip().upper()
    return key[0] if key else "?"


def badge(ticker: str, name: str = "", logo: str = "") -> str:
    """종목 앞에 붙는 동그란 표식.

    로고 주소를 주면 그림을 먼저 쓰고, 못 불러오면 글자 배지로 돌아간다.
    로고는 기본적으로 끄고 쓴다 — 그림을 받아오려면 바깥 서버에 '내가 이
    종목을 보고 있다' 고 알리는 셈이기 때문이다(config 의 show_logos).
    """
    color = badge_color(ticker)
    letter = html.escape(badge_letter(ticker, name))
    fallback = (
        f'<span class="tk-badge" style="background:{color}" aria-hidden="true">{letter}</span>'
    )
    if not logo:
        return fallback
    # 그림이 실패하면 옆에 숨겨둔 글자 배지를 대신 보여준다.
    return (
        f'<span class="tk-logo">'
        f'<img src="{html.escape(logo, quote=True)}" alt="" loading="lazy"'
        f' onerror="this.remove()">{fallback}</span>'
    )


def move(pct: float | None, digits: int = 2) -> str:
    """등락률. 오르면 초록, 내리면 빨강, 0 이면 회색."""
    if pct is None:
        return '<span class="muted">-</span>'
    if pct > 0:
        cls, sign = "up", "+"
    elif pct < 0:
        cls, sign = "down", ""      # 음수는 부호가 이미 붙어 있다
    else:
        cls, sign = "flat", ""
    return f'<span class="{cls}">{sign}{pct:.{digits}f}%</span>'


# --------------------------------------------------------------------------
# 스파크라인
# --------------------------------------------------------------------------
SPARK_W = 88
SPARK_H = 28
SPARK_MIN = 4           # 점이 이보다 적으면 선이라고 할 수 없다


def spark(values, width: int = SPARK_W, height: int = SPARK_H) -> str:
    """최근 흐름을 작은 선 하나로. 끝이 처음보다 높으면 초록, 낮으면 빨강.

    **눈금도 숫자도 없다.** 이건 값을 읽는 그림이 아니라 방향을 보는
    그림이다. 정확한 값은 옆의 숫자가 말한다.
    """
    points = [float(v) for v in (values or []) if v is not None]
    if len(points) < SPARK_MIN:
        return ""

    low, high = min(points), max(points)
    span = high - low
    step = width / (len(points) - 1)
    pad = 2                                   # 선 굵기가 잘리지 않게
    usable = height - pad * 2

    coords = []
    for i, value in enumerate(points):
        x = i * step
        # 평평하면(고저가 같으면) 한가운데 가로선
        y = pad + usable / 2 if span == 0 else pad + usable * (1 - (value - low) / span)
        coords.append(f"{x:.1f},{y:.1f}")

    rising = points[-1] >= points[0]
    cls = "sp-up" if rising else "sp-down"
    # 마우스를 올리면 이게 무엇인지 말해준다. 눈금 없는 선은 기간을 안 적으면
    # 하루치로도 읽힌다.
    moved = (points[-1] - points[0]) / points[0] * 100 if points[0] else None
    tip = "최근 3개월 흐름" + (f" · {moved:+.1f}%" if moved is not None else "")
    line = " ".join(coords)
    # 선 아래를 옅게 채우면 방향이 더 빨리 읽힌다
    area = f"0,{height} {line} {width},{height}"
    return (
        f'<svg class="spark {cls}" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" preserveAspectRatio="none" role="img">'
        f"<title>{html.escape(tip)}</title>"
        f'<polygon class="sp-fill" points="{area}"/>'
        f'<polyline class="sp-line" points="{line}"/></svg>'
    )


# --------------------------------------------------------------------------
# 캔들
# --------------------------------------------------------------------------
CANDLE_H = 190
CANDLE_MIN = 5          # 봉이 이보다 적으면 차트라고 할 수 없다
CANDLE_MAX = 90         # 한 화면에 이보다 많으면 봉이 실오라기가 된다


def candles(bars, height: int = CANDLE_H, width: int = 720) -> str:
    """일봉 차트. 오르면 초록, 내리면 빨강.

    몸통은 시가~종가, 위아래 선은 고가~저가다. 세로 눈금을 함께 그린다 —
    **캔들은 값을 읽는 그림**이라 눈금이 없으면 읽을 수가 없다(방향만 보는
    스파크라인과 다르다).

    넷 중 하나라도 없는 날은 애초에 봉을 만들지 않는다(prices.candles).
    빠진 날을 앞뒤로 메우면 없던 몸통을 그리는 셈이 된다.
    """
    bars = list(bars or [])[-CANDLE_MAX:]
    if len(bars) < CANDLE_MIN:
        return ""

    high = max(b.high for b in bars)
    low = min(b.low for b in bars)
    span = high - low
    if span <= 0:
        return ""

    pad_top, pad_bottom = 8, 20                 # 아래는 날짜 자리
    axis = 62                                   # 오른쪽 눈금 자리
    plot_w = width - axis
    plot_h = height - pad_top - pad_bottom
    step = plot_w / len(bars)
    body_w = max(1.6, min(9.0, step * 0.62))

    def y_of(value: float) -> float:
        return pad_top + plot_h * (1 - (value - low) / span)

    parts = []
    for i, bar in enumerate(bars):
        x = i * step + step / 2
        cls = "c-up" if bar.rising else "c-down"
        top, bottom = y_of(bar.high), y_of(bar.low)
        o, c = y_of(bar.open), y_of(bar.close)
        y0, y1 = min(o, c), max(o, c)
        parts.append(
            f'<line class="{cls} c-wick" x1="{x:.1f}" y1="{top:.1f}" '
            f'x2="{x:.1f}" y2="{bottom:.1f}"/>'
            f'<rect class="{cls} c-body" x="{x - body_w / 2:.1f}" y="{y0:.1f}" '
            f'width="{body_w:.1f}" height="{max(1.0, y1 - y0):.1f}"/>'
        )

    # 가로 눈금 넷. 값이 없으면 캔들을 읽을 수 없다.
    grid = []
    for n in range(4):
        value = low + span * n / 3
        y = y_of(value)
        grid.append(
            f'<line class="c-grid" x1="0" y1="{y:.1f}" x2="{plot_w:.1f}" y2="{y:.1f}"/>'
            f'<text class="c-tick" x="{plot_w + 6:.1f}" y="{y + 3.5:.1f}">{_tick(value)}</text>'
        )

    first, last = bars[0].day.isoformat(), bars[-1].day.isoformat()
    labels = (
        f'<text class="c-tick" x="0" y="{height - 6}">{first}</text>'
        f'<text class="c-tick c-end" x="{plot_w:.1f}" y="{height - 6}">{last}</text>'
    )
    return (
        f'<div class="candle-wrap"><svg class="candles" viewBox="0 0 {width} {height}" '
        f'role="img"><title>일봉 {len(bars)}개 · {first} ~ {last}</title>'
        f'{"".join(grid)}{"".join(parts)}{labels}</svg></div>'
    )


def _tick(value: float) -> str:
    """눈금 숫자. 자릿수에 맞춰 소수를 줄인다."""
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 10:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


__all__ = ["BADGE_COLORS", "CANDLE_MIN", "badge", "badge_color", "badge_letter",
           "candles", "move", "spark"]
