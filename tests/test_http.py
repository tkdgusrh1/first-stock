"""SEC 403 을 부르는 요청 문제들에 대한 회귀 테스트."""

import pytest

from stock_analysis.edgar import _parse_ticker_payload
from stock_analysis.http import ForbiddenError, HttpClient, sanitize_user_agent


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
    assert "tester@example.com" in message   # 지금 값이 무엇인지 보여준다
    assert "doctor" in message               # 다음에 뭘 할지 알려준다
    assert "브라우저" in message


def test_forbidden_tries_every_header_profile_once(monkeypatch):
    """403 은 같은 헤더로 재시도해도 소용없다. 조합을 바꿔가며 딱 한 번씩만."""
    client = HttpClient("Tester tester@example.com")
    calls = []
    monkeypatch.setattr(client.session, "get", lambda *a, **k: (calls.append(1), _Resp(403))[1])

    with pytest.raises(ForbiddenError):
        client.get("https://www.sec.gov/files/company_tickers.json")
    assert len(calls) == len(client.profiles)


def test_switches_to_a_working_profile(monkeypatch):
    """어떤 조합이 통하면 그 뒤로는 계속 그 조합을 쓴다."""
    client = HttpClient("Tester tester@example.com")

    def fake_get(url, timeout=None, headers=None, **kwargs):
        used = headers or client.session.headers
        # 'browser' 조합만 통과시킨다
        return _Resp(200 if "Mozilla" in used.get("User-Agent", "") else 403)

    monkeypatch.setattr(client.session, "get", fake_get)

    assert client.get("https://www.sec.gov/files/company_tickers.json").status_code == 200
    assert client.profile_name == "browser"
    assert "Mozilla" in client.session.headers["User-Agent"]
    assert "tester@example.com" in client.session.headers["User-Agent"]  # 연락처는 유지


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


class _Blocked:
    """SEC 에 아예 못 나가는 상태."""

    def get_json(self, url, **kwargs):
        raise AssertionError("SEC 에 요청하면 안 됩니다")

    def get(self, url, **kwargs):
        raise AssertionError("SEC 에 요청하면 안 됩니다")


TICKER_JSON = '{"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}'


@pytest.mark.parametrize(
    "filename",
    [
        "company_tickers.json",
        "company_tickers.json.txt",      # 브라우저가 .txt 를 붙이는 경우
        "company_tickers (1).json",      # 두 번 받은 경우
    ],
)
def test_manual_file_is_found_despite_browser_renaming(tmp_path, monkeypatch, filename):
    from stock_analysis.edgar import EdgarClient

    monkeypatch.chdir(tmp_path)
    (tmp_path / filename).write_text(TICKER_JSON, encoding="utf-8")

    client = EdgarClient(_Blocked(), cache_dir=tmp_path / ".cache")
    assert client.resolve("NVDA") == ("0001045810", "NVIDIA CORP")


def test_html_saved_by_mistake_is_reported(tmp_path, monkeypatch, caplog):
    """웹페이지로 저장했을 때 조용히 실패하지 말고 이유를 알려준다."""
    from stock_analysis.edgar import EdgarClient

    monkeypatch.chdir(tmp_path)
    (tmp_path / "company_tickers.json").write_text(
        "<html><body>SEC</body></html>", encoding="utf-8"
    )

    client = EdgarClient(_Blocked(), cache_dir=tmp_path / ".cache")
    with caplog.at_level("ERROR"):
        with pytest.raises(AssertionError):     # 파일이 못 쓰이니 네트워크로 넘어간다
            client.resolve("NVDA")
    assert "웹페이지(HTML)로 저장" in caplog.text


def test_bom_and_whitespace_are_tolerated(tmp_path, monkeypatch):
    from stock_analysis.edgar import EdgarClient

    monkeypatch.chdir(tmp_path)
    (tmp_path / "company_tickers.json").write_text(
        "﻿\n  " + TICKER_JSON + "\n", encoding="utf-8"
    )
    client = EdgarClient(_Blocked(), cache_dir=tmp_path / ".cache")
    assert client.resolve("NVDA")[0] == "0001045810"


def test_manual_ticker_file_is_used_when_sec_is_blocked(tmp_path, monkeypatch):
    """브라우저로 직접 받아 폴더에 둔 목록이 있으면 SEC 요청 없이 그걸 쓴다."""
    import json

    from stock_analysis.edgar import EdgarClient

    class Blocked:
        def get_json(self, url, **kwargs):
            raise AssertionError("SEC 에 요청하면 안 됩니다")

        def get(self, url, **kwargs):
            raise AssertionError("SEC 에 요청하면 안 됩니다")

    monkeypatch.chdir(tmp_path)
    (tmp_path / "company_tickers.json").write_text(
        json.dumps({"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}),
        encoding="utf-8",
    )

    client = EdgarClient(Blocked(), cache_dir=tmp_path / ".cache")
    assert client.resolve("NVDA") == ("0001045810", "NVIDIA CORP")


def test_malformed_rows_are_skipped():
    payload = {
        "fields": ["cik", "name", "ticker"],
        "data": [[320193, "Apple Inc.", "AAPL"], ["없음"], [None, None, None]],
    }
    assert list(_parse_ticker_payload(payload)) == ["AAPL"]
