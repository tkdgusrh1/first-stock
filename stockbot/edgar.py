"""SEC EDGAR 조회: 티커→CIK, 제출 목록, 8-K 항목, Form 4 상세."""

from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .http import HttpClient

log = logging.getLogger(__name__)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"
FILING_INDEX_URL = ARCHIVE_BASE + "/{cik_int}/{acc_nodash}/{acc_dash}-index.htm"

_TICKER_CACHE_TTL = 24 * 3600

# 8-K 항목 코드 → 한글 설명. 투자 판단에 중요한 항목은 messages.py 에서 강조한다.
ITEM_8K: dict[str, str] = {
    "1.01": "주요 계약 체결",
    "1.02": "주요 계약 해지",
    "1.03": "파산/법정관리",
    "1.05": "사이버 보안 사고",
    "2.01": "자산 인수/매각 완료",
    "2.02": "실적 발표 (매출·이익·가이던스)",
    "2.03": "채무 발생/차입",
    "2.04": "채무 조기상환 의무 발생",
    "2.05": "구조조정 비용 확정",
    "2.06": "자산 손상차손",
    "3.01": "상장폐지/상장규정 위반",
    "3.02": "지분 비공개 매각(희석)",
    "3.03": "주주 권리 변경",
    "4.01": "회계법인 교체",
    "4.02": "과거 재무제표 신뢰 불가 (재작성)",
    "5.01": "경영권 변경",
    "5.02": "임원·이사 선임/사임",
    "5.03": "정관 변경/회계연도 변경",
    "5.07": "주주총회 표결 결과",
    "7.01": "Reg FD 공개 (가이던스·IR 자료)",
    "8.01": "기타 주요 사항",
    "9.01": "재무제표 및 첨부자료",
}

# 메모 우선순위(가이던스 > 어닝 서프라이즈 > 마진)와 직결되거나 주가에 즉각 영향이 큰 항목
CRITICAL_8K_ITEMS = {"2.02", "7.01", "4.02", "1.03", "3.01", "5.01", "2.06", "1.05", "2.05"}

# Form 4 거래 코드
FORM4_CODES: dict[str, str] = {
    "P": "공개시장 매수",
    "S": "공개시장 매도",
    "A": "무상 취득(RSU/보상)",
    "D": "처분(회사에 반환)",
    "F": "세금 납부용 주식 반납",
    "M": "옵션 행사",
    "G": "증여",
    "C": "전환",
    "X": "옵션 행사(만기)",
    "J": "기타",
    "V": "자발적 조기 보고",
}


@dataclass
class Filing:
    cik: str
    ticker: str
    company: str
    form: str
    accession: str
    filing_date: str
    accepted: str | None
    report_date: str | None
    primary_doc: str
    items: list[str] = field(default_factory=list)
    # Form 4 파싱 결과
    insider: str | None = None
    insider_title: str | None = None
    transactions: list[dict] = field(default_factory=list)

    @property
    def acc_nodash(self) -> str:
        return self.accession.replace("-", "")

    @property
    def doc_url(self) -> str:
        cik_int = str(int(self.cik))
        if self.primary_doc:
            return f"{ARCHIVE_BASE}/{cik_int}/{self.acc_nodash}/{self.primary_doc}"
        return self.index_url

    @property
    def index_url(self) -> str:
        return FILING_INDEX_URL.format(
            cik_int=int(self.cik), acc_nodash=self.acc_nodash, acc_dash=self.accession
        )

    @property
    def accepted_dt(self) -> datetime | None:
        if not self.accepted:
            return None
        try:
            return datetime.fromisoformat(self.accepted.replace("Z", "+00:00"))
        except ValueError:
            return None

    def uid(self) -> str:
        return f"{self.accession}:{self.form}"


class EdgarClient:
    def __init__(self, http: HttpClient, cache_dir: str | Path = ".cache") -> None:
        self.http = http
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ticker_map: dict[str, tuple[str, str]] | None = None

    # --- 티커 → CIK ------------------------------------------------------
    def ticker_map(self) -> dict[str, tuple[str, str]]:
        """{TICKER: (10자리 CIK, 회사명)}"""
        if self._ticker_map is not None:
            return self._ticker_map

        cache = self.cache_dir / "company_tickers.json"
        payload = None
        if cache.exists() and time.time() - cache.stat().st_mtime < _TICKER_CACHE_TTL:
            try:
                payload = json.loads(cache.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = None
        if payload is None:
            payload = self.http.get_json(TICKER_MAP_URL)
            cache.write_text(json.dumps(payload), encoding="utf-8")

        mapping: dict[str, tuple[str, str]] = {}
        for entry in payload.values():
            ticker = str(entry["ticker"]).upper()
            mapping[ticker] = (f"{int(entry['cik_str']):010d}", entry.get("title", ""))
        self._ticker_map = mapping
        return mapping

    def resolve(self, ticker: str | None, cik: str | None = None) -> tuple[str, str]:
        """(10자리 CIK, 회사명) 반환."""
        if cik:
            padded = f"{int(re.sub(r'[^0-9]', '', cik)):010d}"
            name = ""
            for tkr, (mapped_cik, title) in self.ticker_map().items():
                if mapped_cik == padded:
                    name = title
                    break
            return padded, name
        if not ticker:
            raise ValueError("ticker 또는 cik 중 하나는 필요합니다.")
        try:
            return self.ticker_map()[ticker.upper()]
        except KeyError:
            raise ValueError(f"SEC 티커 목록에서 '{ticker}' 를 찾지 못했습니다. cik 를 직접 지정해주세요.") from None

    # --- 제출 목록 -------------------------------------------------------
    def recent_filings(
        self,
        cik: str,
        ticker: str,
        forms: list[str],
        since: date,
        limit: int = 60,
    ) -> list[Filing]:
        data = self.http.get_json(SUBMISSIONS_URL.format(cik=cik))
        company = data.get("name", "")
        recent = data.get("filings", {}).get("recent", {})
        wanted = {f.upper() for f in forms}

        out: list[Filing] = []
        count = len(recent.get("accessionNumber", []))
        for i in range(count):
            form = str(recent["form"][i]).upper()
            if wanted and form not in wanted:
                continue
            filing_date = recent["filingDate"][i]
            try:
                if date.fromisoformat(filing_date) < since:
                    break  # 최신순 정렬이므로 더 볼 필요 없음
            except ValueError:
                continue
            out.append(
                Filing(
                    cik=cik,
                    ticker=ticker,
                    company=company,
                    form=form,
                    accession=recent["accessionNumber"][i],
                    filing_date=filing_date,
                    accepted=_get(recent, "acceptanceDateTime", i),
                    report_date=_get(recent, "reportDate", i) or None,
                    primary_doc=_get(recent, "primaryDocument", i) or "",
                    items=[s.strip() for s in (_get(recent, "items", i) or "").split(",") if s.strip()],
                )
            )
            if len(out) >= limit:
                break
        return out

    # --- Form 4 상세 -----------------------------------------------------
    def enrich_form4(self, filing: Filing) -> Filing:
        """Form 4 XML을 파싱해 내부자 이름과 매수/매도 내역을 채운다."""
        try:
            xml_text = self._fetch_form4_xml(filing)
            if xml_text:
                parse_form4(xml_text, filing)
        except Exception as exc:  # 파싱 실패해도 알림 자체는 나가야 한다
            log.warning("Form 4 파싱 실패 (%s): %s", filing.accession, exc)
        return filing

    def _fetch_form4_xml(self, filing: Filing) -> str | None:
        candidates: list[str] = []
        doc = filing.primary_doc or ""
        if doc.lower().endswith(".xml"):
            # 'xslF345X03/wf-form4_123.xml' → 원본 XML은 접두 디렉터리를 뗀 경로
            candidates.append(doc.split("/")[-1])
            candidates.append(doc)
        base = f"{ARCHIVE_BASE}/{int(filing.cik)}/{filing.acc_nodash}"
        for name in candidates:
            resp = self.http.get(f"{base}/{name}")
            if resp.status_code == 200 and "<ownershipDocument" in resp.text:
                return resp.text
        # 최후 수단: 폴더 목록에서 xml 파일을 찾는다
        resp = self.http.get(f"{base}/")
        if resp.status_code == 200:
            for name in re.findall(r'href="[^"]*?/([^"/]+\.xml)"', resp.text):
                if name.startswith("xsl"):
                    continue
                doc_resp = self.http.get(f"{base}/{name}")
                if doc_resp.status_code == 200 and "<ownershipDocument" in doc_resp.text:
                    return doc_resp.text
        return None


def parse_form4(xml_text: str, filing: Filing) -> Filing:
    """Form 4 XML → 내부자/거래 내역. (네트워크 없이 테스트 가능하도록 분리)"""
    root = ET.fromstring(xml_text)

    owner = root.find("reportingOwner")
    if owner is not None:
        name = owner.findtext("reportingOwnerId/rptOwnerName")
        filing.insider = (name or "").strip() or None
        rel = owner.find("reportingOwnerRelationship")
        if rel is not None:
            titles = []
            if rel.findtext("isDirector") in ("1", "true"):
                titles.append("이사")
            if rel.findtext("isOfficer") in ("1", "true"):
                titles.append((rel.findtext("officerTitle") or "임원").strip())
            if rel.findtext("isTenPercentOwner") in ("1", "true"):
                titles.append("10% 이상 주주")
            filing.insider_title = ", ".join(t for t in titles if t) or None

    for node in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        tx = _parse_tx(node, derivative=False)
        if tx:
            filing.transactions.append(tx)
    for node in root.findall("derivativeTable/derivativeTransaction"):
        tx = _parse_tx(node, derivative=True)
        if tx:
            filing.transactions.append(tx)
    return filing


def _parse_tx(node: ET.Element, derivative: bool) -> dict | None:
    code = _val(node, "transactionCoding/transactionCode")
    shares = _num(_val(node, "transactionAmounts/transactionShares/value"))
    price = _num(_val(node, "transactionAmounts/transactionPricePerShare/value"))
    acq_disp = _val(node, "transactionAmounts/transactionAcquiredDisposedCode/value")
    after = _num(_val(node, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))
    if code is None and shares is None:
        return None
    return {
        "code": code,
        "code_label": FORM4_CODES.get(code or "", code or "?"),
        "security": _val(node, "securityTitle/value"),
        "date": _val(node, "transactionDate/value"),
        "shares": shares,
        "price": price,
        "value": (shares * price) if (shares is not None and price) else None,
        "direction": "취득" if acq_disp == "A" else ("처분" if acq_disp == "D" else None),
        "shares_after": after,
        "derivative": derivative,
    }


def _val(node: ET.Element, path: str) -> str | None:
    text = node.findtext(path)
    if text is None:
        # <value> 래핑이 없는 변형 처리
        text = node.findtext(path.rsplit("/value", 1)[0]) if path.endswith("/value") else None
    return text.strip() if text else None


def _num(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _get(recent: dict, key: str, idx: int) -> str:
    values = recent.get(key)
    if not values or idx >= len(values):
        return ""
    return str(values[idx] or "")


def default_since(lookback_days: int) -> date:
    return (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
