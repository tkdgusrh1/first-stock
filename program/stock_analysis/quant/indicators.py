"""봉(Candle) 목록에서 지표를 계산한다. 전부 **그 날까지의 값만** 쓴다.

i 번째 날의 지표는 bars[0..i] 만 본다 — i+1 이후를 보면 미래 정보다.
값을 낼 수 없으면(앞쪽 자료 부족) None 을 돌려준다. 0 으로 메우지 않는다.
"""

from __future__ import annotations


def sma(closes: list[float], i: int, n: int) -> float | None:
    """i 번째 날까지 n 일 단순이동평균."""
    if n <= 0 or i + 1 < n:
        return None
    window = closes[i + 1 - n:i + 1]
    return sum(window) / n


def highest(values: list[float], i: int, n: int, include_today: bool = False) -> float | None:
    """직전 n 일(오늘 빼고, include_today 면 오늘 포함)의 최고값."""
    end = i + 1 if include_today else i
    start = end - n
    if start < 0 or n <= 0:
        return None
    return max(values[start:end])


def lowest(values: list[float], i: int, n: int, include_today: bool = False) -> float | None:
    end = i + 1 if include_today else i
    start = end - n
    if start < 0 or n <= 0:
        return None
    return min(values[start:end])


def atr(bars, i: int, n: int = 20) -> float | None:
    """평균 실제 변동폭(Average True Range). 손절 거리를 '그 종목의 평소 흔들림' 으로 잡는 데 쓴다."""
    if i < n:
        return None
    total = 0.0
    for k in range(i + 1 - n, i + 1):
        prev = bars[k - 1].close
        b = bars[k]
        total += max(b.high - b.low, abs(b.high - prev), abs(b.low - prev))
    return total / n


def rsi(closes: list[float], i: int, n: int = 14) -> float | None:
    """단순 평균 방식 RSI (0~100). 내린 날이 하나도 없으면 100."""
    if i < n:
        return None
    gains = losses = 0.0
    for k in range(i + 1 - n, i + 1):
        change = closes[k] - closes[k - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def change(closes: list[float], i: int, n: int) -> float | None:
    """n 거래일 전 대비 수익률(소수). 6개월 ≈ 126 거래일."""
    if i < n or not closes[i - n]:
        return None
    return closes[i] / closes[i - n] - 1


def volatility(closes: list[float], i: int, n: int = 20) -> float | None:
    """최근 n 일 일간 수익률의 표준편차를 연율화(×√252)."""
    if i < n:
        return None
    rets = [closes[k] / closes[k - 1] - 1 for k in range(i + 1 - n, i + 1) if closes[k - 1]]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * (252 ** 0.5)
