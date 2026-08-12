"""봇 오케스트레이션: 공시 감시, 지표 계산, 데일리 브리핑."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .assessment import assess
from .commands import CommandRouter
from .config import Config, Watch, load_config
from .earnings import Earnings, due_reminders, next_earnings
from .estimates import EstimateClient
from .filing_text import fetch_filing_text
from .funds import FUND_FORMS, detect_fund
from .fx import FxClient
from .insiders import DEFAULT_DAYS, since_day, summarize
from .recap import build_recap
from .risk_watch import build_risk_change
from .guidance import fetch_guidance
from .econ_calendar import EconEvent, fomc_coverage_end, parse_extra_events, upcoming_events
from .edgar import EdgarClient, default_since
from .http import HttpClient
from .market_calendar import upcoming_market_days
from .messages import (
    format_daily_brief,
    format_downgrade,
    format_earnings_reminder,
    format_filing,
    format_metrics,
    format_news,
    format_price_alert,
    summarize_filing,
)
from .metrics import Metrics, apply_guidance, build_fund_metrics, build_metrics
from .news import NewsWatcher
from .overrides import Overrides
from .peers import find_peers
from .prices import PriceClient
from .state import State
from .telegram import TelegramNotifier
from .timeutil import now
from .track_record import build_track_record
from .xbrl import XbrlClient

log = logging.getLogger(__name__)


@dataclass
class Target:
    watch: Watch
    cik: str
    name: str
    fund: object | None = None     # ETF·펀드면 FundInfo, 아니면 None

    @property
    def ticker(self) -> str:
        return self.watch.ticker or self.cik

    @property
    def is_fund(self) -> bool:
        return self.fund is not None


class Bot:
    def __init__(self, config: Config, dry_run: bool = False) -> None:
        self.config = config
        self.http = HttpClient(user_agent=config.user_agent)
        self.edgar = EdgarClient(self.http, config.cache_dir)
        self.xbrl = XbrlClient(self.http, config.cache_dir)
        self.prices = PriceClient(self.http)
        self.estimates = EstimateClient(self.http)
        self.fx = FxClient(self.http)
        self.state = State(config.state_path)
        self.notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id, dry_run=dry_run)
        self.overrides = Overrides(config.overrides_path)
        self.commands = CommandRouter(self)
        self.news = NewsWatcher(self.http, self.state, config)
        self._targets: list[Target] | None = None
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
        self._metrics_cached_at = time.monotonic()
        self._config_mtime = self._mtime(config.path)

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

        # 계산해둔 지표를 통째로 버리면, 종목을 하나 추가할 때마다 나머지가
        # 다시 '불러오는 중' 으로 돌아간다. 빠진 종목 것만 정리한다.
        live = {t.cik for t in self.targets()}
        for cache in (self._metrics_cache, self._earnings_cache, self._metrics_error,
                      self._report_cache, self._assessment_cache,
                      self._guidance_cache, self._industry_cache, self._estimate_cache,
                      self._track_cache, self._fund_cache, self._risk_cache,
                      self._insider_cache):
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
        self.reload_watchlist()
        log.info("설정을 다시 읽었습니다: %s (종목 %d개)", path, len(self.targets()))
        return True

    # --- 대상 해석 ------------------------------------------------------
    def targets(self) -> list[Target]:
        if self._targets is not None:
            return self._targets

        # 티커 목록을 한 번만 받아둔다. 여기서 실패하면 종목마다 재시도하지 않는다.
        if any(not w.cik for w in self.config.watchlist):
            try:
                self.edgar.ticker_map()
            except Exception as exc:
                log.error("SEC 티커 목록을 받지 못했습니다: %s", exc)
                self._targets = [
                    Target(watch=w, cik=f"{int(w.cik):010d}", name=w.name or "")
                    for w in self.config.watchlist
                    if w.cik
                ]
                return self._targets

        out: list[Target] = []
        for watch in self.config.watchlist:
            try:
                cik, name = self.edgar.resolve(watch.ticker or None, watch.cik)
            except Exception as exc:
                log.error("종목 해석 실패 %s: %s", watch.ticker or watch.cik, exc)
                continue
            out.append(Target(watch=watch, cik=cik, name=watch.name or name))
        self._targets = out
        return out

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

        for target in self.targets():
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
                if notify:
                    text = format_filing(filing, self.config.timezone)
                    if not self.notifier.send(text):
                        log.error("전송 실패로 %s 를 미확인 상태로 둡니다(다음 실행에 재시도).", filing.accession)
                        continue
                self.state.mark_seen(target.cik, filing.uid())
                self.state.add_recent(summarize_filing(filing, self.config.timezone))
                new_filings.append(filing)

        self.state.save()
        return new_filings

    # --- 지표 ------------------------------------------------------------
    def ensure_all_metrics(self, force: bool = False) -> tuple[int, list[str]]:
        """지표가 비어 있는 종목을 채운다. (done, 실패한 티커들)

        종목을 하나 추가한 뒤 다른 종목이 '불러오는 중' 에 영원히 남지 않도록,
        빠진 것을 찾아 알아서 계산한다. 한 종목이 실패해도 나머지는 계속한다.
        """
        done, failed = 0, []
        for target in self.targets():
            if not force and target.cik in self._metrics_cache:
                continue
            try:
                self.metrics_for(target, with_peers=False)
                self.earnings_for(target)
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
                done.append(target.ticker)
            except Exception as exc:
                log.warning("부가 정보 조회 실패 %s: %s", target.ticker, exc)
        return done

    # --- 환율·지수 --------------------------------------------------------
    def refresh_market(self, force: bool = False):
        """환율·주요 지수를 갱신한다. 느리므로 백그라운드에서만 부른다."""
        try:
            return self.fx.refresh(force=force)
        except Exception as exc:
            log.debug("환율·지수 갱신 실패: %s", exc)
            return None

    def market_snapshot(self):
        return self.fx.cached()

    def missing_metrics(self) -> list[Target]:
        """아직 계산되지 않았고 실패로 확정되지도 않은 종목."""
        return [
            t for t in self.targets()
            if t.cik not in self._metrics_cache and t.cik not in self._metrics_error
        ]

    def metrics_errors(self) -> dict[str, str]:
        return dict(self._metrics_error)

    def metrics_for(self, target: Target, with_peers: bool = True) -> Metrics:
        cached = self._metrics_cache.get(target.cik)
        if cached:
            return cached

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
                try:
                    peer_cik, _ = self.edgar.resolve(peer)
                    peer_facts = self.xbrl.company_facts(peer_cik)
                    peer_metrics[peer] = build_metrics(peer, peer_facts, self.prices)
                except Exception as exc:
                    # 비교 종목 하나가 실패해도 본 종목 지표는 나와야 한다
                    log.warning("동종업계 종목 처리 실패 %s: %s", peer, exc)
                    continue

        facts = self.xbrl.company_facts(target.cik)

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
    def earnings_for(self, target: Target) -> Earnings | None:
        """확정일(직접 입력) 우선, 없으면 과거 8-K 2.02 간격으로 추정."""
        if target.cik in self._earnings_cache:
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

    def track_record_for(self, target: Target, refresh: bool = False):
        """과거 가이던스를 지켰는지."""
        if refresh or target.cik not in self._track_cache:
            self.load_guidance_context(target)
        return self._track_cache.get(target.cik)

    def cached_guidance(self) -> dict:
        return dict(self._guidance_cache)

    def cached_track_records(self) -> dict:
        return dict(self._track_cache)

    def cached_funds(self) -> dict:
        return dict(self._fund_cache)

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

    def cached_assessments(self) -> dict:
        return dict(self._assessment_cache)

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
                    metrics.append(self.metrics_for(target, with_peers=False))
                except Exception as exc:
                    log.warning("지표 계산 실패 %s: %s", target.ticker, exc)

        text = format_daily_brief(
            today, market_days, events, metrics, self.config.timezone, self.calendar_warning()
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
                start_dashboard(self, self.config.dashboard_port, self.config.dashboard_open_browser)
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
            self._metrics_cached_at = time.monotonic()

        filings = self.check_filings()
        if filings:
            log.info("새 공시 %d건 전송", len(filings))

        # 아직 비어 있는 종목이 있으면 조용히 채운다
        if self.missing_metrics():
            done, failed = self.ensure_all_metrics()
            if done or failed:
                log.info("지표 채움: 성공 %d, 실패 %s", done, ", ".join(failed) or "없음")

        # 가이던스·이행 이력·업종·보고서도 주기마다 몇 종목씩 채운다
        filled = self.fill_context()
        if filled:
            log.info("부가 정보 채움: %s", ", ".join(filled))

        self.refresh_market()

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


def build_bot(config: Config, dry_run: bool = False) -> Bot:
    return Bot(config, dry_run=dry_run)


def utc_stamp() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def today_str(tz_name: str) -> date:
    return now(tz_name).date()
