"""SEC XBRL companyfacts에서 재무 시계열을 뽑아낸다 (무료·무인증)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date
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

    @property
    def days(self) -> int | None:
        return (self.end - self.start).days if self.start else None


class CompanyFacts:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.entity_name: str = data.get("entityName", "")
        self._facts: dict[str, dict] = data.get("facts", {})

    # --- 원자료 접근 ----------------------------------------------------
    def _raw(self, concept: str) -> list[Fact]:
        for taxonomy in ("us-gaap", "ifrs-full", "dei"):
            node = self._facts.get(taxonomy, {}).get(concept)
            if not node:
                continue
            out: list[Fact] = []
            for unit, entries in (node.get("units") or {}).items():
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
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
            if out:
                return out
        return []

    def _first_available(self, key: str) -> list[Fact]:
        for concept in CONCEPTS.get(key, [key]):
            facts = self._raw(concept)
            if facts:
                return facts
        return []

    # --- 기간 데이터 ----------------------------------------------------
    def quarterly(self, key: str, limit: int = 12) -> list[Fact]:
        """분기 값(오래된 순). 10-K만 있는 4분기는 연간-3분기로 역산해 채운다."""
        facts = self._first_available(key)
        if not facts:
            return []

        quarters = _dedupe_by_end(f for f in facts if f.days and 80 <= f.days <= 100)
        annuals = _dedupe_by_end(f for f in facts if f.days and 350 <= f.days <= 380)

        by_end = {f.end: f for f in quarters}
        for annual in annuals:
            if annual.end in by_end or not annual.start:
                continue
            inside = [f for f in quarters if annual.start <= (f.start or f.end) and f.end <= annual.end]
            if len(inside) != 3:
                continue
            by_end[annual.end] = Fact(
                concept=annual.concept + "(Q4역산)",
                val=annual.val - sum(f.val for f in inside),
                end=annual.end,
                start=max(f.end for f in inside),
                form=annual.form,
                fy=annual.fy,
                fp="Q4",
                filed=annual.filed,
                unit=annual.unit,
            )
        ordered = sorted(by_end.values(), key=lambda f: f.end)
        return ordered[-limit:]

    def annual(self, key: str, limit: int = 6) -> list[Fact]:
        facts = self._first_available(key)
        annuals = _dedupe_by_end(f for f in facts if f.days and 350 <= f.days <= 380)
        return sorted(annuals, key=lambda f: f.end)[-limit:]

    def latest_instant(self, key: str) -> Fact | None:
        facts = [f for f in self._first_available(key) if f.start is None]
        if not facts:
            return None
        return max(facts, key=lambda f: (f.end, f.filed or date.min))

    def ttm(self, key: str) -> float | None:
        quarters = self.quarterly(key, limit=4)
        if len(quarters) < 4:
            annuals = self.annual(key, limit=1)
            return annuals[0].val if annuals else None
        return sum(f.val for f in quarters)

    def ttm_prior(self, key: str) -> float | None:
        """직전 연도 같은 기간의 TTM (전년 동기 비교용)."""
        quarters = self.quarterly(key, limit=8)
        if len(quarters) < 8:
            return None
        return sum(f.val for f in quarters[:4])

    def shares_series(self, limit: int = 12) -> list[Fact]:
        """발행주식수 추이(오래된 순).

        적자 기업이 돈을 어떻게 마련했는지가 여기 드러난다. 주식 수가 계속
        늘면 같은 회사를 사고도 내 몫이 줄어든다(희석).
        """
        instants = [f for f in self._first_available("shares") if f.start is None]
        if not instants:
            instants = [f for f in self._first_available("shares") if f.days and 80 <= f.days <= 100]
        if not instants:
            instants = self._raw("EntityCommonStockSharesOutstanding")
        if not instants:
            return []
        return sorted(_dedupe_by_end(instants), key=lambda f: f.end)[-limit:]

    def shares_outstanding(self) -> float | None:
        fact = self.latest_instant("shares")
        if fact:
            return fact.val
        dei = self._raw("EntityCommonStockSharesOutstanding")
        if dei:
            return max(dei, key=lambda f: (f.end, f.filed or date.min)).val
        weighted = self.quarterly("shares", limit=1)
        return weighted[-1].val if weighted else None


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

    def company_facts(self, cik: str) -> CompanyFacts | None:
        cache = self.cache_dir / f"facts_{cik}.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < _CACHE_TTL:
            try:
                return CompanyFacts(json.loads(cache.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
        try:
            # 대형주의 companyfacts 는 수십 MB 라 기본 타임아웃으로는 모자라다.
            # (TSLA·AAPL 같은 종목이 여기서 계속 실패하던 원인)
            data = self.http.get_json(COMPANYFACTS_URL.format(cik=cik), timeout=120)
        except Exception as exc:
            # 여기서 예외를 올리면 한 종목 때문에 나머지 종목까지 멈춘다.
            # None 을 돌려주고, 무엇이 실패했는지는 호출부가 표시한다.
            log.warning("companyfacts 조회 실패 (CIK %s): %s", cik, exc)
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        cache.write_text(json.dumps(data), encoding="utf-8")
        return CompanyFacts(data)
