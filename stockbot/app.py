"""봇 오케스트레이션: 공시 감시, 지표 계산, 데일리 브리핑."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime

from .config import Config, Watch
from .econ_calendar import parse_extra_events, upcoming_events
from .edgar import EdgarClient, default_since
from .http import HttpClient
from .market_calendar import upcoming_market_days
from .messages import format_daily_brief, format_filing, format_metrics
from .metrics import Metrics, build_metrics
from .prices import PriceClient
from .state import State
from .telegram import TelegramNotifier
from .timeutil import now
from .xbrl import XbrlClient

log = logging.getLogger(__name__)


@dataclass
class Target:
    watch: Watch
    cik: str
    name: str

    @property
    def ticker(self) -> str:
        return self.watch.ticker or self.cik


class Bot:
    def __init__(self, config: Config, dry_run: bool = False) -> None:
        self.config = config
        self.http = HttpClient(user_agent=config.user_agent)
        self.edgar = EdgarClient(self.http, config.cache_dir)
        self.xbrl = XbrlClient(self.http, config.cache_dir)
        self.prices = PriceClient(self.http)
        self.state = State(config.state_path)
        self.notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id, dry_run=dry_run)
        self._targets: list[Target] | None = None
        self._metrics_cache: dict[str, Metrics] = {}

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

    # --- 공시 감시 ------------------------------------------------------
    def check_filings(self, notify: bool = True, force: bool = False) -> list:
        """새 공시를 찾아 알린다. force=True면 첫 실행이어도 알림을 보낸다."""
        since = default_since(self.config.lookback_days)
        new_filings = []

        for target in self.targets():
            forms = target.watch.forms or self.config.forms
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
                new_filings.append(filing)

        self.state.save()
        return new_filings

    # --- 지표 ------------------------------------------------------------
    def metrics_for(self, target: Target, with_peers: bool = True) -> Metrics:
        cached = self._metrics_cache.get(target.cik)
        if cached:
            return cached

        peer_metrics: dict[str, Metrics] = {}
        if with_peers:
            for peer in target.watch.peers:
                try:
                    peer_cik, _ = self.edgar.resolve(peer)
                except Exception as exc:
                    log.warning("동종업계 종목 해석 실패 %s: %s", peer, exc)
                    continue
                peer_facts = self.xbrl.company_facts(peer_cik)
                peer_metrics[peer] = build_metrics(peer, peer_facts, self.prices)

        facts = self.xbrl.company_facts(target.cik)
        metrics = build_metrics(
            target.ticker,
            facts,
            self.prices,
            consensus_eps=target.watch.consensus_eps,
            consensus_revenue=target.watch.consensus_revenue,
            milestones=target.watch.milestones,
            peer_metrics=peer_metrics,
        )
        if not metrics.company:
            metrics.company = target.name
        self._metrics_cache[target.cik] = metrics
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

    # --- 데일리 브리핑 ---------------------------------------------------
    def daily_brief(self, force: bool = False) -> str | None:
        today = now(self.config.timezone).date()
        if not force and self.state.last_brief_date() == today.isoformat():
            return None

        market_days = upcoming_market_days(today, self.config.holiday_lookahead_days)
        events = upcoming_events(
            today,
            self.config.econ_lookahead_days,
            min_importance=int(self.config.raw.get("econ_min_importance", 2)),
            extra=parse_extra_events(self.config.raw.get("econ_extra_events")),
            include_weekly=bool(self.config.raw.get("econ_include_weekly", False)),
        )

        metrics: list[Metrics] = []
        if self.config.metrics_in_brief:
            for target in self.targets():
                try:
                    metrics.append(self.metrics_for(target, with_peers=False))
                except Exception as exc:
                    log.warning("지표 계산 실패 %s: %s", target.ticker, exc)

        text = format_daily_brief(today, market_days, events, metrics, self.config.timezone)
        if self.notifier.send(text):
            self.state.set_last_brief_date(today.isoformat())
            self.state.save()
        return text

    # --- 루프 -------------------------------------------------------------
    def run_forever(self) -> None:
        interval = self.config.poll_interval_sec
        log.info(
            "감시 시작: %s개 종목, %d초 주기, 브리핑 %s",
            len(self.targets()),
            interval,
            self.config.daily_brief_time or "off",
        )
        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                log.info("종료합니다.")
                return
            except Exception as exc:  # 루프는 어떤 오류에도 죽지 않는다
                log.exception("주기 실행 중 오류: %s", exc)
            time.sleep(interval)

    def _tick(self) -> None:
        self._metrics_cache.clear()
        filings = self.check_filings()
        if filings:
            log.info("새 공시 %d건 전송", len(filings))
        if self._brief_due():
            log.info("데일리 브리핑 전송")
            self.daily_brief()

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


def build_bot(config: Config, dry_run: bool = False) -> Bot:
    return Bot(config, dry_run=dry_run)


def utc_stamp() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def today_str(tz_name: str) -> date:
    return now(tz_name).date()
