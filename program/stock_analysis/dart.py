"""금융감독원 DART — 한국 상장사의 공시와 재무제표.

미국은 SEC 가 전부 무료로, 열쇠 없이 공개한다. 한국은 그렇지 않다.
DART 도 무료지만 **열쇠(API 키)를 발급받아야** 한다. 1분이면 되고 돈은
들지 않는다 (opendart.fss.or.kr → 인증키 신청).

열쇠가 없으면 이 모듈은 **아무것도 하지 않고 조용히 비운다.** 시세는
야후에서 따로 받으므로 주가는 보이고 재무제표만 빈다. 그 경우 화면에는
'열쇠가 없어 못 받았다' 고 적는다 — 없는 값을 지어내지 않는다.

계정 이름 맞추기에 대하여
--------------------------
DART 는 회사마다 계정 이름을 조금씩 다르게 적는다("매출액", "수익(매출액)",
"영업수익"…). 표준 계정코드(account_id)가 비어 있는 회사도 많다. 그래서
아래 표는 **아는 이름만 맞춰 보고, 못 맞춘 값은 비워 둔다.** 억지로
끼워 맞추면 틀린 숫자가 맞는 자리에 들어앉는다. 비어 있으면 화면이
'판단 불가' 로 말해주지만, 틀린 값은 아무도 못 알아챈다.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

BASE = "https://opendart.fss.or.kr/api"
CORP_CODE_URL = f"{BASE}/corpCode.xml"
LIST_URL = f"{BASE}/list.json"
FINANCE_URL = f"{BASE}/fnlttSinglAcntAll.json"

# 공시 원문을 사람이 읽는 주소
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

KEY_HELP = (
    "한국 종목의 공시·재무제표를 받으려면 DART 인증키가 필요합니다(무료·1분). "
    "https://opendart.fss.or.kr → 인증키 신청 → 발급받은 키를 config.yml 의 "
    "dart_api_key 에 넣어주세요. 키가 없어도 주가는 그대로 보입니다."
)

_CORP_TTL = 7 * 24 * 3600      # 회사 목록은 자주 안 바뀐다

# 보고서 종류. 분기 자료를 최근 것부터 훑을 때 쓴다.
REPORTS = (
    ("11013", "1분기보고서"),
    ("11012", "반기보고서"),
    ("11014", "3분기보고서"),
    ("11011", "사업보고서"),
)

# --------------------------------------------------------------------------
# 계정 이름 → 우리가 쓰는 항목
#
# 왼쪽은 DART 표준 계정코드, 오른쪽은 회사가 직접 적어 넣는 한글 이름이다.
# 표준코드가 있으면 그걸 먼저 믿고, 없을 때만 이름으로 맞춘다.
# --------------------------------------------------------------------------
BY_ACCOUNT_ID = {
    "ifrs-full_Revenue": "revenue",
    "ifrs-full_RevenueFromContractsWithCustomers": "revenue",
    "dart_OperatingIncomeLoss": "operating_income",
    "ifrs-full_ProfitLossFromOperatingActivities": "operating_income",
    "ifrs-full_ProfitLoss": "net_income",
    "ifrs-full_Equity": "equity",
    "ifrs-full_Liabilities": "total_debt",
    "ifrs-full_CashAndCashEquivalents": "cash",
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "ocf",
}

BY_ACCOUNT_NAME = {
    "매출액": "revenue",
    "수익(매출액)": "revenue",
    "영업수익": "revenue",
    "영업이익": "operating_income",
    "영업이익(손실)": "operating_income",
    "당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "분기순이익": "net_income",
    "반기순이익": "net_income",
    "자본총계": "equity",
    "부채총계": "total_debt",
    "현금및현금성자산": "cash",
    "영업활동현금흐름": "ocf",
    "영업활동으로인한현금흐름": "ocf",
}


# --------------------------------------------------------------------------
# 어떤 공시가 진짜 중요한가
#
# DART 에는 하루에도 수십 건이 올라온다. 전부 알리면 알림이 소음이 되고,
# 소음이 되면 정작 중요한 것을 놓친다. 그래서 **주가에 실제로 영향을 주는
# 것만** 골라 등급을 매긴다.
#
# 고른 기준은 이 프로그램이 미국에서 쓰는 것과 같다.
#   1순위 회사가 스스로 밝힌 전망·실적의 변화
#   2순위 내 지분이 줄어드는 일 (증자·전환사채)
#   3순위 회사의 주인이나 사업이 바뀌는 일
#
# 보고서 이름으로 맞춘다. **못 맞춘 공시는 버리지 않고 '참고' 로 둔다.**
# 이름 규칙이 바뀌어도 공시 자체는 원래 이름 그대로 화면에 남는다.
# --------------------------------------------------------------------------
ALERT, WATCH, PLAIN = "alert", "bad", "plain"

# (이름에 이 말이 들어가면, 등급, 한 줄 제목, 무엇을 봐야 하는지)
RULES: tuple[tuple[str, str, str, str], ...] = (
    # --- 내 지분이 줄어든다 ---
    ("유상증자", ALERT, "유상증자 결정",
     "새 주식을 찍어 파는 것입니다. 발행주식수가 늘어 내 몫이 줄어듭니다. "
     "얼마를 어디에 쓰는지, 할인율이 얼마인지를 보세요."),
    ("전환사채", ALERT, "전환사채(CB) 발행 결정",
     "나중에 주식으로 바뀌는 빚입니다. 전환가와 규모만큼 지분이 희석됩니다."),
    ("신주인수권부사채", ALERT, "신주인수권부사채(BW) 발행 결정",
     "주식을 살 권리가 붙은 빚입니다. 전환사채와 마찬가지로 희석 요인입니다."),
    ("교환사채", ALERT, "교환사채(EB) 발행 결정", "보유 주식과 바꿔주는 빚입니다."),
    ("무상증자", WATCH, "무상증자 결정",
     "주식 수가 늘지만 회사 가치는 그대로입니다. 주당 가격이 그만큼 내려갑니다."),
    ("감자", ALERT, "감자 결정",
     "주식 수를 줄입니다. 결손을 털기 위한 감자라면 재무가 나쁘다는 신호입니다."),

    # --- 실적이 크게 바뀐다 ---
    ("손익구조", ALERT, "매출·손익 30% 이상 변동",
     "직전 사업연도 대비 매출이나 손익이 크게 바뀌었다는 뜻입니다. "
     "이 프로그램의 1순위 판단 재료입니다 — 어느 쪽으로 얼마나 바뀌었는지 보세요."),

    # --- 회사가 바뀐다 ---
    ("회생절차", ALERT, "회생절차 관련", "법정관리입니다. 상장폐지로 이어질 수 있습니다."),
    ("파산", ALERT, "파산 관련", "가장 무거운 공시입니다."),
    ("상장폐지", ALERT, "상장폐지 관련", "거래가 정지되거나 시장에서 빠집니다."),
    ("거래정지", ALERT, "거래정지", "당분간 사고팔 수 없습니다."),
    ("최대주주 변경", ALERT, "최대주주 변경", "회사의 주인이 바뀝니다."),
    ("최대주주변경", ALERT, "최대주주 변경", "회사의 주인이 바뀝니다."),
    ("합병", ALERT, "합병 결정", "다른 회사와 합칩니다. 합병 비율을 보세요."),
    ("분할", ALERT, "분할 결정",
     "회사를 쪼갭니다. 인적분할인지 물적분할인지에 따라 주주에게 미치는 영향이 다릅니다."),
    ("영업양수", ALERT, "영업 양수도 결정", "사업을 사고팝니다. 규모와 대가를 보세요."),
    ("주식교환", ALERT, "주식 교환·이전 결정", "지배구조가 바뀝니다."),

    # --- 주주에게 돌아오는 것 ---
    ("자기주식 취득", WATCH, "자기주식 취득 결정",
     "회사가 자기 주식을 삽니다. 대개 주주에게 유리하지만, 실제로 사들이는지 "
     "이행 여부를 함께 보세요."),
    ("자기주식취득", WATCH, "자기주식 취득 결정",
     "회사가 자기 주식을 삽니다. 실제로 사들이는지 이행 여부를 함께 보세요."),
    ("자기주식 처분", WATCH, "자기주식 처분 결정", "가지고 있던 자기 주식을 내다 팝니다."),
    ("자기주식소각", WATCH, "자기주식 소각 결정", "주식 수가 영구히 줄어 주주에게 유리합니다."),
    ("배당", WATCH, "배당 결정", "얼마를 언제 주는지 보세요."),

    # --- 사업 ---
    ("공급계약", WATCH, "단일판매·공급계약 체결",
     "수주입니다. 계약 금액이 최근 매출의 몇 %인지, 기간이 얼마인지를 보세요."),
    ("투자판단", WATCH, "투자판단 관련 주요경영사항", "회사가 스스로 중요하다고 밝힌 사안입니다."),
    ("소송", WATCH, "소송 등의 제기", "금액과 상대를 보세요."),

    # --- 정기보고서 ---
    ("사업보고서", WATCH, "사업보고서 (연간)", "1년치 확정 재무제표입니다."),
    ("반기보고서", WATCH, "반기보고서", "상반기 재무제표입니다."),
    ("분기보고서", WATCH, "분기보고서", "분기 재무제표입니다."),

    # --- 지분 ---
    ("대량보유", WATCH, "5% 이상 대량보유 보고",
     "누가 지분을 크게 사거나 팔았습니다. 경영 참여 목적인지 단순 투자인지를 보세요."),
    ("소유상황보고", PLAIN, "임원·주요주주 지분 변동",
     "내부자 거래입니다. 한 건씩보다 여러 명이 같은 방향으로 움직이는지를 보세요."),
)


def classify(report_name: str) -> tuple[str, str, str]:
    """보고서 이름 → (등급, 한 줄 제목, 무엇을 봐야 하는지).

    못 맞추면 이름을 그대로 제목으로 쓰고 '참고' 로 둔다. 억지로 등급을
    매기면 안 중요한 것이 빨갛게 뜨고, 그러면 빨간색을 안 믿게 된다.
    """
    plain = re.sub(r"\s+", "", str(report_name or ""))
    for needle, tone, title, why in RULES:
        if re.sub(r"\s+", "", needle) in plain:
            return tone, title, why
    return PLAIN, str(report_name or "").strip() or "공시", ""


@dataclass
class Filing:
    """DART 공시 한 건."""

    rcept_no: str
    name: str                    # 보고서 이름 (예: 분기보고서 (2025.03))
    filer: str = ""
    day: date | None = None
    corp_name: str = ""

    @property
    def url(self) -> str:
        return VIEWER.format(rcept_no=self.rcept_no)


@dataclass
class Financials:
    """한 회사의 한 보고서에서 뽑아낸 값들. 못 맞춘 항목은 아예 없다."""

    corp_code: str = ""
    year: str = ""
    report: str = ""
    values: dict[str, float] = field(default_factory=dict)   # revenue, net_income …
    prior: dict[str, float] = field(default_factory=dict)    # 전년 동기
    unmatched: list[str] = field(default_factory=list)       # 못 맞춘 계정 이름
    rcept_no: str = ""

    @property
    def empty(self) -> bool:
        return not self.values

    @property
    def url(self) -> str:
        return VIEWER.format(rcept_no=self.rcept_no) if self.rcept_no else ""


def summarize(filing: "Filing", ticker: str) -> dict:
    """화면 목록에 쓸 한 줄. 미국 공시와 **같은 모양**으로 만든다.

    같은 모양이어야 화면이 두 시장을 같은 방식으로 그릴 수 있다.
    보고서 원래 이름은 언제나 함께 남긴다 — 우리가 붙인 제목이 틀렸을 때
    사용자가 알아챌 수 있어야 한다.
    """
    tone, title, why = classify(filing.name)
    return {
        "market": "kr",
        "ticker": ticker,
        "company": filing.corp_name,
        "form": "DART",
        "title": title,
        "tone": tone,
        "why": why,                      # 무엇을 봐야 하는지 (판단 기준)
        "report": filing.name,           # DART 가 붙인 원래 이름
        "items": [],
        "date": filing.day.isoformat() if filing.day else "",
        "when": filing.day.isoformat() if filing.day else "",
        "url": filing.url,
        "index_url": filing.url,
    }


# --------------------------------------------------------------------------
# 응답 해석 (네트워크 없이 시험할 수 있게 따로 뒀다)
# --------------------------------------------------------------------------
def parse_corp_codes(payload: bytes) -> dict[str, str]:
    """corpCode.xml(ZIP) → {여섯자리 종목코드: DART 고유번호}

    상장사만 종목코드를 가진다. 비상장은 종목코드가 비어 있어 여기서 빠진다.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not names:
                return {}
            text = zf.read(names[0]).decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        log.debug("DART 회사 목록을 풀지 못했습니다: %s", exc)
        return {}

    out: dict[str, str] = {}
    for block in re.findall(r"<list>(.*?)</list>", text, re.S):
        corp = re.search(r"<corp_code>\s*(\d+)\s*</corp_code>", block)
        stock = re.search(r"<stock_code>\s*(\d{6})\s*</stock_code>", block)
        if corp and stock:
            out[stock.group(1)] = corp.group(1)
    return out


def parse_filings(payload: dict) -> list[Filing]:
    """list.json → 공시 목록. status 가 정상이 아니면 빈 목록."""
    if str((payload or {}).get("status", "")) != "000":
        return []
    out: list[Filing] = []
    for row in (payload.get("list") or []):
        number = str(row.get("rcept_no") or "").strip()
        if not number:
            continue
        out.append(Filing(
            rcept_no=number,
            name=str(row.get("report_nm") or "").strip(),
            filer=str(row.get("flr_nm") or "").strip(),
            day=_day(row.get("rcept_dt")),
            corp_name=str(row.get("corp_name") or "").strip(),
        ))
    return out


def parse_financials(payload: dict) -> Financials:
    """fnlttSinglAcntAll.json → 우리가 쓰는 항목들.

    **못 맞춘 계정은 버리지 않고 이름만 남긴다.** 무엇을 못 읽었는지 알아야
    나중에 표를 고칠 수 있다.
    """
    found = Financials()
    if str((payload or {}).get("status", "")) != "000":
        return found

    for row in (payload.get("list") or []):
        key = _match(row)
        amount = _amount(row.get("thstrm_amount"))
        if key is None:
            name = str(row.get("account_nm") or "").strip()
            if name and name not in found.unmatched:
                found.unmatched.append(name)
            continue
        if amount is None:
            continue
        # 같은 항목이 연결/별도로 두 번 나오면 먼저 나온 것을 쓴다
        found.values.setdefault(key, amount)
        before = _amount(row.get("frmtrm_amount"))
        if before is not None:
            found.prior.setdefault(key, before)
        if not found.rcept_no:
            found.rcept_no = str(row.get("rcept_no") or "").strip()
        if not found.year:
            found.year = str(row.get("bsns_year") or "").strip()
    return found


def _match(row: dict) -> str | None:
    account_id = str(row.get("account_id") or "").strip()
    if account_id in BY_ACCOUNT_ID:
        return BY_ACCOUNT_ID[account_id]
    name = re.sub(r"\s+", "", str(row.get("account_nm") or ""))
    return BY_ACCOUNT_NAME.get(name)


def _amount(text) -> float | None:
    """'1,234,567' → 1234567.0. 빈 값이나 '-' 는 None."""
    raw = str(text or "").strip().replace(",", "")
    if not raw or raw in ("-", "--"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _day(text) -> date | None:
    raw = str(text or "").strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# 클라이언트
# --------------------------------------------------------------------------
class DartClient:
    """DART 에 물어본다. 열쇠가 없으면 아무것도 하지 않는다."""

    def __init__(self, http, api_key: str = "", cache_dir: str | Path = ".cache") -> None:
        self.http = http
        self.api_key = (api_key or "").strip()
        self.cache_dir = Path(cache_dir)
        self._corp_codes: dict[str, str] | None = None

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    @property
    def blocked_reason(self) -> str:
        return "" if self.ready else KEY_HELP

    # --- 회사 고유번호 ----------------------------------------------------
    def corp_codes(self) -> dict[str, str]:
        """{종목코드: 고유번호}. 파일에 받아두고 일주일에 한 번만 새로 받는다."""
        if self._corp_codes is not None:
            return self._corp_codes
        if not self.ready:
            self._corp_codes = {}
            return self._corp_codes

        cache = self.cache_dir / "dart_corp_codes.json"
        try:
            fresh = time.time() - cache.stat().st_mtime < _CORP_TTL
        except OSError:
            fresh = False
        if fresh:
            try:
                self._corp_codes = json.loads(cache.read_text(encoding="utf-8"))
                return self._corp_codes
            except (OSError, ValueError):
                pass

        try:
            resp = self.http.get(CORP_CODE_URL, params={"crtfc_key": self.api_key},
                                 retries=1, timeout=60)
            resp.raise_for_status()
            found = parse_corp_codes(resp.content)
        except Exception as exc:
            log.warning("DART 회사 목록을 받지 못했습니다: %s", exc)
            found = {}

        if found:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(found), encoding="utf-8")
            except OSError:
                pass
        self._corp_codes = found
        return found

    def corp_code(self, stock_code: str) -> str:
        return self.corp_codes().get(str(stock_code).strip(), "")

    def corp_code_cached(self, stock_code: str) -> str:
        """이미 받아둔 목록에서만 찾는다. **네트워크를 쓰지 않는다.**

        화면을 그리는 중에 불리는 자리라 여기서 물어보면 DART 가 느린 날에
        화면이 그만큼 멈춘다. 아직 못 받았으면 빈 문자열을 주고, 받아오는
        일은 백그라운드에 맡긴다.
        """
        return (self._corp_codes or {}).get(str(stock_code).strip(), "")

    # --- 공시 -------------------------------------------------------------
    def filings(self, corp_code: str, since: date, until: date | None = None) -> list[Filing]:
        if not self.ready or not corp_code:
            return []
        end = until or date.today()
        try:
            payload = self.http.get_json(LIST_URL, params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bgn_de": since.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "page_count": "100",
            }, retries=1, timeout=30)
        except Exception as exc:
            log.debug("DART 공시 조회 실패 %s: %s", corp_code, exc)
            return []
        return parse_filings(payload)

    # --- 재무제표 ---------------------------------------------------------
    def financials(self, corp_code: str, year: int, report: str = "11011",
                   consolidated: bool = True) -> Financials:
        """한 보고서의 재무제표. 연결(CFS)을 먼저, 없으면 별도(OFS)를 본다."""
        if not self.ready or not corp_code:
            return Financials()
        for division in (["CFS", "OFS"] if consolidated else ["OFS"]):
            try:
                payload = self.http.get_json(FINANCE_URL, params={
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": report,
                    "fs_div": division,
                }, retries=1, timeout=30)
            except Exception as exc:
                log.debug("DART 재무제표 조회 실패 %s %s: %s", corp_code, year, exc)
                continue
            found = parse_financials(payload)
            if not found.empty:
                found.corp_code, found.report = corp_code, report
                return found
        return Financials()

    def latest_financials(self, corp_code: str, today: date | None = None) -> Financials:
        """가장 최근에 확정된 재무제표. 사업보고서를 먼저 본다.

        분기 자료는 회사마다 올라오는 시점이 달라서, 확실히 있는 것부터
        거슬러 올라간다. 못 찾으면 빈 것을 돌려준다.
        """
        day = today or date.today()
        for year in (day.year - 1, day.year - 2):
            found = self.financials(corp_code, year, "11011")
            if not found.empty:
                found.year = str(year)
                return found
        return Financials()


__all__ = [
    "DartClient", "Filing", "Financials", "KEY_HELP", "VIEWER", "REPORTS",
    "parse_corp_codes", "parse_filings", "parse_financials",
    "BY_ACCOUNT_ID", "BY_ACCOUNT_NAME",
]
