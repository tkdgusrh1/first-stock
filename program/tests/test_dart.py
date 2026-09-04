"""DART(금융감독원) 응답을 읽는 부분.

한국은 미국과 사정이 다르다. SEC 는 전부 무료로 열쇠 없이 주는데, DART 는
무료지만 인증키가 있어야 한다. 그래서 여기서 제일 중요한 것은 **열쇠가
없을 때 조용히 비우는 것**이다. 시세는 야후에서 따로 받으니 주가는 보이고
재무제표만 빈다 — 그 상태를 '값이 없다' 로 정직하게 넘겨야 한다.

두 번째로 중요한 것은 **못 맞춘 계정을 억지로 끼워 맞추지 않는 것**이다.
회사마다 계정 이름을 다르게 적는데, 비슷해 보인다고 넣으면 틀린 숫자가
맞는 자리에 들어앉는다. 비어 있으면 화면이 '판단 불가' 라고 말해주지만,
틀린 값은 아무도 못 알아챈다.
"""

import io
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analysis.dart import (  # noqa: E402
    DartClient,
    parse_corp_codes,
    parse_filings,
    parse_financials,
)

CORP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>
        <stock_code>005930</stock_code><modify_date>20250101</modify_date></list>
  <list><corp_code>00164779</corp_code><corp_name>SK하이닉스</corp_name>
        <stock_code>000660</stock_code><modify_date>20250101</modify_date></list>
  <list><corp_code>00999999</corp_code><corp_name>비상장회사</corp_name>
        <stock_code> </stock_code><modify_date>20250101</modify_date></list>
</result>"""


def corp_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("CORPCODE.xml", CORP_XML)
    return buffer.getvalue()


def filings_payload(status="000"):
    return {
        "status": status, "message": "정상", "page_no": 1, "total_count": 2,
        "list": [
            {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930",
             "report_nm": "분기보고서 (2025.03)", "rcept_no": "20250515000123",
             "flr_nm": "삼성전자", "rcept_dt": "20250515", "rm": ""},
            {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930",
             "report_nm": "주요사항보고서(자기주식취득결정)", "rcept_no": "20250401000456",
             "flr_nm": "삼성전자", "rcept_dt": "20250401", "rm": ""},
        ],
    }


def finance_payload(status="000", extra=None):
    rows = [
        {"rcept_no": "20260310000111", "bsns_year": "2025", "sj_div": "IS",
         "account_id": "ifrs-full_Revenue", "account_nm": "수익(매출액)",
         "thstrm_amount": "300,870,903", "frmtrm_amount": "258,935,494"},
        {"rcept_no": "20260310000111", "bsns_year": "2025", "sj_div": "IS",
         "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익",
         "thstrm_amount": "32,725,961", "frmtrm_amount": "6,566,976"},
        {"rcept_no": "20260310000111", "bsns_year": "2025", "sj_div": "IS",
         "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익",
         "thstrm_amount": "34,451,351", "frmtrm_amount": "15,487,100"},
        {"rcept_no": "20260310000111", "bsns_year": "2025", "sj_div": "BS",
         "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
         "thstrm_amount": "402,192,070", "frmtrm_amount": "363,677,865"},
        {"rcept_no": "20260310000111", "bsns_year": "2025", "sj_div": "BS",
         "account_id": "-표준계정코드 미사용-", "account_nm": "현금및현금성자산",
         "thstrm_amount": "52,235,166", "frmtrm_amount": "69,080,893"},
    ]
    return {"status": status, "message": "정상", "list": rows + (extra or [])}


class FakeHttp:
    def __init__(self, content=b"", payload=None, fail=False):
        self.content, self.payload, self.fail = content, payload, fail
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params", {})))
        if self.fail:
            raise OSError("DART 가 응답하지 않습니다")

        class Resp:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                pass

        return Resp(self.content)

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params", {})))
        if self.fail:
            raise OSError("DART 가 응답하지 않습니다")
        return self.payload


# --- 열쇠가 없을 때 ---------------------------------------------------------
def test_without_a_key_nothing_is_requested(tmp_path):
    """제일 중요한 것. 열쇠가 없으면 조용히 비운다 — 지어내지 않는다."""
    http = FakeHttp()
    client = DartClient(http, api_key="", cache_dir=tmp_path)

    assert not client.ready
    assert client.corp_codes() == {}
    assert client.filings("00126380", date(2026, 1, 1)) == []
    assert client.financials("00126380", 2025).empty
    assert http.calls == []                      # 물어보지도 않았다


def test_the_missing_key_says_how_to_get_one(tmp_path):
    reason = DartClient(FakeHttp(), api_key="", cache_dir=tmp_path).blocked_reason
    assert "무료" in reason and "opendart.fss.or.kr" in reason
    assert "주가는 그대로 보입니다" in reason      # 무엇이 되고 무엇이 안 되는지


def test_a_key_that_is_only_spaces_counts_as_missing(tmp_path):
    assert not DartClient(FakeHttp(), api_key="   ", cache_dir=tmp_path).ready


# --- 회사 목록 --------------------------------------------------------------
def test_listed_companies_are_matched_by_their_stock_code():
    found = parse_corp_codes(corp_zip())

    assert found["005930"] == ("00126380", "삼성전자")
    assert found["000660"] == ("00164779", "SK하이닉스")


def test_unlisted_companies_are_left_out():
    """종목코드가 없으면 살 수 없는 회사다."""
    assert all(corp != "00999999" for corp, _name in parse_corp_codes(corp_zip()).values())
    assert len(parse_corp_codes(corp_zip())) == 2


def test_a_broken_download_does_not_crash():
    assert parse_corp_codes("이건 ZIP 이 아닙니다".encode()) == {}


def test_the_company_list_is_kept_on_disk(tmp_path):
    """2만 개가 넘는 목록이다. 매번 받으면 그것만으로 오래 걸린다."""
    client = DartClient(FakeHttp(content=corp_zip()), api_key="키", cache_dir=tmp_path)
    first = client.corp_codes()

    again = DartClient(FakeHttp(fail=True), api_key="키", cache_dir=tmp_path)

    assert again.corp_codes() == first          # 새로 안 받고도 나온다


# --- 공시 -------------------------------------------------------------------
def test_filings_are_read_with_a_link_to_the_original():
    found = parse_filings(filings_payload())

    assert len(found) == 2
    assert found[0].name == "분기보고서 (2025.03)"
    assert found[0].day == date(2025, 5, 15)
    assert found[0].url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250515000123"


def test_an_error_response_yields_nothing_rather_than_garbage():
    """DART 는 실패해도 200 으로 답하고 status 에 오류를 담는다."""
    assert parse_filings(filings_payload(status="013")) == []
    assert parse_filings({}) == []


# --- 재무제표 ---------------------------------------------------------------
def test_the_main_accounts_are_read():
    found = parse_financials(finance_payload())

    assert found.values["revenue"] == 300870903
    assert found.values["operating_income"] == 32725961
    assert found.values["net_income"] == 34451351
    assert found.values["equity"] == 402192070


def test_last_year_is_kept_so_growth_can_be_worked_out():
    found = parse_financials(finance_payload())
    assert found.prior["revenue"] == 258935494


def test_an_account_without_a_standard_code_is_matched_by_name():
    """표준 계정코드가 비어 있는 회사가 많다."""
    assert parse_financials(finance_payload()).values["cash"] == 52235166


def test_an_account_we_do_not_know_is_left_out_not_guessed():
    """비슷해 보인다고 끼워 맞추면 틀린 숫자가 맞는 자리에 들어앉는다."""
    extra = [{"account_id": "ifrs-full_Something", "account_nm": "처음 보는 계정",
              "thstrm_amount": "999", "bsns_year": "2025"}]
    found = parse_financials(finance_payload(extra=extra))

    assert 999 not in found.values.values()
    assert "처음 보는 계정" in found.unmatched      # 무엇을 못 읽었는지는 남긴다


def test_a_dash_amount_is_not_read_as_zero():
    """빈칸을 0 으로 읽으면 '적자' 로 잘못 판정된다."""
    extra = [{"account_id": "ifrs-full_Liabilities", "account_nm": "부채총계",
              "thstrm_amount": "-", "bsns_year": "2025"}]
    found = parse_financials(finance_payload(extra=extra))

    assert "total_debt" not in found.values


def test_the_report_it_came_from_is_kept():
    found = parse_financials(finance_payload())
    assert found.rcept_no == "20260310000111"
    assert found.url.endswith("20260310000111")


def test_an_error_response_gives_an_empty_result():
    assert parse_financials(finance_payload(status="013")).empty


# --- 실제 호출 --------------------------------------------------------------
def test_the_key_is_sent_but_never_logged(tmp_path):
    http = FakeHttp(payload=filings_payload())
    client = DartClient(http, api_key="비밀열쇠", cache_dir=tmp_path)

    client.filings("00126380", date(2026, 1, 1), date(2026, 3, 1))

    url, params = http.calls[0]
    assert params["crtfc_key"] == "비밀열쇠"
    assert params["bgn_de"] == "20260101" and params["end_de"] == "20260301"


def test_a_network_failure_yields_nothing_rather_than_crashing(tmp_path):
    client = DartClient(FakeHttp(fail=True), api_key="키", cache_dir=tmp_path)
    assert client.filings("00126380", date(2026, 1, 1)) == []
    assert client.financials("00126380", 2025).empty


# --- 열쇠가 왜 안 되는지 말해준다 ---------------------------------------------
def test_a_zip_means_the_key_worked():
    from stock_analysis.dart import read_status

    assert read_status(b"PK\x03\x04anything") == ("000", "")


def test_dart_tells_us_why_it_refused():
    """'안 된다' 만으로는 무엇을 고쳐야 할지 알 수 없다."""
    from stock_analysis.dart import read_status

    code, why = read_status(b"<result><status>010</status><message>x</message></result>")
    assert code == "010"
    assert "등록되지 않은" in why

    code, why = read_status(b'{"status":"020","message":"x"}')
    assert code == "020"
    assert "한도" in why


def test_an_unreadable_answer_is_not_guessed():
    from stock_analysis.dart import read_status

    code, why = read_status(b"garbage")
    assert code == ""
    assert why


class _Resp:
    def __init__(self, content, status_code=200):
        self.content, self.status_code = content, status_code


class _Http:
    def __init__(self, resp):
        self.resp = resp

    def get(self, *a, **k):
        return self.resp


def test_checking_a_bad_key_reports_darts_own_reason():
    from stock_analysis.dart import DartClient

    client = DartClient(_Http(_Resp(b"<result><status>011</status></result>")), "틀린키")
    ok, why = client.check_key()

    assert not ok
    assert "메일 인증" in why


def test_checking_an_empty_key_says_so():
    from stock_analysis.dart import DartClient

    ok, why = DartClient(_Http(None), "").check_key()
    assert not ok and "비어" in why


def test_a_refused_list_leaves_the_reason_behind(tmp_path):
    """화면이 '아직 못 받았습니다' 로만 남으면 고칠 방법이 없다."""
    from stock_analysis.dart import DartClient

    resp = _Resp(b"<result><status>010</status></result>")
    resp.raise_for_status = lambda: None
    client = DartClient(_Http(resp), "틀린키", tmp_path)

    assert client.corp_codes() == {}
    assert "등록되지 않은" in client.last_error
    assert "등록되지 않은" in client.blocked_reason


# --- 여러 회사를 한 번에 (추천 후보를 추리는 유일한 길) ------------------------
def _multi_row(code, corp, name, amount, prior=""):
    return {"stock_code": code, "corp_code": corp, "bsns_year": "2024",
            "rcept_no": "20250311000001", "account_nm": name,
            "thstrm_amount": amount, "frmtrm_amount": prior}


def test_many_companies_come_back_keyed_by_stock_code():
    from stock_analysis.dart import parse_multi

    found = parse_multi({"status": "000", "list": [
        _multi_row("005930", "00126380", "매출액", "300870903000000", "258935494000000"),
        _multi_row("005930", "00126380", "영업이익", "32725961000000"),
        _multi_row("035720", "00258801", "매출액", "7873800000000"),
    ]})

    assert set(found) == {"005930", "035720"}
    assert found["005930"].values["revenue"] == 300_870_903_000_000
    assert found["005930"].prior["revenue"] == 258_935_494_000_000
    assert found["005930"].values["operating_income"] == 32_725_961_000_000
    assert found["005930"].corp_code == "00126380"


def test_unlisted_companies_are_skipped():
    """종목코드가 없으면 살 수 없는 회사다. 후보에 넣을 이유가 없다."""
    from stock_analysis.dart import parse_multi

    found = parse_multi({"status": "000", "list": [
        _multi_row("", "00111111", "매출액", "1000"),
        _multi_row("   ", "00222222", "매출액", "2000"),
        _multi_row("005930", "00126380", "매출액", "3000"),
    ]})

    assert list(found) == ["005930"]


def test_a_refused_answer_gives_nothing():
    """거절당했는데 빈 값을 '자료 없음' 으로 착각하면 안 된다."""
    from stock_analysis.dart import parse_multi

    assert parse_multi({"status": "010", "message": "등록되지 않은 키"}) == {}
    assert parse_multi({}) == {}


def test_the_first_statement_wins_when_both_are_given():
    """같은 항목이 연결·별도로 두 번 온다. 섞어 쓰면 숫자가 어긋난다."""
    from stock_analysis.dart import parse_multi

    found = parse_multi({"status": "000", "list": [
        _multi_row("005930", "00126380", "매출액", "300870903000000"),   # 연결
        _multi_row("005930", "00126380", "매출액", "209969772000000"),   # 별도
    ]})

    assert found["005930"].values["revenue"] == 300_870_903_000_000


class _Batches:
    """묶음마다 무엇을 받았는지 기록하는 가짜 DART."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.asked = []

    def get(self, url, params=None, **kw):
        self.asked.append((params or {}).get("corp_code", ""))
        payload = self.payloads.pop(0) if self.payloads else {"status": "000", "list": []}

        class _R:
            def json(_self):
                return payload
        return _R()


def test_companies_are_asked_in_batches(tmp_path):
    """한 곳씩 물어보면 2,600번이다. 나눠서 한 번에 여러 개를 받는다."""
    from stock_analysis.dart import DartClient

    http = _Batches([
        {"status": "000", "list": [_multi_row("005930", "00126380", "매출액", "300")]},
        {"status": "000", "list": [_multi_row("035720", "00258801", "매출액", "78")]},
    ])
    client = DartClient(http, "열쇠", tmp_path)

    found = client.many_financials(["00126380", "00258801", "00333333"], 2024, chunk=2)

    assert len(http.asked) == 2
    assert http.asked[0] == "00126380,00258801"      # 콤마로 묶어 한 번에
    assert http.asked[1] == "00333333"
    assert set(found) == {"005930", "035720"}


def test_a_refusal_stops_the_batches_and_keeps_the_reason(tmp_path):
    """한도를 넘었는데 계속 두드리면 남의 서버에도 할 짓이 아니다."""
    from stock_analysis.dart import DartClient

    http = _Batches([{"status": "020", "message": "한도 초과"}])
    client = DartClient(http, "열쇠", tmp_path)

    assert client.many_financials(["a", "b", "c", "d"], 2024, chunk=1) == {}
    assert len(http.asked) == 1                      # 첫 거절에서 멈춘다
    assert "한도" in client.last_error


def test_an_empty_batch_does_not_stop_the_rest(tmp_path):
    """'자료 없음(013)' 은 그 묶음만 비는 것이지 막힌 게 아니다."""
    from stock_analysis.dart import DartClient

    http = _Batches([
        {"status": "013", "message": "조회된 데이타가 없습니다"},
        {"status": "000", "list": [_multi_row("005930", "00126380", "매출액", "300")]},
    ])
    client = DartClient(http, "열쇠", tmp_path)

    found = client.many_financials(["a", "b"], 2024, chunk=1)

    assert len(http.asked) == 2
    assert set(found) == {"005930"}


def test_no_key_means_no_requests(tmp_path):
    from stock_analysis.dart import DartClient

    http = _Batches([])
    assert DartClient(http, "", tmp_path).many_financials(["a"], 2024) == {}
    assert not http.asked
