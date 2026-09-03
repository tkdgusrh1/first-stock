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

    assert found["005930"] == "00126380"
    assert found["000660"] == "00164779"


def test_unlisted_companies_are_left_out():
    """종목코드가 없으면 살 수 없는 회사다."""
    assert "00999999" not in parse_corp_codes(corp_zip()).values()
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
