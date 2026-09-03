"""봇 오케스트레이션: 공시 감시, 지표 계산, 데일리 브리핑."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .assessment import assess
from .commands import CommandRouter
from .config import Config, Watch, load_config
from .earnings import Earnings, due_reminders, next_earnings
from .estimates import EstimateClient
from .filing_text import fetch_filing_text
from .funds import FUND_FORMS, detect_fund
from .fx import FxClient
from .macro import MacroClient
from .insiders import DEFAULT_DAYS, since_day, summarize
from .korean import annotate
from .translate import Translator
from .recap import build_recap
from .risk_watch import build_risk_change
from . import markets, screener
from .dart import DartClient
from .dart import summarize as dart_summary
from .universe import DEFAULT_SIZE, UniverseBuilder
from .guidance import fetch_guidance
from .econ_calendar import EconEvent, fomc_coverage_end, parse_extra_events, upcoming_events
from .edgar import EdgarClient, default_since
from .http import HttpClient
from .market_calendar import upcoming_market_days
from .messages import (
    format_daily_brief,
    format_downgrade,
    format_earnings_reminder,
    format_dart_filing,
    format_filing,
    format_metrics,
    format_news,
    format_price_alert,
    summarize_filing,
)
from .metrics import (
    _return_since,
    Metrics,
    apply_guidance,
    build_fund_metrics,
    build_dart_metrics,
    build_metrics,
    build_peer_metrics,
)
from .news import NewsWatcher
from .overrides import Overrides
from .peers import find_peers
from .prices import PriceClient
from .state import State
from .telegram import TelegramNotifier, esc
from .timeutil import now
from .track_record import build_track_record
from .xbrl import XbrlClient

log = logging.getLogger(__name__)

# '시장 흐름' 을 견줄 기준. S&P 500 을 따라가는 가장 거래가 많은 ETF 다.
MARKET_TICKER = "SPY"


@dataclass
class Target:
    watch: Watch
    cik: str
    name: str
    fund: object | None = None     # ETF·펀드면 FundInfo, 아니면 None
    corp_code: str = ""            # 한국 종목의 DART 고유번호 (없을 수 있다)

    @property
    def ticker(self) -> str:
        return self.watch.ticker or self.cik

    @property
    def is_fund(self) -> bool:
        return self.fund is not None

    @property
    def market(self) -> str:
        """미국(us) 인지 한국(kr) 인지. 티커 모양으로 가른다."""
        return markets.market_of(self.ticker)

    @property
    def price_symbol(self) -> str:
        """시세를 받을 때 쓸 기호. 한국은 005930.KS 처럼 붙는다."""
        return markets.price_symbol(self.ticker)


class Bot:
    def __init__(self, config: Config, dry_run: bool = False) -> None:
        self.config = config
        self.http = HttpClient(user_agent=config.user_agent)
        self.edgar = EdgarClient(self.http, config.cache_dir)
        self.xbrl = XbrlClient(self.http, config.cache_dir)
        self.prices = PriceClient(self.http)
        self.estimates = EstimateClient(self.http)
        self.fx = FxClient(self.http)
        self.macro = MacroClient(self.http, config.cache_dir)
        # 한국 종목의 공시·재무제표. 열쇠가 없으면 조용히 비운다.
        self.dart = DartClient(self.http, str(config.raw.get("dart_api_key") or ""),
                               config.cache_dir)
        self.state = State(config.state_path)
        self.notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id, dry_run=dry_run)
        self.overrides = Overrides(config.overrides_path)
        self.translator = self._build_translator()
        self.commands = CommandRouter(self)
        self.news = NewsWatcher(self.http, self.state, config)
        self._targets: list[Target] | None = None
        self.dashboard_server = None      # 스스로 다시 켤 때 화면을 먼저 놓으려고 들고 있는다
        self._targets_full = False       # 설정의 종목을 전부 찾아냈나
        self._metrics_cache: dict[str, Metrics] = {}
        self._metrics_error: dict[str, str] = {}
        self._earnings_cache: dict[str, Earnings | None] = {}
        self._report_cache: dict = {}
        self._assessment_cache: dict = {}
        self._guidance_cache: dict = {}
        self._industry_cache: dict = {}
        self._estimate_cache: dict = {}
        self._track_cache: dict = {}
        self._fund_cache: dict = {}
        self._risk_cache: dict = {}
        self._insider_cache: dict = {}
        self._korean_cache: dict = {}
        self._peer_cache: dict = {}   # 티커별 비교 지표 (종목끼리 공유)
        # 추천 후보를 본 결과. 반나절 걸리는 일이라 파일에 남긴다.
        self._picks = screener.PickStore(Path(config.cache_dir) / "screen.json")
        # 후보 목록은 SEC 매출 순위에서 만든다 (손으로 적은 목록을 쓰지 않는다)
        self.universe_builder = UniverseBuilder(self.http, self.edgar, config.cache_dir)
        self._market_returns: tuple | None = None   # 시장 수익률 (한 번만 받는다)
        self._metrics_cached_at = time.monotonic()
        self._config_mtime = self._mtime(config.path)

    # --- 번역기 ----------------------------------------------------------
    def translate_settings(self) -> dict:
        """config.yml 의 값 위에 화면에서 바꾼 값을 덮는다.

        열쇠를 화면에서 붙여넣을 수 있게 하려고 두 곳을 합친다.
        config 파일을 직접 고치지 않아도 되게 하는 게 목적이다.
        """
        base = self.config.raw.get("translate")
        merged = dict(base) if isinstance(base, dict) else {}
        merged.update(self.overrides.settings("translate"))
        return merged

    def _build_translator(self) -> Translator:
        settings = self.translate_settings()
        return Translator(
            self.http, self.config.cache_dir,
            enabled=bool(settings.get("enabled", True)),
            target=str(settings.get("target", "ko")),
            settings=settings,
        )

    def reload_translator(self) -> Translator:
        """설정을 바꾼 뒤 새 번역기로 갈아끼우고, 만들어둔 한글을 지운다."""
        self.translator = self._build_translator()
        self._korean_cache.clear()
        return self.translator

    def cached_metrics(self) -> dict[str, Metrics]:
        """대시보드가 읽어가는 계산 완료분 (없으면 비어 있음)."""
        return dict(self._metrics_cache)

    def cached_earnings(self) -> dict[str, Earnings | None]:
        return dict(self._earnings_cache)

    # --- 실행 중 갱신 ----------------------------------------------------
    @staticmethod
    def _mtime(path) -> float | None:
        try:
            return Path(path).stat().st_mtime if path else None
        except OSError:
            return None

    def reload_watchlist(self) -> None:
        """overrides 변경 후 감시 목록을 다시 만든다 (재시작 불필요)."""
        self.overrides.load()
        base = [w for w in self.config.watchlist if w.source == "config"]
        self.config.watchlist = self.overrides.apply(base)
        self._targets = None
        self._targets_full = False

        # 계산해둔 지표를 통째로 버리면, 종목을 하나 추가할 때마다 나머지가
        # 다시 '불러오는 중' 으로 돌아간다. 빠진 종목 것만 정리한다.
        live = {t.cik for t in self.targets()}
        for cache in (self._metrics_cache, self._earnings_cache, self._metrics_error,
                      self._report_cache, self._assessment_cache,
                      self._guidance_cache, self._industry_cache, self._estimate_cache,
                      self._track_cache, self._fund_cache, self._risk_cache,
                      self._insider_cache, self._korean_cache):
            for cik in [c for c in cache if c not in live]:
                cache.pop(cik, None)

    def reload_config_if_changed(self) -> bool:
        """config.yml 을 저장하면 재시작 없이 반영한다."""
        path = self.config.path
        if not path:
            return False
        mtime = self._mtime(path)
        if mtime is None or mtime == self._config_mtime:
            return False
        self._config_mtime = mtime
        try:
            fresh = load_config(path)
        except Exception as exc:
            log.error("설정을 다시 읽지 못했습니다(이전 설정 유지): %s", exc)
            return False

        # 실행 중 바꿔도 안전한 값만 갈아끼운다
        keep_dry_run = self.notifier.dry_run
        self.config = fresh
        self.notifier = TelegramNotifier(
            fresh.telegram_token, fresh.telegram_chat_id, dry_run=keep_dry_run
        )
        self.overrides = Overrides(fresh.overrides_path)
        self.translator = self._build_translator()
        self.reload_watchlist()
        log.info("설정을 다시 읽었습니다: %s (종목 %d개)", path, len(self.targets()))
        return True

    # --- 대상 해석 ------------------------------------------------------
    def targets(self) -> list[Target]:
        """감시 대상. 티커를 SEC 의 CIK 로 바꿔둔 것.

        **한 번 정하면 그대로 돌려준다.** 화면을 그릴 때마다 여기서 네트워크를
        쓰면, SEC 가 느리거나 막힌 날에는 그 시간만큼 화면이 통째로 멈춘다.
        SEC 가 막혀서 못 찾은 종목은 retry_unresolved() 로 다시 시도하는데,
        그건 백그라운드 작업에서만 부른다.
        """
        if self._targets is not None:
            return self._targets

        # SEC 티커 목록은 **미국 종목이 있을 때만** 받는다. 한국 종목은 SEC 에
        # 없으므로, SEC 가 막힌 날에 한국 종목까지 같이 사라지면 안 된다.
        us_watch = [w for w in self.config.watchlist
                    if markets.market_of(w.ticker or "") != markets.KR]
        sec_ok = True
        if any(not w.cik for w in us_watch):
            try:
                self.edgar.ticker_map()
            except Exception as exc:
                log.error("SEC 티커 목록을 받지 못했습니다: %s", exc)
                sec_ok = False

        out: list[Target] = []
        for watch in self.config.watchlist:      # 설정에 적힌 순서를 지킨다
            if markets.market_of(watch.ticker or "") == markets.KR:
                out.append(self._korean_target(watch))
                continue
            if not sec_ok:
                if watch.cik:
                    out.append(Target(watch=watch, cik=f"{int(watch.cik):010d}",
                                      name=watch.name or ""))
                continue
            try:
                cik, name = self.edgar.resolve(watch.ticker or None, watch.cik)
            except Exception as exc:
                log.error("종목 해석 실패 %s: %s", watch.ticker or watch.cik, exc)
                continue
            out.append(Target(watch=watch, cik=cik, name=watch.name or name))
        return self._remember_targets(out)

    def _korean_target(self, watch: Watch) -> Target:
        """한국 종목. DART 고유번호를 cik 자리에 담는다.

        열쇠가 없으면 고유번호를 못 받는다. 그래도 대상에서 빼지는 않는다 —
        시세는 야후에서 받을 수 있어서 주가는 보여줄 수 있기 때문이다.
        """
        code = markets.code_of(watch.ticker or "")
        # 여기서 네트워크를 쓰면 안 된다. 화면을 그릴 때마다 불리는 자리라
        # DART 가 느린 날에는 그 시간만큼 화면이 통째로 멈춘다.
        # 회사 목록은 백그라운드(load_dart_codes)에서 미리 받아 둔다.
        # cik 자리에는 **반드시 값이 있어야 한다.** 이 값이 지표·판정 캐시의
        # 열쇠로 쓰이는데, 비어 있으면 '아직 안 채워짐' 으로 영영 남아서
        # 화면이 '불러오는 중' 에서 빠져나오지 못한다.
        # DART 고유번호는 따로 담는다 (열쇠가 없으면 못 받는다).
        return Target(watch=watch, cik=code, name=watch.name or code,
                      corp_code=self.dart.corp_code_cached(code))

    def _remember_targets(self, found: list[Target]) -> list[Target]:
        self._targets = found
        self._targets_full = len(found) == len(self.config.watchlist)
        return found

    def retry_unresolved(self) -> bool:
        """못 찾은 종목이 있으면 다시 해석해본다. **백그라운드에서만** 부른다.

        SEC 가 잠깐 막혔다고 '종목 없음' 이 영영 굳으면 안 되지만,
        화면을 그리는 중에 다시 시도하면 그 시간만큼 화면이 멈춘다.
        """
        if self._targets is None or self._targets_full:
            return self._targets_full
        self._targets = None
        self.targets()
        return self._targets_full

    def unresolved_tickers(self) -> list[str]:
        """설정에는 있는데 SEC 에서 찾지 못한 종목. 화면에 이유를 적으려고 쓴다."""
        found = {t.watch.ticker for t in self.targets()}
        return [w.ticker for w in self.config.watchlist if w.ticker and w.ticker not in found]

    # --- ETF·펀드 판정 ---------------------------------------------------
    def fund_for(self, target: Target):
        """이 종목이 ETF·펀드인지 확인하고, 맞으면 성격을 읽어온다.

        ETF 는 회사가 아니라서 재무제표가 없다. 미리 알아야 '불러오기 실패'
        대신 ETF 화면을 보여줄 수 있다.
        """
        if target.cik in self._fund_cache:
            info = self._fund_cache[target.cik]
            target.fund = info
            return info

        submissions = None
        try:
            submissions = self.edgar.submissions(target.cik)
        except Exception as exc:
            log.debug("종목 정보 조회 실패 %s: %s", target.ticker, exc)

        if submissions and not target.name:
            target.name = submissions.get("name", "") or ""

        info = None
        try:
            info = detect_fund(
                target.ticker,
                submissions,
                in_fund_list=self.edgar.is_fund_ticker(target.ticker),
                name_hint=target.name,
            )
        except Exception as exc:
            log.warning("ETF 판정 실패 %s: %s", target.ticker, exc)

        if info:
            series, klass = self.edgar.fund_ids(target.ticker)
            info.series_id, info.class_id = series, klass
            if info.name and not target.watch.name:
                target.name = info.name

        self._fund_cache[target.cik] = info
        target.fund = info
        return info

    # --- 추천 후보 훑기 ---------------------------------------------------
    #
    # "괜찮은 종목 5개" 를 고르려면 볼 대상이 있어야 한다. 미국 상장사는 1만
    # 개가 넘고 회사 하나의 재무 원자료가 수 MB~수십 MB 라, 전부 훑으려면 몇
    # GB 를 받아야 한다. 그래서 후보 목록(data/universe.yml)을 정해두고 주기
    # 마다 몇 개씩만 본다. 반나절이면 한 바퀴가 돈다.
    #
    # 감시 목록이 항상 먼저다. 추천 때문에 내가 보고 있는 종목이 밀리면
    # 주객이 뒤바뀐다.

    @property
    def recommend(self) -> dict:
        raw = self.config.raw.get("recommend")
        return raw if isinstance(raw, dict) else {}

    @property
    def recommend_enabled(self) -> bool:
        return bool(self.recommend.get("enabled", True))

    def universe(self) -> list[str]:
        """볼 후보 전체. SEC 매출 순위 + 내가 보는 종목 + 직접 넣은 것 - 뺀 것.

        화면을 그릴 때마다 불린다. 저장해둔 목록만 읽고 네트워크는 쓰지 않는다
        (받아 오는 일은 refresh_universe 가 백그라운드에서 한다).
        """
        found = self.universe_builder.cached().tickers
        extra = screener.tickers(self.recommend.get("extra"))
        mine = [t.watch.ticker for t in self.targets() if t.watch.ticker]
        skip = {t.upper() for t in screener.tickers(self.recommend.get("exclude"))}

        out: list[str] = []
        for ticker in found + extra + mine:
            key = ticker.upper()
            if key and key not in skip and key not in out:
                out.append(key)
        return out

    def refresh_universe(self):
        """후보 목록을 SEC 에서 받아 둔다. 느리므로 백그라운드에서만 부른다."""
        if not self.recommend_enabled:
            return self.universe_builder.cached()
        size = int(self.recommend.get("candidates", 0) or 0) or DEFAULT_SIZE
        try:
            return self.universe_builder.ensure(size, now(self.config.timezone).date())
        except Exception as exc:
            log.warning("후보 목록을 받지 못했습니다: %s", exc)
            return self.universe_builder.cached()

    def universe_source(self) -> str:
        """이 후보 목록이 어디서 왔는지. 화면에 그대로 적는다.

        아직 못 받았으면 빈 문자열. 화면이 '못 받았다' 고 말할 수 있어야 한다.
        """
        found = self.universe_builder.cached()
        return "" if found.empty else found.describe()

    def screen_step(self, limit: int | None = None) -> list[str]:
        """후보 몇 개를 실제로 보고 점수를 매겨 둔다. 본 티커들을 돌려준다."""
        if not self.recommend_enabled:
            return []
        if self.missing_metrics():
            return []                    # 감시 목록부터 채운다

        pool = self.universe()
        self._picks.forget_missing(pool)
        today = now(self.config.timezone).date().isoformat()
        count = limit if limit is not None else int(self.recommend.get("per_cycle", 5) or 5)

        looked: list[str] = []
        watching = {t.watch.ticker for t in self.targets() if t.watch.ticker}
        for ticker in self._picks.stale(pool, today)[:max(0, count)]:
            try:
                found = self.judge_candidate(ticker, keep_facts=ticker in watching)
                self._picks.remember(ticker, found, today)
            except Exception as exc:
                log.debug("후보 판정 실패 %s: %s", ticker, exc)
                self._picks.remember(ticker, None, today, error=f"{type(exc).__name__}: {exc}")
            looked.append(ticker)
        if looked:
            self._picks.save()
        return looked

    def judge_candidate(self, ticker: str, keep_facts: bool = False) -> list:
        """후보 하나를 재무제표로 판정한다. 갈래마다 하나씩, 해당 없으면 뺀다.

        감시 목록과 **똑같은 계산**을 쓴다. 추천용으로 따로 만든 잣대라면
        화면의 판정과 어긋나서, 어느 쪽을 믿어야 할지 알 수 없게 된다.
        """
        key = ticker.upper()
        cik, name = self.edgar.resolve(key)

        # 상품명만으로 판단한다. 후보마다 공시 목록을 또 받으면 한 바퀴 도는
        # 시간이 두 배가 된다. SEC 의 펀드 티커 목록이 이미 대부분을 걸러준다.
        if self.edgar.is_fund_ticker(key) or detect_fund(
            key, None, in_fund_list=False, name_hint=name
        ):
            return []

        metrics = build_metrics(key, self.xbrl.company_facts(cik), self.prices)
        if not keep_facts:
            self.xbrl.forget(cik)        # 숫자만 남기고 원자료는 버린다
        if not metrics.company:
            metrics.company = name

        verdict = assess(metrics)
        recap = self.recap_for_ticker(key)
        market_3m, market_6m = self.market_returns()
        found = [
            screener.score_company(metrics, verdict, recap),
            screener.score_growth(metrics, verdict),
            screener.score_momentum(metrics, verdict, market_3m, market_6m),
        ]
        return [pick for pick in found if pick]

    def market_returns(self) -> tuple[float | None, float | None]:
        """시장(S&P 500) 최근 3·6개월 수익률(%). 한 번만 받아 계속 쓴다.

        '시장 흐름' 갈래는 이 값과 견주는 것이 전부다. 이걸 못 받으면 그
        갈래를 통째로 비운다 — 기준 없이 '많이 올랐다' 고 말할 수는 없다.
        """
        if self._market_returns is not None:
            return self._market_returns

        result: tuple[float | None, float | None] = (None, None)
        try:
            quote = self.prices.quote(MARKET_TICKER)
            history = self.prices.history(MARKET_TICKER)
            if quote and quote.price and history:
                result = (_return_since(quote.price, history, 91),
                          _return_since(quote.price, history, 182))
        except Exception as exc:
            log.debug("시장 수익률을 받지 못했습니다: %s", exc)

        if result[0] is None:
            log.info("시장 수익률(%s)을 받지 못해 '시장 흐름' 추천은 비워 둡니다.", MARKET_TICKER)
        self._market_returns = result
        return result

    def recap_for_ticker(self, ticker: str):
        """이미 받아둔 실적 3자 대조가 있으면. 후보 때문에 새로 받지는 않는다."""
        for target in self.targets():
            if target.ticker.upper() == ticker.upper():
                return self.recap_for(target)
        return None

    def top_picks(self, limit: int | None = None) -> dict:
        """갈래별 상위 몇 개. {갈래: [Pick]}"""
        if not self.recommend_enabled:
            return {}
        count = limit if limit is not None else int(self.recommend.get("count", 5) or 5)
        groups = screener.rank_by_category(self._picks.picks(), count)
        watching = {t.watch.ticker.upper() for t in self.targets() if t.watch.ticker}
        for picks in groups.values():
            for pick in picks:
                pick.in_watchlist = pick.ticker.upper() in watching
        return groups

    def screen_progress(self) -> tuple[int, int]:
        """(지금까지 본 수, 후보 전체). 화면에 정직하게 적으려고 쓴다."""
        return self._picks.looked_at, len(self.universe())

    def forms_for(self, target: Target) -> list[str]:
        """감시할 서류 목록. ETF 는 10-Q 를 내지 않으므로 펀드 서류를 본다."""
        if target.watch.forms:
            return target.watch.forms
        return FUND_FORMS if self.fund_for(target) else self.config.forms

    # --- 공시 감시 ------------------------------------------------------
    def check_filings(self, notify: bool = True, force: bool = False) -> list:
        """새 공시를 찾아 알린다. force=True면 첫 실행이어도 알림을 보낸다."""
        since = default_since(self.config.lookback_days)
        new_filings = []
        skipped = 0

        for target in self.targets():
            if target.market == markets.KR:
                continue                 # 한국은 SEC 가 아니라 DART 를 본다
            forms = self.forms_for(target)
            try:
                filings = self.edgar.recent_filings(target.cik, target.ticker, forms, since)
            except Exception as exc:
                log.error("공시 조회 실패 %s: %s", target.ticker, exc)
                continue

            first_run = not self.state.is_bootstrapped(target.cik)
            unseen = [f for f in filings if not self.state.is_seen(target.cik, f.uid())]

            if first_run and not force:
                for filing in unseen:
                    self.state.mark_seen(target.cik, filing.uid())
                self.state.mark_bootstrapped(target.cik)
                log.info("%s: 첫 실행이라 기존 공시 %d건을 기준선으로 저장했습니다.", target.ticker, len(unseen))
                continue

            self.state.mark_bootstrapped(target.cik)
            # 오래된 것부터 알림
            for filing in sorted(unseen, key=lambda f: (f.filing_date, f.accession)):
                if filing.form == "4":
                    self.edgar.enrich_form4(filing)
                    if not _worth_alerting(filing, self.config):
                        # RSU 수령·세금 반납까지 알리면 하루에도 여러 번 울린다.
                        # 이런 건 '내부자 거래' 90일 집계에 이미 들어가 있다.
                        self.state.mark_seen(target.cik, filing.uid())
                        skipped += 1
                        continue
                if notify:
                    text = format_filing(filing, self.config.timezone)
                    if not self.notifier.send(text):
                        log.error("전송 실패로 %s 를 미확인 상태로 둡니다(다음 실행에 재시도).", filing.accession)
                        continue
                self.state.mark_seen(target.cik, filing.uid())
                entry = summarize_filing(filing, self.config.timezone)
                entry["market"] = markets.US
                self.state.add_recent(entry)
                new_filings.append(filing)

        if skipped:
            log.info("보상·세금 목적 Form 4 %d건은 알리지 않았습니다(집계에는 반영).", skipped)
        self.state.save()
        return new_filings

    def check_korean_filings(self, notify: bool = True, force: bool = False) -> list:
        """한국 종목의 DART 공시를 찾아 알린다.

        미국 쪽과 같은 규칙을 쓴다 — 첫 실행에는 기준선만 잡고 알리지 않는다.
        그러지 않으면 처음 켠 날 지난 며칠치가 한꺼번에 쏟아진다.
        """
        if not self.dart.ready:
            return []

        since = now(self.config.timezone).date() - timedelta(days=self.config.lookback_days)
        found: list = []
        for target in self.targets():
            if target.market != markets.KR or not target.corp_code:
                continue
            try:
                filings = self.dart.filings(target.corp_code, since)
            except Exception as exc:
                log.error("DART 공시 조회 실패 %s: %s", target.ticker, exc)
                continue

            first_run = not self.state.is_bootstrapped(target.cik)
            unseen = [f for f in filings if not self.state.is_seen(target.cik, f.rcept_no)]
            if first_run and not force:
                for filing in unseen:
                    self.state.mark_seen(target.cik, filing.rcept_no)
                self.state.mark_bootstrapped(target.cik)
                log.info("%s: 첫 실행이라 DART 공시 %d건을 기준선으로 저장했습니다.",
                         target.ticker, len(unseen))
                continue

            self.state.mark_bootstrapped(target.cik)
            for filing in sorted(unseen, key=lambda f: f.rcept_no):
                entry = dart_summary(filing, target.ticker)
                if notify and not self.notifier.send(format_dart_filing(entry)):
                    log.error("전송 실패로 %s 를 미확인 상태로 둡니다.", filing.rcept_no)
                    continue
                self.state.mark_seen(target.cik, filing.rcept_no)
                self.state.add_recent(entry)
                found.append(filing)

        if found:
            self.state.save()
        return found

    # --- 지표 ------------------------------------------------------------
    def ensure_all_metrics(self, force: bool = False, on_progress=None) -> tuple[int, list[str]]:
        """지표가 비어 있는 종목을 채운다. (done, 실패한 티커들)

        종목을 하나 추가한 뒤 다른 종목이 '불러오는 중' 에 영원히 남지 않도록,
        빠진 것을 찾아 알아서 계산한다. 한 종목이 실패해도 나머지는 계속한다.

        on_progress(끝난 수, 전체, 지금 종목) 를 주면 진행 상황을 알려준다.
        종목당 10초 넘게 걸리는 일이 흔해서, 화면에 '몇 번째인지' 가 없으면
        멈춘 것과 구분이 안 된다.
        """
        self.retry_unresolved()      # SEC 가 막혔던 종목을 여기서 다시 시도한다
        targets = self.targets()
        done, failed = 0, []
        for index, target in enumerate(targets):
            if not force and target.cik in self._metrics_cache:
                continue
            if on_progress:
                on_progress(index, len(targets), target.ticker)
            try:
                self.metrics_for(target, with_peers=False, refresh=force)
                self.earnings_for(target, refresh=force)
                self._metrics_error.pop(target.cik, None)
                done += 1
            except Exception as exc:
                log.warning("지표 계산 실패 %s: %s", target.ticker, exc)
                self._metrics_error[target.cik] = f"{type(exc).__name__}: {exc}"
                failed.append(target.ticker)
        return done, failed

    def fill_context(self, limit: int = 2) -> list[str]:
        """가이던스·업종·보고서를 조금씩 채운다.

        버튼을 누르지 않아도 채워져야 하지만, 공시 원문을 받는 작업이라
        한 번에 다 하면 오래 걸린다. 주기마다 몇 종목씩 나눠서 채운다.
        """
        done: list[str] = []
        for target in self.targets():
            if len(done) >= limit:
                break
            if target.cik in self._guidance_cache and target.cik in self._industry_cache:
                continue
            try:
                if target.cik not in self._guidance_cache:
                    # 가이던스와 과거 이행 이력을 한 번에 받는다
                    self.load_guidance_context(target)
                if target.cik not in self._industry_cache:
                    self.industry_for(target)
                if target.cik not in self._report_cache:
                    self.report_for(target)
                if target.cik not in self._risk_cache:
                    self.risk_for(target)
                if target.cik not in self._insider_cache:
                    self.insiders_for(target)
                # 영어 원문에 한글을 붙이는 건 위 자료가 다 모인 뒤에 한다
                self.korean_for(target, refresh=True)
                done.append(target.ticker)
            except Exception as exc:
                log.warning("부가 정보 조회 실패 %s: %s", target.ticker, exc)
        return done

    # --- 환율·지수·경제지표 -------------------------------------------------
    def refresh_market(self, force: bool = False):
        """환율·주요 지수를 갱신한다. 느리므로 백그라운드에서만 부른다."""
        try:
            return self.fx.refresh(force=force)
        except Exception as exc:
            log.debug("환율·지수 갱신 실패: %s", exc)
            return None

    def market_snapshot(self):
        return self.fx.cached()

    def refresh_macro(self, force: bool = False):
        """물가·금리·고용 값을 갱신한다. 한 달에 한 번 바뀌는 값이라 느긋하게."""
        try:
            return self.macro.refresh(force=force)
        except Exception as exc:
            log.debug("경제 지표 갱신 실패: %s", exc)
            return None

    def macro_snapshot(self):
        return self.macro.cached()

    def missing_metrics(self) -> list[Target]:
        """아직 계산되지 않았고 실패로 확정되지도 않은 종목."""
        return [
            t for t in self.targets()
            if t.cik not in self._metrics_cache and t.cik not in self._metrics_error
        ]

    def metrics_errors(self) -> dict[str, str]:
        return dict(self._metrics_error)

    def metrics_for(self, target: Target, with_peers: bool = True,
                    refresh: bool = False) -> Metrics:
        """종목 지표. refresh=True 면 저장해둔 값을 버리고 다시 계산한다.

        새로고침을 눌렀는데 캐시를 그대로 돌려주면 '눌러도 아무 일이 없는' 화면이
        된다. 그래서 여기서 refresh 를 실제로 존중한다.
        """
        cached = self._metrics_cache.get(target.cik)
        if cached and not refresh:
            return cached
        if refresh:
            # 계산해둔 지표(_metrics_cache)는 여기서 지우지 않는다. 새로 받다가
            # 실패하면 멀쩡하던 숫자까지 사라져서 화면이 더 나빠진다.
            # 성공했을 때 아래에서 덮어쓴다.
            for cache in (self._assessment_cache, self._earnings_cache,
                          self._fund_cache, self._estimate_cache):
                cache.pop(target.cik, None)

        # 한국 종목은 SEC 가 아니라 DART 를 본다.
        if target.market == markets.KR:
            metrics = self._korean_metrics(target)
            self._metrics_cache[target.cik or target.ticker] = metrics
            self._assessment_cache.pop(target.cik or target.ticker, None)
            return metrics

        # ETF·펀드는 재무제표가 없다. 억지로 계산하지 않고 상품 정보를 담는다.
        fund = self.fund_for(target)
        if fund:
            metrics = build_fund_metrics(target.ticker, fund, self.prices)
            self._metrics_cache[target.cik] = metrics
            self._assessment_cache.pop(target.cik, None)
            return metrics

        peer_metrics: dict[str, Metrics] = {}
        if with_peers:
            for peer in self.peer_tickers(target):
                got = self._peer_metrics(peer)
                if got is not None:
                    peer_metrics[peer] = got

        # 새로고침이면 저장해둔 재무 원자료도 버리고 다시 받는다
        facts = (self.xbrl.company_facts(target.cik, max_age=0) if refresh
                 else self.xbrl.company_facts(target.cik))

        # 컨센서스는 직접 입력한 값이 우선. 없으면 자동 수집을 시도한다.
        eps, revenue = target.watch.consensus_eps, target.watch.consensus_revenue
        if eps is None and revenue is None:
            fetched = self.estimate_for(target)
            if fetched:
                eps, revenue = fetched.eps, fetched.revenue

        metrics = build_metrics(
            target.ticker,
            facts,
            self.prices,
            consensus_eps=eps,
            consensus_revenue=revenue,
            milestones=target.watch.milestones,
            peer_metrics=peer_metrics,
        )
        if not metrics.company:
            metrics.company = target.name
        self._metrics_cache[target.cik] = metrics
        self._assessment_cache.pop(target.cik, None)
        return metrics

    def market_state(self, market: str) -> tuple[str, str, bool]:
        """(상태, 현지 시각, 어림인가). 장이 열려 있는지 화면에 적으려고 쓴다.

        **휴장일 표를 손으로 적지 않는다.** 설날·추석은 음력이라 해마다
        날짜가 바뀌는데 그걸 기억으로 적어 넣으면 틀린 날 '장중' 이라고
        말하게 된다. 대신 이미 받아둔 시세에 들어 있는 거래소 상태를 쓴다.
        시세가 없을 때만 시각으로 어림하고, 그때는 어림이라고 밝힌다.
        """
        for target in self.targets():
            if target.market != market:
                continue
            found = self._metrics_cache.get(target.cik or target.ticker)
            state = markets.state_from_feed(getattr(found, "market_state", "") if found else "")
            if state != markets.UNKNOWN_STATE:
                _guess, shown = markets.state_by_clock(market)
                return state, shown, False
        state, shown = markets.state_by_clock(market)
        return state, shown, True

    def load_dart_codes(self) -> int:
        """DART 회사 목록을 미리 받아 둔다. **백그라운드에서만** 부른다.

        한국 종목이 하나도 없으면 받지 않는다. 열쇠가 없어도 마찬가지다.
        """
        if not self.dart.ready:
            return 0
        if not any(t.market == markets.KR for t in self.targets()):
            return 0
        try:
            found = self.dart.corp_codes()
        except Exception as exc:
            log.warning("DART 회사 목록을 받지 못했습니다: %s", exc)
            return 0
        if found:
            self._targets = None          # 고유번호가 생겼으니 다시 짚는다
        return len(found)

    def _korean_metrics(self, target: Target) -> Metrics:
        """한국 종목 지표. DART 에서 재무제표를, 야후에서 시세를 받는다.

        열쇠가 없으면 재무제표 없이 시세만 담긴다. 그 상태를 숨기지 않고
        경고로 남긴다 — 화면이 왜 비었는지 말해줄 수 있어야 한다.
        """
        found = None
        if target.corp_code:
            try:
                found = self.dart.latest_financials(
                    target.corp_code, now(self.config.timezone).date())
            except Exception as exc:
                log.warning("DART 재무제표 조회 실패 %s: %s", target.ticker, exc)

        metrics = build_dart_metrics(
            target.ticker, found, self.prices,
            symbol=target.price_symbol,
            company=target.name or target.watch.name or target.ticker,
        )
        if not self.dart.ready:
            metrics.warnings.append(self.dart.blocked_reason)
        return metrics

    def _peer_metrics(self, peer: str):
        """비교 대상 지표. 여러 종목이 같은 비교 대상을 쓰므로 한 번만 계산한다.

        비교 대상의 companyfacts 는 대형주면 수십 MB 다. 종목마다 다시 받으면
        그것만으로 몇 분이 날아간다.
        """
        key = peer.upper()
        if key in self._peer_cache:
            return self._peer_cache[key]
        result = None
        try:
            peer_cik, _ = self.edgar.resolve(key)
            result = build_peer_metrics(key, self.xbrl.company_facts(peer_cik), self.prices)
        except Exception as exc:
            # 비교 종목 하나가 실패해도 본 종목 지표는 나와야 한다
            log.warning("동종업계 종목 처리 실패 %s: %s", key, exc)
        self._peer_cache[key] = result
        return result

    def send_metrics(self, tickers: list[str] | None = None) -> list[Metrics]:
        selected = self.targets()
        if tickers:
            wanted = {t.upper() for t in tickers}
            selected = [t for t in selected if t.ticker.upper() in wanted]
            if not selected:
                log.error("watchlist 에서 %s 를 찾지 못했습니다.", ", ".join(sorted(wanted)))
        out = []
        for target in selected:
            metrics = self.metrics_for(target)
            self.notifier.send(format_metrics(metrics))
            out.append(metrics)
        return out

    # --- 실적 발표 일정 ---------------------------------------------------
    def earnings_for(self, target: Target, refresh: bool = False) -> Earnings | None:
        """확정일(직접 입력) 우선, 없으면 과거 8-K 2.02 간격으로 추정."""
        if target.cik in self._earnings_cache and not refresh:
            return self._earnings_cache[target.cik]
        if self.fund_for(target):
            # ETF 는 실적을 발표하지 않는다. 헛되이 공시를 뒤지지 않는다.
            self._earnings_cache[target.cik] = None
            return None
        today = now(self.config.timezone).date()
        try:
            info = next_earnings(
                self.edgar, target.cik, target.ticker, target.watch.earnings_date, today
            )
        except Exception as exc:
            log.warning("실적 발표일 조회 실패 %s: %s", target.ticker, exc)
            info = None
        self._earnings_cache[target.cik] = info
        return info

    def earnings_events(self) -> list[EconEvent]:
        """브리핑·캘린더에 끼워 넣을 실적 발표 일정."""
        events = []
        for target in self.targets():
            info = self.earnings_for(target)
            if info:
                events.append(info.to_event(target.watch.name))
        return events

    def send_earnings_reminders(self) -> list[str]:
        """D-7 / D-1 / 당일에 '무엇을 볼지'와 함께 알려준다."""
        today = now(self.config.timezone).date()
        offsets = self.config.earnings_reminder_days
        sent: list[str] = []
        for target in self.targets():
            info = self.earnings_for(target)
            if info is None:
                continue
            delta = due_reminders(info, today, offsets)
            if delta is None:
                continue
            key = f"earn:{target.cik}:{info.day.isoformat()}:{delta}"
            if self.state.reminder_sent(key):
                continue
            text = format_earnings_reminder(target.ticker, target.name, info, today, self.metrics_hint(target))
            if self.notifier.send(text):
                self.state.mark_reminder(key)
                sent.append(f"{target.ticker} D-{delta}")
        if sent:
            self.state.save()
        return sent

    def metrics_hint(self, target: Target) -> Metrics | None:
        """리마인더에 붙일 직전 분기 숫자. 실패해도 리마인더는 나가야 한다."""
        try:
            return self.metrics_for(target, with_peers=False)
        except Exception as exc:
            log.warning("지표 계산 실패 %s: %s", target.ticker, exc)
            return None

    # --- 분기·연간 보고서 본문 ---------------------------------------------
    def report_for(self, target: Target, refresh: bool = False):
        """가장 최근 10-Q(없으면 10-K) 원문에서 사업 설명·MD&A를 뽑아온다.

        요약을 지어내지 않고 회사가 쓴 문장을 그대로 발췌한다.
        """
        if not refresh and target.cik in self._report_cache:
            return self._report_cache[target.cik]

        if self.fund_for(target):
            # ETF 는 10-Q 를 내지 않는다. 대신 투자설명서·연차보고서가 공시로 잡힌다.
            self._report_cache[target.cik] = None
            return None

        result = None
        try:
            filings = self.edgar.recent_filings(
                target.cik, target.ticker, ["10-Q", "10-K"], date(2000, 1, 1), limit=4
            )
            if filings:
                latest = sorted(filings, key=lambda f: f.filing_date)[-1]
                result = fetch_filing_text(self.http, latest)
        except Exception as exc:
            log.warning("보고서 본문 조회 실패 %s: %s", target.ticker, exc)

        self._report_cache[target.cik] = result
        return result

    def cached_reports(self) -> dict:
        return dict(self._report_cache)

    # --- 위험 요인이 이번에 바뀌었나 ---------------------------------------
    def risk_for(self, target: Target, refresh: bool = False):
        """이번 보고서의 Item 1A 를 직전 보고서와 맞춰본다.

        회사가 새 위험을 처음 적어 넣는 순간이 가장 강한 신호다.
        """
        if not refresh and target.cik in self._risk_cache:
            return self._risk_cache[target.cik]
        if self.fund_for(target):
            self._risk_cache[target.cik] = None
            return None

        result = None
        try:
            filings = self.edgar.recent_filings(
                target.cik, target.ticker, ["10-Q", "10-K"], date(2000, 1, 1), limit=6
            )
            ordered = sorted(filings, key=lambda f: f.filing_date, reverse=True)
            current = self.report_for(target)

            previous = None
            # 10-Q 는 위험 요인을 통째로 싣지 않는 일이 흔하다.
            # 실제로 실려 있는 가장 최근 것을 비교 상대로 삼는다.
            for filing in ordered[1:3]:
                candidate = fetch_filing_text(self.http, filing)
                if candidate and candidate.section("risk"):
                    previous = candidate
                    break

            result = build_risk_change(target.ticker, current, previous)
        except Exception as exc:
            log.warning("위험 요인 비교 실패 %s: %s", target.ticker, exc)

        self._risk_cache[target.cik] = result
        return result

    def cached_risks(self) -> dict:
        return dict(self._risk_cache)

    # --- 내부자가 자기 돈으로 샀나 ----------------------------------------
    def insiders_for(self, target: Target, refresh: bool = False):
        """최근 90일 Form 4 를 모아 공개시장 매수·매도만 집계한다."""
        if not refresh and target.cik in self._insider_cache:
            return self._insider_cache[target.cik]
        if self.fund_for(target):
            self._insider_cache[target.cik] = None
            return None

        result = None
        try:
            today = now(self.config.timezone).date()
            filings = self.edgar.recent_filings(
                target.cik, target.ticker, ["4"], since_day(today, DEFAULT_DAYS), limit=20
            )
            for filing in filings:
                self.edgar.enrich_form4(filing)
            result = summarize(target.ticker, filings, DEFAULT_DAYS)
        except Exception as exc:
            log.warning("내부자 거래 집계 실패 %s: %s", target.ticker, exc)

        self._insider_cache[target.cik] = result
        return result

    def cached_insiders(self) -> dict:
        return dict(self._insider_cache)

    # --- 영어 원문에 한글 붙이기 -------------------------------------------
    def korean_for(self, target: Target, refresh: bool = False) -> dict:
        """이 종목의 영어 문장들에 한글 설명을 만들어 둔다.

        규칙으로 옮길 수 있는 문장은 규칙으로, 나머지는 기계 번역으로 채운다.
        네트워크를 쓸 수 있으므로 화면을 그릴 때가 아니라 여기서 미리 해둔다.
        """
        if not refresh and target.cik in self._korean_cache:
            return self._korean_cache[target.cik]

        notes: dict = {}
        try:
            report = self._report_cache.get(target.cik)
            if report:
                notes.update(annotate(list(report.company_words), "sentence",
                                      self.translator, limit=12))
                for section in report.sections:
                    notes.update(annotate(section.paragraphs[:4], "section",
                                          self.translator, limit=6))

            risk = self._risk_cache.get(target.cik)
            if risk:
                notes.update(annotate(list(risk.added), "risk", self.translator, limit=6))
                notes.update(annotate([f.sentence for f in risk.flags], "risk",
                                      self.translator, limit=6))

            guidance = self._guidance_cache.get(target.cik)
            if guidance:
                sentences = [i.sentence for i in guidance.items] + list(guidance.results)
                notes.update(annotate(sentences, "sentence", self.translator, limit=8))

            track = self._track_cache.get(target.cik)
            if track:
                notes.update(annotate([i.sentence for i in track.items[:6]], "sentence",
                                      self.translator, limit=6))
        except Exception as exc:
            log.warning("한글 설명 생성 실패 %s: %s", target.ticker, exc)

        self._korean_cache[target.cik] = notes
        return notes

    def cached_korean(self) -> dict:
        return dict(self._korean_cache)

    # --- 실적 3자 대조 -----------------------------------------------------
    def recap_for(self, target: Target):
        """실제 · 컨센서스 · 가이던스를 한 자리에서 비교한다 (계산만, 조회 없음)."""
        metrics = self._metrics_cache.get(target.cik)
        if metrics is None or metrics.is_fund:
            return None
        return build_recap(target.ticker, metrics, self._guidance_cache.get(target.cik))

    # --- 자동 업데이트 확인 -------------------------------------------------
    def check_update(self, force: bool = False) -> tuple[str | None, bool]:
        """새 버전이 나왔는지 확인한다. 하루에 한 번만 물어본다."""
        today = now(self.config.timezone).date().isoformat()
        if not force and self.state.last_update_check() == today:
            return self.state.known_latest(), False

        try:
            import updater

            latest, newer = updater.check_latest()
        except Exception as exc:
            log.debug("업데이트 확인 실패: %s", exc)
            return None, False

        self.state.set_last_update_check(today)
        if latest:
            self.state.set_known_latest(latest)
        self.state.save()

        if newer and latest and self.auto_update_enabled:
            return latest, self.self_update(latest)

        if newer and latest and self.state.notified_version() != latest:
            from . import __version__

            self.notifier.send(
                f"🆕 <b>새 버전 {esc_version(latest)}</b> 이 나왔습니다 (지금 {esc_version(__version__)})\n"
                "화면의 <b>업데이트</b> 버튼을 누르거나 <b>업데이트.bat</b> 을 실행하세요."
            )
            self.state.set_notified_version(latest)
            self.state.save()
        return latest, newer

    def apply_update(self) -> tuple[bool, str]:
        try:
            import updater

            return updater.apply_update()
        except Exception as exc:
            return False, f"업데이트 중 오류: {exc}"

    # --- 스스로 갱신 ------------------------------------------------------
    @property
    def auto_update_enabled(self) -> bool:
        """새 버전을 알아서 받아 깔지. config 로 끌 수 있다."""
        return bool(self.config.raw.get("auto_update", True))

    def self_update(self, latest: str) -> bool:
        """새 버전을 받아 깔고 스스로 다시 켠다.

        사람이 버튼을 누르지 않아도 최신으로 돈다. 자동으로 코드를 바꾸는
        일이라 updater 쪽에 안전장치를 뒀다 — 새 코드가 안 켜지면 이전
        버전으로 되돌린다. 여기서는 되돌아온 경우 다시 켜지 않는다.
        """
        from . import __version__

        log.info("새 버전 %s 을 자동으로 받습니다 (지금 %s)", latest, __version__)
        try:
            import updater

            ok, message = updater.auto_update()
        except Exception as exc:
            log.warning("자동 갱신 실패: %s", exc)
            return False

        if not ok:
            log.warning("자동 갱신 실패: %s", message)
            self.notifier.send(
                f"⚠️ 새 버전 {esc_version(latest)} 을 자동으로 깔지 못했습니다.\n{esc(message)}"
            )
            return False

        self.notifier.send(
            f"🆕 <b>{esc_version(latest)} 로 갱신했습니다</b> (이전 {esc_version(__version__)})\n"
            "새 버전으로 다시 시작합니다."
        )
        self.state.save()
        self.restart()
        return True

    def restart(self) -> None:
        """지금 프로세스를 끝내고 새 코드로 다시 켠다.

        화면(포트)을 먼저 놓아야 새로 켜지는 쪽이 같은 주소를 잡는다.
        터미널에서 직접 띄운 경우에는 창을 뺏지 않고 그냥 알리기만 한다.
        """
        import os
        import sys

        if sys.stdout is not None and getattr(sys.stdout, "isatty", lambda: False)():
            log.info("갱신을 마쳤습니다. 직접 띄우신 창이라 다시 켜지 않습니다 — 재시작해주세요.")
            return

        server, self.dashboard_server = self.dashboard_server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception as exc:
                log.debug("화면을 닫지 못했습니다: %s", exc)

        try:
            import updater

            boot = updater._bootstrap()
            if boot is None:
                log.warning("다시 켜지 못했습니다. '시작하기' 를 실행해주세요.")
                return
            boot.launch_detached()
        except Exception as exc:
            log.warning("다시 켜지 못했습니다(%s). '시작하기' 를 실행해주세요.", exc)
            return
        log.info("새 버전으로 다시 켰습니다. 이 프로세스는 여기서 끝냅니다.")
        os._exit(0)

    # --- 속보 ------------------------------------------------------------
    def check_news(self) -> list:
        """관심 종목·시장 속보를 찾아 먼저 띄운다."""
        if not self.news.enabled:
            return []
        try:
            items = self.news.new_items([t.ticker for t in self.targets()])
        except Exception as exc:
            log.warning("속보 확인 실패: %s", exc)
            return []
        if not items:
            return []

        sent = []
        for item in items:
            if self.notifier.send(format_news(item)):
                sent.append(item)
        self.news.mark_sent(sent)
        if sent:
            self.state.save()
        return sent

    # --- 가이던스 (메모 1순위) ---------------------------------------------
    def load_guidance_context(self, target: Target, history: int = 6) -> None:
        """실적 발표문을 여러 개 읽어 '지금 가이던스' 와 '과거 이행 이력' 을 함께 만든다.

        메모의 단서 — 가이던스는 회사가 관리할 수 있으니 과거 이행 이력을
        확인하라 — 를 그대로 따른다. 같은 공시를 두 번 받지 않도록 한 번에 처리한다.
        """
        if self.fund_for(target):
            self._guidance_cache[target.cik] = None
            self._track_cache[target.cik] = None
            return

        latest = None
        reports = []
        try:
            filings = self.edgar.recent_filings(
                target.cik, target.ticker, ["8-K"], date(2000, 1, 1), limit=80
            )
            earnings_8k = [f for f in filings if "2.02" in f.items or "7.01" in f.items]
            for filing in sorted(earnings_8k, key=lambda f: f.filing_date, reverse=True)[:history]:
                report = fetch_guidance(self.http, self.edgar, filing)
                if not report:
                    continue
                reports.append(report)
                if latest is None and (report.found or report.results):
                    latest = report
        except Exception as exc:
            log.warning("가이던스 조회 실패 %s: %s", target.ticker, exc)

        record = None
        if reports:
            try:
                facts = self.xbrl.company_facts(target.cik)
                record = build_track_record(target.ticker, reports, facts)
            except Exception as exc:
                log.warning("가이던스 이행 이력 계산 실패 %s: %s", target.ticker, exc)

        self._guidance_cache[target.cik] = latest
        self._track_cache[target.cik] = record

        metrics = self._metrics_cache.get(target.cik)
        if metrics is not None:
            apply_guidance(metrics, latest, record)
            self._assessment_cache.pop(target.cik, None)

    def guidance_for(self, target: Target, refresh: bool = False):
        """가장 최근 실적 발표(8-K 2.02)에서 가이던스 문장을 찾아온다."""
        if refresh or target.cik not in self._guidance_cache:
            self.load_guidance_context(target)
        return self._guidance_cache.get(target.cik)

    def cached_guidance(self) -> dict:
        return dict(self._guidance_cache)

    def cached_track_records(self) -> dict:
        return dict(self._track_cache)


    # --- 컨센서스 (메모 2순위) ---------------------------------------------
    def estimate_for(self, target: Target, refresh: bool = False):
        """애널리스트 예상치. 자동 수집이 막히면 None (직접 입력 안내로 넘어간다)."""
        if not refresh and target.cik in self._estimate_cache:
            return self._estimate_cache[target.cik]
        result = None
        try:
            result = self.estimates.fetch(target.ticker)
        except Exception as exc:
            log.info("컨센서스 조회 실패 %s: %s", target.ticker, exc)
        self._estimate_cache[target.cik] = result
        return result

    def cached_estimates(self) -> dict:
        return dict(self._estimate_cache)

    # --- 동종업계 ----------------------------------------------------------
    def industry_for(self, target: Target, refresh: bool = False):
        """SEC 산업분류(SIC)로 같은 업종 종목을 찾는다."""
        if not refresh and target.cik in self._industry_cache:
            return self._industry_cache[target.cik]
        result = None
        try:
            result = find_peers(
                self.http, self.edgar, target.cik, target.ticker,
                limit=int(self.config.raw.get("auto_peer_count", 4)),
            )
        except Exception as exc:
            log.warning("동종업계 조회 실패 %s: %s", target.ticker, exc)
        self._industry_cache[target.cik] = result
        return result

    def cached_industries(self) -> dict:
        return dict(self._industry_cache)

    def peer_tickers(self, target: Target) -> list[str]:
        """직접 지정한 peers 가 있으면 그것을, 없으면 자동 탐색 결과를 쓴다."""
        if target.watch.peers:
            return target.watch.peers
        industry = self.industry_for(target)
        return industry.peers if industry else []

    # --- 가격 알림 --------------------------------------------------------
    def check_price_alerts(self) -> list[str]:
        """52주 신고가·신저가와 하루 급변을 알린다. 같은 날 같은 사유는 한 번만."""
        threshold = float(self.config.raw.get("price_alert_pct", 7))
        today = now(self.config.timezone).date().isoformat()
        sent: list[str] = []

        for target in self.targets():
            metrics = self._metrics_cache.get(target.cik)
            if metrics is None or not metrics.price:
                continue

            for reason, text in _price_events(metrics, threshold):
                key = f"price:{target.cik}:{today}:{reason}"
                if self.state.reminder_sent(key):
                    continue
                body = format_price_alert(target.ticker, target.name, metrics, text)
                if self.notifier.send(body):
                    self.state.mark_reminder(key)
                    sent.append(f"{target.ticker} {reason}")
        if sent:
            self.state.save()
        return sent

    # --- 상황 판단 --------------------------------------------------------
    def assessment_for(self, target: Target):
        metrics = self._metrics_cache.get(target.cik)
        if metrics is None:
            return None
        if target.cik not in self._assessment_cache:
            self._assessment_cache[target.cik] = assess(metrics)
        return self._assessment_cache[target.cik]

    def check_deterioration(self) -> list[str]:
        """지난번보다 나빠진 종목을 찾아 알린다."""
        messages: list[str] = []
        for target in self.targets():
            current = self.assessment_for(target)
            if current is None:
                continue
            previous = self.state.last_level(target.cik)
            if previous and previous != current.level and _worse(current.level, previous):
                text = format_downgrade(target.ticker, target.name, previous, current)
                if self.notifier.send(text):
                    messages.append(f"{target.ticker} {previous}→{current.level}")
            self.state.set_last_level(target.cik, current.level)
        if messages:
            self.state.save()
        return messages

    def calendar_warning(self) -> str | None:
        """FOMC 같은 확정 일정 데이터가 만료되면 알려준다."""
        coverage = fomc_coverage_end()
        today = now(self.config.timezone).date()
        if coverage is None:
            return "FOMC 일정 데이터가 없습니다. data/fomc.yml 을 채워주세요."
        if (coverage - today).days < 60:
            return (
                f"FOMC 일정이 {coverage.isoformat()} 까지만 있습니다. "
                "federalreserve.gov 에서 다음 연도 일정을 data/fomc.yml 에 추가해주세요."
            )
        return None

    # --- 데일리 브리핑 ---------------------------------------------------
    def daily_brief(self, force: bool = False) -> str | None:
        today = now(self.config.timezone).date()
        if not force and self.state.last_brief_date() == today.isoformat():
            return None

        market_days = upcoming_market_days(today, self.config.holiday_lookahead_days)
        # 관심 종목 실적 발표일을 경제지표 일정과 같이 놓고 본다
        extra = parse_extra_events(self.config.raw.get("econ_extra_events")) + self.earnings_events()
        events = upcoming_events(
            today,
            self.config.econ_lookahead_days,
            min_importance=int(self.config.raw.get("econ_min_importance", 2)),
            extra=extra,
            include_weekly=bool(self.config.raw.get("econ_include_weekly", False)),
        )

        metrics: list[Metrics] = []
        if self.config.metrics_in_brief:
            for target in self.targets():
                try:
                    metrics.append(self.metrics_for(target, with_peers=False, refresh=force))
                except Exception as exc:
                    log.warning("지표 계산 실패 %s: %s", target.ticker, exc)

        groups = self.top_picks()
        seen, total = self.screen_progress()
        any_pick = any(groups.values())
        text = format_daily_brief(
            today, market_days, events, metrics, self.config.timezone,
            self.calendar_warning(), self.macro_snapshot(),
            picks=groups,
            pick_scope=f"후보 {total}개 중 {seen}개를 본 결과입니다." if any_pick else "",
        )
        if self.notifier.send(text):
            self.state.set_last_brief_date(today.isoformat())
            self.state.save()
        return text

    # --- 루프 -------------------------------------------------------------
    def run_forever(self, dashboard: bool | None = None) -> None:
        if dashboard is None:
            dashboard = self.config.dashboard_enabled
        if dashboard:
            from .dashboard import start_dashboard

            try:
                self.dashboard_server = start_dashboard(
                    self, self.config.dashboard_port, self.config.dashboard_open_browser
                )
            except Exception as exc:
                log.error("대시보드를 띄우지 못했습니다(감시는 계속됩니다): %s", exc)

        commands_on = self.config.telegram_commands and not self.notifier.dry_run
        log.info(
            "감시 시작: %s개 종목, %d초 주기, 브리핑 %s, 텔레그램 명령 %s",
            len(self.targets()),
            self.config.poll_interval_sec,
            self.config.daily_brief_time or "off",
            "on" if commands_on else "off",
        )
        next_check = 0.0
        while True:
            try:
                if time.monotonic() >= next_check:
                    self._tick()
                    next_check = time.monotonic() + self.config.poll_interval_sec

                # 명령을 켜두면 롱폴링으로 대기하면서 즉시 응답한다.
                # 꺼져 있으면 다음 확인 시각까지 그냥 잔다.
                remaining = max(1.0, next_check - time.monotonic())
                if self.config.telegram_commands and not self.notifier.dry_run:
                    self.commands.poll(timeout=int(min(25, remaining)))
                else:
                    time.sleep(remaining)
            except KeyboardInterrupt:
                log.info("종료합니다.")
                return
            except Exception as exc:  # 루프는 어떤 오류에도 죽지 않는다
                log.exception("주기 실행 중 오류: %s", exc)
                time.sleep(5)

    def _tick(self) -> None:
        self.reload_config_if_changed()
        # 지표는 계산 비용이 커서 매 주기가 아니라 1시간마다 새로 뽑는다.
        # (대시보드가 방금 계산한 값을 다음 주기에 날려버리지 않도록)
        if time.monotonic() - self._metrics_cached_at > 3600:
            self._metrics_cache.clear()
            self._earnings_cache.clear()
            self._metrics_error.clear()      # 실패했던 종목도 다시 시도해본다
            self._assessment_cache.clear()
            self._report_cache.clear()
            self._guidance_cache.clear()
            self._track_cache.clear()
            self._risk_cache.clear()
            self._insider_cache.clear()
            self._korean_cache.clear()
            self._peer_cache.clear()
            self._metrics_cached_at = time.monotonic()

        filings = self.check_filings()
        if filings:
            log.info("새 공시 %d건 전송", len(filings))

        korean = self.check_korean_filings()
        if korean:
            log.info("한국 공시 %d건 전송", len(korean))

        # 아직 비어 있는 종목이 있으면 조용히 채운다
        if self.missing_metrics():
            done, failed = self.ensure_all_metrics()
            if done or failed:
                log.info("지표 채움: 성공 %d, 실패 %s", done, ", ".join(failed) or "없음")

        # 가이던스·이행 이력·업종·보고서도 주기마다 몇 종목씩 채운다
        filled = self.fill_context()
        if filled:
            log.info("부가 정보 채움: %s", ", ".join(filled))

        # 추천 후보도 주기마다 몇 개씩 본다. 위의 감시 목록 처리가 다 끝난
        # 뒤에 하는 것이 중요하다 — 추천 때문에 내 종목이 밀리면 안 된다.
        self.load_dart_codes()           # 한국 종목이 있으면 회사 목록을 받아 둔다
        self.refresh_universe()          # 후보 목록 자체는 한 달에 한 번만 받는다
        looked = self.screen_step()
        if looked:
            seen, total = self.screen_progress()
            log.info("추천 후보 확인: %s (%d/%d)", ", ".join(looked), seen, total)

        self.refresh_market()
        self.refresh_macro()

        reminders = self.send_earnings_reminders()
        if reminders:
            log.info("실적 리마인더 전송: %s", ", ".join(reminders))

        self.check_update()

        news = self.check_news()
        if news:
            log.info("속보 %d건 전송", len(news))

        alerts = self.check_price_alerts()
        if alerts:
            log.info("가격 알림: %s", ", ".join(alerts))

        # 상태가 나빠진 종목이 있으면 알린다
        downgrades = self.check_deterioration()
        if downgrades:
            log.info("상태 악화 알림: %s", ", ".join(downgrades))

        if self._brief_due():
            log.info("데일리 브리핑 전송")
            self.daily_brief()

        self.state.set_last_check(now(self.config.timezone).strftime("%Y-%m-%d %H:%M"))
        self.state.save()

    def _brief_due(self) -> bool:
        target_time = self.config.daily_brief_time
        if not target_time:
            return False
        current = now(self.config.timezone)
        if self.state.last_brief_date() == current.date().isoformat():
            return False
        try:
            hour, minute = (int(x) for x in target_time.split(":"))
        except ValueError:
            log.warning("daily_brief_time 형식이 잘못되었습니다: %s", target_time)
            return False
        return current >= current.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _worth_alerting(filing, config) -> bool:
    """이 Form 4 를 알릴 값어치가 있는가.

    임원이 RSU 를 받거나 세금 낼 주식을 반납해도 전부 Form 4 로 올라온다.
    그걸 다 알리면 정작 '자기 돈으로 샀다' 는 신호가 파묻힌다.
    공개시장 매수(P)·매도(S)만 알리고 나머지는 집계에 맡긴다.
    """
    if str(config.raw.get("insider_alerts", "trades")).lower() == "all":
        return True
    for tx in filing.transactions or []:
        if tx.get("derivative"):
            continue
        if (tx.get("code") or "").upper() in ("P", "S"):
            return True
    # 파싱에 실패해 거래 내역이 비었으면 놓치지 않도록 알린다
    return not filing.transactions


def _price_events(m: Metrics, threshold: float) -> list[tuple[str, str]]:
    """(사유 키, 사람이 읽을 문장) 목록. 확인된 숫자만 쓴다."""
    events: list[tuple[str, str]] = []

    if m.high_52w and m.price >= m.high_52w:
        events.append(("high52", f"52주 신고가입니다 (이전 최고 ${m.high_52w:,.2f})"))
    elif m.low_52w and m.price <= m.low_52w:
        events.append(("low52", f"52주 신저가입니다 (이전 최저 ${m.low_52w:,.2f})"))

    change = m.price_change_pct
    if change is not None and abs(change) >= threshold:
        direction = "급등" if change > 0 else "급락"
        events.append((f"move{'up' if change > 0 else 'down'}",
                       f"하루 만에 {change:+.1f}% {direction}했습니다 (기준 ±{threshold:g}%)"))
    return events


def esc_version(value: str) -> str:
    """버전 문자열에는 특수문자가 없지만 방어적으로 정리한다."""
    return "".join(c for c in str(value) if c.isalnum() or c in "._-")


def _worse(current: str, previous: str) -> bool:
    """등급이 나빠졌는지. good > fair > poor 순."""
    order = {"good": 3, "fair": 2, "poor": 1, "unknown": 0}
    return order.get(current, 0) < order.get(previous, 0) and order.get(current, 0) > 0


