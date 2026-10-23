"""종목 바구니 — '그 해에 실제로 큰 회사' 였던 곳 (그 해 매출 순위).

지금 큰 회사들로 과거를 시험하면 미래를 알고 고른 셈이다 — 2016년의 백테스트가 2024년에야 커진 회사를
고르게 된다. 그래서 회계연도마다 그 해 매출 순위로 바구니를 따로 만들고, 그 매출이 공개된 뒤부터만 쓴다.

- 미국: SEC XBRL frames(그 해 연간 매출, 모든 제출 기업 — 매출 항목 여러 개 중 큰 값)
  → 지금 티커가 있는 회사만 → 매출 상위 N. 다음 해 10월 1일부터 쓴다(회계연도가 6월에 끝나는 회사의
  연간 보고서까지 다 나온 뒤). 지주회사 전환 등으로 SEC 번호(CIK)가 바뀐 회사는 예전 번호의 매출을
  이름이 같은 지금 회사에 잇는다(예: 엑슨모빌).
- 한국: DART 다중회사 주요계정(그 해 사업보고서 매출) → 지금 상장된 회사만 → 매출 상위 N.
  다음 해 4월 15일부터 쓴다(사업보고서 마감 3월 말). 같은 응답의 영업이익·자산·자본·순이익과
  접수일도 저장한다 — 한국 수익성 점수에 쓴다.

SEC 원자료에는 단위를 잘못 넣은 매출이 실제로 있다(2023년 매출이 30조 달러로 적힌 소형주, 해마다 1000배로
적는 회사). 그대로 순위를 매기면 이런 회사가 1위가 된다. 그래서 ① 어떤 기업보다도 큰 값 ② 총자산의 100배가
넘는 값 ③ 앞뒤 해보다 한 해만 크게 튀었는데 자산에 비해서도 이상한(자산의 10배 넘는) 값이나 수백 배 튄 값은
빼고, 뺀 것은 화면에 적는다. 값을 고쳐 쓰지는 않는다. 한 해만 진짜로 튄 회사(2021년 백신 매출)는 남긴다.

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

# 일반 회사의 매출 항목(universe.REVENUE_CONCEPTS) 말고도 — 은행·증권의 순영업수익, 전력·가스 회사의 영업수익,
# 2018년 회계기준 변경 전에 쓰던 옛 항목들. 회사마다 가장 큰 값(전체 매출)을 쓴다.
MORE_CONCEPTS = ("RevenuesNetOfInterestExpense", "RegulatedAndUnregulatedOperatingRevenue", "SalesRevenueGoodsNet",
                 "SalesRevenueServicesNet", "OilAndGasRevenue", "ElectricUtilityRevenue",
                 "HealthCareOrganizationRevenue", "RealEstateRevenueNet")

# 단위 오류 거르기
CAP = {"us": 1.5e12, "kr": 2e15}  # 1년 매출이 이보다 큰 회사는 없다(월마트 약 0.7조 달러, 삼성전자 약 300조 원)
SPIKE_BOTH = 10                   # 앞(1~2년 전)·뒤(1~2년 뒤) 모두보다 이만큼 크면 그 해만 튄 값
SPIKE_ONE = 30                    # 한쪽(최근 해는 앞쪽)만 있으면
SPIKE_SURE = 100                  # 이만큼 튀었으면 자산과 상관없이 단위 오류
ASSET_RATIO = 100                 # 매출이 총자산(앞뒤 해 중 가장 큰 값)의 이만큼을 넘으면 단위 오류
ASSET_ODD = 10                    # 튄 해에 매출이 총자산의 이만큼을 넘으면 단위 오류로 본다(도매·상사도 드묾)
KEEP_ASSETS = 3000                # 해마다 매출 상위 이만큼만 총자산을 남긴다
KEEP_NAMES = 1500                 # 〃 회사 이름(번호가 바뀐 회사를 이름으로 잇는다)
KEEP_FIN = 600                    # 한국 — 〃 주요계정 줄

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
    ciks: dict = field(default_factory=dict)        # 미국만 {티커: [CIK, 예전 CIK…]} — 재무를 받을 때 쓴다
    dropped: dict = field(default_factory=dict)     # {회계연도: [[회사, 매출, 뺀 이유], …]} 순위 안에 들 뻔한 것만

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
        def by_year(d):
            return {str(y): v for y, v in d.items()}
        return {"market": self.market, "size": self.size, "years": by_year(self.years), "names": self.names,
                "filers": by_year(self.filers), "fetched": by_year(self.fetched), "fin": by_year(self.fin),
                "ciks": self.ciks, "dropped": by_year(self.dropped)}

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
        ciks = {t: (v if isinstance(v, list) else [v]) for t, v in (raw.get("ciks") or {}).items()}
        return cls(market, int(raw.get("size") or size), years("years"), dict(raw.get("names") or {}),
                   years("filers"), years("fetched"), years("fin"), ciks, years("dropped"))


def pick_ticker(tickers: list[str]) -> str:
    """한 회사에 티커가 여럿이면(클래스주·우선주) 하나만. '-' 없는 보통주 → 짧은 것 → B 주(BRK-B·BF-B).

    BRK-A 처럼 1주가 수십만 달러인 클래스를 고르면 소액 계좌는 한 주도 못 산다.
    """
    def key(t):
        suffix = t.rsplit("-", 1)[-1] if "-" in t else ""
        return ("-" in t, len(t), {"B": 0, "A": 2}.get(suffix, 1), t)
    return min(tickers, key=key)


_FORM_WORDS = {"inc", "incorporated", "corp", "corporation", "co", "company", "companies", "ltd", "limited", "plc",
               "llc", "lp", "the", "new", "de", "nv", "sa", "ag", "se", "holdings", "holding", "group"}


def name_key(name: str) -> str:
    """회사 이름 비교용 — 'Exxon Mobil Corporation' · 'EXXON MOBIL CORP /NJ/' · 'ExxonMobil Holdings Corp' 를 같게
    (법인 꼬리말과 띄어쓰기를 뺀다)."""
    text = re.sub(r"/[a-z]{1,4}/?", " ", str(name or "").lower())
    return "".join(w for w in re.sub(r"[^a-z0-9]+", " ", text).split() if w not in _FORM_WORDS)


def suspicious(market: str, year: int, key: str, rev: dict, assets: dict) -> str | None:
    """단위를 잘못 넣은 것으로 보이는 매출이면 그 이유. 한 해 값만 보고 빼지 않게 앞뒤 해·총자산과 견준다.

    rev·assets = {회계연도: {회사: 값}}. 앞뒤 해는 2년씩 보고 큰 값을 쓴다(옆 해가 거꾸로 틀린 경우에 버티게).
    총자산도 앞뒤 해 중 큰 값 — 그 해 총자산만 잘못 적힌 회사를 매출 오류로 몰지 않게.
    """
    value = rev[year][key]
    if value > CAP.get(market, CAP["us"]):
        return "어느 기업보다도 큰 1년 매출"
    held = [assets[y][key] for y in (year - 1, year, year + 1) if assets.get(y, {}).get(key)]
    per_asset = value / max(held) if held else None
    if per_asset is not None and per_asset >= ASSET_RATIO:
        return f"총자산의 {per_asset:,.0f}배"
    sides = []
    for near in ((year - 1, year - 2), (year + 1, year + 2)):
        got = [rev[y][key] for y in near if rev.get(y, {}).get(key)]
        if got:
            sides.append(max(got))
    jump = value / max(sides) if sides else 0.0
    if jump >= (SPIKE_BOTH if len(sides) == 2 else SPIKE_ONE) and (
            jump >= SPIKE_SURE or (per_asset is not None and per_asset >= ASSET_ODD)):
        return f"앞뒤 해 매출의 {jump:,.0f}배" + (f", 총자산의 {per_asset:,.0f}배" if per_asset else "")
    return None


def _top(values: dict, n: int) -> list:
    return sorted(values, key=lambda k: -values[k])[:n]


class BasketBuilder:
    def __init__(self, cache_dir: str | Path, http=None, edgar=None, dart=None) -> None:
        self.dir = Path(cache_dir)
        self.http, self.edgar, self.dart = http, edgar, dart

    def path(self, market: str) -> Path:
        return self.dir / f"quant_basket_{market}.json"

    def raw_path(self, market: str) -> Path:
        """해마다 받은 원자료(모든 회사의 매출·총자산) — 단위 오류를 앞뒤 해와 견줘 거르려고 남긴다."""
        return self.dir / f"quant_basket_{market}_raw.json"

    def load(self, market: str) -> Basket:
        size = SIZES.get(market, 200)
        raw = self._read(self.path(market))
        return Basket.from_dict(raw, market, size)

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, path: Path, data: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("종목 바구니를 저장하지 못했습니다: %s", exc)

    def save(self, basket: Basket) -> None:
        self._write(self.path(basket.market), basket.to_dict())

    def ensure(self, market: str, today: date | None = None, progress=None) -> Basket:
        """없는 해만 받는다. 최근 두 해는 한 달에 한 번 다시(늦게 낸 회사를 넣으려고). 순위는 매번 다시 매긴다."""
        today = today or date.today()
        raw = self._read(self.raw_path(market))
        for part in ("rev", "assets", "names", "fin", "fetched"):
            raw.setdefault(part, {})
        first = today.year - YEARS_BACK
        wanted = range(first - (market == "us"), today.year)    # 미국은 맨 앞 한 해를 더 — '튄 값' 견주기용
        for part in ("rev", "assets", "fin", "fetched"):        # 창 밖으로 밀려난 옛 해는 지운다
            raw[part] = {y: v for y, v in raw[part].items() if y.isdigit() and int(y) >= wanted.start}
        todo = [y for y in wanted
                if str(y) not in raw["rev"]
                or (y >= today.year - 2 and time.time() - float(raw["fetched"].get(str(y)) or 0) > REFRESH)]
        for k, year in enumerate(todo):
            if progress:
                progress(k, len(todo), f"{year}년 매출 순위")
            try:
                got = self._us_raw(year) if market == "us" else self._kr_raw(year)
            except Exception as exc:                      # 한 해가 실패해도 나머지는 받는다
                log.warning("%s %s년 바구니를 받지 못했습니다: %s", market, year, exc)
                got = None
            if not got:
                continue
            rev, assets, names, fin = got
            raw["rev"][str(year)], raw["assets"][str(year)] = rev, assets
            raw["names"].update(names)
            if fin is not None:
                raw["fin"][str(year)] = fin
            raw["fetched"][str(year)] = time.time()
            self._write(self.raw_path(market), raw)
        if not raw["rev"]:
            return self.load(market)
        try:
            basket = self._rank(market, raw, range(first, today.year))
        except Exception as exc:                          # 티커 목록을 못 받았으면 전에 만든 것을 쓴다
            log.warning("%s 종목 바구니 순위를 매기지 못했습니다: %s", market, exc)
            return self.load(market)
        self.save(basket)
        return basket

    # --- 순위 ---------------------------------------------------------------
    def _rank(self, market: str, raw: dict, years) -> Basket:
        size = SIZES.get(market, 200)
        basket = Basket(market, size)
        rev = {int(y): v for y, v in raw["rev"].items()}
        assets = {int(y): v for y, v in raw["assets"].items()}
        if market == "us":
            per_cik, names, by_name = self._us_tickers()
        elif self.dart is not None and getattr(self.dart, "ready", False):
            names = {code: name for code, (_corp, name) in self.dart.corp_codes().items()}
        else:
            raise RuntimeError("DART 인증키가 없어 지금 상장된 회사 목록을 모릅니다")
        if len(names) < MIN_FILERS:
            raise RuntimeError("지금 상장된 회사 목록(티커)을 받지 못했습니다")
        for year in years:
            if year not in rev:
                continue
            ranked, dropped = [], []
            for key in sorted(rev[year], key=lambda k: -rev[year][k]):
                if market == "us":
                    ticker, own = self._us_ticker(key, rev[year], raw["names"], per_cik, by_name)
                else:
                    ticker, own = (key, None) if key in names else (None, None)
                if ticker is None or ticker in ranked:
                    continue
                reason = suspicious(market, year, key, rev, assets)
                if reason:
                    dropped.append([names.get(ticker) or raw["names"].get(key) or ticker, rev[year][key], reason])
                    continue
                ranked.append(ticker)
                if own is not None:
                    found = basket.ciks.setdefault(ticker, [own])
                    if int(key) not in found:
                        found.append(int(key))
                if len(ranked) >= size:
                    break
            basket.years[year] = ranked
            basket.filers[year] = len(rev[year])
            basket.fetched[year] = raw["fetched"].get(str(year))
            basket.names.update({t: names[t] for t in ranked if t in names})
            if dropped:
                basket.dropped[year] = dropped
            if market == "kr":
                rows = raw["fin"].get(str(year)) or {}
                basket.fin[year] = {t: rows[t] for t in ranked if t in rows}
        return basket

    # --- 미국 ---------------------------------------------------------------
    def _frame(self, concept: str, period: str) -> list[dict]:
        """한 항목·한 기간의 행들. 그 기간에 그 항목이 없으면(404) 빈 것, **받다 실패하면 예외** —
        한 항목만 빠진 채 순위를 매기면 그 항목을 쓰는 큰 회사들이 통째로 빠진다."""
        from ..universe import FRAMES_URL

        resp = self.http.get(FRAMES_URL.format(concept=concept, period=period), retries=2, timeout=90)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return (resp.json() or {}).get("data") or []

    def _us_raw(self, year: int):
        from ..universe import REVENUE_CONCEPTS

        rev: dict[str, float] = {}
        entity: dict[str, str] = {}
        for concept in REVENUE_CONCEPTS + MORE_CONCEPTS:
            for row in self._frame(concept, str(year)):
                try:
                    cik, value = str(int(row["cik"])), float(row["val"])
                except (KeyError, TypeError, ValueError):
                    continue
                if value > rev.get(cik, 0.0):
                    rev[cik] = value
                    entity[cik] = str(row.get("entityName") or "")
        if len(rev) < MIN_FILERS:
            return None
        big = set(_top(rev, KEEP_ASSETS))
        assets = {}
        for row in self._frame("Assets", f"{year}Q4I"):
            try:
                cik, value = str(int(row["cik"])), float(row["val"])
            except (KeyError, TypeError, ValueError):
                continue
            if cik in big and value > 0:
                assets[cik] = value
        names = {cik: entity[cik] for cik in _top(rev, KEEP_NAMES) if entity.get(cik)}
        return rev, assets, names, None

    def _us_tickers(self):
        """{CIK: [티커…]}, {티커: 이름}, {이름 열쇠: CIK}(그 이름을 쓰는 CIK 가 하나뿐인 것만)."""
        try:
            funds = {t.upper() for t in self.edgar.fund_map()}
        except Exception:
            funds = set()
        per_cik: dict[int, list[str]] = {}
        names: dict[str, str] = {}
        for ticker, (cik, name) in self.edgar.ticker_map().items():
            t = str(ticker).upper()
            if t in funds or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t):
                continue
            try:
                per_cik.setdefault(int(cik), []).append(t)
            except (TypeError, ValueError):
                continue
            names[t] = name
        by_name: dict[str, int | None] = {}
        for cik, tickers in per_cik.items():
            key = name_key(names[tickers[0]])
            if key:
                by_name[key] = cik if by_name.get(key, cik) == cik else None
        return per_cik, names, by_name

    @staticmethod
    def _us_ticker(key: str, rev_year: dict, entity: dict, per_cik: dict, by_name: dict):
        """매출을 낸 CIK → (티커, 지금 그 티커의 CIK). 티커가 없는 번호는 이름이 같은 지금 회사가
        그 해 따로 매출을 내지 않았을 때만 잇는다(지주회사 전환 등으로 번호만 바뀐 경우)."""
        cik = int(key)
        own = cik if cik in per_cik else by_name.get(name_key(entity.get(key, "")))
        if own is None or (own != cik and str(own) in rev_year):
            return None, None
        return pick_ticker(per_cik[own]), own

    # --- 한국 ---------------------------------------------------------------
    def _kr_raw(self, year: int):
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
        rev = {code: row["revenue"] for code, row in rows.items()}
        assets = {code: row["assets"] for code, row in rows.items() if (row.get("assets") or 0) > 0}
        return rev, assets, {}, {code: rows[code] for code in _top(rev, KEEP_FIN)}
