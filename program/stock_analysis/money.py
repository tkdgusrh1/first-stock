"""돈을 그 나라 단위로 적는다.

한국 주식을 달러로 적으면 읽는 사람이 판단을 못 한다. '삼성전자 75,000'
앞에 $ 가 붙어 있으면 7,500만 원짜리 주식으로 읽힌다. 자릿수가 세 자리씩
끊기는 것도 한국에서 쓰는 방식이 아니다 — 만·억·조로 끊어야 크기가 잡힌다.

    미국   $302.1B      1주 $185.42
    한국   302조 1,000억원   1주 75,000원

큰 값과 한 주 값은 규칙이 다르다. 매출은 '조·억' 으로 줄여야 크기가 보이고,
주가는 줄이면 안 된다 — 75,000원을 '7.5만원' 으로 적으면 호가를 못 읽는다.
"""

from __future__ import annotations

USD = "USD"
KRW = "KRW"

SYMBOL = {USD: "$", KRW: "원"}

# 한국은 만(10^4)에서 네 자리씩 끊는다. 큰 것부터 본다.
_KR_UNITS = ((1e12, "조"), (1e8, "억"), (1e4, "만"))


def is_won(currency: str) -> bool:
    return str(currency or USD).upper() == KRW


def price(value: float | None, currency: str = USD) -> str:
    """한 주 값(주가·EPS·매수가). **줄이지 않는다.**

    75,000원을 '7.5만원' 으로 적으면 호가를 읽을 수 없다. 미국은 센트까지
    보는 게 보통이라 소수 두 자리를 남긴다.
    """
    if value is None:
        return "-"
    if is_won(currency):
        # 원 단위는 소수가 의미 없다. 다만 1원 미만이면 반올림해 0 이 되므로
        # 그때만 소수를 남긴다 (동전주·환산값).
        return f"{value:,.0f}원" if abs(value) >= 1 else f"{value:,.2f}원"
    return f"${value:,.2f}"


def exact(value: float | None, currency: str = USD) -> str:
    """자릿수를 그대로 보여야 하는 값(투자 원금·평가액)."""
    if value is None:
        return "-"
    if is_won(currency):
        return f"{value:,.0f}원"
    return f"${value:,.2f}"


def amount(value: float | None, currency: str = USD) -> str:
    """매출·자본처럼 큰 값. 크기가 한눈에 잡히게 줄인다.

    한국은 신문에 적는 방식 그대로 두 단위까지 쓴다 — '300조 8,709억원'.
    '300.9조원' 은 틀린 건 아니지만 한국에서 쓰는 말이 아니고, 소수점 아래를
    보려면 한 번 더 계산해야 한다. 미국은 T·B·M·K 로 끊는다.
    """
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    size = abs(value)

    if is_won(currency):
        return sign + _won(size)

    for cut, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if size >= cut:
            return f"{sign}${size / cut:,.2f}{unit}"
    return f"{sign}${size:,.0f}"


def _won(size: float) -> str:
    """조 → 억 → 만 → 원. 조 단위는 억까지 같이 적는다."""
    if size >= 1e8:
        eok = size / 1e8
        if round(eok) >= 10000:                  # 1조 이상
            cho, rest = divmod(round(eok), 10000)
            return f"{cho:,}조원" if not rest else f"{cho:,}조 {rest:,}억원"
        # 1.5억을 '2억' 으로 적으면 3분의 1이 사라진다. 작을 때만 소수를 남긴다.
        # 다만 '1.50억' 처럼 뒤에 붙는 0 은 지운다 — 읽는 데 방해만 된다.
        if eok < 100:
            return f"{eok:,.2f}".rstrip("0").rstrip(".") + "억원"
        return f"{eok:,.0f}억원"
    if size >= 1e4:
        return f"{size / 1e4:,.0f}만원"
    return f"{size:,.0f}원"


def span(low: float | None, high: float | None, currency: str = USD) -> str:
    """범위(52주 최저~최고). 단위는 뒤에 한 번만 붙인다."""
    if low is None or high is None:
        return "-"
    if is_won(currency):
        return f"{low:,.0f} ~ {high:,.0f}원"
    return f"${low:,.2f} ~ ${high:,.2f}"


def shares(value: float | None, currency: str = USD) -> str:
    """발행주식수. 돈이 아니지만 끊는 자리는 같은 규칙을 따른다."""
    if not value:
        return "-"
    if is_won(currency):
        for cut, unit in _KR_UNITS:
            if value >= cut:
                return f"{value / cut:,.2f}{unit}주"
        return f"{value:,.0f}주"
    return f"{value / 1e6:,.0f}M"


__all__ = ["USD", "KRW", "SYMBOL", "is_won",
           "price", "exact", "amount", "span", "shares"]
