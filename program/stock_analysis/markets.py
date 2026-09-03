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


__all__ = [
    "US", "KR", "MARKET_NAME", "KOSPI", "KOSDAQ",
    "market_of", "code_of", "board_of", "price_symbol", "price_symbols", "display",
]
