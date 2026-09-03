"""티커만 보고 **어느 나라 증시인지** 가른다.

지금까지 이 프로그램은 미국 하나만 봤다. SEC 가 재무제표와 공시를 전부
무료로, 열쇠 없이 공개하기 때문에 그럴 수 있었다. 한국은 사정이 다르다.

  · 공시·재무제표 — 금융감독원 DART. **무료지만 열쇠(API 키)가 필요하다.**
  · 시세          — 야후 파이낸스에서 `005930.KS` 처럼 받는다. 열쇠 불필요.

그래서 한국 종목은 '시세는 되는데 재무제표는 열쇠가 있어야 되는' 상태가
될 수 있다. 그 경우 **없는 값을 지어내지 않고 판단 불가로 둔다.** 화면에서
왜 비어 있는지도 함께 밝힌다.

티커 모양만으로 가른다. 종목 코드가 여섯 자리 숫자면 한국, 글자면 미국이다.
한국 코드는 `005930`, `005930.KS`, `A005930` 세 가지로 들어올 수 있다.
"""

from __future__ import annotations

import re

US, KR = "us", "kr"

MARKET_NAME = {US: "미국", KR: "한국"}

# 005930 · 005930.KS · 005930.KQ · A005930
_KR = re.compile(r"^A?(\d{6})(?:\.(KS|KQ))?$", re.I)

KOSPI, KOSDAQ = "KS", "KQ"


def market_of(ticker: str) -> str:
    """이 티커가 어느 시장인지. 모르면 미국으로 본다(지금까지의 동작)."""
    return KR if _KR.match(str(ticker or "").strip()) else US


def code_of(ticker: str) -> str:
    """한국 종목의 여섯 자리 코드. 한국 종목이 아니면 빈 문자열."""
    found = _KR.match(str(ticker or "").strip())
    return found.group(1) if found else ""


def board_of(ticker: str) -> str:
    """코스피(KS) 인지 코스닥(KQ) 인지. 티커에 안 적혀 있으면 빈 문자열."""
    found = _KR.match(str(ticker or "").strip())
    return (found.group(2) or "").upper() if found else ""


def price_symbol(ticker: str, board: str = "") -> str:
    """시세를 받을 때 쓸 기호.

    야후는 한국 종목을 `005930.KS`(코스피) / `035720.KQ`(코스닥) 로 준다.
    어느 시장인지 모르면 코스피로 먼저 시도한다 — 종목 수가 훨씬 많다.
    """
    code = code_of(ticker)
    if not code:
        return str(ticker or "").upper()
    return f"{code}.{(board_of(ticker) or board or KOSPI).upper()}"


def price_symbols(ticker: str) -> list[str]:
    """차례로 시도해볼 시세 기호. 시장이 안 적혀 있으면 두 군데 다 본다."""
    code = code_of(ticker)
    if not code:
        return [str(ticker or "").upper()]
    board = board_of(ticker)
    if board:
        return [f"{code}.{board}"]
    return [f"{code}.{KOSPI}", f"{code}.{KOSDAQ}"]


def display(ticker: str) -> str:
    """화면에 쓸 이름. 한국 종목은 여섯 자리 코드만 보여준다."""
    return code_of(ticker) or str(ticker or "").upper()


# --------------------------------------------------------------------------
# 지금 장이 열려 있나
#
# 휴장일 표를 손으로 적지 않는다. 설날·추석은 음력이라 해마다 날짜가
# 바뀌는데, 그걸 내가 기억으로 적어 넣으면 틀린 날 '장중' 이라고 말하게
# 된다. 대신 **시세 제공처가 알려주는 상태를 그대로 쓴다.** 거래소에서
# 나온 값이라 휴장일도 자동으로 맞는다.
#
# 시세를 못 받았을 때만 시각으로 어림한다. 그때는 '어림' 이라고 밝힌다.
# --------------------------------------------------------------------------
OPEN, PRE, POST, CLOSED, UNKNOWN_STATE = "open", "pre", "post", "closed", "unknown"

STATE_LABEL = {
    OPEN: "장중",
    PRE: "장 시작 전",
    POST: "장 마감 후",
    CLOSED: "장 마감",
    UNKNOWN_STATE: "확인 안 됨",
}

STATE_ICON = {OPEN: "🟢", PRE: "🌙", POST: "🌙", CLOSED: "⚫", UNKNOWN_STATE: "⚪"}

# 거래 시간 (현지 시각). 어림할 때만 쓴다.
HOURS = {
    US: ((9, 30), (16, 0), "America/New_York", "미 동부"),
    KR: ((9, 0), (15, 30), "Asia/Seoul", "한국"),
}

# 시세 제공처가 주는 값 → 우리 표현
_FROM_FEED = {
    "REGULAR": OPEN,
    "PRE": PRE, "PREPRE": PRE,
    "POST": POST, "POSTPOST": POST,
    "CLOSED": CLOSED,
}


def state_from_feed(market_state: str | None) -> str:
    """시세 제공처가 알려준 장 상태. 모르는 값이면 '확인 안 됨'."""
    return _FROM_FEED.get(str(market_state or "").upper(), UNKNOWN_STATE)


def state_by_clock(market: str, moment=None) -> tuple[str, str]:
    """시각으로 어림한 장 상태. (상태, 현지 시각 표시)

    **어림이다.** 휴장일을 모르기 때문에 공휴일에도 '장중' 이라고 할 수 있다.
    화면에서 반드시 '어림' 이라고 밝히고 쓴다.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    start, end, zone, _label = HOURS.get(market, HOURS[US])
    try:
        here = (moment or datetime.now(tz=ZoneInfo(zone))).astimezone(ZoneInfo(zone))
    except Exception:
        return UNKNOWN_STATE, ""

    shown = here.strftime("%H:%M")
    if here.weekday() >= 5:                      # 토·일
        return CLOSED, shown
    minutes = here.hour * 60 + here.minute
    if minutes < start[0] * 60 + start[1]:
        return PRE, shown
    if minutes >= end[0] * 60 + end[1]:
        return POST, shown
    return OPEN, shown


def hours_text(market: str) -> str:
    """거래 시간을 한 줄로."""
    start, end, _zone, label = HOURS.get(market, HOURS[US])
    return f"{label} {start[0]:02d}:{start[1]:02d}~{end[0]:02d}:{end[1]:02d}"


__all__ = [
    "US", "KR", "MARKET_NAME", "KOSPI", "KOSDAQ",
    "market_of", "code_of", "board_of", "price_symbol", "price_symbols", "display",
    "OPEN", "PRE", "POST", "CLOSED", "UNKNOWN_STATE",
    "STATE_LABEL", "STATE_ICON", "HOURS",
    "state_from_feed", "state_by_clock", "hours_text",
]
