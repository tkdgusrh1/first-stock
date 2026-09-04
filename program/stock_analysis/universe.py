"""추천 후보 목록을 **SEC 자료에서 직접 만든다.**

전에는 후보 티커를 파일에 손으로 적어 두었다. 그건 공개 자료가 아니라
누군가의 판단이고, 그 판단이 곧 '무엇을 추천받는가' 를 정해 버린다.
목록에 없는 회사는 아무리 좋아도 영영 안 나온다. 그래서 없앴다.

지금은 SEC 가 공개하는 두 가지만 쓴다.
  · XBRL frames — 한 번의 요청으로 **모든 제출 기업의 연간 매출**을 준다
  · company_tickers.json — 그 기업들의 실제 티커

매출 상위 N개를 후보로 삼는다. 매출을 고른 이유는, 무료로 구할 수 있는
값 중에 '실제로 장사를 하고 있는 회사' 를 가리는 데 가장 덜 자의적이기
때문이다. 시가총액은 주가가 필요해 기업마다 따로 받아야 하고, 그러면
후보를 정하는 데만 몇천 번을 물어봐야 한다.

받지 못하면 **비워 둔다.** 대신 쓸 목록을 몰래 끼워 넣지 않는다.
화면에는 이 목록이 어디서 왔는지, 언제 받은 것인지 항상 적는다.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/CY{period}.json"

# 매출을 담는 XBRL 항목은 회사마다 다르다. 회계 기준이 바뀌면서 여러 개가
# 함께 쓰이고 있어서, 여러 항목을 받아 기업별로 가장 큰 값을 쓴다.
REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)

DEFAULT_SIZE = 300          # 후보로 삼을 상위 기업 수
CACHE_TTL = 30 * 24 * 3600  # 매출 순위는 자주 바뀌지 않는다. 한 달에 한 번.
MIN_USABLE = 50             # 이보다 적게 받으면 제대로 받은 것으로 치지 않는다
RETRY_AFTER = 6 * 3600      # 실패했을 때 다시 물어보기까지 기다리는 시간


@dataclass
class Universe:
    """후보 목록과 그 출처. 출처를 모르는 목록은 쓰지 않는다."""

    tickers: list[str] = field(default_factory=list)
    source: str = ""            # 사람이 읽을 출처 한 줄
    period: str = ""            # 어느 회계연도 매출인지
    fetched: str = ""           # 언제 받았는지 (YYYY-MM-DD)
    total_filers: int = 0       # SEC 가 준 기업 수 (그중 상위 몇 개를 골랐는지 알려면)

    @property
    def empty(self) -> bool:
        return not self.tickers

    def describe(self) -> str:
        where = "DART" if self.source == "dart" else "SEC"
        if self.empty:
            return f"후보 목록을 {where} 에서 받지 못했습니다."
        if self.source == "dart":
            return (
                f"DART 에 {self.period}년 사업보고서를 낸 상장사 {self.total_filers:,}곳 중 "
                f"매출 상위 {len(self.tickers)}개 ({self.fetched} 기준)"
            )
        return (
            f"SEC 에 {self.period} 매출을 신고한 {self.total_filers:,}개 기업 중 "
            f"매출 상위 {len(self.tickers)}개 ({self.fetched} 기준)"
        )


def _periods(today: date) -> list[str]:
    """확정된 연간 자료가 있을 만한 회계연도를 최근 것부터.

    연간 자료는 회계연도가 끝나고도 한참 뒤에야 다 모인다. 그래서 작년부터
    거슬러 올라가며 찾는다. 분기가 아니라 연간을 쓰는 이유는, 회사마다
    회계 분기가 달라서 특정 분기 프레임에는 절반쯤이 빠지기 때문이다.
    """
    return [str(today.year - n) for n in (1, 2, 3)]


class KoreanUniverseBuilder:
    """DART 에서 한국 후보 목록을 만든다.

    한국에는 SEC 의 frames 같은 '전 기업 매출 한 번에' 창구가 없다. 대신
    '다중회사 주요계정' 이 여러 회사를 한 번에 주므로, 상장사 전체를 묶음으로
    나눠 물어보고 매출로 줄 세운다. 상장사는 2,600곳쯤이라 100개씩 26번이면
    끝난다 — 한 곳씩 물어보면 2,600번이라 하루가 간다.

    받지 못하면 **비워 둔다.** 대신 쓸 목록을 몰래 끼워 넣지 않는다.
    """

    def __init__(self, dart, cache_dir: str | Path) -> None:
        self.dart = dart
        self.path = Path(cache_dir) / "universe_kr.json"
        self._cached: Universe | None = None
        self._failed_at = 0.0

    def cached(self) -> Universe:
        if self._cached is not None:
            return self._cached
        self._cached = _load(self.path)
        return self._cached

    def ensure(self, size: int = DEFAULT_SIZE, today: date | None = None) -> Universe:
        saved = self.cached()
        if not saved.empty and _fresh(self.path):
            return saved
        # 실패한 직후에 또 묻지 않는다. 이 조회는 스물몇 번의 요청이라,
        # DART 가 막힌 날 감시 주기마다 되풀이하면 하루 한도를 그것만으로 쓴다.
        if time.time() - self._failed_at < RETRY_AFTER:
            return saved

        got = self.build(size, today)
        if not got.empty:
            self._failed_at = 0.0
            self._cached = got
            _save(self.path, got)
            return got
        self._failed_at = time.time()
        return saved

    def build(self, size: int = DEFAULT_SIZE, today: date | None = None) -> Universe:
        day = today or date.today()
        if not getattr(self.dart, "ready", False):
            return Universe()

        companies = self.dart.corp_codes()          # {종목코드: (고유번호, 이름)}
        if len(companies) < MIN_USABLE:
            log.info("DART 상장사 목록을 받지 못했습니다 (%d개).", len(companies))
            return Universe()

        by_code: dict[str, float] = {}
        used_year = ""
        for year in (day.year - 1, day.year - 2):
            found = self.dart.many_financials(
                [corp for corp, _name in companies.values()], year)
            for code, fin in found.items():
                revenue = fin.values.get("revenue")
                if revenue and revenue > 0:
                    by_code[code] = revenue
            if len(by_code) >= MIN_USABLE:
                used_year = str(year)
                break
            by_code.clear()

        if len(by_code) < MIN_USABLE:
            log.info("DART 에서 매출 순위를 받지 못했습니다 (기업 %d개).", len(by_code))
            return Universe()

        ranked = sorted(by_code, key=lambda c: by_code[c], reverse=True)[:max(1, size)]
        return Universe(
            tickers=ranked,
            source="dart",
            period=used_year,
            fetched=day.isoformat(),
            total_filers=len(by_code),
        )


def _fresh(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime < CACHE_TTL
    except OSError:
        return False


def _load(path: Path) -> Universe:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Universe()
    return Universe(
        tickers=[str(t).upper() for t in (raw.get("tickers") or [])],
        source=str(raw.get("source") or ""),
        period=str(raw.get("period") or ""),
        fetched=str(raw.get("fetched") or ""),
        total_filers=int(raw.get("total_filers") or 0),
    )


def _save(path: Path, universe: Universe) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "tickers": universe.tickers,
            "source": universe.source,
            "period": universe.period,
            "fetched": universe.fetched,
            "total_filers": universe.total_filers,
        }, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.debug("후보 목록을 저장하지 못했습니다: %s", exc)


class UniverseBuilder:
    """SEC 에서 후보 목록을 받아 온다. 받은 것은 파일에 저장한다."""

    def __init__(self, http, edgar, cache_dir: str | Path) -> None:
        self.http = http
        self.edgar = edgar
        self.path = Path(cache_dir) / "universe.json"
        self._cached: Universe | None = None
        self._failed_at = 0.0

    # --- 저장해둔 것 ------------------------------------------------------
    def cached(self) -> Universe:
        if self._cached is not None:
            return self._cached
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._cached = Universe()
            return self._cached
        self._cached = Universe(
            tickers=[str(t).upper() for t in (raw.get("tickers") or [])],
            source=str(raw.get("source") or ""),
            period=str(raw.get("period") or ""),
            fetched=str(raw.get("fetched") or ""),
            total_filers=int(raw.get("total_filers") or 0),
        )
        return self._cached

    def _save(self, universe: Universe) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({
                "tickers": universe.tickers,
                "source": universe.source,
                "period": universe.period,
                "fetched": universe.fetched,
                "total_filers": universe.total_filers,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.debug("후보 목록을 저장하지 못했습니다: %s", exc)

    def _fresh(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime < CACHE_TTL
        except OSError:
            return False

    # --- 받아 오기 --------------------------------------------------------
    def ensure(self, size: int = DEFAULT_SIZE, today: date | None = None) -> Universe:
        """후보 목록. 저장해둔 게 쓸 만하면 그걸 쓰고, 아니면 새로 받는다."""
        saved = self.cached()
        if not saved.empty and self._fresh():
            return saved

        # 한 번 실패했으면 한동안 다시 묻지 않는다. 이 조회는 항목마다 수 MB 라,
        # SEC 가 막힌 날 감시 주기(15분)마다 열두 번씩 시도하면 그것만으로
        # 하루를 다 쓴다. 남의 서버에도 할 짓이 아니다.
        if time.time() - self._failed_at < RETRY_AFTER:
            return saved

        got = self.build(size, today)
        if not got.empty:
            self._failed_at = 0.0
            self._cached = got
            self._save(got)
            return got
        self._failed_at = time.time()
        return saved            # 새로 못 받았으면 예전 것이라도 쓴다

    def build(self, size: int = DEFAULT_SIZE, today: date | None = None) -> Universe:
        """SEC 에서 매출 순위를 받아 후보 목록을 만든다. 실패하면 빈 목록."""
        day = today or date.today()

        by_cik: dict[int, float] = {}
        used_period = ""
        for period in _periods(day):
            for concept in REVENUE_CONCEPTS:
                for cik, value in self._frame(concept, period).items():
                    if value > by_cik.get(cik, 0.0):
                        by_cik[cik] = value
            if len(by_cik) >= MIN_USABLE:
                used_period = period
                break

        if len(by_cik) < MIN_USABLE:
            log.info("SEC 에서 매출 순위를 받지 못했습니다 (기업 %d개).", len(by_cik))
            return Universe()

        # 티커를 못 붙이면 살 수 없는 목록이다. 요청한 개수보다 훨씬 적게
        # 나왔다면 티커 목록 쪽이 잘못된 것이니 쓰지 않는다.
        tickers = self._to_tickers(by_cik, size)
        if len(tickers) < min(MIN_USABLE, size):
            log.info("매출 순위는 받았지만 티커를 붙이지 못했습니다 (%d개).", len(tickers))
            return Universe()

        return Universe(
            tickers=tickers,
            source="SEC XBRL frames (us-gaap 매출) + company_tickers.json",
            period=f"{used_period}년",
            fetched=day.isoformat(),
            total_filers=len(by_cik),
        )

    def _frame(self, concept: str, period: str) -> dict[int, float]:
        """한 항목·한 해의 값을 {CIK: 값} 으로. 실패하면 빈 것."""
        url = FRAMES_URL.format(concept=concept, period=period)
        try:
            payload = self.http.get_json(url, retries=1, timeout=60)
        except Exception as exc:
            log.debug("frames 조회 실패 %s %s: %s", concept, period, exc)
            return {}

        out: dict[int, float] = {}
        for row in (payload or {}).get("data") or []:
            try:
                cik, value = int(row["cik"]), float(row["val"])
            except (KeyError, TypeError, ValueError):
                continue
            if value > 0:
                out[cik] = max(value, out.get(cik, 0.0))
        log.debug("frames %s %s: 기업 %d개", concept, period, len(out))
        return out

    def _to_tickers(self, by_cik: dict[int, float], size: int) -> list[str]:
        """매출 순위에 실제 티커를 붙인다. 티커가 없는 기업은 뺀다.

        비상장이거나 ADR 만 있는 기업은 SEC 에 보고서는 내지만 티커 목록에
        없다. 살 수 없는 것을 추천할 수는 없으니 여기서 걸러진다.
        """
        try:
            ticker_map = self.edgar.ticker_map()
        except Exception as exc:
            log.debug("티커 목록을 받지 못했습니다: %s", exc)
            return []

        # {CIK: 티커}. 같은 회사에 여러 티커(클래스주)가 있으면 짧은 쪽을 쓴다.
        by_number: dict[int, str] = {}
        for ticker, (cik, _name) in ticker_map.items():
            try:
                number = int(cik)
            except (TypeError, ValueError):
                continue
            current = by_number.get(number)
            if current is None or (len(ticker), ticker) < (len(current), current):
                by_number[number] = ticker.upper()

        funds = self._fund_tickers()
        out: list[str] = []
        for cik, _revenue in sorted(by_cik.items(), key=lambda kv: -kv[1]):
            ticker = by_number.get(cik)
            # ETF·펀드는 회사가 아니다. 매출 순위에 섞여 들어올 일은 없지만,
            # 목록이 겹칠 수 있으니 여기서 확실히 뺀다.
            if ticker and ticker not in funds and ticker not in out:
                out.append(ticker)
            if len(out) >= size:
                break
        return out

    def _fund_tickers(self) -> set[str]:
        try:
            return {t.upper() for t in self.edgar.fund_map()}
        except Exception:
            return set()


__all__ = ["Universe", "UniverseBuilder", "REVENUE_CONCEPTS", "DEFAULT_SIZE", "FRAMES_URL"]
