"""공시 이벤트 스터디 — 공시가 나온 뒤 주가가 **실제로** 어떻게 움직였나.

공시를 퀀트 신호로 쓰거나 모델을 학습시키려면 먼저 이 표가 있어야 한다.
'이 종류의 공시 뒤에 평균 얼마나 움직였나' 가 곧 신호의 근거이고,
공시마다 붙는 수익률이 곧 학습의 정답(라벨)이다.

미래 정보를 쓰지 않는 규칙 (백테스트 엔진과 같은 원칙)
------------------------------------------------------
  · 공시를 **알고 나서 처음 살 수 있는 시가**에 산 것으로 친다.
      미국 — SEC 접수 시각(미 동부)이 있다. 09:00 전에 접수됐으면 그날 시가,
             그 뒤면 다음 거래일 시가. (09:00~09:30 은 장 시작과 너무 가까워
             실제로 읽고 주문하기 어렵다고 보고 다음 날로 넘긴다)
      한국 — DART 목록에는 날짜만 있고 시각이 없다. 장중 공시인지 알 수 없으니
             **항상 다음 거래일 시가**로 잡는다. 늦게 사는 쪽으로 틀리는 건
             수익을 줄일 뿐이지만, 일찍 사는 쪽으로 틀리면 없는 수익이 생긴다.
  · h일 수익률 = 들어간 날 시가 → h번째 거래일 종가 (들어간 날이 1번째).
  · 기간이 아직 다 안 지난 공시는 비워 둔다. 0 으로 메우지 않는다.
  · 시장 대비 수익률 = 같은 날 같은 방식으로 산 지수 ETF 수익률을 뺀 값.
    시장 전체가 오른 날의 공시가 '호재' 로 보이는 착시를 걷어낸다.

이 표가 실제보다 좋게 보이게 만드는 것 (결과에 항상 같이 적는다)
  · 지금 감시 목록에 있는 종목만 본다 — 그사이 상장폐지된 종목이 없다(생존 편향).
  · 배당이 빠져 있다. 거래 비용·세금도 빠져 있다.
  · 같은 종목의 공시가 며칠 사이에 몰리면 기간이 겹쳐 서로 독립이 아니다.
    t 값이 실제보다 크게 나온다.
"""

from __future__ import annotations

import csv
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

from .dart import classify as classify_dart
from .edgar import ITEM_8K
from .timeutil import ET, parse_sec_datetime

HORIZONS = (1, 5, 20)
MIN_SAMPLE = 30                 # 이보다 적으면 평균을 믿지 않는다고 표시
SAME_DAY_CUTOFF = time(9, 0)    # 미 동부 이 시각 전에 접수된 공시만 그날 시가에 산다
SPLIT = 0.7                     # 앞 70% / 뒤 30% 기간으로 나눠 따로 본다 (backtest 와 같음)

# 시장 대비 수익률을 잴 때 빼는 지수 ETF
BENCHMARK = {"us": ("SPY", "S&P 500 (SPY)"), "kr": ("069500.KS", "KODEX 200")}

# 사건이 아니라 첨부 목록일 뿐인 8-K 항목
SKIP_8K_ITEMS = {"9.01"}

WARNINGS = (
    "지금 감시 목록에 있는 종목만으로 계산했습니다. 그사이 상장폐지된 종목이 빠져 있어 실제보다 좋게 나옵니다(생존 편향).",
    "배당·거래 비용·세금이 빠져 있습니다.",
    "같은 종목의 공시가 며칠 사이에 몰리면 기간이 겹쳐 서로 독립이 아닙니다. t 값은 실제보다 크게 나옵니다.",
    "한국 공시는 접수 시각을 알 수 없어 항상 다음 거래일 시가에 산 것으로 계산했습니다.",
)


@dataclass
class Event:
    """공시 하나가 만든 사건 하나. 8-K 하나에 항목이 여럿이면 사건도 여럿이다."""

    market: str                 # us | kr
    ticker: str
    kind: str                   # 묶는 열쇠 (예: "8-K 2.02", "유상증자 결정")
    title: str                  # 한글 설명
    filed: date                 # 접수일
    accepted: datetime | None   # 접수 시각 (미 동부, 시간대 없음). 한국은 None
    source: str = ""            # 원문 주소


@dataclass
class Outcome:
    event: Event
    entry_day: date | None = None
    entry_price: float | None = None
    returns: dict[int, float | None] = field(default_factory=dict)
    bench: dict[int, float | None] = field(default_factory=dict)
    note: str = ""              # 비었으면 왜 비었는지

    def abnormal(self, h: int) -> float | None:
        r, b = self.returns.get(h), self.bench.get(h)
        if r is None or b is None:
            return None
        return r - b


# --- 공시 → 사건 ---------------------------------------------------------
def us_events(filings: list) -> list[Event]:
    """SEC 8-K → 항목별 사건. 정정 공시(8-K/A)는 새 사건이 아니라서 받지 않는다."""
    out: list[Event] = []
    for f in filings:
        if str(f.form).upper() != "8-K":
            continue
        try:
            filed = date.fromisoformat(f.filing_date)
        except (TypeError, ValueError):
            continue
        accepted = _parse_et(f.accepted)
        for item in f.items:
            if item in SKIP_8K_ITEMS:
                continue
            out.append(Event("us", f.ticker, f"8-K {item}", ITEM_8K.get(item, f"8-K 항목 {item}"),
                             filed, accepted, f.doc_url))
    return out


def kr_events(filings: list, ticker: str) -> list[Event]:
    """DART 공시 → 사건. 이름으로 종류를 못 가린 공시와 정정 공시는 뺀다.

    정정 공시는 원래 공시를 고친 것이라, 넣으면 같은 사건을 두 번 센다.
    """
    out: list[Event] = []
    for f in filings:
        name = (f.name or "").strip()
        if not f.day or not name or "정정" in name:
            continue
        _tone, title, _why = classify_dart(name)
        if title == name:           # 규칙에 안 걸렸다 — 종류를 지어내지 않는다
            continue
        out.append(Event("kr", ticker, title, title, f.day, None, f.url))
    return out


def _parse_et(raw: str | None) -> datetime | None:
    """EDGAR 접수 시각 → 미 동부 시각(시간대 없이). 비교를 단순하게 하려고 시간대를 뗀다."""
    moment = parse_sec_datetime(raw, ET)
    return moment.replace(tzinfo=None) if moment else None


# --- 사건 → 수익률 -------------------------------------------------------
def entry_index(days: list[date], event: Event) -> int | None:
    """공시를 알고 나서 처음 살 수 있는 봉의 번호. 없으면 None."""
    if event.accepted is not None and event.accepted.time() < SAME_DAY_CUTOFF:
        first, same_day_ok = event.accepted.date(), True
    else:
        first, same_day_ok = (event.accepted.date() if event.accepted else event.filed), False
    for i, day in enumerate(days):
        if day > first or (same_day_ok and day == first):
            return i
    return None


def measure(event: Event, bars: list, bench_bars: list,
            horizons: tuple[int, ...] = HORIZONS) -> Outcome:
    """bars = 그 종목의 확정된 일봉(오래된 순), bench_bars = 지수 ETF 일봉."""
    out = Outcome(event)
    days = [b.day for b in bars]
    i = entry_index(days, event)
    if i is None:
        out.note = "공시 뒤 거래일 봉이 아직 없습니다"
        return out
    entry = bars[i]
    if not entry.open or entry.open <= 0:
        out.note = "들어갈 날의 시가가 없습니다"
        return out
    out.entry_day, out.entry_price = entry.day, entry.open
    bench = {b.day: b for b in bench_bars}
    for h in horizons:
        j = i + h - 1
        if j >= len(bars):
            out.returns[h] = out.bench[h] = None
            continue
        out.returns[h] = bars[j].close / entry.open - 1
        start, end = bench.get(entry.day), bench.get(bars[j].day)
        out.bench[h] = (end.close / start.open - 1
                        if start and end and start.open and start.open > 0 else None)
    if all(v is None for v in out.returns.values()):
        out.note = "기간이 아직 다 지나지 않았습니다"
    return out


def settled(bars: list, today: date) -> list:
    """오늘 봉은 장중이면 아직 바뀌는 값이다. 확정된 봉(어제까지)만 쓴다."""
    return [b for b in bars if b.day < today]


# --- 묶어서 보기 ---------------------------------------------------------
def summarize(outcomes: list[Outcome], horizon: int) -> list[dict]:
    """사건 종류별 시장 대비 수익률. 표본이 많은 순."""
    groups: dict[str, list[Outcome]] = {}
    for o in outcomes:
        if o.abnormal(horizon) is not None:
            groups.setdefault(o.event.kind, []).append(o)
    rows = []
    for kind, items in groups.items():
        items.sort(key=lambda o: o.event.filed)
        values = [o.abnormal(horizon) for o in items]
        cut = max(1, int(len(values) * SPLIT))
        early, late = values[:cut], values[cut:]
        rows.append({
            "kind": kind,
            "title": items[0].event.title,
            "market": items[0].event.market,
            "n": len(values),
            "tickers": len({o.event.ticker for o in items}),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "hit": sum(v > 0 for v in values) / len(values),
            "t": t_stat(values),
            "early": statistics.fmean(early) if early else None,
            "late": statistics.fmean(late) if late else None,
            "enough": len(values) >= MIN_SAMPLE,
        })
    rows.sort(key=lambda r: (-r["n"], r["kind"]))
    return rows


def t_stat(values: list[float]) -> float | None:
    """평균이 0 과 얼마나 떨어져 있나. 2 를 넘어야 우연이 아닐 가능성이 크다(겹침이 없을 때)."""
    if len(values) < 2:
        return None
    sd = statistics.stdev(values)
    if sd == 0:
        return None
    return statistics.fmean(values) / (sd / math.sqrt(len(values)))


# --- 학습용 표로 내보내기 ------------------------------------------------
def columns(horizons: tuple[int, ...] = HORIZONS) -> list[str]:
    cols = ["market", "ticker", "kind", "title", "filed", "accepted_et", "entry_day", "entry_price"]
    cols += [f"r{h}" for h in horizons] + [f"bench{h}" for h in horizons]
    cols += [f"abn{h}" for h in horizons] + ["note", "source"]
    return cols


def to_row(o: Outcome, horizons: tuple[int, ...] = HORIZONS) -> dict:
    e = o.event
    row = {
        "market": e.market, "ticker": e.ticker, "kind": e.kind, "title": e.title,
        "filed": e.filed.isoformat(),
        "accepted_et": e.accepted.isoformat(sep=" ") if e.accepted else "",
        "entry_day": o.entry_day.isoformat() if o.entry_day else "",
        "entry_price": _num(o.entry_price), "note": o.note, "source": e.source,
    }
    for h in horizons:
        row[f"r{h}"] = _num(o.returns.get(h))
        row[f"bench{h}"] = _num(o.bench.get(h))
        row[f"abn{h}"] = _num(o.abnormal(h))
    return row


def write_csv(outcomes: list[Outcome], path: str | Path,
              horizons: tuple[int, ...] = HORIZONS) -> int:
    """엑셀에서 한글이 깨지지 않게 BOM 을 붙인다. 빈 값은 빈 칸 그대로 둔다."""
    rows = sorted(outcomes, key=lambda o: (o.event.filed, o.event.ticker, o.event.kind))
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns(horizons))
        writer.writeheader()
        for o in rows:
            writer.writerow(to_row(o, horizons))
    return len(rows)


def _num(value: float | None) -> str:
    return "" if value is None else f"{value:.10g}"
