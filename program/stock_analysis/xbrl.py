"""SEC XBRL companyfacts에서 재무 시계열을 뽑아낸다 (무료·무인증)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .http import HttpClient

log = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_CACHE_TTL = 6 * 3600

# 기업마다 쓰는 태그가 달라서 후보를 순서대로 시도한다.
CONCEPTS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "operating_income": ["OperatingIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"],
    "tax_expense": ["IncomeTaxExpenseBenefit"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                               "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "debt_lt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "debt_st": ["LongTermDebtCurrent", "ShortTermBorrowings", "DebtCurrent"],
    "shares": ["CommonStockSharesOutstanding", "WeightedAverageNumberOfDilutedSharesOutstanding",
               "WeightedAverageNumberOfSharesOutstandingBasic"],
}


_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

UNITS = {"USD", "USD/shares", "shares"}
NOT_ADDITIVE = {"shares"}            # 가중평균 주식수는 빼서 분기를 만들 수 없다


@dataclass(frozen=True)
class Fact:
    concept: str
    val: float
    end: date
    start: date | None
    form: str
    fy: int | None
    fp: str | None
    filed: date | None
    unit: str
    accn: str = ""            # 이 값을 담아 낸 공시의 접수번호

    @property
    def days(self) -> int | None:
        return (self.end - self.start).days if self.start else None

    def filing_url(self, cik: str | int) -> str:
        """이 숫자가 실제로 적힌 SEC 공시 주소.

        '이 값이 진짜인가' 를 확인하는 유일한 방법은 원문을 여는 것이다.
        접수번호가 없으면 빈 문자열 — 없는 링크를 지어내지 않는다.

        회사 CIK 를 받아서 쓴다. 접수번호 앞자리를 CIK 로 쓰면 안 된다 —
        그건 제출을 대행한 쪽의 번호일 수 있어서 주소가 어긋난다.
        """
        number = str(cik).lstrip("0")
        if not self.accn or not number:
            return ""
        plain = self.accn.replace("-", "")
        return f"{_ARCHIVE}/{number}/{plain}/{self.accn}-index.htm"


class CompanyFacts:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.entity_name: str = data.get("entityName", "")
        self.cik: str = str(data.get("cik", "") or "")
        self._facts: dict[str, dict] = data.get("facts", {})
        self.other_units: set[str] = set()

    # --- 원자료 접근 ----------------------------------------------------
    def _raw(self, concept: str) -> list[Fact]:
        for taxonomy in ("us-gaap", "ifrs-full", "dei"):
            node = self._facts.get(taxonomy, {}).get(concept)
            if not node:
                continue
            out: list[Fact] = []
            for unit, entries in (node.get("units") or {}).items():
                # 달러·주당달러·주식수만 쓴다. 위안·유로로 보고하는 회사의 숫자를
                # 달러로 표시하면 크기가 몇 배씩 틀린다 — 그런 값은 비워두고 알린다.
                if unit not in UNITS:
                    self.other_units.add(unit)
                    continue
                for entry in entries:
                    try:
                        out.append(
                            Fact(
                                concept=concept,
                                val=float(entry["val"]),
                                end=date.fromisoformat(entry["end"]),
                                start=date.fromisoformat(entry["start"]) if entry.get("start") else None,
                                form=str(entry.get("form", "")),
                                fy=entry.get("fy"),
                                fp=entry.get("fp"),
                                filed=date.fromisoformat(entry["filed"]) if entry.get("filed") else None,
                                unit=unit,
                                accn=str(entry.get("accn", "")),
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
            if out:
                return out
        return []

    @property
    def foreign_currency(self) -> list[str]:
        """달러가 아닌 통화로 보고된 항목이 있으면 그 통화들(경고용)."""
        for concept in CONCEPTS["revenue"] + CONCEPTS["net_income"]:
            self._raw(concept)
        return sorted(u for u in self.other_units if "/" not in u and u != "pure")

    def _pick(self, key: str, keep) -> list[Fact]:
        """후보 항목 중 **가장 최근 기간까지 있는** 것의 값들.

        회사가 항목 이름을 바꾸면 옛 이름에도 몇 년 전 값이 남아 있다. 목록 순서만
        보고 고르면 3년 전에 멈춘 숫자를 '최근' 으로 쓰게 된다. 끝나는 날이 같으면
        목록 앞쪽(더 정확한 항목)을 쓴다.
        """
        best: list[Fact] = []
        best_end = date.min
        for concept in CONCEPTS.get(key, [key]):
            facts = [f for f in self._raw(concept) if keep(f)]
            if not facts:
                continue
            end = max(f.end for f in facts)
            if end > best_end:
                best, best_end = facts, end
        return best

    # --- 기간 데이터 ----------------------------------------------------
    def quarterly(self, key: str, limit: int = 12) -> list[Fact]:
        """분기 값(오래된 순).

        10-Q 의 현금흐름표는 3개월이 아니라 **연초부터 누적**(3·6·9개월)으로만 나온다.
        그래서 3개월짜리가 없는 분기는 같은 해 누적끼리 빼서 만든다
        (2분기 = 6개월 − 3개월, 4분기 = 12개월 − 9개월). 그래도 없으면
        연간 − 같은 해 3개 분기로 4분기를 채운다.
        """
        facts = self._pick(key, lambda f: f.days is not None)
        if not facts:
            return []

        quarters = _dedupe_by_end(f for f in facts if 80 <= f.days <= 100)
        by_end = {f.end: f for f in quarters}
        if key in NOT_ADDITIVE:
            return sorted(by_end.values(), key=lambda f: f.end)[-limit:]

        # 같은 날 시작한 누적값끼리 차례로 뺀다.
        runs: dict[date, dict[date, Fact]] = {}
        for f in _dedupe_by_span(f for f in facts if 80 <= f.days <= 380):
            runs.setdefault(f.start, {})[f.end] = f
        for run in runs.values():
            ends = sorted(run)
            for before, after in zip(ends, ends[1:]):
                if after in by_end or not 80 <= (after - before).days <= 100:
                    continue
                later = run[after]
                by_end[after] = Fact(
                    concept=later.concept + "(누적차감)",
                    val=later.val - run[before].val,
                    end=after,
                    start=before + timedelta(days=1),
                    form=later.form, fy=later.fy, fp=later.fp,
                    filed=later.filed, unit=later.unit, accn=later.accn,
                )

        annuals = _dedupe_by_end(f for f in facts if 350 <= f.days <= 380)
        for annual in annuals:
            if annual.end in by_end or not annual.start:
                continue
            inside = [f for f in by_end.values()
                      if annual.start <= (f.start or f.end) and f.end <= annual.end and f.days and f.days <= 100]
            if len(inside) != 3:
                continue
            by_end[annual.end] = Fact(
                concept=annual.concept + "(Q4역산)",
                val=annual.val - sum(f.val for f in inside),
                end=annual.end,
                start=max(f.end for f in inside) + timedelta(days=1),
                form=annual.form,
                fy=annual.fy,
                fp="Q4",
                filed=annual.filed,
                unit=annual.unit,
                accn=annual.accn,
            )
        ordered = sorted(by_end.values(), key=lambda f: f.end)
        return ordered[-limit:]

    def first_filed(self, key: str) -> dict[date, date]:
        """{기간 종료일: 그 기간 숫자가 처음 공개된 날}. 나중의 정정·비교 칸은 무시한다."""
        out: dict[date, date] = {}
        for f in self._pick(key, lambda f: f.days is not None and f.filed is not None):
            if f.end not in out or f.filed < out[f.end]:
                out[f.end] = f.filed
        return out

    def last_quarters(self, key: str, count: int = 4) -> list[Fact]:
        """빈틈 없이 이어진 최근 count 개 분기. 중간에 빠진 분기가 있으면 [].

        분기 하나가 빠진 채 4개를 더하면 15개월치나 9개월치가 '최근 1년' 이 된다.
        """
        quarters = self.quarterly(key, limit=count)
        if len(quarters) < count or not consecutive(quarters):
            return []
        return quarters

    def annual(self, key: str, limit: int = 6) -> list[Fact]:
        facts = self._pick(key, lambda f: f.days is not None and 350 <= f.days <= 380)
        return sorted(_dedupe_by_end(facts), key=lambda f: f.end)[-limit:]

    def latest_instant(self, key: str) -> Fact | None:
        facts = self._pick(key, lambda f: f.start is None)
        if not facts:
            return None
        return max(facts, key=lambda f: (f.end, f.filed or date.min))

    def ttm(self, key: str) -> float | None:
        quarters = self.last_quarters(key, 4)
        if quarters:
            return sum(f.val for f in quarters)
        annuals = self.annual(key, limit=1)
        return annuals[0].val if annuals else None

    def ttm_prior(self, key: str) -> float | None:
        """직전 연도 같은 기간의 TTM (전년 동기 비교용). 8개 분기가 이어져야 한다."""
        quarters = self.last_quarters(key, 8)
        return sum(f.val for f in quarters[:4]) if quarters else None

    def shares_series(self, limit: int = 12) -> list[Fact]:
        """발행주식수 추이(오래된 순).

        적자 기업이 돈을 어떻게 마련했는지가 여기 드러난다. 주식 수가 계속
        늘면 같은 회사를 사고도 내 몫이 줄어든다(희석).
        """
        instants = self._pick("shares", lambda f: f.start is None)
        if not instants:
            instants = self.quarterly("shares", limit=limit)
        if not instants:
            instants = self._raw("EntityCommonStockSharesOutstanding")
        if not instants:
            return []
        return sorted(_dedupe_by_end(instants), key=lambda f: f.end)[-limit:]

    def shares_outstanding(self) -> float | None:
        """가장 최근 날짜의 발행주식수. 재무제표 값과 표지(dei) 값 중 더 최근 것."""
        found = [f for f in (self.latest_instant("shares"),) if f]
        found += self._raw("EntityCommonStockSharesOutstanding")
        if found:
            return max(found, key=lambda f: (f.end, f.filed or date.min)).val
        weighted = self.quarterly("shares", limit=1)
        return weighted[-1].val if weighted else None


def consecutive(quarters: list[Fact]) -> bool:
    """분기들이 빠짐없이 이어져 있는가(분기말 간격 70~110일)."""
    return all(70 <= (b.end - a.end).days <= 110 for a, b in zip(quarters, quarters[1:]))


def _dedupe_by_span(facts) -> list[Fact]:
    """같은 (시작, 종료) 기간이 여러 번 보고되면 가장 나중에 제출된 값."""
    best: dict[tuple, Fact] = {}
    for fact in facts:
        span = (fact.start, fact.end)
        current = best.get(span)
        if current is None or (fact.filed or date.min) > (current.filed or date.min):
            best[span] = fact
    return list(best.values())


def _dedupe_by_end(facts) -> list[Fact]:
    """같은 종료일이 여러 번 보고되면(원본/수정본) 가장 나중에 제출된 값을 쓴다."""
    best: dict[date, Fact] = {}
    for fact in facts:
        current = best.get(fact.end)
        if current is None or (fact.filed or date.min) > (current.filed or date.min):
            best[fact.end] = fact
    return list(best.values())


class XbrlClient:
    def __init__(self, http: HttpClient, cache_dir: str | Path = ".cache") -> None:
        self.http = http
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_error: str | None = None

    def forget(self, cik: str) -> bool:
        """저장해둔 재무 원자료를 지운다.

        추천 후보를 훑을 때 쓴다. 회사 하나의 원자료가 수 MB~수십 MB 라
        후보 250개를 그대로 쌓아두면 남의 컴퓨터에 1GB 넘게 남는다.
        계산이 끝난 뒤 숫자만 남기고 원자료는 버린다. 감시 목록 종목은
        자주 다시 보므로 여기서 지우지 않는다.
        """
        try:
            (self.cache_dir / f"facts_{cik}.json").unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def company_facts(self, cik: str, max_age: float = _CACHE_TTL) -> CompanyFacts | None:
        """재무 원자료. max_age=0 이면 저장해둔 것을 무시하고 새로 받는다.

        화면의 '지표' 버튼처럼 사용자가 직접 새로고침을 누른 경우에만 0 을 쓴다.
        """
        cache = self.cache_dir / f"facts_{cik}.json"
        saved = None
        if cache.exists():
            try:
                saved = CompanyFacts(json.loads(cache.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                saved = None
        if saved is not None and max_age > 0 and time.time() - cache.stat().st_mtime < max_age:
            return saved

        try:
            # 대형주의 companyfacts 는 수십 MB 라 기본 타임아웃으로는 모자라다.
            # (TSLA·AAPL 같은 종목이 여기서 계속 실패하던 원인)
            data = self.http.get_json(COMPANYFACTS_URL.format(cik=cik), timeout=120)
        except Exception as exc:
            # 여기서 예외를 올리면 한 종목 때문에 나머지 종목까지 멈춘다.
            # None 을 돌려주고, 무엇이 실패했는지는 호출부가 표시한다.
            log.warning("companyfacts 조회 실패 (CIK %s): %s", cik, exc)
            self.last_error = f"{type(exc).__name__}: {exc}"
            # 새로고침이 실패했다고 이미 있던 숫자까지 지우지는 않는다.
            return saved
        cache.write_text(json.dumps(data), encoding="utf-8")
        return CompanyFacts(data)
