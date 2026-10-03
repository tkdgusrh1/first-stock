"""그 날 **알 수 있었던** 재무 — 백테스트에 성장을 넣기 위한 자료.

지금 재무로 과거를 고르면 미래 정보다(2023년에 2025년 실적을 알고 고른 셈).
SEC XBRL 의 숫자에는 '제출일(filed)' 이 붙어 있어서, 각 분기 매출이 언제 처음
공개됐는지 안다. 그 날부터만 쓴다.

- 같은 분기가 여러 번 보고되면(다음 해 비교 칸·정정) **처음 제출된 값과 날짜**를 쓴다.
  나중에 고친 값을 쓰면 그 시점엔 몰랐던 숫자가 섞인다.
- 4분기는 10-K 연간에서 1~3분기를 빼서 만들고, 날짜는 10-K 제출일이다.
- 회사가 매출 항목 이름을 바꾼 경우(예: SalesRevenueNet → RevenueFromContract…)도 이어 붙인다.

한국(DART)은 아직 이런 제출일 묶음을 만들지 않았다 — 그래서 성장 전략은 미국만 된다.
"""

from __future__ import annotations

import bisect
from datetime import date, timedelta

from ..xbrl import CONCEPTS, CompanyFacts, Fact


def _first_reported(facts, low: int, high: int) -> dict:
    """{종료일: 처음 제출된 Fact} — 기간 길이가 low~high 일인 것만."""
    best: dict[date, Fact] = {}
    for f in facts:
        if not f.days or not (low <= f.days <= high) or f.filed is None:
            continue
        cur = best.get(f.end)
        if cur is None or f.filed < cur.filed:
            best[f.end] = f
    return best


def revenue_growth_series(facts: CompanyFacts | None) -> list[tuple[date, float]]:
    """[(알게 된 날, 최근 4분기 매출의 전년 대비 성장률)] — 알게 된 날 순."""
    if facts is None:
        return []
    raw: list[Fact] = []
    for concept in CONCEPTS["revenue"]:
        raw.extend(facts._raw(concept))
    if not raw:
        return []
    quarters = _first_reported(raw, 80, 120)     # 16주 분기까지
    annuals = _first_reported(raw, 350, 380)
    known = {end: (f.val, f.filed) for end, f in quarters.items()}
    for end, annual in annuals.items():
        if end in known or not annual.start:
            continue
        inside = [q for q in quarters.values()
                  if annual.start - timedelta(days=3) <= (q.start or q.end) and q.end <= end]
        if len(inside) == 3:
            known[end] = (annual.val - sum(q.val for q in inside), annual.filed)
    ends = sorted(known)
    out = []
    for k in range(7, len(ends)):
        window = ends[k - 7:k + 1]
        # 8개 분기가 빈틈없이 이어져 있어야 한다(빠진 분기가 있으면 비교가 엉터리가 된다)
        if not all(70 <= (b - a).days <= 125 for a, b in zip(window, window[1:])):
            continue
        recent = sum(known[e][0] for e in window[4:])
        prior = sum(known[e][0] for e in window[:4])
        if prior <= 0:
            continue
        seen = max(known[e][1] for e in window)
        out.append((seen, recent / prior - 1))
    out.sort(key=lambda pair: pair[0])
    return out


def growth_at(series: list[tuple[date, float]], day: date) -> float | None:
    """그 날까지 공개된 것 중 가장 최근 성장률. 아직 하나도 없으면 None."""
    if not series:
        return None
    k = bisect.bisect_right([d for d, _ in series], day) - 1
    return series[k][1] if k >= 0 else None


def growth_data(bot, market: str) -> dict:
    """{티커: 성장률 시계열} — 미국 감시 종목만. 받아둔 재무 파일을 쓴다(없으면 받는다)."""
    if market != "us":
        return {}
    out = {}
    for target in bot.cached_targets():
        if target.market != market or getattr(target, "is_fund", False):
            continue
        try:
            facts = bot.xbrl.company_facts(target.cik)
        except Exception:
            continue
        series = revenue_growth_series(facts)
        if series:
            out[target.ticker] = series
    return out
