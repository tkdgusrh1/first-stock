"""팩터 재료 — 종목마다 '그 날 알 수 있었던' 값.

가격에서 나오는 것(모멘텀·변동성)은 그 날 종가까지로, 재무에서 나오는 것(수익성·장부가치·실적 서프라이즈)은
그 숫자가 처음 공개된 날(SEC 제출일)부터만 쓴다. 나중에 고친 값은 쓰지 않는다. 퀀트는 미국 주식만.

미국 (SEC, 분기마다)
- 수익성 = 최근 4분기 매출총이익 ÷ 총자산 (Novy-Marx 2013). 매출총이익을 따로 안 내는 회사는 매출 − 매출원가.
  은행·보험처럼 둘 다 없으면 비운다.
- 가치 = 자본(장부가) ÷ 시가총액. 시가총액 = 그 날 종가 × 주식 수. 주식 수는 그 숫자의 기준일 뒤에 있었던
  액면분할만큼 곱해 '지금 가격 기준' 으로 맞춘다(야후 가격이 분할을 소급 반영하므로). 기준일은 표지의
  발행주식수면 표지에 적힌 날, 표지에 합계가 없어 분기 가중평균을 쓰면 **제출일** — 분기가 끝난 뒤 제출 전에
  분할하면 보고서 숫자를 분할 뒤로 고쳐 싣기 때문이다(그 분할을 또 곱하면 시가총액이 몇십 배가 된다).
- 실적 서프라이즈(SUE) = (이번 분기 순이익 − 1년 전 같은 분기) ÷ 앞선 8분기 동안 그 차이의 표준편차
  (Bernard·Thomas 1989 의 계절 무작위 보행 방식). 주당이익 대신 순이익을 써서 분할에 흔들리지 않게 했다.
  '알게 된 날' 은 분기 보고서(10-Q·10-K) 제출일이다 — 실적 발표(보도자료)보다 며칠~몇 주 늦다.
"""

from __future__ import annotations

import bisect
import math
from array import array
from dataclasses import dataclass, field
from datetime import date

from .fundamentals import _first_reported, _known, growth_from_known, merge_known
from .pricedb import split_factor_after

FLOW_MAX_AGE = 200       # 최근 4분기 합이 이보다 오래된 분기에서 끝나면 '지금 값' 으로 쓰지 않는다(일)
STOCK_MAX_AGE = 200      # 자산·자본
SHARES_MAX_AGE = 400     # 주식 수는 천천히 바뀐다
DEI = "EntityCommonStockSharesOutstanding"
DILUTED = "WeightedAverageNumberOfDilutedSharesOutstanding"


class Points:
    """[(알게 된 날, 기간 끝, 값)] — '그 날까지 알려진 것 중 기간 끝이 가장 최근인 값' 을 빨리 찾는다."""

    __slots__ = ("seen", "best", "pts")

    def __init__(self, points) -> None:
        pts = self.pts = sorted(points)
        self.seen = [p[0] for p in pts]
        self.best, top = [], None
        for p in pts:
            if top is None or p[1] >= top[1]:
                top = p
            self.best.append(top)

    def __bool__(self) -> bool:
        return bool(self.seen)

    def at(self, day: date, max_age: int | None = None):
        """(기간 끝, 값) 또는 None."""
        k = bisect.bisect_right(self.seen, day) - 1
        if k < 0:
            return None
        _seen, end, value = self.best[k]
        if max_age is not None and (day - end).days > max_age:
            return None
        return end, value


def _first_instants(facts) -> dict:
    """시점 값(자산·자본·주식 수) — {날짜: (값, 처음 알게 된 날)}."""
    best: dict = {}
    for f in facts:
        if f.start is not None or f.filed is None:
            continue
        cur = best.get(f.end)
        if cur is None or f.filed < cur[1]:
            best[f.end] = (f.val, f.filed)
    return best


def quarters(tags: dict, by_order: bool = False, positive: bool = False) -> dict:
    """{분기말: (값, 알게 된 날)} — 여러 태그를 잇고, 4분기는 연간 − 1~3분기."""
    return merge_known([_known(facts, positive) for facts in (tags or {}).values()], by_order)


def instants(tags: dict, by_order: bool = False) -> dict:
    return merge_known([_first_instants(facts) for facts in (tags or {}).values()], by_order)


def _ordered(data: dict, item: str) -> dict:
    """{태그: [Fact]} 를 secfacts.ITEMS 의 우선순위대로."""
    from .secfacts import ITEMS

    tags = data.get(item) or {}
    order = [tag for _tax, tag in ITEMS.get(item, ())]
    return {t: tags[t] for t in sorted(tags, key=lambda t: order.index(t) if t in order else len(order))}


def share_counts(tags: dict) -> dict:
    """{기준일: (주식 수, 알게 된 날)} — 표지의 발행주식수(기준일 = 표지 날짜)와, 표지에 합계가 없는 보고서의
    분기 희석 가중평균(기준일 = 제출일). 같은 보고서에 둘 다 있으면 표지 숫자만 쓴다(뜻이 다른 두 숫자가
    번갈아 섞이지 않게)."""
    cover = _first_instants(tags.get(DEI, []))
    with_cover = {filed for _val, filed in cover.values()}
    quarters_seen = sorted(_first_reported(tags.get(DILUTED, []), 80, 120).values(), key=lambda f: f.end)
    out = {f.filed: (f.val, f.filed) for f in quarters_seen if f.filed not in with_cover}   # 한 보고서에 두 분기면 최근 분기
    out.update(cover)
    return out


SHARE_JUMP = 3.0         # 분할을 맞춘 주식 수가 앞 기록들과 이만큼 넘게 다르면, 한 번 더 이어질 때까지 쓰지 않는다


def clean_shares(points, splits) -> list:
    """[(알게 된 날, 기준일, 주식 수)] 에서 분할을 맞춰도 앞 기록들과 3배 넘게 다른 것을 걸러 낸다.

    분할 날짜 언저리의 표지 숫자(이미 분할 뒤인데 또 곱해짐)나, 분할을 고쳐 싣지 않은 보고서는 시가총액을 몇십 배로
    만든다. 그런 기록은 바로 쓰지 않고, 다음 기록도 같은 수준이면(합병·대규모 발행처럼 진짜 바뀐 것) 그 다음 기록이
    나온 날부터 둘 다 쓴다 — 그 날까지 알 수 있던 것만으로 정한다."""
    def near(a, b):
        return max(a / b, b / a) <= SHARE_JUMP if a > 0 and b > 0 else False

    out, kept, pending = [], [], None
    for seen, end, val in sorted(points, key=lambda p: (p[1], p[0])):
        adj = val * split_factor_after(splits, end)
        if not kept or any(near(adj, k) for k in kept[-2:]):
            out.append((seen, end, val))
            kept.append(adj)
            pending = None
        elif pending is not None and near(adj, pending[3]):
            out += [(max(seen, pending[0]), pending[1], pending[2]), (seen, end, val)]
            kept += [pending[3], adj]
            pending = None
        else:
            pending = (seen, end, val, adj)
    return out


def merge_companies(parts: list[dict]) -> dict:
    """같은 회사가 SEC 번호(CIK)를 바꿨을 때(지주회사 전환 등) 지금 번호(parts[0])와 예전 번호들의 재무를 합친다.

    예전 번호의 값은 지금 번호에 같은 기간·같은 길이의 값이 없거나, 있어도 같은 숫자(2% 안)일 때만 쓴다 — 같은
    숫자면 더 일찍 제출된 예전 값이 '처음 알게 된 날' 이 되고, 다른 숫자면(예전 회사가 자회사로 남아 범위가 다른
    재무를 계속 냄) 섞지 않는다."""
    from .fundamentals import SAME_NUMBER

    def length(f) -> int:                 # 0 = 시점 값, 3 = 분기, 12 = 1년(누적 6·9개월도 따로)
        return 0 if f.start is None else round((f.end - f.start).days / 30)

    own, *older = [p or {} for p in parts] or [{}]
    out = {item: {tag: list(facts) for tag, facts in tags.items()} for item, tags in own.items()}
    mine: dict = {}
    for item, tags in own.items():
        for facts in tags.values():
            for f in facts:
                mine.setdefault((item, f.end, length(f)), []).append(f.val)
    def fits(item, f) -> bool:
        vals = mine.get((item, f.end, length(f)))
        return vals is None or any(abs(f.val - v) <= SAME_NUMBER * max(abs(v), 1e-9) for v in vals)

    for data in older:
        for item, tags in data.items():
            for tag, facts in tags.items():
                out.setdefault(item, {}).setdefault(tag, []).extend(f for f in facts if fits(item, f))
    return out


def ttm(known: dict) -> list[tuple[date, date, float]]:
    """[(알게 된 날, 마지막 분기말, 최근 4분기 합)] — 4개 분기가 빈틈없이 이어진 경우만."""
    ends = sorted(known)
    out = []
    for k in range(3, len(ends)):
        window = ends[k - 3:k + 1]
        if not all(70 <= (b - a).days <= 125 for a, b in zip(window, window[1:])):
            continue
        out.append((max(known[e][1] for e in window), window[-1], sum(known[e][0] for e in window)))
    return out


def sue_events(known: dict) -> list[tuple[date, date, float]]:
    """[(알게 된 날, 분기말, SUE)]. 13개 분기(이번 + 1년 전 + 그 차이 8개)가 이어져 있어야 계산한다."""
    ends = sorted(known)
    out = []
    for k in range(12, len(ends)):
        window = ends[k - 12:k + 1]
        if not all(70 <= (b - a).days <= 125 for a, b in zip(window, window[1:])):
            continue
        val = [known[e][0] for e in window]
        diffs = [val[j] - val[j - 4] for j in range(4, 12)]          # 앞선 8번의 '1년 전 대비' 차이
        mean = sum(diffs) / len(diffs)
        sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1))
        if sd <= 0:
            continue
        # 알게 된 날 = 창 안 숫자가 모두 나온 날(대개 이번 분기 제출일, 다른 태그에서 늦게 채운 값이 있으면 그 날)
        out.append((max(known[e][1] for e in window), window[-1], (val[12] - val[8]) / sd))
    return out


@dataclass
class Company:
    gross: Points = field(default_factory=lambda: Points([]))       # 최근 4분기 매출총이익
    assets: Points = field(default_factory=lambda: Points([]))
    equity: Points = field(default_factory=lambda: Points([]))
    shares: Points = field(default_factory=lambda: Points([]))
    growth: list = field(default_factory=list)                       # [(알게 된 날, 매출 성장률)]
    sue: list = field(default_factory=list)                          # [(알게 된 날, 분기말, SUE)]


def build_company(data: dict) -> Company:
    """FactsDB.company() 의 {항목: {태그: [Fact]}} → 계산에 쓸 시계열."""
    revenue = quarters(data.get("revenue"), positive=True)
    gross = quarters(data.get("gross_profit"))
    if not gross and revenue and data.get("cogs"):
        cost = quarters(data["cogs"], positive=True)
        gross = {e: (revenue[e][0] - cost[e][0], max(revenue[e][1], cost[e][1])) for e in revenue if e in cost}
    return Company(
        gross=Points(ttm(gross)),
        assets=Points((v[1], e, v[0]) for e, v in instants(data.get("assets")).items()),
        equity=Points((v[1], e, v[0]) for e, v in instants(_ordered(data, "equity"), by_order=True).items()),
        shares=Points((v[1], e, v[0]) for e, v in share_counts(data.get("shares") or {}).items()),
        growth=growth_from_known(revenue),
        sue=sue_events(quarters(_ordered(data, "net_income"), by_order=True)),
    )


class Book:
    """재무 팩터 재료(미국, SEC 제출일 기준). 값을 못 구하면 None — 0 이나 평균으로 메우지 않는다."""

    def __init__(self, companies: dict | None = None, splits: dict | None = None) -> None:
        self.companies: dict[str, Company] = companies or {}
        self.splits: dict[str, list] = splits or {}
        self._shares: dict[str, Points] = {}             # 분할을 맞춰 앞뒤가 안 맞는 기록을 거른 주식 수(처음 쓸 때 만든다)

    def quality(self, t: str, day: date) -> float | None:
        c = self.companies.get(t)
        if c is None:
            return None
        gp, assets = c.gross.at(day, FLOW_MAX_AGE), c.assets.at(day, STOCK_MAX_AGE)
        if not gp or not assets or assets[1] <= 0:
            return None
        return gp[1] / assets[1]

    def book_to_market(self, t: str, day: date, price: float) -> float | None:
        c = self.companies.get(t)
        if c is None or not price or price <= 0:
            return None
        if t not in self._shares:
            self._shares[t] = Points(clean_shares(c.shares.pts, self.splits.get(t, [])))
        equity, shares = c.equity.at(day, STOCK_MAX_AGE), self._shares[t].at(day, SHARES_MAX_AGE)
        if not equity or not shares or shares[1] <= 0:
            return None
        cap = price * shares[1] * split_factor_after(self.splits.get(t, []), shares[0])
        return equity[1] / cap if cap > 0 else None

    def growth(self, t: str) -> list:
        c = self.companies.get(t)
        return c.growth if c else []

    def recent_sue(self, t: str, day: date, within: int = 7) -> float | None:
        """그 날까지 within 일 안에 새로 알게 된 분기의 SUE. 없으면 None."""
        c = self.companies.get(t)
        if c is None:
            return None
        for seen, _end, value in reversed(c.sue):
            if seen <= day:
                return value if (day - seen).days <= within else None
        return None


# --------------------------------------------------------------------------
# 가격에서 나오는 것
# --------------------------------------------------------------------------
def momentum_12_1(closes, i: int) -> float | None:
    """12개월 전부터 1개월 전까지의 수익률(마지막 한 달은 뺀다 — 단기 되돌림을 피하려는 관례)."""
    if i < 252 or not closes[i - 252]:
        return None
    return closes[i - 21] / closes[i - 252] - 1


class RollingVol:
    """종목마다 일간 수익률의 누적합·제곱 누적합을 한 번만 만들어, 1년 변동성을 바로 꺼낸다."""

    def __init__(self) -> None:
        self._sums: dict[int, tuple] = {}

    def at(self, closes, i: int, n: int = 252) -> float | None:
        if i < n:
            return None
        key = id(closes)
        got = self._sums.get(key)
        if got is None or got[0] is not closes:
            s, q, acc, acc2 = array("d", [0.0]), array("d", [0.0]), 0.0, 0.0
            for k in range(1, len(closes)):
                r = closes[k] / closes[k - 1] - 1 if closes[k - 1] else 0.0
                acc += r
                acc2 += r * r
                s.append(acc)
                q.append(acc2)
            got = self._sums[key] = (closes, s, q)
        _c, s, q = got
        total, total2 = s[i] - s[i - n], q[i] - q[i - n]
        mean = total / n
        var = (total2 - n * mean * mean) / (n - 1)
        return math.sqrt(max(var, 0.0)) * math.sqrt(252)


def rank_pct(values: dict, higher_better: bool = True) -> dict:
    """{티커: 값} → {티커: 0~1 백분위}. 같은 값은 같은 순위(평균 순위). None 은 뺀다."""
    present = sorted((v, t) for t, v in values.items() if v is not None)
    if not higher_better:
        present = sorted(((-v, t) for v, t in present))
    n = len(present)
    if n < 2:
        return {t: 0.5 for _, t in present}
    out, k = {}, 0
    while k < n:
        j = k
        while j + 1 < n and present[j + 1][0] == present[k][0]:
            j += 1
        rank = (k + j) / 2 / (n - 1)
        for _, t in present[k:j + 1]:
            out[t] = rank
        k = j + 1
    return out
