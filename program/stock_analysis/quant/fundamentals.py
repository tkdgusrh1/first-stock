"""그 날 **알 수 있었던** 재무 — 백테스트에 성장을 넣기 위한 자료.

지금 재무로 과거를 고르면 미래 정보다(2023년에 2025년 실적을 알고 고른 셈).
SEC XBRL 의 숫자에는 '제출일(filed)' 이 붙어 있어서, 각 분기 매출이 언제 처음
공개됐는지 안다. 그 날부터만 쓴다.

- 같은 분기가 여러 번 보고되면(다음 해 비교 칸·정정) **처음 제출된 값과 날짜**를 쓴다.
  나중에 고친 값을 쓰면 그 시점엔 몰랐던 숫자가 섞인다.
- 4분기는 10-K 연간에서 1~3분기를 빼서 만들고, 날짜는 10-K 제출일이다.
- 매출 항목은 하나만 쓴다(일부 매출과 전체 매출을 섞지 않게). 이름을 바꾼 회사는 옛 이름으로 앞쪽만 잇는다.
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


Q4_FLOOR = 0.25          # 매출처럼 늘 양수인 항목: 4분기가 1~3분기 중 가장 작은 분기의 이만큼도 안 되면 버린다


def _known(raw: list[Fact], positive: bool = False) -> dict:
    """{분기말: (값, 처음 알게 된 날)} — 한 항목 안에서. 4분기는 연간 − 1~3분기.

    positive(매출·매출원가): 연간 보고서에서 사업을 떼어 내며(중단 사업) 숫자를 다시 나누면 '연간 − 처음 낸
    1~3분기' 가 0 이하이거나 터무니없이 작아진다. 그런 4분기는 만들지 않는다(빈칸이 틀린 값보다 낫다)."""
    quarters = _first_reported(raw, 80, 120)     # 16주 분기까지
    annuals = _first_reported(raw, 350, 380)
    known = {end: (f.val, f.filed) for end, f in quarters.items()}
    for end, annual in annuals.items():
        if end in known or not annual.start:
            continue
        inside = [q for q in quarters.values()
                  if annual.start - timedelta(days=3) <= (q.start or q.end) and q.end <= end]
        if len(inside) == 3:
            q4 = annual.val - sum(q.val for q in inside)
            if positive and (q4 <= 0 or q4 < Q4_FLOOR * min(q.val for q in inside)):
                continue
            known[end] = (q4, annual.filed)
    return known


def revenue_growth_series(facts: CompanyFacts | None) -> list[tuple[date, float]]:
    """[(알게 된 날, 최근 4분기 매출의 전년 대비 성장률)] — 알게 된 날 순."""
    if facts is None:
        return []
    # 매출 항목은 하나만 쓴다. 여러 항목을 섞으면 '계약 매출' 같은 일부와 전체 매출이 한 줄에 섞인다.
    # 가장 최근 분기까지 있는 항목, 같은 날이면 더 큰(전체) 매출. 이름을 바꾼 회사는 옛 이름의
    # 분기로 앞쪽 빈칸만 채운다.
    return growth_from_known(merge_known([_known(facts._raw(c), positive=True) for c in CONCEPTS["revenue"]]))


SAME_NUMBER = 0.02       # 겹치는 분기의 4분의 3 이상이 이만큼 안으로 같으면 '같은 숫자를 다른 이름으로 낸 태그'


def merge_known(series: list[dict], by_order: bool = False) -> dict:
    """여러 태그의 {분기말: (값, 알게 된 날)} 을 하나로 — 가장 최근까지 있는 태그를 쓰고, 그 태그가
    시작되기 전 빈칸은 옛 태그로 채운다. 회계기준이 바뀌며 태그 이름을 바꾼 회사를 잇는 방법.
    가운데 빈칸은 겹치는 분기의 값이 대개 같은(또는 겹치는 분기가 없는) 태그로만 채운다 — 태그를 갔다가
    되돌린 회사(구글 매출 2019~2023)는 잇고, 일부 매출만 담은 태그가 섞이지는 않게.
    같은 날까지 있는 태그끼리는 큰 값(전체 매출), by_order=True 면 앞에 둔 태그(우선순위)."""
    ranked = [(k, s) for k, s in enumerate(series) if s]
    if not ranked:
        return {}
    ranked.sort(key=lambda ks: (max(ks[1]), -ks[0] if by_order else ks[1][max(ks[1])][0]), reverse=True)
    known = dict(ranked[0][1])
    start = min(known)
    for _k, older in ranked[1:]:
        gaps = [abs(v[0] - known[e][0]) / max(abs(known[e][0]), 1e-9) for e, v in older.items() if e in known]
        same = not gaps or sum(g <= SAME_NUMBER for g in gaps) >= 0.75 * len(gaps)   # 정정 한두 개에는 버틴다
        for end, value in older.items():
            if end < start or same:
                known.setdefault(end, value)
    return known


def growth_from_known(known: dict) -> list[tuple[date, float]]:
    """{분기말: (매출, 알게 된 날)} → [(알게 된 날, 최근 4분기 매출의 전년 대비 성장률)]."""
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


GROWTH_MAX_AGE = 200     # 마지막 성장률이 이보다 오래전에 나온 것이면 '지금 값' 으로 쓰지 않는다(일)


def growth_at(series: list[tuple[date, float]], day: date, max_age: int = GROWTH_MAX_AGE) -> float | None:
    """그 날까지 공개된 것 중 가장 최근 성장률. 아직 없거나 너무 오래된 것뿐이면 None
    (매출 태그를 바꿔 끊긴 회사의 몇 년 전 성장률을 지금 것처럼 쓰지 않는다)."""
    if not series:
        return None
    k = bisect.bisect_right([d for d, _ in series], day) - 1
    if k < 0 or (day - series[k][0]).days > max_age:
        return None
    return series[k][1]


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
