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
    line = " ".join(coords)
    # 선 아래를 옅게 채우면 방향이 더 빨리 읽힌다
    area = f"0,{height} {line} {width},{height}"
    return (
        f'<svg class="spark {cls}" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" preserveAspectRatio="none" aria-hidden="true">'
        f'<polygon class="sp-fill" points="{area}"/>'
        f'<polyline class="sp-line" points="{line}"/></svg>'
    )


__all__ = ["BADGE_COLORS", "badge", "badge_color", "badge_letter", "move", "spark"]
