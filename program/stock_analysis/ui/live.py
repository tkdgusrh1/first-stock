"""몇 초마다 화면이 받아가는 값과 차트 자료. **네트워크를 쓰지 않는다** — 받아둔 값만.

화면 전체를 다시 그리면 스크롤·펼친 칸·차트 확대가 다 풀린다. 바뀌는 건 주가
몇 칸뿐이라 그 칸만 보낸다. 칸마다 처음 그릴 때와 **같은 함수**로 만든다 —
그래야 새로 끼운 칸과 처음 칸이 어긋나지 않는다.
"""

from __future__ import annotations

from .. import money
from .kit import change_html, esc, extended_html, price_text, spark_for, trade_time

CHART_DAYS = 1300       # 차트에 보내는 봉(약 5년). '5년' 단추가 거짓말이 되지 않게
MA_WINDOWS = (20, 60, 120)


def fields(m) -> dict:
    """한 종목의 바뀌는 칸들."""
    last = (getattr(m, "bars", None) or [None])[-1]
    return {
        "price": esc(price_text(m)),
        "change": change_html(m),
        "time": esc(trade_time(m)),
        "timeLong": esc(trade_time(m, long=True)),
        "ext": extended_html(m),
        "spark": spark_for(m),
        "num": m.price if m is not None else None,
        "bar": bar_json(last) if last else None,
    }


def items(bot, tickers: list[str]) -> dict:
    """요청한 종목들의 바뀌는 칸. 감시 목록에 없는 건 빼고 돌려준다."""
    wanted = {t.upper() for t in tickers if t}
    metrics = bot.cached_metrics()
    out = {}
    for target in bot.cached_targets():
        if target.ticker.upper() not in wanted:
            continue
        m = metrics.get(target.cik)
        if m is not None:
            out[target.ticker] = fields(m)
    return out


def chart_data(bot, ticker: str) -> dict:
    """차트 한 장에 필요한 것 — 봉 · 거래량 · 이동평균선.

    이동평균은 **창이 다 찬 날부터만** 낸다. 앞쪽 19일은 20일 평균을 낼 수
    없는데, 있는 만큼만 평균 내서 그리면 그 구간 선이 거짓말을 한다.
    """
    wanted = str(ticker or "").upper()
    for target in bot.cached_targets():
        if target.ticker.upper() != wanted:
            continue
        m = bot.cached_metrics().get(target.cik)
        bars = list(getattr(m, "bars", None) or [])[-CHART_DAYS:] if m else []
        closes = [b.close for b in bars]
        averages = {}
        for window in MA_WINDOWS:
            points = []
            running = sum(closes[:window - 1]) if len(closes) >= window else 0.0
            for i in range(window - 1, len(bars)):
                running += closes[i]
                if i >= window:
                    running -= closes[i - window]
                points.append({"time": bars[i].day.isoformat(), "value": round(running / window, 6)})
            averages[f"ma{window}"] = points
        return {
            "ticker": target.ticker,
            "currency": getattr(m, "currency", money.USD) if m else money.USD,
            "live": bool(getattr(m, "market_open", False)) if m else False,
            "bars": [bar_json(b) for b in bars],
            **averages,
        }
    return {"ticker": wanted, "bars": [], **{f"ma{w}": [] for w in MA_WINDOWS}}


def bar_json(bar) -> dict:
    """봉 하나를 차트가 읽는 모양으로. 거래량이 없으면 빼 둔다(0 으로 메우지 않는다)."""
    item = {"time": bar.day.isoformat(), "open": bar.open, "high": bar.high,
            "low": bar.low, "close": bar.close}
    if bar.volume is not None:
        item["volume"] = bar.volume
    return item


__all__ = ["CHART_DAYS", "MA_WINDOWS", "bar_json", "chart_data", "fields", "items"]
