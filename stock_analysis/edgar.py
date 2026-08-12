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

TICKER_MAP_URLS = [
    "https://www.sec.gov/files/company_tickers.json",
    "https://www.sec.gov/files/company_tickers_exchange.json",
]
# ETF·뮤추얼펀드는 위 목록에 없다. SEC 가 따로 내주는 펀드 목록을 합쳐야
# ETHU·CONL·VOO 같은 티커를 찾을 수 있다.
TICKER_MF_URL = "https://www.sec.gov/files/company_tickers_mf.json"
# 목록 전체를 못 받을 때 티커 하나만 조회하는 최후 수단
BROWSE_EDGAR_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&ticker={ticker}&type=8-K&dateb=&owner=include&count=1&output=atom"
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

    def uid(self) -> str:
        return f"{self.accession}:{self.form}"


class EdgarClient:
    def __init__(self, http: HttpClient, cache_dir: str | Path = ".cache") -> None:
        self.http = http
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ticker_map: dict[str, tuple[str, str]] | None = None
        self._fund_map: dict[str, tuple[str, str]] | None = None
        self._fund_meta: dict[str, tuple[str, str]] = {}   # 티커 → (seriesId, classId)
        self._submissions: dict[str, tuple[float, dict]] = {}

    # --- ETF·펀드 티커 ---------------------------------------------------
    def fund_map(self) -> dict[str, tuple[str, str]]:
        """{TICKER: (CIK, "")} — ETF·뮤추얼펀드 전용 목록.

        이 목록에는 회사명이 없다(펀드는 상품이라 이름이 따로 붙는다).
        이름은 나중에 submissions 에서 채운다.
        """
        if self._fund_map is not None:
            return self._fund_map

        payload = None
        for manual in find_manual_ticker_files(self.cache_dir.parent, fund=True):
            payload = _read_manual_ticker_file(manual)
            if payload is not None:
                log.info("직접 받아둔 ETF 목록을 사용합니다: %s", manual)
                break

        cache = self.cache_dir / "company_tickers_mf.json"
        if payload is None and cache.exists():
            try:
                payload = json.loads(cache.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = None

        if payload is None or time.time() - (cache.stat().st_mtime if cache.exists() else 0) > _TICKER_CACHE_TTL:
            try:
                fetched = self.http.get_json(TICKER_MF_URL)
                if _parse_ticker_payload(fetched):
                    payload = fetched
                    cache.write_text(json.dumps(payload), encoding="utf-8")
            except Exception as exc:
                # 펀드 목록은 있으면 좋은 것일 뿐. 실패해도 일반 종목은 그대로 돌아간다.
                log.info("ETF 티커 목록을 받지 못했습니다(일반 종목은 영향 없음): %s", exc)

        self._fund_map = _parse_ticker_payload(payload or {})
        self._fund_meta = _parse_fund_ids(payload or {})
        if self._fund_map:
            log.info("ETF·펀드 티커 %d개를 함께 봅니다.", len(self._fund_map))
        return self._fund_map

    def is_fund_ticker(self, ticker: str) -> bool:
        try:
            return ticker.upper() in self.fund_map()
        except Exception:
            return False

    def fund_ids(self, ticker: str) -> tuple[str, str]:
        self.fund_map()
        return self._fund_meta.get(ticker.upper(), ("", ""))

    # --- 티커 → CIK ------------------------------------------------------
    def ticker_map(self) -> dict[str, tuple[str, str]]:
        """{TICKER: (10자리 CIK, 회사명)}"""
        if self._ticker_map is not None:
            return self._ticker_map

        payload = None

        # 1) 사용자가 브라우저로 직접 받아 폴더에 둔 파일이 있으면 최우선으로 쓴다.
        #    (파이썬만 SEC 에 막히고 브라우저는 열리는 환경을 위한 탈출구)
        for manual in find_manual_ticker_files(self.cache_dir.parent):
            payload = _read_manual_ticker_file(manual)
            if payload is not None:
                log.info("직접 받아둔 티커 목록을 사용합니다: %s", manual)
                break

        # 2) 최근에 받아둔 캐시
        cache = self.cache_dir / "company_tickers.json"
        if payload is None and cache.exists():
            fresh = time.time() - cache.stat().st_mtime < _TICKER_CACHE_TTL
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached = None
            if cached and fresh:
                payload = cached
            elif cached:
                payload = cached      # 오래됐어도 없는 것보단 낫다 (아래에서 갱신 시도)
                try:
                    payload = self.http.get_json(TICKER_MAP_URLS[0])
                    cache.write_text(json.dumps(payload), encoding="utf-8")
                except Exception as exc:
                    log.warning("티커 목록 갱신 실패, 예전 캐시를 계속 사용합니다: %s", exc)

        # 3) 새로 받기
        if payload is None:
            payload = self._fetch_ticker_payload()
            cache.write_text(json.dumps(payload), encoding="utf-8")

        mapping = _parse_ticker_payload(payload)
        if not mapping:
            raise RuntimeError("SEC 티커 목록을 해석하지 못했습니다.")

        # ETF·펀드 목록을 덧붙인다. 같은 티커가 겹치면 회사 쪽을 우선한다.
        for ticker, entry in self.fund_map().items():
            mapping.setdefault(ticker, entry)

        self._ticker_map = mapping
        return mapping

    def _fetch_ticker_payload(self) -> dict:
        """형식이 다른 두 목록을 순서대로 시도한다."""
        last: Exception | None = None
        for url in TICKER_MAP_URLS:
            try:
                return self.http.get_json(url)
            except Exception as exc:
                last = exc
                log.warning("티커 목록 조회 실패 (%s): %s", url, exc)
        raise last if last else RuntimeError("티커 목록을 받지 못했습니다.")

    def _resolve_one(self, ticker: str) -> tuple[str, str] | None:
        """목록 전체를 못 받았을 때 티커 하나만 EDGAR 검색으로 찾는다."""
        try:
            text = self.http.get_text(BROWSE_EDGAR_URL.format(ticker=ticker.upper()))
        except Exception as exc:
            log.warning("EDGAR 개별 조회 실패 %s: %s", ticker, exc)
            return None
        cik = re.search(r"CIK=(\d{10})", text) or re.search(r"<cik>(\d+)</cik>", text)
        if not cik:
            return None
        name = re.search(r"<conformed-name>([^<]+)</conformed-name>", text)
        return f"{int(cik.group(1)):010d}", (name.group(1).strip() if name else "")

    def resolve(self, ticker: str | None, cik: str | None = None) -> tuple[str, str]:
        """(10자리 CIK, 회사명) 반환."""
        if cik:
            padded = f"{int(re.sub(r'[^0-9]', '', cik)):010d}"
            name = ""
            try:
                # 회사명은 있으면 좋은 정보일 뿐이다. 목록이 막혀도 CIK 만으로 동작해야 한다.
                for _, (mapped_cik, title) in self.ticker_map().items():
                    if mapped_cik == padded:
                        name = title
                        break
            except Exception as exc:
                log.debug("CIK %s 의 회사명을 못 찾았습니다(무시): %s", padded, exc)
            return padded, name
        if not ticker:
            raise ValueError("ticker 또는 cik 중 하나는 필요합니다.")

        try:
            mapping = self.ticker_map()
        except Exception:
            # 목록을 통째로 못 받아도 종목 하나는 살릴 수 있는지 시도해본다
            fund = self.fund_map().get(ticker.upper())
            if fund:
                return fund
            found = self._resolve_one(ticker)
            if found:
                return found
            raise

        try:
            return mapping[ticker.upper()]
        except KeyError:
            found = self._resolve_one(ticker)
            if found:
                return found
            raise ValueError(
                f"'{ticker}' 를 SEC에서 찾지 못했습니다. 미국 상장 종목의 티커가 맞는지 확인해주세요."
            ) from None

    # --- 제출 목록 -------------------------------------------------------
    def submissions(self, cik: str, max_age: float = 600.0) -> dict:
        """공시 목록 원본. 회사 정보(SIC·업종·상장 시장)도 여기 들어 있다.

        같은 주기 안에서 여러 번 부르므로 잠깐 캐시해 SEC 요청을 아낀다.
        """
        cached = self._submissions.get(cik)
        if max_age > 0 and cached and time.time() - cached[0] < max_age:
            return cached[1]
        data = self.http.get_json(SUBMISSIONS_URL.format(cik=cik))
        self._submissions[cik] = (time.time(), data)
        return data

    def recent_filings(
        self,
        cik: str,
        ticker: str,
        forms: list[str],
        since: date,
        limit: int = 60,
    ) -> list[Filing]:
        # 새 공시를 찾는 일에는 캐시를 쓰지 않는다. 여기서 늦으면 알림이 늦는다.
        data = self.submissions(cik, max_age=0)
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


def find_manual_ticker_files(*folders: Path, fund: bool = False) -> list[Path]:
    """직접 저장해둔 티커 목록 파일을 찾는다.

    브라우저로 저장하면 이름이 조금씩 달라진다
    (company_tickers.json.txt, company_tickers (1).json …). 다 받아준다.
    fund=True 면 ETF 목록(company_tickers_mf.json)만 찾는다.
    """
    seen: list[Path] = []
    for folder in (Path("."), *folders):
        try:
            candidates = sorted(folder.glob("company_tickers*"))
        except OSError:
            continue
        for path in candidates:
            is_fund_file = "_mf" in path.name.lower()
            if is_fund_file != fund:
                continue
            if path.is_file() and path.resolve() not in {p.resolve() for p in seen}:
                seen.append(path)
    return seen


def _read_manual_ticker_file(path: Path) -> dict | None:
    """직접 저장한 파일을 읽는다. 잘못 저장했으면 무엇이 문제인지 알려준다."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError as exc:
        log.warning("%s 를 열지 못했습니다: %s", path, exc)
        return None

    if not text:
        log.warning("%s 가 비어 있습니다.", path)
        return None

    if text[:1] in "<":
        log.error(
            "%s 는 JSON 이 아니라 웹페이지(HTML)로 저장됐습니다.\n"
            "  브라우저에서 저장할 때 파일 형식을 '모든 파일' 로 두고 "
            "company_tickers.json 으로 저장해주세요.",
            path,
        )
        return None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        log.error("%s 를 해석하지 못했습니다(%s). 파일을 다시 저장해주세요.", path, exc)
        return None

    if not isinstance(payload, dict) or not _parse_ticker_payload(payload):
        log.error("%s 안에서 티커 정보를 찾지 못했습니다. 다른 페이지를 저장한 것 같습니다.", path)
        return None
    return payload


def _parse_ticker_payload(payload: dict) -> dict[str, tuple[str, str]]:
    """SEC 의 두 가지 티커 목록 형식을 모두 받아준다."""
    mapping: dict[str, tuple[str, str]] = {}

    # 형식 A: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    if isinstance(payload, dict) and "data" not in payload:
        for entry in payload.values():
            if not isinstance(entry, dict) or "ticker" not in entry:
                continue
            try:
                mapping[str(entry["ticker"]).upper()] = (
                    f"{int(entry['cik_str']):010d}",
                    entry.get("title", ""),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return mapping

    # 형식 B: {"fields": ["cik","name","ticker","exchange"], "data": [[...], ...]}
    # 형식 C: {"fields": ["cik","seriesId","classId","symbol"], ...}  ← ETF·펀드 목록
    fields = [str(f).lower() for f in payload.get("fields", [])]
    ticker_key = "ticker" if "ticker" in fields else ("symbol" if "symbol" in fields else None)
    if ticker_key is None or "cik" not in fields:
        return mapping
    cik_at, ticker_at = fields.index("cik"), fields.index(ticker_key)
    name_at = fields.index("name") if "name" in fields else None
    for row in payload.get("data", []):
        try:
            ticker = str(row[ticker_at]).strip().upper()
            if not ticker:
                continue
            mapping[ticker] = (
                f"{int(row[cik_at]):010d}",
                str(row[name_at]) if name_at is not None else "",
            )
        except (IndexError, TypeError, ValueError):
            continue
    return mapping


def _parse_fund_ids(payload: dict) -> dict[str, tuple[str, str]]:
    """ETF 목록에서 {티커: (seriesId, classId)} 를 뽑는다.

    이 두 값이 있으면 SEC 에서 그 상품 하나만 걸러 볼 수 있다.
    """
    fields = [str(f).lower() for f in (payload or {}).get("fields", [])]
    if "symbol" not in fields:
        return {}
    symbol_at = fields.index("symbol")
    series_at = fields.index("seriesid") if "seriesid" in fields else None
    class_at = fields.index("classid") if "classid" in fields else None

    out: dict[str, tuple[str, str]] = {}
    for row in payload.get("data", []):
        try:
            ticker = str(row[symbol_at]).strip().upper()
            if not ticker:
                continue
            out[ticker] = (
                str(row[series_at]) if series_at is not None else "",
                str(row[class_at]) if class_at is not None else "",
            )
        except (IndexError, TypeError, ValueError):
            continue
    return out


def _get(recent: dict, key: str, idx: int) -> str:
    values = recent.get(key)
    if not values or idx >= len(values):
        return ""
    return str(values[idx] or "")


def default_since(lookback_days: int) -> date:
    return (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
