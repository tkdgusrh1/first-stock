"""종목 바구니 — '그 해에 실제로 큰 회사' 였던 곳 (그 해 매출 순위).

지금 큰 회사들로 과거를 시험하면 미래를 알고 고른 셈이다 — 2016년의 백테스트가 2024년에야 커진 회사를
고르게 된다. 그래서 회계연도마다 그 해 매출 순위로 바구니를 따로 만들고, 그 매출이 공개된 뒤부터만 쓴다.

- 미국: SEC XBRL frames(그 해 연간 매출, 모든 제출 기업 — 매출 항목 네 개 중 큰 값)
  → 지금 티커가 있는 회사만 → 매출 상위 N. 다음 해 10월 1일부터 쓴다(회계연도가 6월에 끝나는 회사의
  연간 보고서까지 다 나온 뒤).
- 한국: DART 다중회사 주요계정(그 해 사업보고서 매출) → 지금 상장된 회사만 → 매출 상위 N.
  다음 해 4월 15일부터 쓴다(사업보고서 마감 3월 말). 같은 응답의 영업이익·자산·자본·순이익과
  접수일도 저장한다 — 한국 수익성 점수에 쓴다.

남은 함정: 그 사이 상장폐지·합병된 회사는 지금 티커가 없어 빠진다(생존 편향). 결과 화면에 적는다.
받지 못한 해는 비워 둔다. 대신 쓸 목록을 몰래 끼워 넣지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

EFFECTIVE = {"us": (10, 1), "kr": (4, 15)}      # 회계연도 Y 의 순위를 쓰기 시작하는 날(Y+1 년)
YEARS_BACK = 11
SIZES = {"us": 500, "kr": 200}
REFRESH = 30 * 24 * 3600        # 최근 두 해는 늦게 내는 회사가 있어 한 달에 한 번 다시 받는다
MIN_FILERS = 50                 # 이보다 적게 받은 해는 제대로 받은 것으로 치지 않는다
KR_COVERAGE = 0.5               # 상장사의 절반도 못 받았으면 도중에 끊긴 것 — 순위가 엉터리라 쓰지 않는다
KR_FIN_KEYS = ("revenue", "operating_income", "assets", "equity", "net_income")

UNIVERSES = {
    "us500": ("us", "미국 큰 회사 500 (그 해 매출 순위)"),
    "kr200": ("kr", "한국 큰 회사 200 (그 해 매출 순위)"),
}


def is_basket(universe: str) -> bool:
    return universe in UNIVERSES


@dataclass
class Basket:
    market: str
    size: int
    years: dict = field(default_factory=dict)       # {회계연도: [티커, …] 매출 순}
    names: dict = field(default_factory=dict)       # {티커: 회사 이름}
    filers: dict = field(default_factory=dict)      # {회계연도: 매출을 낸 기업 수(티커 유무와 상관없이)}
    fetched: dict = field(default_factory=dict)     # {회계연도: 받은 시각(epoch 초)}
    fin: dict = field(default_factory=dict)         # 한국만 {회계연도: {종목코드: {항목: 값, 'known': 접수일}}}
    ciks: dict = field(default_factory=dict)        # 미국만 {티커: CIK} — 재무를 받을 때 쓴다

    @property
    def empty(self) -> bool:
        return not any(self.years.values())

    def start_of(self, year: int) -> date:
        month, day = EFFECTIVE.get(self.market, (10, 1))
        return date(year + 1, month, day)

    def year_for(self, day: date) -> int | None:
        """그 날 쓸 수 있는 가장 최근 회계연도(매출이 다 공개된 뒤)."""
        usable = [y for y, names in self.years.items() if names and self.start_of(y) <= day]
        return max(usable) if usable else None

    def members(self, day: date) -> list[str]:
        year = self.year_for(day)
        return list(self.years.get(year, [])) if year is not None else []

    def all_tickers(self) -> list[str]:
        """어느 해든 한 번이라도 바구니에 든 종목 — 최근 해 순위부터."""
        out: dict[str, None] = {}
        for year in sorted(self.years, reverse=True):
            for t in self.years[year]:
                out.setdefault(t, None)
        return list(out)

    def to_dict(self) -> dict:
        return {"market": self.market, "size": self.size, "years": {str(y): v for y, v in self.years.items()},
                "names": self.names, "filers": {str(y): v for y, v in self.filers.items()},
                "fetched": {str(y): v for y, v in self.fetched.items()},
                "fin": {str(y): v for y, v in self.fin.items()}, "ciks": self.ciks}

    @classmethod
    def from_dict(cls, raw: dict, market: str, size: int) -> "Basket":
        def years(name):
            out = {}
            for k, v in (raw.get(name) or {}).items():
                try:
                    out[int(k)] = v
                except (TypeError, ValueError):
                    continue
            return out
        return cls(market, int(raw.get("size") or size), years("years"), dict(raw.get("names") or {}),
                   years("filers"), years("fetched"), years("fin"), dict(raw.get("ciks") or {}))


def pick_ticker(tickers: list[str]) -> str:
    """한 회사에 티커가 여럿이면(클래스주·우선주) 하나만. '-' 없는 보통주 → 짧은 것 → B 주(BRK-B·BF-B).

    BRK-A 처럼 1주가 수십만 달러인 클래스를 고르면 소액 계좌는 한 주도 못 산다.
    """
    def key(t):
        suffix = t.rsplit("-", 1)[-1] if "-" in t else ""
        return ("-" in t, len(t), {"B": 0, "A": 2}.get(suffix, 1), t)
    return min(tickers, key=key)


class BasketBuilder:
    def __init__(self, cache_dir: str | Path, http=None, edgar=None, dart=None) -> None:
        self.dir = Path(cache_dir)
        self.http, self.edgar, self.dart = http, edgar, dart

    def path(self, market: str) -> Path:
        return self.dir / f"quant_basket_{market}.json"

    def load(self, market: str) -> Basket:
        size = SIZES.get(market, 200)
        try:
            raw = json.loads(self.path(market).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Basket(market, size)
        return Basket.from_dict(raw if isinstance(raw, dict) else {}, market, size)

    def save(self, basket: Basket) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path(basket.market).with_suffix(".tmp")
            tmp.write_text(json.dumps(basket.to_dict(), ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path(basket.market))
        except OSError as exc:
            log.warning("종목 바구니를 저장하지 못했습니다: %s", exc)

    def ensure(self, market: str, today: date | None = None, progress=None) -> Basket:
        """없는 해만 받는다. 최근 두 해는 한 달에 한 번 다시(늦게 낸 회사를 넣으려고)."""
        today = today or date.today()
        basket = self.load(market)
        wanted = list(range(today.year - YEARS_BACK, today.year))
        todo = [y for y in wanted
                if y not in basket.years or not basket.years[y]
                or (y >= today.year - 2 and time.time() - float(basket.fetched.get(y) or 0) > REFRESH)]
        for k, year in enumerate(todo):
            if progress:
                progress(k, len(todo), f"{year}년 매출 순위")
            try:
                got = self._us_year(year) if market == "us" else self._kr_year(year)
            except Exception as exc:                      # 한 해가 실패해도 나머지는 받는다
                log.warning("%s %s년 바구니를 받지 못했습니다: %s", market, year, exc)
                got = None
            if not got:
                continue
            ranked, filers, names, fin, ciks = got
            basket.years[year] = ranked[:basket.size]
            basket.filers[year] = filers
            basket.fetched[year] = time.time()
            basket.names.update({t: names[t] for t in basket.years[year] if t in names})
            basket.ciks.update({t: ciks[t] for t in basket.years[year] if t in ciks})
            if fin is not None:
                basket.fin[year] = {t: fin[t] for t in basket.years[year] if t in fin}
            self.save(basket)
        return basket

    # --- 미국 ---------------------------------------------------------------
    def _frame(self, concept: str, year: int) -> dict[int, float]:
        """한 항목·한 해의 {CIK: 값}. 그 해에 그 항목이 없으면(404) 빈 것, **받다 실패하면 예외** —
        한 항목만 빠진 채 순위를 매기면 그 항목을 쓰는 큰 회사들이 통째로 빠진다."""
        from ..universe import FRAMES_URL

        resp = self.http.get(FRAMES_URL.format(concept=concept, period=str(year)), retries=2, timeout=90)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        out: dict[int, float] = {}
        for row in (resp.json() or {}).get("data") or []:
            try:
                cik, value = int(row["cik"]), float(row["val"])
            except (KeyError, TypeError, ValueError):
                continue
            if value > 0:
                out[cik] = max(value, out.get(cik, 0.0))
        return out

    def _us_year(self, year: int):
        from ..universe import REVENUE_CONCEPTS

        by_cik: dict[int, float] = {}
        for concept in REVENUE_CONCEPTS:
            for cik, value in self._frame(concept, year).items():
                by_cik[cik] = max(value, by_cik.get(cik, 0.0))
        if len(by_cik) < MIN_FILERS:
            return None
        tickers = self.edgar.ticker_map()
        try:
            funds = {t.upper() for t in self.edgar.fund_map()}
        except Exception:
            funds = set()
        per_cik: dict[int, list[str]] = {}
        names: dict[str, str] = {}
        for ticker, (cik, name) in tickers.items():
            t = str(ticker).upper()
            if t in funds or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t):
                continue
            try:
                per_cik.setdefault(int(cik), []).append(t)
            except (TypeError, ValueError):
                continue
            names[t] = name
        ranked, ciks = [], {}
        for cik, _value in sorted(by_cik.items(), key=lambda kv: -kv[1]):
            if cik in per_cik:
                ticker = pick_ticker(per_cik[cik])
                ranked.append(ticker)
                ciks[ticker] = cik
        return ranked, len(by_cik), names, None, ciks

    # --- 한국 ---------------------------------------------------------------
    def _kr_year(self, year: int):
        if self.dart is None or not getattr(self.dart, "ready", False):
            return None
        companies = self.dart.corp_codes()              # {종목코드: (고유번호, 이름)}
        if len(companies) < MIN_FILERS:
            return None
        found = self.dart.many_financials([corp for corp, _ in companies.values()], year)
        if len(found) < len(companies) * KR_COVERAGE:
            log.info("DART %s년 주요계정을 %d/%d곳만 받았습니다 — 다음에 다시", year, len(found), len(companies))
            return None
        rows = {}
        for code, fin in found.items():
            revenue = fin.values.get("revenue")
            if not revenue or revenue <= 0:
                continue
            known = ""
            if re.fullmatch(r"\d{14}", fin.rcept_no or ""):
                known = f"{fin.rcept_no[:4]}-{fin.rcept_no[4:6]}-{fin.rcept_no[6:8]}"
            rows[code] = {k: fin.values[k] for k in KR_FIN_KEYS if k in fin.values} | {"known": known}
        if len(rows) < MIN_FILERS:
            return None
        ranked = sorted(rows, key=lambda c: -rows[c]["revenue"])
        names = {code: name for code, (_corp, name) in companies.items()}
        return ranked, len(rows), names, rows, {}
