"""미국 재무 — 퀀트 바구니(수백 회사)의 '그 날 알 수 있었던' 숫자.

화면용 재무(xbrl.CompanyFacts)는 회사마다 companyfacts 전체(대형주는 수십 MB)를 받는다. 바구니 전체를
그렇게 받을 수는 없어서, 필요한 항목만 SEC companyconcept 로 따로 받아(항목당 수십 KB) 작게 저장한다.

받는 항목: 매출 · 매출총이익(없는 회사는 매출원가) · 순이익 · 총자산 · 자본 · 발행주식수.
항목마다 회사가 쓰는 태그가 해마다 바뀐다(예: 2018년 회계기준 변경 뒤 매출 태그, 브로드컴은 2020년부터 자본을
'비지배지분 포함' 태그로만 낸다). 그래서 후보 태그를 받아 두고, 계산할 때 '가장 최근까지 있는 태그 + 그 앞
빈칸은 옛 태그' 로 잇는다.
값마다 제출일(filed)이 붙어 있어 '처음 알게 된 날' 부터만 쓴다(fundamentals.py 와 같은 원칙).

받지 못한 항목은 비워 둔다. 다른 항목으로 메우지 않는다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

from ..xbrl import Fact

log = logging.getLogger(__name__)

CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{tag}.json"

ITEMS: dict[str, tuple[tuple[str, str], ...]] = {
    "revenue": (("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"), ("us-gaap", "Revenues"),
                ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"), ("us-gaap", "SalesRevenueNet"),
                ("us-gaap", "RevenuesNetOfInterestExpense")),                 # 마지막은 은행·증권
    "gross_profit": (("us-gaap", "GrossProfit"),),
    "cogs": (("us-gaap", "CostOfRevenue"), ("us-gaap", "CostOfGoodsAndServicesSold"),
             ("us-gaap", "CostOfGoodsSold")),
    "net_income": (("us-gaap", "NetIncomeLoss"), ("us-gaap", "ProfitLoss")),
    "assets": (("us-gaap", "Assets"),),
    "equity": (("us-gaap", "StockholdersEquity"),
               ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")),
    "shares": (("dei", "EntityCommonStockSharesOutstanding"),
               ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding")),
}
# 앞 태그로 충분하면 뒤 태그는 묻지 않는 항목(뒤 태그는 뜻이 조금 다른 대신 값 — 비지배지분 포함 등).
# 앞 태그가 지금은 안 쓰이거나 옛날까지 거슬러 있지 않으면 뒤 태그도 받아, 앞 태그가 없는 기간만 채운다.
FIRST_ONLY = {"net_income", "equity", "shares"}
STALE_DAYS = 200                 # 앞 태그의 마지막 분기(시점) 값이 이보다 오래됐으면 회사가 태그를 바꾼 것
HISTORY_YEARS = 12               # 앞 태그가 이만큼 옛날부터 있어야 백테스트 기간을 다 덮는다
UNITS = {"USD", "shares"}

FRESH = 7 * 24 * 3600            # 일주일 안에 받은 회사는 다시 묻지 않는다
NEXT_FILING = 135                # 최근 기간 끝 + 이만큼 지났으면 다음 분기 보고서가 나왔을 것 — 다시 받는다
MAX_AGE = 120 * 24 * 3600        # 그래도 넉 달에 한 번은 다시


def parse_concept(payload: dict, tag: str) -> list[Fact]:
    """companyconcept 응답 → Fact 목록(달러·주식 단위만, 제출일 있는 것만)."""
    out = []
    for unit, entries in ((payload or {}).get("units") or {}).items():
        if unit not in UNITS:
            continue
        for e in entries or []:
            try:
                out.append(Fact(concept=tag, val=float(e["val"]), end=date.fromisoformat(e["end"]),
                                start=date.fromisoformat(e["start"]) if e.get("start") else None,
                                form=str(e.get("form", "")), fy=e.get("fy"), fp=e.get("fp"),
                                filed=date.fromisoformat(e["filed"]) if e.get("filed") else None,
                                unit=unit, accn=str(e.get("accn", ""))))
            except (KeyError, TypeError, ValueError):
                continue
    return [f for f in out if f.filed is not None]


def covers(facts: list[Fact], today: date) -> bool:
    """이 태그 하나로 충분한가 — 지금도 쓰고 있고, 백테스트 처음 무렵부터 있다(분기·시점 값 기준)."""
    ends = [f.end for f in facts if f.start is None or 80 <= (f.end - f.start).days <= 120]
    return bool(ends) and (today - max(ends)).days <= STALE_DAYS and min(ends).year <= today.year - HISTORY_YEARS


def pack(facts: list[Fact]) -> str:
    return json.dumps([[f.start.toordinal() if f.start else 0, f.end.toordinal(), f.val,
                        f.filed.toordinal(), f.form] for f in facts], separators=(",", ":"))


def unpack(text: str, tag: str, unit: str = "USD") -> list[Fact]:
    out = []
    for start, end, val, filed, form in json.loads(text or "[]"):
        out.append(Fact(concept=tag, val=float(val), end=date.fromordinal(end),
                        start=date.fromordinal(start) if start else None, form=form, fy=None, fp=None,
                        filed=date.fromordinal(filed), unit=unit))
    return out


class FactsDB:
    """{CIK: {항목: {태그: [Fact]}}} 를 SQLite 에 작게. 여러 스레드가 같이 써도 되게 잠금."""

    def __init__(self, path: str | Path, http=None) -> None:
        self.path = Path(path)
        self.http = http
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(
                "CREATE TABLE IF NOT EXISTS facts (cik INTEGER, item TEXT, tag TEXT, data TEXT,"
                " PRIMARY KEY (cik, item, tag)) WITHOUT ROWID;"
                "CREATE TABLE IF NOT EXISTS seen (cik INTEGER PRIMARY KEY, fetched REAL, newest INTEGER, note TEXT);")
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def company(self, cik: int) -> dict[str, dict[str, list[Fact]]]:
        with self.lock:
            rows = self.db.execute("SELECT item, tag, data FROM facts WHERE cik=?", (int(cik),)).fetchall()
        out: dict[str, dict[str, list[Fact]]] = {}
        for item, tag, data in rows:
            out.setdefault(item, {})[tag] = unpack(data, tag, "shares" if item == "shares" else "USD")
        return out

    def seen(self, cik: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT fetched, newest, note FROM seen WHERE cik=?", (int(cik),)).fetchone()
        return {"fetched": row[0] or 0.0, "newest": row[1], "note": row[2] or ""} if row else None

    def due(self, cik: int, today: date) -> bool:
        s = self.seen(cik)
        if not s:
            return True
        age = time.time() - s["fetched"]
        if age < FRESH:
            return False
        if age > MAX_AGE or not s["newest"]:
            return True
        return (today - date.fromordinal(s["newest"])).days > NEXT_FILING

    def _get(self, cik: int, taxonomy: str, tag: str) -> list[Fact] | None:
        """한 태그. 없으면(404) 빈 목록, 받다 실패하면 None."""
        url = CONCEPT_URL.format(cik=int(cik), taxonomy=taxonomy, tag=tag)
        try:
            resp = self.http.get(url, retries=2, timeout=60)
        except Exception as exc:
            log.debug("companyconcept 실패 %s %s: %s", cik, tag, exc)
            return None
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            return None
        try:
            return parse_concept(resp.json(), tag)
        except ValueError:
            return None

    def fetch(self, cik: int, today: date | None = None) -> bool:
        """한 회사의 항목을 모두 받아 저장한다. 하나라도 받다 실패하면 저장하지 않고 False(다음에 다시)."""
        today = today or date.today()
        got: dict[tuple[str, str], list[Fact]] = {}
        for item, tags in ITEMS.items():
            if item == "cogs" and any(got.get(("gross_profit", t)) for _, t in ITEMS["gross_profit"]):
                continue                        # 매출총이익을 직접 내는 회사는 매출원가가 필요 없다
            for taxonomy, tag in tags:
                facts = self._get(cik, taxonomy, tag)
                if facts is None:
                    return False
                got[(item, tag)] = facts
                if item in FIRST_ONLY and covers(facts, today):
                    break
        newest = max((f.end for facts in got.values() for f in facts), default=None)
        with self.lock:
            self.db.execute("DELETE FROM facts WHERE cik=?", (int(cik),))
            self.db.executemany("INSERT INTO facts VALUES (?,?,?,?)",
                                [(int(cik), item, tag, pack(facts)) for (item, tag), facts in got.items() if facts])
            self.db.execute("INSERT OR REPLACE INTO seen VALUES (?,?,?,?)",
                            (int(cik), time.time(), newest.toordinal() if newest else None, ""))
            self.db.commit()
        return True

    def update(self, ciks, today: date | None = None, progress=None, workers: int = 3) -> tuple[int, int, int]:
        """(받은 회사, 건너뛴 회사, 못 받은 회사). 앞에 있는 회사부터(지금 바구니를 먼저 넣어 부르면 된다)."""
        from concurrent.futures import ThreadPoolExecutor

        today = today or date.today()
        ciks = list(dict.fromkeys(int(c) for c in ciks if c))
        todo = [c for c in ciks if self.due(c, today)]
        skipped = len(ciks) - len(todo)
        if not todo or self.http is None:
            return 0, skipped, 0
        state = {"done": 0, "ok": 0, "bad": 0}
        lock = threading.Lock()

        def one(cik):
            ok = self.fetch(cik, today)
            with lock:
                state["done"] += 1
                state["ok" if ok else "bad"] += 1
                if progress:
                    progress(state["done"] - 1, len(todo), f"CIK {cik}")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, todo))
        return state["ok"], skipped, state["bad"]
