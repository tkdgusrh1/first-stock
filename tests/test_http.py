"""SEC 403 을 부르는 요청 문제들에 대한 회귀 테스트."""

import pytest

from stockbot.edgar import _parse_ticker_payload
from stockbot.http import ForbiddenError, HttpClient, sanitize_user_agent


# --- User-Agent 정리 --------------------------------------------------------
def test_korean_name_is_replaced_but_email_kept():
    """한글이 헤더에 들어가면 SEC 가 403 으로 막는다. 이메일은 살려야 한다."""
    result = sanitize_user_agent("홍길동 hong@gmail.com")
    assert result.isascii()
    assert "hong@gmail.com" in result
    assert "홍길동" not in result


def test_english_name_is_left_alone():
    assert sanitize_user_agent("Gildong Hong hong@gmail.com") == "Gildong Hong hong@gmail.com"


def test_extra_whitespace_is_collapsed():
    assert sanitize_user_agent("  Kim   Chulsoo   kim@a.co  ") == "Kim Chulsoo kim@a.co"


def test_email_only_gets_a_name():
    result = sanitize_user_agent("hong@gmail.com")
    assert result.isascii() and "hong@gmail.com" in result
    assert len(result.split()) >= 2      # SEC 는 이름 + 이메일 형식을 요구한다


def test_no_email_still_returns_ascii():
    assert sanitize_user_agent("홍길동").isascii()


def test_accented_letters_are_transliterated():
    assert sanitize_user_agent("José Álvarez jose@a.co") == "Jose Alvarez jose@a.co"


# --- 요청 헤더 ---------------------------------------------------------------
def test_headers_include_what_sec_expects():
    client = HttpClient("Gildong Hong hong@gmail.com")
    headers = client.session.headers
    assert "hong@gmail.com" in headers["User-Agent"]
    assert "gzip" in headers["Accept-Encoding"]
    assert headers["Accept"]              # 비어 있으면 WAF 가 막는 경우가 있다
    assert all(str(v).isascii() for v in headers.values())


def test_user_agent_is_sanitized_at_the_client_too():
    client = HttpClient("상현 tkdgusrh1@gmail.com")
    assert client.session.headers["User-Agent"].isascii()


# --- 403 처리 ----------------------------------------------------------------
class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = "Forbidden"


def test_forbidden_raises_with_actionable_message(monkeypatch):
    client = HttpClient("Tester tester@example.com")
    monkeypatch.setattr(client.session, "get", lambda *a, **k: _Resp(403))

    with pytest.raises(ForbiddenError) as exc:
        client.get("https://www.sec.gov/files/company_tickers.json")

    message = str(exc.value)
    assert "403" in message
    assert "user_agent" in message          # 무엇을 고쳐야 하는지 알려준다
    assert "tester@example.com" in message  # 지금 값이 무엇인지 보여준다


def test_forbidden_is_not_retried(monkeypatch):
    """403 은 재시도해도 소용없다. 바로 알려주고 끝내야 한다."""
    client = HttpClient("Tester tester@example.com")
    calls = []
    monkeypatch.setattr(client.session, "get", lambda *a, **k: (calls.append(1), _Resp(403))[1])

    with pytest.raises(ForbiddenError):
        client.get("https://www.sec.gov/files/company_tickers.json")
    assert len(calls) == 1


def test_non_sec_403_is_returned_normally(monkeypatch):
    client = HttpClient("Tester tester@example.com")
    monkeypatch.setattr(client.session, "get", lambda *a, **k: _Resp(403))
    assert client.get("https://stooq.com/q/l/?s=aapl.us").status_code == 403


# --- 티커 목록 형식 ----------------------------------------------------------
def test_parses_classic_ticker_format():
    payload = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    assert _parse_ticker_payload(payload) == {"AAPL": ("0000320193", "Apple Inc.")}


def test_parses_exchange_ticker_format():
    """두 번째 후보 URL 은 형식이 다르다."""
    payload = {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], [1045810, "NVIDIA CORP", "NVDA", "Nasdaq"]],
    }
    parsed = _parse_ticker_payload(payload)
    assert parsed["AAPL"] == ("0000320193", "Apple Inc.")
    assert parsed["NVDA"] == ("0001045810", "NVIDIA CORP")


def test_malformed_rows_are_skipped():
    payload = {
        "fields": ["cik", "name", "ticker"],
        "data": [[320193, "Apple Inc.", "AAPL"], ["없음"], [None, None, None]],
    }
    assert list(_parse_ticker_payload(payload)) == ["AAPL"]
