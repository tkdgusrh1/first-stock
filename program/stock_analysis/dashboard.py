"""내 컴퓨터에서 브라우저로 보는 대시보드.

외부 라이브러리 없이 파이썬 표준 http.server 만 쓴다.
localhost(127.0.0.1)에만 열리므로 다른 기기에서는 접속할 수 없다.

설계 원칙:
  1) 화면은 절대 멈추지 않는다. 오래 걸리는 작업은 백그라운드로 돌리고,
     그 동안에도 직전 화면을 그대로 보여준다.
  2) 버튼을 누르지 않아도 정보가 다 채워져 있다.
  3) 숫자에는 출처를, 용어에는 설명을 붙인다. 지어낸 문장은 넣지 않는다.
"""

from __future__ import annotations

import html
import logging
import os
import threading
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import markets, screener
from .assessment import LEVEL_ICON, LEVEL_LABEL
from .econ_calendar import parse_extra_events, upcoming_events
from .glossary import groups, lookup
from .macro import FRED_HOME
from .market_calendar import upcoming_market_days
from .metrics import STATUS_ICON, Metrics, _money, _pct
from .korean import guidance_line, note_for, period_ko
from .news import TIER_NAMES
from .news import publisher_tier as news_tier
from .position import build as build_position
from .position import krw_rate_from, won
from .timeutil import ago, clock, dday, kdate, now

log = logging.getLogger(__name__)

TONE_CLASS = {"alert": "tone-alert", "good": "tone-good", "bad": "tone-bad", "plain": "tone-plain"}
LOCK_TIMEOUT = 0.4
AUTOFILL_TRIES = 3      # 자동 채움을 연달아 몇 번까지 다시 해볼지


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def term(label: str) -> str:
    """용어에 설명 툴팁과 사전 링크를 붙인다."""
    entry = lookup(label)
    if not entry:
        return esc(label)
    return (
        f'<a class="term" href="#term-{esc(entry.key)}" title="{esc(entry.short)}">'
        f'{esc(label)}<sup>?</sup></a>'
    )


class Dashboard:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.lock = threading.Lock()
        self.notice: str | None = None
        self.busy: str | None = None
        self._last_body: str | None = None
        self._market_thread: threading.Thread | None = None
        self._autofill_tries = 0
        self.rejected: str | None = None    # 작업 중에 누른 버튼

    # --- 동작 -----------------------------------------------------------
    def run_action(self, action: str, params: dict) -> str:
        one = lambda name: (params.get(name) or [""])[0].strip()  # noqa: E731
        self._autofill_tries = 0        # 사람이 눌렀으면 자동 채움도 다시 시작한다

        if action == "check":
            return self._background("공시를 확인하는 중…", self._do_check)
        if action == "metrics":
            return self._background("지표를 계산하는 중…", self._do_metrics)
        if action == "brief":
            return self._background("브리핑을 보내는 중…", self._do_brief)
        if action == "news":
            return self._background("속보를 확인하는 중…", self._do_news)
        if action == "update":
            return self._background("최신 버전으로 갱신하는 중…", self._do_update)
        if action == "reports":
            return self._background("분기보고서를 읽는 중… (종목당 20초쯤)", self._do_reports)
        if action == "add":
            raw = " ".join(one("ticker").split())
            if not raw:
                return "티커나 회사 이름을 입력해주세요."
            # 한글 회사 이름이면 여기서 종목 코드로 바꾼다.
            # 여섯 자리 숫자를 외우게 하는 건 말이 안 된다.
            if markets.is_korean_name(raw):
                found = self._korean_code(raw)
                if not found.startswith("0") and not found.isdigit():
                    return found          # 못 찾았거나 여러 개 — 그대로 알린다
                raw = found
            ticker = raw.split(":")[0].split()[0].upper()
            return self._background(f"{ticker} 를 추가하는 중…", lambda: self._do_add(raw, ticker))
        if action == "remove":
            ticker = one("ticker").upper()
            return self._background(f"{ticker} 를 빼는 중…", lambda: self._do_remove(ticker))
        if action == "consensus":
            return self._set_consensus(one("ticker"), one("eps"), one("revenue"))
        if action == "position":
            return self._set_position(one("ticker"), one("price"), one("shares"))
        if action == "key":
            return self._set_key(one("name"), one("value"))
        if action == "translator":
            return self._set_translator(one("provider"), one("key"))
        if action == "translate_test":
            return self._background("번역기를 시험하는 중…", self._do_translate_test)
        if action == "memo":
            return self._set_memo(one("ticker"), one("memo"))
        if action == "quit":
            return self._do_quit()
        return "알 수 없는 동작입니다."

    def _do_quit(self) -> str:
        """감시를 완전히 끈다.

        창 없이 도는 프로그램이라 끄는 방법이 눈에 안 보인다. 그래서 늘 보고
        있는 이 화면에 종료 버튼을 뒀다. (창을 못 찾아 폴더를 지우지도 못하는
        일이 실제로 있었다 — 돌고 있는 프로그램이 폴더를 붙잡고 있기 때문이다.)
        """
        self.busy = None
        try:
            self.bot.state.save()
        except Exception as exc:
            log.warning("종료 전 저장 실패: %s", exc)
        log.info("사용자가 화면에서 종료를 눌렀습니다.")
        threading.Timer(0.7, stop_process).start()      # 답을 보낸 뒤에 끈다
        return "감시를 멈췄습니다."

    def _background(self, message: str, func) -> str:
        if self.busy:
            # 이 답을 그냥 돌려주면 화면에 안 보인다. 작업 중일 때는 notice 자리를
            # busy 표시가 차지하기 때문이다. 그래서 눌렀는데 아무 반응이 없는
            # 것처럼 보였다. 눌렀다는 사실을 따로 들고 있다가 같이 띄운다.
            label = _plain(message).rstrip("… .")
            self.rejected = f"{label} — 지금 작업이 끝난 뒤에 다시 눌러주세요."
            return f"이미 실행 중입니다: {self.busy}"

        def worker():
            try:
                with self.lock:
                    result = func()
                self.notice = result
            except Exception as exc:
                log.exception("대시보드 작업 실패")
                self.notice = f"오류가 났습니다: {exc}"
            finally:
                self.busy = None

        self.busy = message
        threading.Thread(target=worker, daemon=True).start()
        return message

    def _do_check(self) -> str:
        filings = self.bot.check_filings()
        return f"새 공시 {len(filings)}건을 찾았습니다." if filings else "새 공시가 없습니다."

    def _progress(self, label: str):
        """어디까지 왔는지 화면에 계속 알려준다.

        종목당 10초 넘게 걸리는 일이 흔하다. 진행 표시가 없으면 도는 중인지
        멈춘 건지 알 수가 없어서, 사용자는 '계속 로딩만 한다' 고 느끼게 된다.
        """
        def report(index: int, total: int, ticker: str) -> None:
            self.busy = f"{label} ({index + 1}/{total}) {ticker}…"
        return report

    def _do_metrics(self, force: bool = True) -> str:
        if not self.bot.targets():
            return self._no_targets_message()
        done, failed = self.bot.ensure_all_metrics(
            force=force, on_progress=self._progress("지표를 계산하는 중")
        )
        if not done and not failed:
            return "새로 계산할 종목이 없습니다."
        message = f"{done}개 종목 지표를 새로 계산했습니다."
        if failed:
            message += f" 실패: {', '.join(failed)} — 잠시 뒤 다시 시도해보세요."
        return message

    def _no_targets_message(self) -> str:
        missing = self.bot.unresolved_tickers()
        if missing:
            return (f"SEC 에서 {', '.join(missing[:5])} 를 찾지 못했습니다. "
                    "접속이 막혔을 수 있습니다 (program/logs/실행기록.log 확인).")
        return "감시 중인 종목이 없습니다. 아래에서 티커를 추가해주세요."

    def _do_fill(self) -> str:
        done, failed = self.bot.ensure_all_metrics(
            force=False, on_progress=self._progress("종목 정보를 불러오는 중")
        )
        if not self.bot.targets():
            return self._no_targets_message()
        # 가이던스·업종도 같이 채운다 (버튼을 누르지 않아도 보이도록)
        self.busy = "가이던스·업종을 확인하는 중…"
        self.bot.fill_context(limit=3)
        return f"{done}개 종목 정보를 불러왔습니다." + (f" 실패: {', '.join(failed)}" if failed else "")

    def _do_reports(self) -> str:
        loaded, missing, guided, flagged = 0, [], 0, 0
        for target in self.bot.targets():
            report = self.bot.report_for(target, refresh=True)
            if report and report.sections:
                loaded += 1
            else:
                missing.append(target.ticker)
            # 가이던스·위험 요인·업종도 같이 채운다 (같은 공시를 다시 받지 않도록 한 번에)
            guidance = self.bot.guidance_for(target, refresh=True)
            if guidance and guidance.found:
                guided += 1
            risk = self.bot.risk_for(target, refresh=True)
            if risk and risk.flags:
                flagged += 1
            self.bot.insiders_for(target, refresh=True)
            self.bot.industry_for(target, refresh=True)
        message = f"보고서 {loaded}개, 가이던스 {guided}개를 읽었습니다."
        if flagged:
            message += f" 위험 요인에 무겁게 볼 표현이 있는 종목 {flagged}개."
        if missing:
            message += f" 본문을 찾지 못한 종목: {', '.join(missing)}"
        return message

    def _do_news(self) -> str:
        items = self.bot.check_news()
        if not items:
            return "새 속보가 없습니다."
        return f"속보 {len(items)}건을 찾았습니다."

    def _do_update(self) -> str:
        ok, message = self.bot.apply_update()
        if ok:
            return message + " ← '끄기' 를 누른 뒤 '시작하기' 를 다시 실행하면 새 버전으로 돕니다."
        return message

    def _do_brief(self) -> str:
        self.bot.daily_brief(force=True)
        if self.bot.notifier.dry_run:
            return "텔레그램이 꺼져 있어 콘솔에만 출력했습니다."
        return "텔레그램으로 브리핑을 보냈습니다."

    def _korean_code(self, name: str) -> str:
        """한글 회사 이름 → 종목 코드. 못 정하면 사람이 읽을 안내를 돌려준다.

        비슷하다고 아무거나 고르지 않는다. 엉뚱한 회사를 감시하게 된다.
        """
        dart = getattr(self.bot, "dart", None)
        if dart is None or not dart.ready:
            return ("DART 인증키가 없어 회사 이름으로는 찾을 수 없습니다. "
                    "종목 코드(예: 005930)로 넣거나, 화면 아래 '열쇠 보관함' 에 "
                    "DART 인증키를 넣어주세요.")
        code, _found, others = dart.resolve_name(name)
        if code:
            return code
        if others:
            candidates = " · ".join(f"{n}({c})" for c, n in others[:6])
            return f"'{name}' 과 비슷한 회사가 여럿입니다. 하나를 골라주세요 → {candidates}"
        if not dart.corp_codes():
            return "아직 DART 회사 목록을 받지 못했습니다. 잠시 뒤 다시 시도해주세요."
        return f"'{name}' 을(를) 상장사 목록에서 찾지 못했습니다. 종목 코드로 넣어보세요."

    def _do_add(self, raw: str, ticker: str) -> str:
        reply = _plain(self.bot.commands.handle(f"/add {raw}"))
        self.bot.ensure_all_metrics(force=False)
        return reply

    def _do_remove(self, ticker: str) -> str:
        return _plain(self.bot.commands.handle(f"/remove {ticker}"))

    def _set_consensus(self, ticker: str, eps: str, revenue: str) -> str:
        parts = []
        if eps:
            parts.append(f"eps={eps}")
        if revenue:
            parts.append(f"rev={revenue}")
        if not ticker or not parts:
            return "컨센서스 값을 입력해주세요."
        with self.lock:
            reply = _plain(self.bot.commands.handle(f"/consensus {ticker} {' '.join(parts)}"))
            self.bot.ensure_all_metrics(force=True)
        return reply

    def _set_position(self, ticker: str, price: str, shares: str) -> str:
        """내 매수가·수량 저장. 둘 다 비우면 지운다."""
        if not ticker:
            return "종목을 알 수 없습니다."
        with self.lock:
            try:
                for name, raw in (("buy_price", price), ("buy_shares", shares)):
                    text = raw.replace(",", "").strip()
                    # 저장하기 전에 숫자인지 확인한다. 나중에 설정을 읽을 때 터지면 안 된다.
                    self.bot.overrides.set_field(ticker, name, float(text) if text else None)
                self.bot.overrides.save()
                self.bot.reload_watchlist()
            except ValueError:
                return "숫자로 넣어주세요. 예: 매수가 48.20 / 수량 10"
            except Exception as exc:
                return f"저장하지 못했습니다: {exc}"
        if not price.strip() and not shares.strip():
            return f"{ticker} 보유 정보를 지웠습니다."
        return f"{ticker} 매수가 {price} · 수량 {shares} 을(를) 저장했습니다."

    def _set_key(self, name: str, value: str) -> str:
        """인증키·토큰을 화면에서 넣는다. 저장 자리는 프로그램 폴더 바깥이다."""
        with self.lock:
            try:
                return self.bot.save_key(name, value)
            except Exception as exc:
                return f"저장하지 못했습니다: {exc}"

    def _set_translator(self, provider: str, key: str) -> str:
        """번역 열쇠를 화면에서 저장한다. config.yml 을 직접 고치지 않아도 되게."""
        provider = (provider or "auto").strip().lower()
        key = (key or "").strip()
        field = {
            "deepl": "deepl_key", "azure": "azure_key",
            "papago": "papago_id_key", "google_cloud": "google_cloud_key",
        }.get(provider)

        with self.lock:
            try:
                self.bot.overrides.set_setting("translate", "provider", "auto")
                if field:
                    self.bot.overrides.set_setting("translate", field, key)
                self.bot.overrides.save()
                self.bot.reload_translator()
            except Exception as exc:
                return f"저장하지 못했습니다: {exc}"

        if not key:
            return "열쇠를 지웠습니다. 무료 번역으로 돌아갑니다."
        label = {"deepl": "DeepL", "azure": "Azure 번역기",
                 "papago": "파파고", "google_cloud": "Google 번역 API"}.get(provider, provider)
        return f"{label} 열쇠를 저장했습니다. 아래 '번역 시험' 을 눌러 확인해보세요."

    def _do_translate_test(self) -> str:
        """실제로 한 문장을 번역해본다. 되는지 눈으로 확인하는 게 가장 확실하다."""
        sample = "Revenue increased 78% year over year to $213.0 million."
        result = self.bot.translator.translate(sample)
        if not result:
            available = self.bot.translator.available()
            if not available:
                return "쓸 수 있는 번역기가 없습니다. 열쇠를 넣거나 무료 번역을 켜주세요."
            return "번역기가 응답하지 않았습니다. 열쇠가 맞는지 확인해주세요."
        return f"{result.label} 로 번역했습니다 → {result.text}"

    def _set_memo(self, ticker: str, memo: str) -> str:
        if not ticker:
            return "종목을 알 수 없습니다."
        with self.lock:
            try:
                self.bot.overrides.set_field(ticker, "note", memo)
                self.bot.overrides.save()
                self.bot.reload_watchlist()
            except Exception as exc:
                return f"메모를 저장하지 못했습니다: {exc}"
        return f"{ticker} 메모를 저장했습니다." if memo else f"{ticker} 메모를 지웠습니다."

    def load_initial(self) -> None:
        self._background("종목 정보를 불러오는 중…", self._do_fill)
        self.start_market_refresh()

    def start_market_refresh(self, interval: float = 60.0) -> None:
        """환율·지수는 따로 도는 스레드가 갱신한다.

        공시·지표 작업과 같은 잠금을 쓰지 않는다. 그래야 지표를 계산하는
        30초 동안에도 환율은 계속 최신으로 바뀐다.
        """
        if self._market_thread is not None:
            return

        def worker():
            while True:
                try:
                    self.bot.refresh_market()
                    self.bot.refresh_macro()     # 자기 주기(6시간)를 스스로 지킨다
                except Exception as exc:
                    log.debug("환율 갱신 실패: %s", exc)
                time.sleep(interval)

        self._market_thread = threading.Thread(target=worker, daemon=True)
        self._market_thread.start()

    def autofill_if_needed(self) -> None:
        """빈 종목이 있으면 알아서 채운다. 단, 끝없이 반복하지는 않는다.

        화면은 새로고침될 때마다 여기를 지난다. 계속 실패하는 종목이 있으면
        '불러오는 중' 이 영원히 반복돼서 멈춘 것처럼 보인다. 몇 번 해보고
        안 되면 손을 뗀다 — 실패 이유는 각 종목 칸에 그대로 표시된다.
        """
        if self.busy:
            return
        missing = self.bot.missing_metrics()
        if not missing:
            self._autofill_tries = 0
            return
        if self._autofill_tries >= AUTOFILL_TRIES:
            return
        self._autofill_tries += 1
        names = ", ".join(t.ticker for t in missing[:3])
        more = f" 외 {len(missing) - 3}개" if len(missing) > 3 else ""
        self._background(f"{names}{more} 정보를 불러오는 중…", self._do_fill)

    # --- 화면 -----------------------------------------------------------
    def render(self, market: str = markets.US) -> str:
        self.start_market_refresh()      # 화면을 처음 열 때부터 환율이 돌게 한다
        self.autofill_if_needed()
        if self.lock.acquire(timeout=LOCK_TIMEOUT):
            try:
                body = self._build_body(market)
                self._last_body = body
            finally:
                self.lock.release()
        else:
            body = self._last_body or _loading_body()

        page = _PAGE.format(body=body, refresh=4 if self.busy else 90)
        page = page.replace("<!--THEME-->", _THEME_SCRIPT, 1)
        return page.replace("<!--NOTICE-->", self._notice_block(), 1)

    def render_goodbye(self) -> str:
        """종료 직후 마지막 화면. 자동 새로고침을 걸면 없는 서버를 두드린다."""
        page = _PAGE.format(body=_goodbye_body(), refresh=86400)
        page = page.replace("<!--THEME-->", _THEME_SCRIPT, 1)
        return page.replace("<!--NOTICE-->", "", 1)

    def _build_body(self, market: str = markets.US) -> str:
        """한 시장의 화면. 미국과 한국은 보는 자료가 아예 달라 섞지 않는다.

        미국은 SEC 가 전부 무료로 주지만 한국은 DART 열쇠가 필요하고,
        가져올 수 있는 항목도 다르다. 한 화면에 섞으면 어떤 숫자가 어느
        기준으로 나온 것인지 알 수 없게 된다.
        """
        bot = self.bot
        config = bot.config
        today = now(config.timezone).date()
        targets = [t for t in bot.targets() if t.market == market]

        metrics = bot.cached_metrics()
        earnings = bot.cached_earnings()
        reports = bot.cached_reports()
        errors = bot.metrics_errors()
        guidance = bot.cached_guidance()
        estimates = bot.cached_estimates()
        industries = bot.cached_industries()
        tracks = bot.cached_track_records()
        risks = bot.cached_risks()
        insiders = bot.cached_insiders()
        koreans = bot.cached_korean()
        assessments = {t.cik: bot.assessment_for(t) for t in targets}
        quotes = bot.market_snapshot()
        krw = krw_rate_from(quotes)
        recaps = {t.cik: bot.recap_for(t) for t in targets}

        market_days = upcoming_market_days(today, max(config.holiday_lookahead_days, 30))
        events = upcoming_events(
            today,
            max(config.econ_lookahead_days, 21),
            min_importance=int(config.raw.get("econ_min_importance", 2)),
            extra=parse_extra_events(config.raw.get("econ_extra_events")),
            include_weekly=bool(config.raw.get("econ_include_weekly", False)),
        )
        recent = bot.state.recent(40)
        news = bot.state.news(25)
        latest = bot.state.known_latest()

        rows = [
            (t, metrics.get(t.cik), earnings.get(t.cik), assessments.get(t.cik))
            for t in targets
        ]
        return "\n".join(
            [
                _header(today, market_days, bot.state.last_check(), config, news, quotes),
                _market_tabs(market, bot),
                "<!--NOTICE-->",
                _update_banner(latest),
                _summary_table(rows, today, errors, bot.unresolved_tickers()),
                _detail_cards(rows, recent, today, errors, reports, guidance, estimates,
                              industries, tracks, risks, insiders, recaps, krw, koreans),
                _filings(recent, [t.ticker for t in targets], market),
                # 아래 셋은 전부 미국 기준이다. 한국 화면에 그대로 띄우면
                # 한국 증시 이야기로 읽힌다. 대신 무엇이 아직 없는지 밝힌다.
                _picks_section(bot.top_picks(), bot.screen_progress(),
                               bot.recommend_enabled, bot.universe_source())
                if market == markets.US else _not_yet_here(bot),
                _macro_section(bot.macro_snapshot()) if market == markets.US else "",
                _schedule(today, market_days, events) if market == markets.US else "",
                _translate_section(bot) if market == markets.US else "",
                # 열쇠는 두 화면 모두에 둔다. 한국 화면이 비는 이유가 바로 이것이라,
                # 미국 화면까지 건너가서 넣게 만들면 안 된다.
                _keys_section(bot),
                _glossary_section(),
                _footer(bot.calendar_warning()),
            ]
        )

    def _notice_block(self) -> str:
        if self.busy:
            waiting = ""
            if self.rejected:
                text, self.rejected = self.rejected, None
                waiting = f'<div class="notice">🕐 {esc(text)}</div>'
            return (
                f'<div class="notice busy">⏳ {esc(self.busy)} '
                '<span class="muted">— 끝나면 자동으로 새로고침됩니다</span></div>'
                f"{waiting}"
            )
        self.rejected = None
        if self.notice:
            text, self.notice = self.notice, None
            bad = any(word in text for word in ("❌", "오류", "실패", "거부", "찾지 못"))
            cls, icon = ("notice bad", "") if bad else ("notice", "✅ ")
            return f'<div class="{cls}">{icon}{esc(text)}</div>'
        return ""


def stop_process() -> None:
    """이 프로그램을 끝낸다. (테스트에서는 이 함수만 바꿔치기한다)"""
    os._exit(0)


def _goodbye_body() -> str:
    return """
<div class="gate">
  <h1>⏻ 감시를 멈췄습니다</h1>
  <p class="sub">이 창은 닫으셔도 됩니다.</p>
  <p class="gate-note">다시 보려면 프로그램 폴더의 <b>시작하기</b> 파일을 더블클릭하세요.</p>
  <p class="gate-note">이제 프로그램 폴더를 지우거나 옮길 수 있습니다.
     돌고 있는 동안에는 윈도우가 폴더를 붙잡고 있어서
     <i>"사용 중인 폴더"</i> 라고 나옵니다.</p>
</div>"""


def _plain(text: str | None) -> str:
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", text or "완료")).strip()


# --------------------------------------------------------------------------
# 머리말
# --------------------------------------------------------------------------
def _not_yet_here(bot) -> str:
    """한국 화면에서 아직 못 하는 것을 그대로 적는다.

    미국 화면에 있는 것이 여기 없으면 '고장 났나' 싶어진다. 아직 안 만든
    것과 고장 난 것은 다르므로, 무엇이 왜 없는지 밝혀 둔다.
    """
    dart = getattr(bot, "dart", None)
    key_line = (
        '<li><b>재무제표·공시</b> — DART 인증키가 없어 비어 있습니다. '
        '무료·1분: <a href="https://opendart.fss.or.kr" target="_blank" rel="noopener">'
        'opendart.fss.or.kr</a> 에서 받아 이 화면 아래 <b>열쇠 보관함</b> 에 '
        '붙여넣으세요.</li>'
        if not (dart and dart.ready) else
        '<li><b>재무제표</b> — DART 사업보고서의 <b>연간 확정치</b>를 씁니다. '
        '미국(최근 4개 분기 합산)보다 한 걸음 늦습니다.</li>'
    )
    return f"""
<section><details class="fold" data-keep="kr-limits" open>
  <summary class="fold-h"><h2>한국 화면에서 아직 안 되는 것</h2></summary>
  <div class="fold-body">
  <ul class="bullets small">
    {key_line}
    <li><b>PER · PSR</b> — 시가총액을 구하려면 발행주식수가 필요한데 아직 받지 않습니다.
        그래서 밸류에이션은 '판단 불가' 로 둡니다. 지어내지 않습니다.</li>
    <li><b>눈여겨볼 종목(추천)</b> — 미국 화면에만 있습니다. SEC 가 전체 기업의 매출을
        한 번에 주기 때문에 후보 순위를 만들 수 있는데, DART 에는 그런 창구가 없습니다.</li>
    <li><b>휴장일 · 경제지표 · 번역</b> — 미국 기준이라 미국 화면에만 둡니다.</li>
  </ul>
  <p class="muted small">지금 한국 화면에서 되는 것: <b>주가 · 등락 · 52주 위치 ·
     재무제표 판정(다섯 축) · 체크리스트 · 내 매수가 손익</b>.</p>
  </div>
</details></section>"""


def _market_tabs(current: str, bot) -> str:
    """미국 / 한국 전환 단추.

    두 시장은 보는 자료가 아예 다르다. 미국은 SEC 가 전부 무료로 주지만
    한국은 DART 열쇠가 필요하고, 받아올 수 있는 항목도 다르다. 그래서
    한 화면에 섞지 않고 나눈다.
    """
    counts = {markets.US: 0, markets.KR: 0}
    for target in bot.targets():
        counts[target.market] = counts.get(target.market, 0) + 1

    tabs = []
    for key in (markets.US, markets.KR):
        live = " on" if key == current else ""
        note = ""
        dart = getattr(bot, "dart", None)
        if key == markets.KR and counts[key] and not (dart and dart.ready):
            note = ('<span class="tab-warn" title="DART 인증키가 없어 재무제표는 '
                    '비어 있습니다">열쇠 필요</span>')
        state, shown, guessed = bot.market_state(key)
        title = (f"{markets.hours_text(key)} · 현지 {shown}"
                 + (" · 시세를 못 받아 시각으로 어림한 값입니다(휴장일은 반영 안 됨)"
                    if guessed else " · 거래소가 알려준 상태입니다"))
        mark = "~" if guessed else ""
        tabs.append(
            f'<a class="tab{live}" href="/?m={esc(key)}" title="{esc(title)}">'
            f'{esc(markets.MARKET_NAME[key])}'
            f'<span class="tab-n">{counts[key]}</span>'
            f'<span class="tab-open">{markets.STATE_ICON[state]} '
            f'{esc(mark + markets.STATE_LABEL[state])}</span>{note}</a>'
        )
    return f'<nav class="tabs">{"".join(tabs)}</nav>'


def _header(today: date, market_days, last_check, config, news=None, market=None) -> str:
    todays = [d for d in market_days if d.day == today]
    if todays:
        status, cls = f"{todays[0].kind} — {todays[0].name}", "closed"
    elif today.weekday() >= 5:
        status, cls = "주말 휴장", "closed"
    else:
        status, cls = "정상 개장 (한국시간 22:30~05:00)", "open"

    urgent = sum(1 for n in (news or []) if int(n.get("severity", 1)) >= 3)
    badge = f' <span class="badge-count">{urgent}</span>' if urgent else ""
    news_panel = _news_panel(news)

    return f"""
<header>
  <div class="left-col">
    <h1>📈 관심 종목 감시</h1>
    <p class="sub">{esc(kdate(today))} · 미국 증시 <span class="badge {cls}">{esc(status)}</span></p>
    <p class="sub">마지막 공시 확인 {esc(last_check or "아직 없음")} ·
       {config.poll_interval_sec // 60}분마다 자동 확인 ·
       <a href="#glossary">용어 사전</a></p>
    {_market_strip(market)}
  </div>
  <div class="right-col">
   <div class="actions">
    <form method="post" action="/action"><input type="hidden" name="action" value="news">
      <button type="submit">📰 속보{badge}</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="check">
      <button type="submit">🔄 공시</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="metrics">
      <button type="submit">📊 지표</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="reports">
      <button type="submit">📄 보고서</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="brief">
      <button type="submit">✉️ 브리핑</button></form>
    <button type="button" id="themebtn" class="ghost theme" onclick="cycleTheme()">🕗 시간에 맞춰</button>
    <form method="post" action="/action"
          onsubmit="return confirm('감시를 완전히 멈춥니다.\\n\\n다시 보려면 시작하기 파일을 더블클릭하세요. 계속할까요?')">
      <input type="hidden" name="action" value="quit">
      <button type="submit" class="ghost" title="감시를 완전히 종료합니다">⏻ 종료</button></form>
   </div>
   {news_panel}
  </div>
</header>"""


# --------------------------------------------------------------------------
# 전 종목 요약 표
# --------------------------------------------------------------------------
SUMMARY_COLUMNS = [
    "종목", "상황", "주가", "시총", "매출(TTM)", "매출성장",
    "영업이익률", "ROE", "ROIC", "PER", "PSR", "런웨이", "체크", "실적발표",
]


def _summary_table(rows, today, errors=None, unresolved=None) -> str:
    if not rows:
        # 설정에는 있는데 화면에 없다면 '없다' 가 아니라 '못 찾았다' 이다.
        # 그 둘을 같은 말로 적으면 SEC 가 막혔을 때 원인을 영영 못 찾는다.
        if unresolved:
            names = ", ".join(esc(t) for t in unresolved[:8])
            reason = (
                f'<p class="warn">⚠️ 설정에 있는 <b>{names}</b> 를 SEC 에서 찾지 못했습니다.</p>'
                '<p class="muted small">대개 SEC 접속이 막힌 경우입니다 '
                "(공유기·백신·VPN·회사망). 잠시 뒤 자동으로 다시 시도합니다. "
                "계속 이러면 <code>program/logs/실행기록.log</code> 를 확인해주세요.</p>"
            )
        else:
            reason = '<p class="muted">감시 중인 종목이 없습니다. 아래에서 티커를 입력해 추가해보세요.</p>'
        return f"""
<section>
  <h2>관심 종목</h2>
  {reason}
  {_add_form()}
</section>"""

    errors = errors or {}
    head = "".join(f"<th>{term(c)}</th>" for c in SUMMARY_COLUMNS)
    body = "".join(
        _summary_row(t, m, e, a, today, errors.get(t.cik)) for t, m, e, a in rows
    )
    return f"""
<section>
  <h2>전체 종목 한눈에 <span class="count">{len(rows)}개</span></h2>
  <p class="hint">항목 이름의 <sup>?</sup> 를 누르면 그 용어가 무엇인지 설명이 나옵니다.</p>
  <div class="scroll">
    <table class="summary">
      <thead><tr>{head}</tr></thead>
      <tbody>{body}</tbody>
    </table>
  </div>
  {_add_form()}
</section>"""


def _summary_row(target, m: Metrics | None, earnings, verdict, today, error=None) -> str:
    ticker = f'<a href="#{esc(target.ticker)}"><b>{esc(target.ticker)}</b></a>'
    if target.name:
        ticker += f'<br><span class="muted small">{esc(target.name)}</span>'

    if m is None:
        empty = "".join("<td class='num muted'>…</td>" for _ in range(len(SUMMARY_COLUMNS) - 2))
        state = "<span class='down'>불러오기 실패</span>" if error else "<span class='muted'>불러오는 중…</span>"
        return f"<tr><td>{ticker}</td><td>{state}</td>{empty}</tr>"

    if verdict:
        situation = (
            f'<span class="verdict v-{esc(verdict.level)}">{verdict.icon} {esc(verdict.label)}</span>'
        )
    else:
        situation = '<span class="muted">-</span>'

    price = "-"
    if m.price:
        price = f"${m.price:,.2f}"
        if m.price_change_pct is not None:
            cls = "up" if m.price_change_pct >= 0 else "down"
            price += f'<br><span class="small {cls}">{m.price_change_pct:+.2f}%</span>'
        if m.extended_price:
            cls = "up" if (m.extended_change_pct or 0) >= 0 else "down"
            extra = f" {m.extended_change_pct:+.2f}%" if m.extended_change_pct is not None else ""
            price += (
                f'<br><span class="small muted">{esc(m.extended_label)}</span>'
                f'<br><span class="small {cls}">${m.extended_price:,.2f}{extra}</span>'
            )
        if m.pct_from_high is not None:
            # 52주 최고 대비 위치. 지금이 비싼 편인지 싼 편인지 한 눈에.
            price += f'<br><span class="small muted">52주 고점 대비 {m.pct_from_high:+.0f}%</span>'

    # ETF 는 기업 재무 지표가 존재하지 않는다. 빈칸 아홉 개 대신 이유를 적는다.
    if m.is_fund:
        checks = m.checks
        passes = sum(1 for c in checks if c.status == "pass")
        fails = sum(1 for c in checks if c.status == "fail")
        warns = sum(1 for c in checks if c.status == "warn")
        note = m.fund.risk_label if m.fund else "ETF"
        return (
            f"<tr><td>{ticker}</td><td class='num'>{situation}</td><td class='num'>{price}</td>"
            f"<td class='num muted etfnote' colspan='9'>"
            f"{esc(note)} · ETF 라 매출·ROE 같은 기업 지표가 없습니다 "
            f"— <a href=\"#{esc(target.ticker)}\">상세에서 상품 정보 보기</a></td>"
            f"<td class='num'><span class=\"up\">✅{passes}</span> "
            f"<span class=\"warnmark\">⚠️{warns}</span> <span class=\"down\">❌{fails}</span></td>"
            f"<td class='num muted'>-</td></tr>"
        )

    margin = _pct(m.op_margin)
    if m.op_margin is not None and m.op_margin_prior is not None:
        up = m.op_margin > m.op_margin_prior
        margin += f' <span class="{"up" if up else "down"}">{"↑" if up else "↓"}</span>'

    growth = "-"
    if m.revenue_growth is not None:
        cls = "up" if m.revenue_growth >= 0 else "down"
        growth = f'<span class="{cls}">{m.revenue_growth:+.0%}</span>'

    checks = m.checks + m.priority
    passes = sum(1 for c in checks if c.status == "pass")
    fails = sum(1 for c in checks if c.status == "fail")
    warns = sum(1 for c in checks if c.status == "warn")
    check_cell = (
        f'<span class="up">✅{passes}</span> <span class="warnmark">⚠️{warns}</span> '
        f'<span class="down">❌{fails}</span>'
    )

    if earnings:
        when = f"{earnings.day.isoformat()}<br><span class='small muted'>{dday(today, earnings.day)}"
        when += " · 추정" if earnings.estimated else " · 확정"
        when += "</span>"
    elif target.watch.earnings_date:
        when = target.watch.earnings_date.isoformat()
    else:
        when = '<span class="muted">-</span>'

    cells = [
        situation,
        price,
        _money(m.market_cap),
        _money(m.revenue_ttm),
        growth,
        margin,
        _pct(m.roe),
        _pct(m.roic),
        f"{m.per:.1f}x" if m.per else "-",
        f"{m.psr:.1f}x" if m.psr else "-",
        f"{m.runway_years:.1f}년" if m.runway_years is not None else "-",
        check_cell,
        when,
    ]
    tds = "".join(f'<td class="num">{c}</td>' for c in cells)
    return f"<tr><td>{ticker}</td>{tds}</tr>"


def _add_form() -> str:
    return """
  <form method="post" action="/action" class="addform">
    <input type="hidden" name="action" value="add">
    <input type="text" name="ticker"
           placeholder="티커 또는 회사 이름 (예: TSLA · 삼성전자 · 005930)"
           maxlength="24" autocomplete="off" required>
    <button type="submit">+ 종목 추가</button>
  </form>"""


# --------------------------------------------------------------------------
# 종목별 상세
# --------------------------------------------------------------------------
def _detail_cards(rows, recent, today, errors, reports, guidance, estimates, industries,
                  tracks=None, risks=None, insiders=None, recaps=None, krw=None,
                  koreans=None) -> str:
    if not rows:
        return ""
    tracks, risks = tracks or {}, risks or {}
    insiders, recaps = insiders or {}, recaps or {}
    koreans = koreans or {}
    cards = "".join(
        _detail_card(
            t, m, e, a, recent, today, errors.get(t.cik), reports.get(t.cik),
            guidance.get(t.cik), estimates.get(t.cik), industries.get(t.cik),
            tracks.get(t.cik), risks.get(t.cik), insiders.get(t.cik),
            recaps.get(t.cik), krw, koreans.get(t.cik),
        )
        for t, m, e, a in rows
    )
    return (
        '<section><h2>종목별 상세 '
        '<span class="count">제목을 누르면 펼쳐집니다</span></h2>'
        f'<div class="stack">{cards}</div></section>'
    )


def _group(title: str, body: str, headline: str = "", level: str = "", open_: bool = False) -> str:
    """카드 안의 한 구획.

    접힌 상태에서도 **결론 한 줄**이 보여야 한다. 그래야 무엇을 펼칠지 고를 수 있다.
    """
    if not body.strip():
        return ""
    dot = f'<span class="dot d-{esc(level)}"></span>' if level else ""
    note = f'<span class="grp-note">{esc(headline)}</span>' if headline else ""
    return (
        f'<details class="grp"{" open" if open_ else ""}>'
        f'<summary>{dot}<b>{esc(title)}</b>{note}</summary>'
        f'<div class="grp-body">{body}</div></details>'
    )


def _detail_card(target, m, earnings, verdict, recent, today, error, report,
                 guidance=None, estimate=None, industry=None, track=None,
                 risk=None, insider=None, recap=None, krw=None, korean=None) -> str:
    parts = [f'<details class="card wide stock" id="{esc(target.ticker)}">']

    title = f'<h3>{esc(target.ticker)}</h3>'
    title_price = ""
    if m and m.price:
        change = ""
        if m.price_change_pct is not None:
            cls = "up" if m.price_change_pct >= 0 else "down"
            change = f' <span class="{cls}">{m.price_change_pct:+.2f}%</span>'
        extended = ""
        if m.extended_price:
            cls = "up" if (m.extended_change_pct or 0) >= 0 else "down"
            pct = f" {m.extended_change_pct:+.2f}%" if m.extended_change_pct is not None else ""
            extended = (
                f'<span class="ext">{esc(m.extended_label)} '
                f'<b class="{cls}">${m.extended_price:,.2f}{pct}</b></span>'
            )
        state = f'<span class="tag">{esc(m.market_state)}</span>' if m.market_state else ""
        title_price = f'<span class="price">${m.price:,.2f}{change} {state}{extended}</span>'
    # 접혀 있어도 종목·주가·상황은 보이게 summary 안에 넣는다
    verdict_chip = ""
    if verdict:
        verdict_chip = (
            f'<span class="verdict v-{esc(verdict.level)}">{verdict.icon} {esc(verdict.label)}</span>'
        )
    subtitle = target.name or (m.company if m else "")
    fund = getattr(m, "fund", None) if m else None
    fund_chip = ""
    if fund is not None:
        cls = "etf risky" if fund.high_risk else "etf"
        fund_chip = f'<span class="tag {cls}">{esc(fund.risk_label)}</span>'
    parts.append(
        f'<summary class="card-head">{title}'
        f'<span class="sub cname">{esc(subtitle)}</span>'
        f'{fund_chip}{verdict_chip}{title_price}</summary>'
    )
    parts.append('<div class="card-body">')
    parts.append(f'<p class="sub">CIK {esc(target.cik)}{_remove_inline(target.ticker)}</p>')

    if m is None:
        if error:
            parts.append(
                '<p class="warn">⚠️ 정보를 가져오지 못했습니다.</p>'
                f'<p class="sub">{esc(error)}</p>'
                '<form method="post" action="/action">'
                '<input type="hidden" name="action" value="metrics">'
                '<button type="submit">다시 시도</button></form>'
            )
        else:
            parts.append('<p class="muted">정보를 불러오는 중입니다… (10~30초)</p>')
        parts.append("</div></details>")
        return "".join(parts)

    if m.warnings:
        for warning in m.warnings:
            parts.append(f'<p class="warn">⚠️ {esc(warning)}</p>')

    # ETF 는 회사가 아니다. 매출·ROE 칸을 비워두는 대신 ETF 기준으로 보여준다.
    if m.is_fund:
        parts.append(_assessment_block(verdict))
        parts.append('<div class="group"><div class="group-title">📦 이 ETF 는 무엇인가</div>')
        parts.append(_fund_block(m, target))
        parts.append("</div>")
        parts.append('<div class="group"><div class="group-title">🔍 기록</div>')
        parts.append(_filings_for(target, recent))
        parts.append(_memo_block(target))
        parts.append("</div>")
        parts.append("</div></details>")
        return "".join(parts)

    # 항상 펼쳐두는 것: 지금 상황 · 내 손익 · 다음 실적일
    parts.append(_assessment_block(verdict))
    parts.append(_position_block(target, m, krw))
    if earnings:
        kind = "추정" if earnings.estimated else "확정"
        parts.append(
            f'<p class="line">📆 <b>실적 발표</b> {esc(kdate(earnings.day))} '
            f'<span class="muted">({esc(dday(today, earnings.day))}, {kind})</span></p>'
        )

    # 나머지는 구획으로 접어둔다. 접힌 줄에 결론이 보이므로 무엇을 펼칠지 고를 수 있다.
    parts.append(_group(
        "🎯 메모 기준 판단",
        _checks_block(m) + _milestones_block(target),
        _checks_headline(m), _checks_level(m), open_=True,
    ))
    parts.append(_group(
        "📈 가이던스와 실적",
        _recap_block(recap) + _guidance_block(guidance, korean) + _track_block(track, korean)
        + _consensus_block(target, m, estimate),
        _guidance_headline(guidance, track, recap),
        (track.level if track else "") or (recap.level if recap else ""),
    ))
    parts.append(_group(
        "⚠️ 위험 요인 변화",
        _risk_block(risk, korean), risk.summary if risk else "아직 확인하지 않았습니다.",
        risk.level if risk else "unknown",
    ))
    parts.append(_group(
        "👤 내부자 거래",
        _insider_block(insider), insider.summary if insider else "아직 확인하지 않았습니다.",
        insider.level if insider else "unknown",
    ))
    parts.append(_group(
        "📊 숫자와 추이", _numbers_block(m) + _trends_block(m),
        _numbers_headline(m),
    ))
    parts.append(_group(
        "📄 회사가 밝힌 내용", _report_block(report, korean),
        _report_headline(report),
    ))
    parts.append(_group(
        "🔍 비교와 기록",
        _peers_block(m, industry) + _filings_for(target, recent)
        + _memo_block(target) + _inputs_block(target, m) + _sources_block(m),
        "동종업계 · 공시 기록 · 직접 입력 · 출처",
    ))

    parts.append("</div></details>")
    return "".join(parts)


# --- 구획 제목에 붙는 한 줄 결론 --------------------------------------------
def _checks_headline(m: Metrics) -> str:
    checks = m.checks + m.priority
    passes = sum(1 for c in checks if c.status == "pass")
    fails = sum(1 for c in checks if c.status == "fail")
    state = "흑자" if m.profitable else ("적자" if m.profitable is False else "손익 미확인")
    return f"{state} 기업 · 통과 {passes} · 미달 {fails}"


def _checks_level(m: Metrics) -> str:
    fails = sum(1 for c in m.checks + m.priority if c.status == "fail")
    passes = sum(1 for c in m.checks + m.priority if c.status == "pass")
    if fails >= 3:
        return "poor"
    return "good" if passes > fails else "fair"


def _guidance_headline(guidance, track, recap) -> str:
    parts = []
    if guidance is not None and getattr(guidance, "found", False) and guidance.items:
        parts.append(f"회사 제시 {guidance.items[0].range_text or '문장 참조'}")
    if track is not None and track.judged:
        parts.append(track.summary)
    if recap is not None and not recap.empty:
        parts.append(recap.summary)
    return " · ".join(parts) or "가이던스 원문과 과거 이행 이력"


def _numbers_headline(m: Metrics) -> str:
    bits = []
    if m.revenue_ttm:
        bits.append(f"매출 {_money(m.revenue_ttm)}")
    if m.high_52w and m.low_52w:
        bits.append(f"52주 ${m.low_52w:,.0f}~${m.high_52w:,.0f}")
    if m.share_growth_1y is not None:
        bits.append(f"희석 {m.share_growth_1y:+.1%}")
    return " · ".join(bits) or "핵심 숫자와 분기 추이"


def _report_headline(report) -> str:
    if report is None:
        return "아직 읽지 않았습니다."
    if not report.sections:
        return f"{report.form} {report.filing_date} · 표준 항목을 찾지 못함"
    return f"{report.form} {report.filing_date} · {len(report.sections)}개 항목 원문 발췌"


def _assessment_block(verdict) -> str:
    """지금 이 종목이 어떤 상황인지. 모든 문장이 숫자에서 나온다."""
    if verdict is None:
        return ""
    axes = []
    for axis in verdict.axes:
        evidence = "".join(f"<li>{esc(item)}</li>" for item in axis.evidence)
        axes.append(
            f'<div class="axis a-{esc(axis.level)}">'
            f'<div class="axis-head">{LEVEL_ICON[axis.level]} <b>{esc(axis.name)}</b>'
            f'<span class="tag">{esc(LEVEL_LABEL[axis.level])}</span></div>'
            f'<p class="axis-line">{esc(axis.headline)}</p>'
            f'<ul class="evidence">{evidence}</ul></div>'
        )

    watch = "".join(f"<li>{esc(point)}</li>" for point in verdict.watch_points)
    watch_html = f'<div class="watch"><b>지금 확인할 것</b><ul>{watch}</ul></div>' if watch else ""

    return f"""
<div class="verdict-box v-{esc(verdict.level)}">
  <div class="verdict-head">
    <span class="big">{verdict.icon}</span>
    <div>
      <b>지금 상황: {esc(verdict.label)}</b>
      <p class="sub">{esc(verdict.headline)}</p>
    </div>
  </div>
  <div class="axes">{"".join(axes)}</div>
  {watch_html}
</div>"""


def _numbers_block(m: Metrics) -> str:
    stats = [
        ("시가총액", _money(m.market_cap)),
        ("매출 TTM", _money(m.revenue_ttm)),
        ("순이익 TTM", _money(m.net_income_ttm)),
        ("영업이익 TTM", _money(m.operating_income_ttm)),
        ("영업현금흐름", _money(m.ocf_ttm)),
        ("잉여현금흐름", _money(m.fcf_ttm)),
        ("보유 현금", _money(m.cash)),
        ("총부채", _money(m.total_debt)),
        ("자기자본", _money(m.equity)),
        ("52주 범위",
         f"${m.low_52w:,.2f} ~ ${m.high_52w:,.2f}" if (m.low_52w and m.high_52w) else "-"),
        ("EPS TTM", f"${m.eps_ttm:,.2f}" if m.eps_ttm else "-"),
        ("주식수", f"{m.shares / 1e6:,.0f}M" if m.shares else "-"),
        ("희석", f"{m.share_growth_1y:+.1%}" if m.share_growth_1y is not None else "-"),
        ("기준 분기", m.as_of.isoformat() if m.as_of else "-"),
    ]
    cells = "".join(
        f"<div><dt>{term(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in stats
    )
    return f'<h4>핵심 숫자</h4><dl class="stats">{cells}</dl>'


def _trends_block(m: Metrics) -> str:
    charts = []
    revenue = m.trends.get("revenue") or m.quarterly_revenue
    if len(revenue) >= 2:
        charts.append(_bars("분기 매출", revenue, _money))
    margin = m.trends.get("op_margin")
    if margin and len(margin) >= 2:
        charts.append(_bars("분기 영업이익률", margin, lambda v: f"{v:.1f}%"))
    income = m.trends.get("net_income")
    if income and len(income) >= 2:
        charts.append(_bars("분기 순이익", income, _money))
    shares = m.trends.get("shares")
    if shares and len(shares) >= 2:
        # 오른쪽으로 갈수록 막대가 높아지면 주식이 늘어난 것 = 내 몫이 줄었다
        charts.append(_bars("발행주식수", shares, lambda v: f"{v / 1e6:,.0f}M"))
    if not charts:
        return ""
    return f'<h4>추이 <span class="muted small">최근 8개 분기 · 방향이 중요합니다</span></h4><div class="charts">{"".join(charts)}</div>'


def _bars(title: str, series, fmt) -> str:
    values = [v for _, v in series][-8:]
    labels = [d for d, _ in series][-8:]
    peak = max((abs(v) for v in values), default=0)
    if not peak:
        return ""
    bars = []
    for day, value in zip(labels, values):
        height = max(3, round(abs(value) / peak * 52))
        cls = "bar" if value >= 0 else "bar neg"
        bars.append(
            f'<div class="barwrap" title="{esc(day.isoformat())} · {esc(fmt(value))}">'
            f'<div class="{cls}" style="height:{height}px"></div>'
            f'<span class="barlabel">{esc(str(day.year)[2:])}.{day.month:02d}</span></div>'
        )
    last = fmt(values[-1])
    return (
        f'<div class="chart"><span class="chart-title">{esc(title)} '
        f'<b>{esc(last)}</b></span><div class="bars">{"".join(bars)}</div></div>'
    )


def _checks_block(m: Metrics) -> str:
    state = "흑자" if m.profitable else ("적자" if m.profitable is False else "판단 불가")
    priority = "".join(_check_item(c) for c in m.priority)
    checks = "".join(_check_item(c) for c in m.checks)
    return (
        f'<h4>우선순위 판단 <span class="muted small">가이던스 → 어닝 서프라이즈 → 마진</span></h4>'
        f'<ul class="checks">{priority}</ul>'
        f'<h4>{esc(state)} 기업 체크리스트</h4><ul class="checks">{checks}</ul>'
    )


def _check_item(check) -> str:
    icon = STATUS_ICON.get(check.status, "•")
    return (
        f'<li class="st-{esc(check.status)}"><span class="icon">{icon}</span>'
        f'<b>{esc(check.label)}</b><span class="detail">{esc(check.detail)}</span></li>'
    )


def _report_block(report, korean=None) -> str:
    """분기·연간 보고서에서 뽑아온 회사 본인의 설명. 원문 그대로."""
    if report is None:
        return (
            '<h4>분기보고서 내용</h4>'
            '<p class="muted">아직 읽지 않았습니다. 위 <b>보고서 읽기</b> 버튼을 누르면 '
            '최신 10-Q/10-K 원문에서 사업 설명과 경영진 논의를 가져옵니다.</p>'
        )

    header = (
        f'<p class="sub">출처: {esc(report.form)} · 제출 {esc(report.filing_date)}'
        + (f" · 기준일 {esc(report.period)}" if report.period else "")
        + f' · <a href="{esc(report.url)}" target="_blank" rel="noopener">원문 보기</a></p>'
    )

    if not report.sections:
        return (
            f'<h4>분기보고서 내용</h4>{header}'
            '<p class="muted">이 보고서에서는 표준 항목(Item 1 / MD&A)을 찾지 못했습니다. '
            '원문을 직접 확인해주세요.</p>'
        )

    blocks = []
    if report.company_words:
        items = "".join(_quote_ko(s, korean) for s in report.company_words)
        blocks.append(
            '<details open><summary>회사가 직접 밝힌 내용 '
            f'<span class="muted small">{len(report.company_words)}문장</span></summary>'
            f'<ul class="quotes ko-list">{items}</ul></details>'
        )

    for section in report.sections:
        preview = section.paragraphs[:4]
        body = "".join(_quote_ko(p, korean, "section", tag="div") for p in preview)
        more = ""
        if len(section.paragraphs) > len(preview):
            more = (
                f'<p class="muted small">… 이 항목에는 문단이 {len(section.paragraphs)}개 있습니다. '
                f'전체는 <a href="{esc(report.url)}" target="_blank" rel="noopener">원문</a>에서 보세요.</p>'
            )
        blocks.append(
            f"<details><summary>{esc(section.title)}</summary>{body}{more}</details>"
        )

    return (
        '<h4>분기보고서 내용 <span class="muted small">한글은 자동 요약·번역, 영어가 원문입니다</span></h4>'
        + header
        + "".join(blocks)
    )


def _milestones_block(target) -> str:
    """적자 기업 체크리스트 ⑤. 직접 적어둔 마일스톤을 매번 같이 띄운다."""
    milestones = target.watch.milestones
    if not milestones:
        return ""
    items = "".join(f"<li>{esc(x)}</li>" for x in milestones)
    return (
        '<h4>핵심 마일스톤 <span class="muted small">공시가 뜨면 이 항목부터 대조하세요</span></h4>'
        f"<ul class='bullets'>{items}</ul>"
    )


def _memo_block(target) -> str:
    if not target.watch.note:
        return ""
    return f'<h4>내 메모</h4><p class="quote">{esc(target.watch.note)}</p>'


def _guidance_block(guidance, korean=None) -> str:
    """메모 1순위. 회사가 실적 발표문에 쓴 전망 문장을 그대로 가져온다."""
    if guidance is None:
        return (
            '<h4>가이던스 <span class="muted small">메모 1순위</span></h4>'
            '<p class="muted">아직 읽지 않았습니다. 위 <b>보고서 읽기</b> 버튼을 누르면 '
            '최근 실적 발표(8-K 2.02)의 보도자료에서 전망 문장을 찾아옵니다.</p>'
        )

    header = (
        f'<p class="sub">출처: {esc(guidance.form)} · 제출 {esc(guidance.filing_date)} · '
        f'<a href="{esc(guidance.url)}" target="_blank" rel="noopener">원문 보기</a></p>'
    )

    if not guidance.items:
        body = (
            '<p class="muted">이 발표문에서는 전망 문장을 찾지 못했습니다. '
            '표현 방식이 회사마다 달라 놓칠 수 있으니 원문을 직접 확인해주세요.</p>'
        )
    else:
        rows = []
        for item in guidance.items:
            tags = []
            if item.metric:
                tags.append(f'<span class="tag">{esc(item.metric)}</span>')
            if item.period:
                tags.append(f'<span class="tag">{esc(period_ko(item.period))}</span>')
            value = f'<b class="gv">{esc(item.range_text)}</b>' if item.range_text else ""
            # 회사가 무엇을 얼마로 제시했는지를 먼저 한글로 한 줄
            headline = guidance_line(item.metric, item.period, item.range_text)
            korean_line = f'<b class="ko-line">{esc(headline)}</b>' if headline else ""
            rows.append(
                f'<li class="ko"><div class="g-line">{"".join(tags)} {value}</div>'
                f'{korean_line}'
                + _quote_ko(item.sentence, korean, tag="div", rule=not headline) + "</li>"
            )
        body = f'<ul class="guidance ko-list">{"".join(rows)}</ul>'

    results = ""
    if guidance.results:
        items = "".join(_quote_ko(s, korean) for s in guidance.results)
        results = (
            f'<details><summary>발표문의 실적 설명 {len(guidance.results)}문장</summary>'
            f'<ul class="quotes ko-list">{items}</ul></details>'
        )

    caution = (
        '<p class="hint">⚠️ 가이던스는 회사가 관리할 수 있는 숫자입니다(낮게 부르기·정의 변경 등). '
        '<b>과거에 제시한 가이던스를 실제로 지켰는지</b> 이력과 현금흐름표를 함께 확인하세요.</p>'
    )
    return (
        '<h4>가이던스 <span class="muted small">메모 1순위 · 원문 발췌</span></h4>'
        + header + body + results + caution
    )


def _position_block(target, m: Metrics, krw_rate) -> str:
    """내가 산 가격 대비 지금. 달러와 원화를 나눠서 보여준다."""
    position = build_position(target.watch, m, krw_rate)
    if position is None or position.value is None:
        return ""

    cls = position.direction
    rows = [
        ("매수가", f"${position.buy_price:,.2f} × {position.shares:,.4g}주"),
        ("투자 원금", f"${position.cost:,.2f}"),
        ("현재 평가", f"${position.value:,.2f}"),
    ]
    cells = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in rows)

    sign = "+" if position.profit >= 0 else "−"
    profit = (
        f'<span class="{cls}"><b>{sign}${abs(position.profit):,.2f}</b> '
        f'({position.profit_pct:+.2f}%)</span>'
    )
    won_line = ""
    if position.profit_krw is not None:
        won_line = (
            f' · 원화 <span class="{cls}"><b>{esc(won(position.profit_krw))}</b></span>'
            f' <span class="muted small">(지금 환율 ₩{krw_rate:,.2f} 기준)</span>'
        )

    return (
        '<div class="mine">'
        f'<div class="mine-head">💼 <b>내 보유</b> {profit}{won_line}</div>'
        f'<dl class="stats tight">{cells}</dl></div>'
    )


def _recap_block(recap) -> str:
    """실적 · 컨센서스 · 가이던스 3자 대조."""
    if recap is None or recap.empty:
        return ""

    rows = []
    for line in recap.known:
        gap = ""
        if line.gap_pct is not None:
            cls = "up" if line.gap_pct >= 0 else "down"
            gap = f' <span class="{cls}">({line.gap_pct:+.1f}%)</span>'
        rows.append(
            f"<tr><td>{esc(line.label)}</td>"
            f'<td class="num"><b>{esc(line.actual_text)}</b></td>'
            f'<td class="num">{esc(line.expected_text)}{gap}</td>'
            f"<td>{line.icon} {esc(line.verdict)}</td></tr>"
        )

    period = f' <span class="muted small">기준 분기 {esc(recap.period)}</span>' if recap.period else ""
    source = ""
    if recap.guidance_url:
        source = (
            f'<p class="sub">가이던스 출처: {esc(recap.guidance_date)} · '
            f'<a href="{esc(recap.guidance_url)}" target="_blank" rel="noopener">원문</a></p>'
        )
    return (
        f'<h4>실적 3자 대조{period}</h4>'
        '<div class="scroll"><table class="summary track">'
        "<thead><tr><th>비교</th><th>실제</th><th>기대·약속</th><th>결과</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>{source}"
        '<p class="hint">회사가 약속한 값(가이던스)과 시장이 기대한 값(컨센서스)을 '
        '실제와 나란히 놓은 것입니다. 메모의 1순위와 2순위가 여기서 만납니다.</p>'
    )


def _risk_block(risk, korean=None) -> str:
    """위험 요인이 이번에 바뀌었는지. 회사가 쓴 문장 그대로."""
    if risk is None:
        return (
            '<p class="muted">아직 확인하지 않았습니다. 위 <b>보고서</b> 버튼을 누르면 '
            '이번 10-Q/10-K 의 위험 요인을 직전 보고서와 맞춰봅니다.</p>'
        )

    header = ""
    if risk.current_form:
        header = f'<p class="sub">이번 {esc(risk.current_form)} {esc(risk.current_date)}'
        if risk.previous_form:
            header += f' ↔ 직전 {esc(risk.previous_form)} {esc(risk.previous_date)}'
        if risk.current_url:
            header += f' · <a href="{esc(risk.current_url)}" target="_blank" rel="noopener">원문</a>'
        header += "</p>"

    flags = ""
    if risk.flags:
        items = "".join(
            f'<li><b>{esc(f.label)}</b> — {esc(f.meaning)}'
            + _quote_ko(f.sentence, korean, "risk", tag="div", topic=False) + "</li>"
            for f in risk.flags
        )
        flags = f'<div class="fundwarn"><b>⚠️ 무겁게 볼 표현</b><ul>{items}</ul></div>'

    added = ""
    if risk.added:
        quotes = "".join(_quote_ko(p, korean, "risk") for p in risk.added)
        more = ""
        if risk.added_total > len(risk.added):
            more = f'<p class="muted small">… 새 문단이 모두 {risk.added_total}개 있습니다.</p>'
        # 무거운 표현은 이미 위에 뽑아 놨다. 같은 문장을 두 번 읽히지 않도록 접어둔다.
        open_attr = "" if risk.flags else " open"
        added = (
            f'<details{open_attr}><summary>직전에 없던 위험 문단 {risk.added_total}개 '
            '<span class="muted small">한글 요약 + 원문</span></summary>'
            f'<ul class="quotes ko-list">{quotes}</ul>{more}</details>'
        )
    elif risk.compared:
        added = '<p class="muted">직전 보고서와 견줘 새로 추가된 문단이 없습니다.</p>'
    elif risk.no_material_changes:
        added = (
            '<p class="muted">이 보고서는 위험 요인을 다시 싣지 않고 '
            "'중요한 변화 없음' 이라고만 밝혔습니다. 전체 목록은 최신 10-K 에 있습니다.</p>"
        )
    else:
        added = '<p class="muted">비교할 직전 보고서의 위험 요인을 찾지 못했습니다.</p>'

    removed = ""
    if risk.removed_total:
        removed = (
            f'<p class="sub">직전에 있다가 빠진 문단이 {risk.removed_total}개 있습니다. '
            "위험이 해소됐을 수도, 서술을 합친 것일 수도 있어 판단하지 않습니다.</p>"
        )

    return header + flags + added + removed


def _insider_block(insider) -> str:
    """내부자가 자기 돈으로 샀는지. 보상·세금 거래는 빼고 센다."""
    if insider is None:
        return (
            '<p class="muted">아직 확인하지 않았습니다. 감시 주기마다 자동으로 채워집니다.</p>'
        )

    lines = [f'<p class="line"><b>{esc(insider.summary)}</b></p>']
    if insider.note:
        lines.append(f'<p class="hint">{esc(insider.note)}</p>')

    if insider.trades:
        rows = "".join(
            f"<tr><td>{esc(t.day)}</td>"
            f"<td>{esc(t.person)}<br><span class='muted small'>{esc(t.title)}</span></td>"
            f'<td><span class="{"up" if t.is_buy else "down"}">'
            f'{"매수" if t.is_buy else "매도"}</span></td>'
            f'<td class="num">{t.shares:,.0f}주</td>'
            f'<td class="num">{f"${t.price:,.2f}" if t.price else "-"}</td>'
            f'<td class="num">{esc(_money(t.value))}</td>'
            f'<td><a href="{esc(t.url)}" target="_blank" rel="noopener">원문</a></td></tr>'
            for t in insider.trades[:10]
        )
        lines.append(
            '<div class="scroll"><table class="summary track">'
            "<thead><tr><th>날짜</th><th>사람</th><th>구분</th><th>수량</th>"
            "<th>단가</th><th>금액</th><th></th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )

    lines.append(
        '<p class="hint">P(자기 돈으로 매수)와 S(시장 매도)만 셉니다. '
        'RSU 수령·세금 납부용 반납·옵션 행사는 매매 의사와 무관해 합계에서 뺐습니다.</p>'
    )
    return "".join(lines)


def _track_block(track, korean=None) -> str:
    """메모의 단서 — 가이던스는 관리될 수 있으니 '과거에 지켰는지' 를 본다."""
    if track is None:
        return (
            '<h4>과거 가이던스 이행 <span class="muted small">약속을 지켜온 회사인가</span></h4>'
            '<p class="muted">아직 확인하지 않았습니다. 위 <b>보고서</b> 버튼을 누르면 '
            '과거 실적 발표문에서 제시했던 매출 범위를 찾아 실제 실적과 맞춰봅니다.</p>'
        )

    level_class = {"good": "v-good", "fair": "v-fair", "poor": "v-poor"}.get(track.level, "v-unknown")
    head = (
        f'<h4>과거 가이던스 이행 <span class="muted small">약속을 지켜온 회사인가</span></h4>'
        f'<p class="line {level_class}"><b>{esc(track.summary)}</b></p>'
    )

    if not track.items:
        return head + (
            '<p class="muted">과거 실적 발표문에서 전망 문장을 찾지 못했습니다. '
            '회사가 수치 전망을 내지 않는 경우도 흔합니다.</p>'
        )

    judged = [i for i in track.items if i.verdict != "확인 불가"]
    rows = []
    for item in judged[:8]:
        gap = ""
        if item.gap_pct is not None:
            cls = "up" if item.gap_pct >= 0 else "down"
            gap = f' <span class="{cls}">({item.gap_pct:+.1f}%)</span>'
        period = item.target_end.isoformat() if item.target_end else "-"
        rows.append(
            f"<tr><td>{esc(item.filed)}</td>"
            f"<td>{esc(period)}</td>"
            f'<td class="num">{esc(item.promised_text)}</td>'
            f'<td class="num">{esc(item.actual_text)}{gap}</td>'
            f'<td><b>{item.icon} {esc(item.verdict)}</b></td></tr>'
        )

    table = ""
    if rows:
        table = (
            '<div class="scroll"><table class="summary track">'
            "<thead><tr><th>발표일</th><th>대상 분기</th>"
            f"<th>{term('가이던스')}</th><th>실제 매출</th><th>결과</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )

    quotes = []
    for item in track.items[:6]:
        note = f'<p class="sub">{esc(item.reason)}</p>' if item.reason else ""
        quotes.append(
            f'<li class="ko"><p class="sub">{esc(item.filed)} · '
            f'<a href="{esc(item.url)}" target="_blank" rel="noopener">원문 공시</a></p>'
            + _quote_ko(item.sentence, korean, tag="div") + note + "</li>"
        )
    detail = (
        f'<details><summary>회사가 쓴 문장 {len(track.items)}개 보기</summary>'
        f'<ul class="quotes ko-list">{"".join(quotes)}</ul></details>'
    )

    caution = (
        '<p class="hint">매출 가이던스만 자동으로 맞춰봅니다. 조정 EPS·EBITDA 는 '
        '회사가 정의를 정하는 숫자라 SEC 제출 실적과 바로 비교할 수 없어 판정하지 않습니다.</p>'
    )
    return head + table + detail + caution


def _fund_block(m: Metrics, target) -> str:
    """ETF 화면. 없는 숫자를 만들어 넣지 않고, 확인된 것만 적는다."""
    info = m.fund
    if info is None:
        return ""

    rows = [("성격", info.risk_label)]
    if info.name:
        rows.append(("정식 명칭", info.name))
    if info.kind:
        rows.append(("담는 대상", info.kind))
    if info.leverage:
        rows.append(("배수", f"{info.leverage:g}배" + (" (역방향)" if info.inverse else "")))
    elif info.inverse:
        rows.append(("방향", "인버스 (기초자산과 반대)"))
    if info.daily_reset:
        rows.append(("되맞춤 주기", "매일"))
    if info.sic_label:
        rows.append(("SEC 분류", f"{info.sic_label} (SIC {info.sic})"))
    if info.series_id:
        rows.append(("SEC 시리즈 ID", info.series_id))
    rows.append(("CIK", target.cik))

    cells = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in rows)

    warnings = "".join(f'<li>{esc(w)}</li>' for w in info.warnings)
    warn_block = (
        f'<div class="fundwarn"><b>⚠️ 구조상 알아둘 것</b><ul>{warnings}</ul></div>'
        if warnings else ""
    )
    notes = "".join(f"<li>{esc(n)}</li>" for n in info.notes)
    note_block = f'<ul class="bullets">{notes}</ul>' if notes else ""

    checks = "".join(_check_item(c) for c in m.checks)
    why = (
        '<p class="hint">ETF 는 회사가 아니라 여러 자산을 담아둔 그릇입니다. '
        '매출·ROE·영업이익률 같은 기업 지표가 존재하지 않아 '
        '<b>메모의 5체크 대신 ETF 기준</b>으로 봅니다.</p>'
    )
    return (
        f'<dl class="stats">{cells}</dl>{warn_block}{note_block}'
        f'<h4>ETF 체크리스트</h4><ul class="checks">{checks}</ul>{why}'
    )


def _consensus_block(target, m: Metrics, estimate) -> str:
    """메모 2순위. 자동 수집이 되면 그것을, 안 되면 어디서 찾는지 안내한다."""
    from .estimates import links_for

    lines = ['<h4>어닝 서프라이즈 <span class="muted small">메모 2순위</span></h4>']

    if m.surprise:
        s = m.surprise
        bits = []
        if s.get("eps_surprise_pct") is not None:
            cls = "up" if s["eps_surprise_pct"] >= 0 else "down"
            bits.append(
                f'EPS 실제 <b>{s["actual_eps"]:.2f}</b> vs 예상 {s["consensus_eps"]:.2f} '
                f'<span class="{cls}">({s["eps_surprise_pct"]:+.1f}%)</span>'
            )
        if s.get("rev_surprise_pct") is not None:
            cls = "up" if s["rev_surprise_pct"] >= 0 else "down"
            bits.append(
                f'매출 실제 <b>{_money(s["actual_revenue"])}</b> vs 예상 {_money(s["consensus_revenue"])} '
                f'<span class="{cls}">({s["rev_surprise_pct"]:+.1f}%)</span>'
            )
        lines.append(f'<p class="line">{" · ".join(bits)}</p>')
        lines.append(f'<p class="sub">기준 분기 {esc(s.get("period", "-"))}</p>')

    if estimate and estimate.found:
        detail = []
        if estimate.eps is not None:
            detail.append(f"EPS {estimate.eps:.2f}")
        if estimate.revenue is not None:
            detail.append(f"매출 {_money(estimate.revenue)}")
        if estimate.analysts:
            detail.append(f"애널리스트 {estimate.analysts}명")
        lines.append(
            f'<p class="sub">이번 분기 예상치: {esc(" · ".join(detail))} '
            f'<span class="tag">{esc(estimate.source)}</span></p>'
        )

    if estimate and estimate.history:
        rows = "".join(
            f"<li>{esc(h.get('quarter') or '-')} · 실제 {h.get('actual')} vs 예상 {h.get('estimate')}"
            + (f" ({h['surprise_pct']:+.1%})" if isinstance(h.get("surprise_pct"), float) else "")
            + "</li>"
            for h in estimate.history
        )
        lines.append(f"<details><summary>과거 서프라이즈 이력</summary><ul class='bullets'>{rows}</ul></details>")

    if not m.surprise and not (estimate and estimate.found):
        links = "".join(
            f'<li><a href="{esc(url)}" target="_blank" rel="noopener">{esc(name)}</a> '
            f'<span class="muted">— {esc(hint)}</span></li>'
            for name, url, hint in links_for(target.ticker)
        )
        lines.append(
            '<p class="muted">컨센서스를 자동으로 가져오지 못했습니다. '
            'SEC 공시에는 없는 값이라(증권사가 만드는 숫자) 아래에서 확인해 '
            '<b>직접 입력</b>에 넣어주세요. 한 번 넣으면 실적 발표마다 자동 비교합니다.</p>'
            f'<ul class="bullets">{links}</ul>'
        )
    return "".join(lines)


def _peers_block(m: Metrics, industry=None) -> str:
    if industry and not m.peers:
        note = (
            f'<p class="sub">SEC 업종 분류: {esc(industry.description or industry.sic)} '
            f'(SIC {esc(industry.sic)})</p>'
        )
        if industry.peers:
            return (
                "<h4>동종업계</h4>" + note
                + f'<p class="muted">비교 대상: {esc(", ".join(industry.peers))} — '
                "지표를 새로고침하면 수치가 채워집니다.</p>"
            )
        return "<h4>동종업계</h4>" + note + '<p class="muted">같은 업종에서 티커가 있는 회사를 찾지 못했습니다.</p>'

    if not m.peers:
        return ""
    rows = []
    for ticker, peer in m.peers.items():
        bits = []
        if peer.get("per"):
            bits.append(f"PER {peer['per']:.1f}x")
        if peer.get("psr"):
            bits.append(f"PSR {peer['psr']:.1f}x")
        if peer.get("op_margin") is not None:
            bits.append(f"영업이익률 {_pct(peer['op_margin'])}")
        if peer.get("revenue_growth") is not None:
            bits.append(f"매출성장 {peer['revenue_growth']:+.0%}")
        rows.append(
            f"<li><b>{esc(ticker)}</b> <span class='detail'>{esc(' · '.join(bits) or '데이터 없음')}</span></li>"
        )
    note = ""
    if industry:
        source = "직접 지정" if not industry.peers else f"SEC 업종 자동 탐색 · SIC {industry.sic}"
        note = f'<p class="sub">{esc(industry.description or "")} ({esc(source)})</p>'
    return f"<h4>동종업계 비교</h4>{note}<ul class='checks'>{''.join(rows)}</ul>"


def _filings_for(target, recent) -> str:
    mine = [r for r in recent if r.get("ticker") == target.ticker][:5]
    if not mine:
        return ""
    items = "".join(
        f'<li><span class="when">{esc(r.get("when", ""))}</span> '
        f'<span class="tag">{esc(r.get("form", ""))}</span> '
        f'<a href="{esc(r.get("url", "#"))}" target="_blank" rel="noopener">{esc(r.get("title", ""))}</a></li>'
        for r in mine
    )
    return f"<h4>이 종목 최근 공시</h4><ul class='bullets'>{items}</ul>"


def _inputs_block(target, m: Metrics) -> str:
    """직접 넣어야 정확해지는 값들 (컨센서스·매수가·메모)."""
    watch = target.watch
    eps = watch.consensus_eps if watch.consensus_eps is not None else ""
    revenue = watch.consensus_revenue if watch.consensus_revenue is not None else ""
    memo = watch.note or ""
    buy_price = watch.buy_price if watch.buy_price is not None else ""
    buy_shares = watch.buy_shares if watch.buy_shares is not None else ""

    surprise_hint = ""
    if m.surprise is None:
        surprise_hint = (
            '<p class="hint">컨센서스를 넣어두면 실적 발표 직후 '
            '<b>어닝 서프라이즈(메모 2순위)</b>를 자동으로 계산합니다.</p>'
        )

    return f"""
<details class="inputs"><summary>직접 입력 (컨센서스 · 내 매수가 · 메모)</summary>
  {surprise_hint}
  <form method="post" action="/action" class="inline">
    <input type="hidden" name="action" value="consensus">
    <input type="hidden" name="ticker" value="{esc(target.ticker)}">
    <label>EPS 컨센서스<input type="text" name="eps" value="{esc(eps)}" placeholder="예: 1.01"></label>
    <label>매출 컨센서스<input type="text" name="revenue" value="{esc(revenue)}" placeholder="예: 45000000000"></label>
    <button type="submit">저장</button>
  </form>
  <form method="post" action="/action" class="inline">
    <input type="hidden" name="action" value="position">
    <input type="hidden" name="ticker" value="{esc(target.ticker)}">
    <label>내 매수가($)<input type="text" name="price" value="{esc(buy_price)}" placeholder="예: 48.20"></label>
    <label>수량<input type="text" name="shares" value="{esc(buy_shares)}" placeholder="예: 10"></label>
    <button type="submit">저장</button>
  </form>
  <form method="post" action="/action" class="inline">
    <input type="hidden" name="action" value="memo">
    <input type="hidden" name="ticker" value="{esc(target.ticker)}">
    <label>내 메모<input type="text" name="memo" value="{esc(memo)}"
           placeholder="왜 담았는지, 무엇을 지켜볼지"></label>
    <button type="submit">저장</button>
  </form>
</details>"""


def _sources_block(m: Metrics) -> str:
    """숫자를 어디서 가져왔는지. **원문까지 짚어 준다.**

    '이 값을 어떻게 믿나' 에 대한 답은 하나뿐이다 — 원문을 열어 직접 보는 것.
    그래서 항목 이름과 기간만 적지 않고, 더한 분기를 하나씩 펼쳐서 덧셈을
    눈으로 검산할 수 있게 하고, 그 분기가 실린 SEC 공시로 바로 가게 한다.
    """
    if not m.sources:
        return ""

    rows = []
    for source in m.sources.values():
        head = f'<b>{esc(source.label)}</b>'
        if source.note:
            rows.append(f'<li>{head} <span class="muted">{esc(source.note)}</span></li>')
            continue

        total = f' = <b>{esc(_money(source.total))}</b>' if source.total is not None else ""
        line = (f'{head} <code>{esc(source.concept)}</code> '
                f'<span class="muted">{esc(source.how)}</span>{total}')

        if source.checkable:
            parts = "".join(
                f'<li>{esc(part.when)} · {esc(part.shown)}'
                + (f' <a href="{esc(part.url)}" target="_blank" rel="noopener">'
                   f'{esc(part.form or "공시")} 원문</a>' if part.url else "")
                + '</li>'
                for part in source.parts
            )
            rows.append(
                f'<li>{line}'
                f'<details class="src-parts"><summary>더한 분기 {len(source.parts)}개 보기</summary>'
                f'<ul class="bullets small">{parts}</ul></details></li>'
            )
        else:
            link = (f' <a href="{esc(source.url)}" target="_blank" rel="noopener">공시 원문</a>'
                    if source.url else "")
            rows.append(f'<li>{line}{link}</li>')

    return (
        '<details class="sources"><summary>이 숫자들의 출처 · 원문 대조</summary>'
        f'<ul class="bullets">{"".join(rows)}</ul>'
        '<p class="muted small">모든 재무 수치는 SEC에 제출된 XBRL 원본에서 계산했습니다. '
        '수정 공시가 있으면 가장 나중에 제출된 값을 씁니다.<br>'
        '<b>숫자가 미심쩍으면 원문을 열어 직접 대조해보세요.</b> '
        '한 종목만 맞춰봐도 같은 코드가 만든 나머지를 믿을 근거가 됩니다. '
        '터미널에서 <code>python main.py verify 티커</code> 로 한 번에 볼 수도 있습니다.</p></details>'
    )


def _remove_inline(ticker: str) -> str:
    return (
        ' · <form method="post" action="/action" class="inline-form"'
        ' onsubmit="return confirm(\'감시 목록에서 뺄까요?\')">'
        '<input type="hidden" name="action" value="remove">'
        f'<input type="hidden" name="ticker" value="{esc(ticker)}">'
        '<button type="submit" class="ghost small">감시 목록에서 빼기</button></form>'
    )


# --------------------------------------------------------------------------
# 공시 · 일정 · 사전
# --------------------------------------------------------------------------
def _update_banner(latest) -> str:
    from . import __version__

    if not latest or _as_tuple(latest) <= _as_tuple(__version__):
        return ""
    return f"""
<div class="notice update">
  🆕 <b>새 버전 {esc(latest)}</b> 이 나왔습니다 (지금 {esc(__version__)}).
  <form method="post" action="/action" class="inline-form">
    <input type="hidden" name="action" value="update">
    <button type="submit">지금 업데이트</button>
  </form>
  <span class="muted small">설정과 기록은 그대로 유지됩니다. 갱신 뒤 봇만 재시작하면 됩니다.</span>
</div>"""


def _as_tuple(value) -> tuple:
    return tuple(int(p) if str(p).isdigit() else 0 for p in str(value).split("."))


# --------------------------------------------------------------------------
# 영어 원문에 한글 얹기
#   한글이 위, 영어 원문은 접어서 아래. 원문을 지우지는 않는다.
#   기계 번역은 반드시 '기계 번역' 이라고 밝힌다 — 틀릴 수 있기 때문이다.
# --------------------------------------------------------------------------
def _ko(text: str, table: dict | None = None, kind: str = "sentence"):
    """미리 만들어둔 한글 설명을 찾고, 없으면 규칙만으로 그 자리에서 만든다."""
    if table and text in table:
        return table[text]
    return note_for(text, kind)


def _quote_ko(text: str, table: dict | None = None, kind: str = "sentence",
              tag: str = "li", topic: bool = True, rule: bool = True) -> str:
    """한글 한 줄 + 접어둔 영어 원문.

    topic=False 면 주제 이름표와 설명을 뺀다. 부르는 쪽에서 이미
    같은 말을 해둔 자리(무겁게 볼 표현 등)에서 같은 문장이 두 번 나오지 않게.
    """
    note = _ko(text, table, kind)

    head = ""
    if note.topic and topic:
        head += f'<span class="ko-topic">{esc(note.topic)}</span>'
    if note.line and rule:
        head += f'<b class="ko-line">{esc(note.line)}</b>'
    elif note.machine:
        engine = note.engine or "기계 번역"
        head += (
            f'<span class="ko-line">{esc(note.machine)}</span>'
            f'<span class="ko-mark" title="{esc(engine)} 자동 번역입니다. 틀릴 수 있으니 원문을 함께 보세요">'
            f'{esc(engine)} 번역</span>'
        )
    if note.meaning and topic:
        head += f'<p class="sub">{esc(note.meaning)}</p>'

    if note.line and rule and note.machine:
        head += (
            '<details class="ko-more"><summary>번역문 더 보기</summary>'
            f'<p class="sub">{esc(note.machine)} '
            f'<span class="ko-mark">{esc(note.engine or "기계 번역")} 번역</span></p></details>'
        )

    original = (
        '<details class="ko-src"><summary>영어 원문</summary>'
        f'<p class="quote">{esc(text)}</p></details>'
    )
    if not head and rule:
        # 옮길 말이 없으면 원문을 그대로 펼쳐 둔다 (숨기면 정보가 사라진다).
        # rule=False 는 부르는 쪽이 이미 한글을 써둔 경우라 접어도 된다.
        return f'<{tag} class="ko"><p class="quote">{esc(text)}</p></{tag}>'
    return f'<{tag} class="ko">{head}{original}</{tag}>'


def _parse_when(raw) -> object | None:
    """저장해둔 ISO 시각 문자열을 되살린다. 형식이 어긋나면 조용히 포기한다."""
    if not raw:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _market_strip(snapshot) -> str:
    """환율과 주요 지수. 화면 맨 위 한 줄.

    환율은 전부 1달러 기준이라 읽는 방향이 하나로 통일된다.
    """
    if snapshot is None or snapshot.empty:
        return (
            '<div class="strip loading">💱 환율·지수를 불러오는 중입니다…'
            ' <span class="muted small">1분마다 자동으로 갱신합니다</span></div>'
        )

    cells = []
    for rate in snapshot.rates:
        move = ""
        if rate.change_pct is not None:
            move = f'<span class="{rate.direction}">{rate.change_pct:+.2f}%</span>'
        cells.append(
            f'<div class="q"><span class="q-name">{esc(rate.label)}</span>'
            f'<span class="q-val">{esc(rate.text)}</span>{move}</div>'
        )

    index_cells = []
    for index in snapshot.indexes:
        move = ""
        if index.change_pct is not None:
            move = f'<span class="{index.direction}">{index.change_pct:+.2f}%</span>'
        index_cells.append(
            f'<div class="q" title="{esc(index.note)}"><span class="q-name">{esc(index.label)}</span>'
            f'<span class="q-val">{esc(index.text)}</span>{move}</div>'
        )

    stamp = clock(snapshot.fetched_at)
    fx_group = (
        f'<div class="strip-group"><span class="strip-label">💱 1달러 =</span>{"".join(cells)}</div>'
        if cells else ""
    )
    index_group = (
        f'<div class="strip-group"><span class="strip-label">📈 지수</span>{"".join(index_cells)}</div>'
        if index_cells else ""
    )
    return (
        f'<div class="strip">{fx_group}{index_group}'
        f'<span class="strip-when muted small">{esc(stamp)} 기준</span></div>'
    )


def _news_panel(news) -> str:
    """속보는 작게, 접어서. 진짜 중요한 것만 펼쳐둔다."""
    if not news:
        return """
<details class="newsbox"><summary>📰 속보 <span class="muted small">아직 없음</span></summary>
  <p class="muted small">감시 주기마다 자동으로 확인합니다.
     🚨 급의 사안이 생기면 위 <b>속보 확인</b> 버튼에 숫자가 붙습니다.</p>
</details>"""

    # 속보는 새 것이 위로 와야 한다. 저장 순서가 아니라 기사 시각으로 줄 세운다.
    news = sorted(news, key=lambda n: str(n.get("when") or ""), reverse=True)
    urgent = [n for n in news if int(n.get("severity", 1)) >= 3]
    rest = [n for n in news if int(n.get("severity", 1)) < 3]

    def row(entry):
        severity = int(entry.get("severity", 1))
        icon = {3: "🚨", 2: "🟠", 1: "🟡"}.get(severity, "🟡")
        tickers = "".join(f'<a class="tag" href="#{esc(t)}">{esc(t)}</a>'
                          for t in entry.get("tickers", []))
        if entry.get("macro"):
            tickers = '<span class="tag macro">시장 전체</span>' + tickers
        reasons = " · ".join(entry.get("reasons", []))
        title = esc(entry.get("title", ""))
        url = entry.get("url")
        head = f'<a href="{esc(url)}" target="_blank" rel="noopener">{title}</a>' if url else title

        # 저장된 시각은 UTC/원문 기준이다. 화면에는 한국시간으로 바꿔 보여준다.
        moment = _parse_when(entry.get("when"))
        when = clock(moment) if moment else esc((entry.get("when") or "")[5:16].replace("T", " "))
        elapsed = ago(moment) if moment else ""

        publisher = entry.get("publisher") or entry.get("source") or ""
        tier = int(entry.get("tier") or news_tier(publisher))
        tier_chip = (
            f'<span class="src t{tier}" title="{esc(TIER_NAMES.get(tier, ""))}">{esc(publisher)}</span>'
            if publisher else ""
        )
        return (
            f'<li class="n{severity}"><span class="n-ic">{icon}</span>'
            f'<div><div class="n-t">{head}</div>'
            f'<div class="n-m"><span class="when">{esc(when)}</span>'
            + (f'<span class="fresh">{esc(elapsed)}</span>' if elapsed else "")
            + f'{tier_chip}{tickers}'
            + (f'<span class="n-why">{esc(reasons)}</span>' if reasons else "")
            + "</div></div></li>"
        )

    open_attr = " open" if urgent else ""
    count = f'<span class="badge-count">{len(urgent)}</span>' if urgent else ""
    top = "".join(row(n) for n in urgent[:5])
    more = "".join(row(n) for n in rest[:10])
    more_block = (
        f'<details class="submore"><summary class="small">그 외 {len(rest)}건</summary>'
        f'<ul class="news">{more}</ul></details>' if rest else ""
    )
    return f"""
<details class="newsbox"{open_attr}>
  <summary>📰 속보 {count}<span class="muted small">
    🚨 {len(urgent)}건 · 전체 {len(news)}건</span></summary>
  <ul class="news">{top}</ul>
  {more_block}
</details>"""


def _filings(recent, tickers=None, market: str = markets.US) -> str:
    """최근 공시. **보고 있는 시장 것만** 보여준다.

    한국 화면에 미국 공시가 섞이면 어느 쪽 이야기인지 알 수 없다.
    공시마다 '무엇을 봐야 하는지' 를 함께 적는다 — '유상증자결정' 다섯
    글자만으로는 그게 좋은 일인지 나쁜 일인지 알 수 없기 때문이다.
    """
    # 감시 목록에서 뺀 종목의 공시가 남아 있으면 혼란스럽다. 지금 보는 종목만.
    if tickers is not None:
        allowed = {t.upper() for t in tickers}
        recent = [r for r in recent if str(r.get("ticker", "")).upper() in allowed]
    # 시장 표시가 없는 것은 예전에 쌓인 미국 공시다
    recent = [r for r in recent if (r.get("market") or markets.US) == market]

    where = "SEC EDGAR" if market == markets.US else "금융감독원 DART"
    if not recent:
        rows = ('<p class="muted">감시 중인 종목의 새 공시가 올라오면 여기와 '
                f'텔레그램에 함께 표시됩니다. (출처: {esc(where)})</p>')
    else:
        items = []
        for entry in recent:
            tone = TONE_CLASS.get(entry.get("tone", "plain"), "tone-plain")
            report = entry.get("report") or ""
            original = (f'<div class="f-orig">{esc(report)}</div>'
                        if report and report != entry.get("title") else "")
            why = (f'<div class="f-why">👉 {esc(entry["why"])}</div>'
                   if entry.get("why") else "")
            items.append(
                f'<li class="{tone}">'
                f'<span class="when">{esc(entry.get("when", ""))}</span>'
                f'<span class="tag">{esc(entry.get("form", ""))}</span>'
                f'<b>{esc(entry.get("ticker", ""))}</b> '
                f'<span class="detail">{esc(entry.get("title", ""))}</span> '
                f'<a href="{esc(entry.get("url", "#"))}" target="_blank" rel="noopener">원문</a>'
                f'{original}{why}'
                "</li>"
            )
        rows = f'<ul class="filings">{"".join(items)}</ul>'
    return (f'<section><h2>최근 공시 '
            f'<span class="count">감시 중인 종목만 · {esc(where)}</span></h2>{rows}</section>')


def _picks_head(count: str) -> str:
    return f'<summary class="fold-h"><h2>눈여겨볼 종목 <span class="count">{count}</span></h2></summary>'


def _picks_section(groups, progress, enabled: bool, source: str) -> str:
    """지표가 괜찮아 보이는 회사를 갈래별로.

    **사라는 뜻이 아니다.** 여기서 하는 일은 공시된 재무제표와 주가를 같은
    잣대로 줄 세워, 직접 들여다볼 만한 것을 앞으로 끌어오는 것뿐이다.
    그래서 뽑힌 이유를 숫자와 함께 늘 같이 적고, 확인 못 한 항목도 숨기지
    않는다.

    갈래를 나눈 이유는 **묻는 질문이 다르기 때문이다.** '탄탄한가' 와
    '커지고 있는가' 와 '시장이 사고 있는가' 는 서로 다른 물음이라, 한 줄로
    세우면 답이 섞인다. 갈래마다 무엇으로 봤는지와 그 한계를 함께 적는다.

    처음에는 티커만 보여주고, 눌러야 이유가 펼쳐진다.
    """
    if not enabled:
        return ""

    seen, total = progress
    where = source or "후보 목록을 SEC 에서 받지 못했습니다."
    missing = (
        '<p class="muted small">후보 목록을 SEC 에서 받지 못했습니다. '
        '받을 때까지 감시 목록 안에서만 봅니다 — 대신 쓸 목록을 지어내지 않습니다.</p>'
        if not source else ""
    )
    scope = f"후보 {total}개 중 {seen}개 확인"
    found = {key: picks for key, picks in (groups or {}).items() if picks}

    if not found:
        left = max(0, total - seen)
        more = f" 남은 {left}개를 계속 보는 중입니다." if left else ""
        body = (
            f'<p class="muted small">아직 추천할 만한 종목을 찾지 못했습니다. ({esc(scope)})'
            f'{esc(more)}</p>' if total else ""
        )
        return (
            f'<section><details class="fold" data-keep="picks" open>{_picks_head("지표로 고른 것")}'
            f'<div class="fold-body">{missing}{body}</div></details></section>'
        )

    blocks = []
    for key in (screener.BLUE, screener.GROWTH, screener.MOMENTUM):
        picks = found.get(key)
        if not picks:
            continue
        blocks.append(
            f'<h3 class="pk-group">{esc(screener.CATEGORY_NAME[key])} {len(picks)}개 '
            f'<span class="count">{esc(screener.CATEGORY_HOW[key])}</span></h3>'
            f'<p class="pk-warn">⚠ {_bold(screener.CATEGORY_WARNING[key])}</p>'
            f'<div class="picks">{_pick_cards(picks, key)}</div>'
        )

    return f"""
<section><details class="fold" data-keep="picks" open>
  {_picks_head(f"지표로 고른 것 · {esc(scope)}")}
  <div class="fold-body">
  <p class="hint"><b>사라는 뜻이 아닙니다.</b> 공시된 재무제표와 주가를 같은 잣대로 줄 세워,
     직접 들여다볼 만한 것을 앞으로 끌어온 것입니다.
     <b>티커를 누르면</b> 왜 뽑혔는지가 숫자와 함께 펼쳐집니다.</p>
  {"".join(blocks)}
  {missing}
  <p class="muted small"><b>후보 목록:</b> {esc(where)} — 손으로 적은 목록이 아니라
     SEC 가 공개한 매출 순위에서 만듭니다.<br>
     <b>갈래끼리는 점수를 견주지 않습니다.</b> 묻는 질문이 달라서, 한 줄로 세우면 답이 섞입니다.
     한 회사가 여러 갈래에 들어갈 수 있습니다 — 같은 회사를 다른 질문으로 본 것입니다.<br>
     가이던스·컨센서스 대조는 감시 목록 종목에만 있어서 순위에 넣지 않았습니다.
     있는 경우 <b>[참고]</b> 로 표시만 합니다.<br>
     ETF 는 추천하지 않습니다 — 줄 세우려면 규모나 보수를 알아야 하는데 무료 공개 자료에 그게 없습니다.
     (감시 목록에 넣은 ETF 는 위에서 지금까지대로 다 보여드립니다.)</p>
  </div>
</details></section>"""


def _bold(text: str) -> str:
    """**강조** 만 굵게. 나머지는 그대로 이스케이프한다."""
    parts = esc(text).split("**")
    return "".join(part if i % 2 == 0 else f"<b>{part}</b>" for i, part in enumerate(parts))


def _pick_cards(picks, group: str = "") -> str:
    """한 줄에 하나씩. 접혀 있을 때는 티커와 회사 이름만 보인다.

    '감시 목록에 추가' 는 접힌 채로도 누를 수 있게 접기 바깥에 둔다.
    펼쳐야만 담을 수 있으면 한 번 더 누르게 만드는 셈이다.
    """
    rows = []
    for rank, pick in enumerate(picks, 1):
        reasons = "".join(f"<li>{esc(r)}</li>" for r in pick.reasons[:5])
        cautions = "".join(f"<li>{esc(c)}</li>" for c in pick.cautions[:4])
        caution_block = (
            f'<div class="pk-care"><div class="pk-care-h">확인하고 보세요</div>'
            f'<ul class="plain small">{cautions}</ul></div>'
        ) if cautions else ""

        # 판단에서 뺀 값. 지우지 않는 이유는, 값이 없는 것과 못 미더운 것은
        # 다르기 때문이다. ROE 100% 는 그 자체로 '자기자본이 거의 없다' 는 정보다.
        notes = "".join(f"<li>{esc(n)}</li>" for n in pick.notes[:4])
        note_block = (
            f'<div class="pk-note"><div class="pk-care-h">참고 — 판단에는 넣지 않은 값</div>'
            f'<ul class="plain small">{notes}</ul></div>'
        ) if notes else ""

        if pick.in_watchlist:
            action = '<span class="tag">이미 감시 중</span>'
        else:
            action = (
                f'<form method="post" action="/action" class="pk-add">'
                f'<input type="hidden" name="action" value="add">'
                f'<input type="hidden" name="ticker" value="{esc(pick.ticker)}">'
                f'<button type="submit">＋ 감시 목록에 추가</button></form>'
            )

        rows.append(
            f'<div class="pk">'
            f'<details class="pk-d" data-keep="pick-{esc(group)}-{esc(pick.ticker)}">'
            f'<summary class="pk-sum">'
            f'<span class="pk-rank">{rank}</span>'
            f'<span class="pk-ticker">{esc(pick.ticker)}</span>'
            f'<span class="muted small pk-name">{esc(pick.name)}</span>'
            f'</summary>'
            f'<div class="pk-body">'
            f'<p class="pk-line">{esc(pick.headline)}</p>'
            f'<ul class="plain small pk-why">{reasons}</ul>'
            f'{caution_block}'
            f'{note_block}'
            f'</div></details>'
            f'<div class="pk-act">{action}</div>'
            f'</div>'
        )
    return "".join(rows)


def _macro_section(snapshot) -> str:
    """물가·금리·고용의 지금 값.

    아래 '경제지표 일정' 이 언제 나오는지를 알려준다면, 여기는 지금 얼마인지를
    알려준다. 개별 종목 실적과 무관하게 PER 전체를 눌렀다 푸는 배경이라
    종목 카드 다음, 일정 앞에 둔다.
    """
    if snapshot is None or snapshot.empty:
        return (
            '<section><h2>경제 지표 <span class="count">물가·금리·고용</span></h2>'
            '<p class="muted small">값을 불러오는 중입니다. '
            '받지 못하면 이 자리는 비워 둡니다 — 추정치를 대신 넣지 않습니다.</p></section>'
        )

    cards = []
    for r in snapshot.readings:
        move = ""
        if r.change_text:
            tone = r.tone or "flat"
            arrow = {"up": "▲", "down": "▼"}.get(r.direction, "―")
            move = (f'<span class="mi-move {tone}" title="직전 발표 대비">'
                    f'{arrow} {esc(r.change_text)}</span>')
        note = f'<div class="mi-read">{esc(r.note)}</div>' if r.note else ""
        cards.append(
            f'<div class="mi">'
            f'<div class="mi-top"><span class="mi-name">{term(r.label)}</span>'
            f'<span class="muted small mi-when">{esc(r.when)}</span></div>'
            f'<div class="mi-val">{esc(r.text)}{move}</div>'
            f'{note}'
            f'<p class="muted small">{esc(r.spec.meaning)}</p>'
            f'</div>'
        )

    stamp = clock(snapshot.fetched_at)
    return f"""
<section><h2>경제 지표 <span class="count">물가·금리·고용, 지금 값</span></h2>
  <p class="hint">화살표는 <b>직전 발표 대비</b> 변화입니다.
     <span class="mi-move good">초록</span>은 주식에 유리한 방향,
     <span class="mi-move bad">빨강</span>은 불리한 방향 —
     실업률·고용처럼 방향 해석이 갈리는 값은 <span class="mi-move flat">회색</span>으로 둡니다.</p>
  <div class="macro">{"".join(cards)}</div>
  <p class="muted small">출처 <a href="{FRED_HOME}" target="_blank" rel="noopener">세인트루이스 연준 FRED</a>
     (원자료: 미 노동통계국·상무부·연준) ·
     {esc(stamp)}에 받음 · 6시간마다 갱신</p>
</section>"""


def _schedule(today, market_days, events) -> str:
    holiday_items = "".join(
        f"<li><span class='when'>{esc(kdate(d.day))}</span> {esc(d.name)} "
        f"<span class='tag'>{esc(d.kind)}</span> <span class='muted'>{esc(dday(today, d.day))}</span></li>"
        for d in market_days
        if d.day >= today
    ) or "<li class='muted'>예정된 휴장일 없음</li>"

    event_items = []
    for event in events[:24]:
        mark = {3: "🔴", 2: "🟠", 1: "🟡"}.get(event.importance, "•")
        when = f" {event.time_et} ET" if event.time_et else ""
        est = " <span class='muted'>(추정)</span>" if event.estimated else ""
        event_items.append(
            f"<li><span class='when'>{esc(kdate(event.day))}{esc(when)}</span> "
            f"{mark} {esc(event.name)}{est}</li>"
        )
    events_html = "".join(event_items) or "<li class='muted'>예정된 일정 없음</li>"

    return f"""
<section class="two-col">
  <div><h2>휴장·조기폐장</h2><ul class="plain">{holiday_items}</ul></div>
  <div><h2>경제지표·실적 일정</h2><ul class="plain">{events_html}</ul></div>
</section>"""


# 화면에서 넣을 수 있는 열쇠. 무엇에 쓰이는지와 어디서 받는지를 같이 적는다.
_KEY_FIELDS = (
    ("dart_api_key", "DART 인증키", "한국 종목의 공시·재무제표. 없으면 한국 화면이 비어 있습니다.",
     "https://opendart.fss.or.kr", "opendart.fss.or.kr — 무료, 1분"),
    ("github_token", "GitHub 토큰", "저장소가 비공개일 때 자동 업데이트에 씁니다. 공개면 필요 없습니다.",
     "https://github.com/settings/tokens", "github.com → Settings → Developer settings"),
    ("telegram_token", "텔레그램 봇 토큰", "휴대폰으로 알림을 받을 때 씁니다.",
     "https://t.me/BotFather", "텔레그램에서 @BotFather 에게 /newbot"),
    ("telegram_chat_id", "텔레그램 대화방 번호", "알림을 어느 방으로 보낼지.",
     "https://t.me/userinfobot", "텔레그램에서 @userinfobot 에게 아무 말이나"),
)


def _keys_section(bot) -> str:
    """열쇠 보관함. 폴더를 지우고 새로 받아도 남는 자리에 넣게 한다.

    지금까지는 config.yml 안에 적게 했는데, 그 파일이 program 폴더 안이라
    폴더를 지우면 열쇠도 같이 없어졌다. 지웠다 깔 때마다 열쇠를 다시 찾아
    넣는 일이 반복돼서, 저장 자리를 사용자 폴더로 옮기고 넣는 곳도 화면에 뒀다.
    """
    from . import secrets

    stored = secrets.load()
    sources = getattr(bot.config, "key_sources", {}) or {}

    rows = []
    for name, label, why, url, where_from in _KEY_FIELDS:
        source = sources.get(name) or (str(secrets.path()) if stored.get(name) else "")
        value = stored.get(name, "")
        if source:
            place = "이 화면(폴더 밖)" if source == str(secrets.path()) else source
            shown = (f'<br><span class="muted small nowrap">{esc(secrets.masked(value))}</span>'
                     if value else "")
            state = (f'<span class="up">넣었음</span>'
                     f'<br><span class="muted small">{esc(place)}</span>{shown}')
        else:
            state = '<span class="muted">없음</span>'
        rows.append(
            f'<tr><td><b>{esc(label)}</b><br><span class="muted small">{esc(why)}</span></td>'
            f'<td>{state}</td>'
            f'<td class="muted small"><a href="{esc(url)}" target="_blank" rel="noopener">'
            f'{esc(where_from)}</a></td>'
            '<td><form method="post" action="/action" class="inline">'
            '<input type="hidden" name="action" value="key">'
            f'<input type="hidden" name="name" value="{esc(name)}">'
            '<input type="password" name="value" placeholder="붙여넣기" autocomplete="off">'
            '<button type="submit">저장</button></form></td></tr>'
        )

    have = sum(1 for name, *_ in _KEY_FIELDS if sources.get(name) or stored.get(name))
    return f"""
<section id="keys">
  <details class="fold" data-keep="keys">
    <summary><h2>열쇠 보관함 <span class="count">{have}/{len(_KEY_FIELDS)}개 넣음 · 눌러서 펼치기</span></h2></summary>
  <p class="hint">여기 넣은 열쇠는 <b>프로그램 폴더 바깥</b>에 저장됩니다.
     그래서 폴더를 통째로 지우고 새로 받아도 그대로 남습니다 — 다시 넣지 않아도 됩니다.
     <b>{esc(str(secrets.path()))}</b></p>
  <div class="scroll">
    <table class="summary translate">
      <thead><tr><th>열쇠</th><th>상태</th><th>어디서 받나</th><th>넣기</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
  <p class="hint">열쇠는 이 컴퓨터에만 저장되고 어디로도 보내지 않습니다.
     화면에는 길이와 앞뒤 두 글자만 보여줍니다 — 화면을 캡처해도 값은 새지 않습니다.
     빈칸으로 저장하면 지워집니다.
     config.yml 에 적어둔 값이 있으면 그쪽이 먼저입니다.</p>
  </details>
</section>"""


def _translate_section(bot) -> str:
    """번역 설정. 열쇠를 화면에서 붙여넣게 한다 (config 파일을 안 열어도 되게)."""
    try:
        translator = bot.translator
        settings = bot.translate_settings()
        ready = translator.available()
    except Exception:
        return ""

    from .translate import PROVIDERS

    if not bool(settings.get("enabled", True)):
        now_using = "번역 꺼짐 — 규칙으로 옮긴 한글만 나옵니다"
    elif ready:
        from .translate import PROVIDER_BY_KEY

        now_using = "지금 쓰는 번역기: " + PROVIDER_BY_KEY[ready[0]].label
        if len(ready) > 1:
            backups = " → ".join(PROVIDER_BY_KEY[k].label for k in ready[1:])
            now_using += f" (안 되면 {backups} 순서로)"
    else:
        now_using = "쓸 수 있는 번역기가 없습니다"

    rows = []
    for provider in PROVIDERS:
        if not provider.needs_key:
            continue
        field = {"deepl": "deepl_key", "azure": "azure_key",
                 "papago": "papago_id_key", "google_cloud": "google_cloud_key"}[provider.key]
        has_key = bool(str(settings.get(field, "")).strip()) or provider.key in ready
        state = ('<span class="up">열쇠 있음</span>' if has_key
                 else '<span class="muted">열쇠 없음</span>')
        rows.append(
            f'<tr><td><b>{esc(provider.label)}</b></td><td>{state}</td>'
            f'<td class="muted small">{esc(provider.note)}</td>'
            '<td><form method="post" action="/action" class="inline">'
            '<input type="hidden" name="action" value="translator">'
            f'<input type="hidden" name="provider" value="{esc(provider.key)}">'
            '<input type="password" name="key" placeholder="열쇠 붙여넣기" autocomplete="off">'
            "<button type=\"submit\">저장</button></form></td></tr>"
        )

    return f"""
<section id="translate">
  <details class="fold">
    <summary><h2>번역 설정 <span class="count">{esc(now_using)} · 눌러서 펼치기</span></h2></summary>
  <p class="hint">영어 공시를 한글로 옮기는 데 쓰는 번역기입니다.
     <b>아무것도 안 하셔도 됩니다</b> — 열쇠 없이 쓰는 무료 번역이 기본으로 켜져 있습니다.
     더 정확한 번역을 원하시면 아래에서 열쇠를 하나 넣으시면 됩니다.
     넣어둔 것이 실패하면 자동으로 다음 번역기로 넘어갑니다.</p>
  <div class="scroll">
    <table class="summary translate">
      <thead><tr><th>번역기</th><th>상태</th><th>어디서 받나</th><th>열쇠 넣기</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
  <form method="post" action="/action" class="inline">
    <input type="hidden" name="action" value="translate_test">
    <button type="submit">🧪 번역 시험</button>
  </form>
  <p class="hint">시험을 누르면 문장 하나를 실제로 번역해보고 결과를 위에 띄웁니다.
     열쇠는 이 컴퓨터의 설정 파일에만 저장되고 어디로도 보내지 않습니다.</p>
  </details>
</section>"""


def _glossary_section() -> str:
    blocks = []
    for group, terms in groups().items():
        items = []
        for entry in terms:
            rows = [f'<p class="g-short">{esc(entry.short)}</p>']
            if entry.formula:
                rows.append(f'<p class="g-row"><span class="g-key">계산</span> <code>{esc(entry.formula)}</code></p>')
            if entry.how_to_read:
                rows.append(f'<p class="g-row"><span class="g-key">읽는 법</span> {esc(entry.how_to_read)}</p>')
            if entry.caution:
                rows.append(f'<p class="g-row caution"><span class="g-key">주의</span> {esc(entry.caution)}</p>')
            if entry.source:
                rows.append(f'<p class="g-row"><span class="g-key">출처</span> {esc(entry.source)}</p>')
            items.append(
                f'<div class="g-item" id="term-{esc(entry.key)}">'
                f'<h4>{esc(entry.name)}</h4>{"".join(rows)}</div>'
            )
        # 묶음마다 따로 접는다. 필요한 갈래만 펼쳐 보면 된다.
        blocks.append(
            f'<details class="g-group"><summary>{esc(group)} '
            f'<span class="muted small">{len(terms)}개</span></summary>'
            f'<div class="g-items">{"".join(items)}</div></details>'
        )

    total = sum(len(terms) for terms in groups().values())
    return f"""
<section id="glossary">
  <details class="fold">
    <summary><h2>용어 사전 <span class="count">{total}개 · 눌러서 펼치기</span></h2></summary>
    <p class="hint">화면에 나오는 지표가 무엇이고, 어떻게 계산했고,
       무엇을 조심해야 하는지 모아뒀습니다.
       표 항목 이름의 <sup>?</sup> 를 누르면 그 용어로 바로 옵니다.</p>
    <div class="glossary">{"".join(blocks)}</div>
  </details>
</section>"""


def _footer(warning) -> str:
    from . import __version__

    warn = f'<p class="warn">⚠️ {esc(warning)}</p>' if warning else ""
    return f"""
<footer>
  {warn}
  <p class="muted">버전 {esc(__version__)} · 재무 수치는 SEC EDGAR·XBRL 원본에서 계산,
     보고서 본문은 원문 발췌, 주가는 Stooq 또는 Yahoo Finance 종가(실시간 아님).</p>
  <p class="muted">이 화면은 정보를 모아 보여줄 뿐 매매 신호가 아닙니다.
     투자 판단과 그 결과의 책임은 본인에게 있습니다.</p>
  <p class="muted">이 화면은 내 컴퓨터에서만 열립니다 (127.0.0.1).
     브라우저를 닫아도 감시는 창 없이 뒤에서 계속 돕니다 — 멈추려면 '끄기' 를 실행하세요.</p>
</footer>"""


def _loading_body() -> str:
    return """
<header><div><h1>📈 관심 종목 감시</h1>
<p class="sub">종목 정보를 불러오는 중입니다… 잠시만 기다려주세요.</p></div></header>
<!--NOTICE-->
<div class="notice busy">⏳ SEC에서 공시와 재무 데이터를 받아오는 중입니다.
처음엔 종목당 10초쯤 걸리고, 끝나면 이 화면이 자동으로 채워집니다.</div>"""


# --------------------------------------------------------------------------
# HTTP 서버
# --------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    dashboard: Dashboard = None

    def log_message(self, fmt, *args):
        log.debug("dashboard %s", fmt % args)

    def _guard(self) -> bool:
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self.send_error(403, "localhost only")
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            wanted = (parse_qs(parsed.query).get("m") or [markets.US])[0]
            market = wanted if wanted in (markets.US, markets.KR) else markets.US
            self._html(self.dashboard.render(market))
        elif path == "/healthz":            # 살아 있는지 확인용 (시작 스크립트가 쓴다)
            self._text("ok")
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._guard():
            return
        if urlparse(self.path).path != "/action":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        params = parse_qs(self.rfile.read(length).decode("utf-8"))
        action = (params.get("action") or [""])[0]

        if action == "quit":
            # 끝난 뒤에는 열어볼 화면이 없다. 되돌려보내지 말고 여기서 끝낸다.
            self.dashboard.run_action(action, params)
            self._html(self.dashboard.render_goodbye())
            return

        message = self.dashboard.run_action(action, params)
        if not self.dashboard.busy:
            self.dashboard.notice = message
        # 보던 시장으로 되돌려 보낸다. 무조건 '/' 로 보내면 한국 화면에서
        # 버튼 한 번 누를 때마다 미국 화면으로 튕긴다.
        back = (params.get("m") or [""])[0]
        self.send_response(303)
        self.send_header("Location", f"/?m={back}" if back in (markets.US, markets.KR) else "/")
        self.end_headers()

    def _html(self, text: str):
        self._respond(text.encode("utf-8"), "text/html; charset=utf-8")

    def _text(self, text: str):
        self._respond(text.encode("utf-8"), "text/plain; charset=utf-8")

    def _respond(self, payload: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def start_dashboard(bot, port: int = 8765, open_browser: bool = True, preload: bool = True):
    dashboard = Dashboard(bot)
    handler = type("Handler", (_Handler,), {"dashboard": dashboard})

    server = None
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError as exc:
            log.debug("포트 %s 사용 중: %s", candidate, exc)
    if server is None:
        raise OSError(f"{port}~{port + 9} 포트가 모두 사용 중입니다.")

    server.dashboard = dashboard
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    log.info("대시보드: %s", url)
    if preload:
        dashboard.load_initial()
    if open_browser:
        threading.Timer(1.0, lambda: _open(url)).start()
    return server


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:
        log.warning("브라우저를 열지 못했습니다(%s). 직접 %s 에 접속하세요.", exc, url)


# 테마 전환. 화면이 그려지기 전에 적용해야 새로고침할 때마다 흰 화면이 번쩍이지 않는다.
# (이 페이지는 90초마다 자동 새로고침되므로 특히 중요하다)
_THEME_SCRIPT = """<script>
// 화면 밝기. 기본은 '시간' — 낮에는 밝게, 저녁부터 어둡게 알아서 바뀐다.
// 이 페이지는 90초마다 새로고침되므로, 그려지기 전에 정해야 흰 화면이 번쩍이지 않는다.
var DAY_START = 7;    // 07:00 부터 밝게
var DAY_END = 19;     // 19:00 부터 어둡게
var THEME_ORDER = ['auto', 'system', 'light', 'dark'];
var THEME_LABEL = {
  auto: '🕗 시간에 맞춰', system: '🌗 시스템', light: '☀️ 밝게', dark: '🌙 어둡게'
};

function themeByClock() {
  var hour = new Date().getHours();
  return (hour >= DAY_START && hour < DAY_END) ? 'light' : 'dark';
}
function currentTheme() {
  try { return localStorage.getItem('theme') || 'auto'; } catch (e) { return 'auto'; }
}
function applyTheme(choice) {
  var root = document.documentElement;
  if (choice === 'auto') { root.setAttribute('data-theme', themeByClock()); }
  else if (choice === 'system') { root.removeAttribute('data-theme'); }
  else { root.setAttribute('data-theme', choice); }
}
applyTheme(currentTheme());          // 첫 그림 전에 바로 적용

function paintThemeButton() {
  var button = document.getElementById('themebtn');
  if (!button) { return; }
  var choice = currentTheme();
  var text = THEME_LABEL[choice];
  if (choice === 'auto') {
    text += themeByClock() === 'dark' ? ' · 지금 어둡게' : ' · 지금 밝게';
  }
  button.textContent = text;
  button.title = '화면 밝기: ' + text + ' (눌러서 변경)\\n'
    + '시간에 맞춰 = ' + DAY_START + '시~' + DAY_END + '시 밝게, 그 밖은 어둡게';
}
function cycleTheme() {
  var next = THEME_ORDER[(THEME_ORDER.indexOf(currentTheme()) + 1) % THEME_ORDER.length];
  try { localStorage.setItem('theme', next); } catch (e) {}
  applyTheme(next);
  paintThemeButton();
}
// 자동일 때는 페이지를 열어둔 채 저녁이 되어도 알아서 넘어가야 한다
setInterval(function () {
  if (currentTheme() === 'auto') { applyTheme('auto'); paintThemeButton(); }
}, 60000);
document.addEventListener('DOMContentLoaded', paintThemeButton);

// 접었다 편 것을 기억한다.
//
// 이 화면은 90초마다 스스로 새로고침된다. 그때 펼쳐둔 것이 도로 닫히면
// 읽던 자리를 잃는다. 'data-keep' 이 붙은 것만 기억하므로, 기억하지
// 말아야 할 것까지 따라 열리는 일은 없다.
function foldKey(el) { return 'fold:' + el.getAttribute('data-keep'); }

function rememberFold(event) {
  try { localStorage.setItem(foldKey(event.target), event.target.open ? '1' : '0'); }
  catch (e) {}
}

function restoreFolds() {
  var items = document.querySelectorAll('details[data-keep]');
  for (var i = 0; i < items.length; i++) {
    var el = items[i], saved = null;
    try { saved = localStorage.getItem(foldKey(el)); } catch (e) {}
    if (saved === '1') { el.open = true; }
    else if (saved === '0') { el.open = false; }
    el.addEventListener('toggle', rememberFold);
  }
}
document.addEventListener('DOMContentLoaded', restoreFolds);
</script>"""

_PAGE = """<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>관심 종목 감시</title>
<!--THEME-->
<style>
/* 색은 여기 한 곳에서만 정한다.
   기본값 = 밝은 화면. 시스템이 어두우면 자동으로, 사람이 고르면 그 선택이 이긴다. */
:root {{
  color-scheme: light;
  --bg:#f6f7f9; --fg:#1b1d21; --muted:#6b7280; --card:#ffffff; --line:#e5e7eb;
  --accent:#2563eb; --good:#15803d; --bad:#b91c1c; --alert:#b45309; --zebra:#fafbfc;
  --quote:#f3f4f6;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --bg:#14161a; --fg:#e8eaed; --muted:#9aa1ab; --card:#1c1f24; --line:#2b2f36;
    --accent:#60a5fa; --good:#4ade80; --bad:#f87171; --alert:#fbbf24; --zebra:#191c21;
    --quote:#22262c;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --bg:#14161a; --fg:#e8eaed; --muted:#9aa1ab; --card:#1c1f24; --line:#2b2f36;
  --accent:#60a5fa; --good:#4ade80; --bad:#f87171; --alert:#fbbf24; --zebra:#191c21;
  --quote:#22262c;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; padding:24px; background:var(--bg); color:var(--fg); max-width:1600px;
  margin-inline:auto;
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",
              "Noto Sans KR",Segoe UI,sans-serif; line-height:1.6;
}}
h1 {{ font-size:1.5rem; margin:0 0 4px; }}
h2 {{ font-size:1.1rem; margin:34px 0 12px; padding-bottom:6px; border-bottom:2px solid var(--line); }}
h3 {{ font-size:1.25rem; margin:0; }}
h4 {{ font-size:.85rem; margin:18px 0 8px; color:var(--muted); font-weight:700; }}
p {{ margin:3px 0; }}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
sup {{ font-size:.65em; color:var(--accent); margin-left:1px; }}
.term {{ color:inherit; border-bottom:1px dotted var(--muted); }}
.term:hover {{ color:var(--accent); text-decoration:none; }}
.sub {{ color:var(--muted); font-size:.85rem; }}
.hint {{ color:var(--muted); font-size:.8rem; margin-bottom:10px; }}
.muted {{ color:var(--muted); }}
.small {{ font-size:.75rem; }}
.count {{ color:var(--muted); font-weight:400; font-size:.85rem; }}
.up {{ color:var(--good); }} .down {{ color:var(--bad); }} .warnmark {{ color:var(--alert); }}
.warn {{ color:var(--alert); font-size:.85rem; }}
header {{ display:flex; flex-wrap:wrap; gap:16px; justify-content:space-between; align-items:flex-start; }}
.actions {{ display:flex; flex-wrap:wrap; gap:8px; }}
button {{ font:inherit; padding:7px 12px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; white-space:nowrap; }}
button:hover {{ border-color:var(--accent); color:var(--accent); }}
button.ghost {{ border:none; background:none; color:var(--muted); padding:2px 6px; }}
.badge {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:.78rem; white-space:nowrap; }}
.badge.open {{ background:rgba(21,128,61,.14); color:var(--good); }}
.badge.closed {{ background:rgba(185,28,28,.14); color:var(--bad); }}
.notice {{ margin:16px 0; padding:10px 14px; border-radius:8px; background:var(--card); border:1px solid var(--line); }}
.notice.busy {{ border-color:var(--alert); color:var(--alert); }}
.notice.bad {{ border-color:var(--bad); color:var(--bad); }}
.notice.update {{ border-color:var(--accent); display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.inline-form {{ display:inline; }}
/* 속보 패널 — 작게, 접어서 */
.right-col {{ display:flex; flex-direction:column; gap:10px; align-items:stretch;
  flex:1 1 560px; max-width:680px; }}
.right-col .actions {{ justify-content:flex-end; }}
.newsbox {{ border:1px solid var(--line); border-radius:12px; background:var(--card);
  padding:9px 13px; font-size:.9rem; }}
.newsbox > summary {{ font-weight:700; color:var(--fg); display:flex; gap:8px; align-items:center; }}
.newsbox > summary .muted {{ font-weight:400; margin-left:auto; }}
.badge-count {{ background:var(--bad); color:#fff; border-radius:999px; padding:0 7px;
  font-size:.72rem; font-weight:700; }}
ul.news {{ list-style:none; padding:0; margin:8px 0 0; }}
ul.news li {{ display:flex; gap:8px; padding:8px 0; border-top:1px solid var(--line); }}
ul.news li:first-child {{ border-top:none; }}
.n-ic {{ font-size:.9rem; line-height:1.5; }}
.n-t {{ font-size:.87rem; font-weight:600; line-height:1.45; }}
.n-t a {{ color:var(--fg); }}
.n-t a:hover {{ color:var(--accent); }}
.n-m {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-top:3px; font-size:.72rem; }}
.n-why {{ color:var(--alert); }}
.tag.macro {{ background:rgba(185,28,28,.12); color:var(--bad); border-color:transparent; }}
.submore {{ margin:6px 0 0; }}
/* 기사 시각과 매체 신뢰도 */
.n-m .when {{ color:var(--muted); font-variant-numeric:tabular-nums; }}
.fresh {{ color:var(--accent); font-weight:700; }}
.src {{ padding:1px 7px; border-radius:999px; border:1px solid var(--line); font-size:.7rem; }}
.src.t3 {{ background:rgba(21,128,61,.13); color:var(--good); border-color:transparent; font-weight:700; }}
.src.t2 {{ background:var(--bg); color:var(--muted); }}
.src.t1 {{ background:transparent; color:var(--muted); font-style:italic; }}

/* 환율·지수 한 줄 */
.strip {{ display:flex; flex-wrap:wrap; gap:10px 22px; align-items:center;
  background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:9px 14px; margin-top:14px; font-size:.85rem; }}
.strip.loading {{ color:var(--muted); }}
.strip-group {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
.strip-label {{ font-size:.74rem; color:var(--muted); font-weight:700; }}
.strip .q {{ display:flex; gap:5px; align-items:baseline; }}
.q-name {{ color:var(--muted); font-size:.76rem; }}
.q-val {{ font-weight:700; font-variant-numeric:tabular-nums; }}
.strip-when {{ margin-left:auto; }}

/* ETF */
.tag.etf {{ background:rgba(37,99,235,.12); color:var(--accent); border-color:transparent; font-weight:700; }}
.tag.etf.risky {{ background:rgba(180,83,9,.15); color:var(--alert); }}
.fundwarn {{ border:1px solid var(--alert); border-radius:10px; padding:10px 14px;
  margin:10px 0; background:rgba(180,83,9,.07); font-size:.85rem; }}
.fundwarn ul {{ margin:6px 0 0; padding-left:18px; }}
table.track td {{ font-variant-numeric:tabular-nums; }}
td.etfnote {{ text-align:left !important; white-space:normal; font-size:.8rem; }}
table.translate td {{ text-align:left; white-space:normal; }}
table.translate input[type=password] {{ width:180px; }}

/* 카드 안의 접이식 구획 — 접힌 줄에도 결론이 보인다 */
details.grp {{ border:1px solid var(--line); border-radius:10px; margin:10px 0;
  background:var(--bg); }}
details.grp > summary {{ padding:10px 14px; cursor:pointer; list-style:none;
  display:flex; gap:8px; align-items:center; flex-wrap:wrap; font-size:.92rem; }}
details.grp > summary::-webkit-details-marker {{ display:none; }}
details.grp > summary::after {{ content:"▸"; color:var(--muted); margin-left:auto; font-size:.8rem; }}
details.grp[open] > summary::after {{ content:"▾"; }}
details.grp > summary:hover {{ background:var(--zebra); }}
details.grp[open] > summary {{ border-bottom:1px solid var(--line); }}
.grp-body {{ padding:2px 14px 14px; }}
.grp-body > h4:first-child {{ margin-top:12px; }}
.grp-note {{ color:var(--muted); font-size:.8rem; font-weight:400; }}
.dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; flex:none;
  background:var(--muted); }}
.dot.d-good {{ background:var(--good); }}
.dot.d-fair {{ background:var(--alert); }}
.dot.d-poor {{ background:var(--bad); }}
.dot.d-unknown {{ background:var(--line); border:1px solid var(--muted); }}

/* 내 보유 */
.mine {{ border:1px solid var(--accent); border-radius:10px; padding:10px 14px;
  margin:10px 0; background:color-mix(in srgb, var(--accent) 7%, transparent); }}
.mine-head {{ font-size:.95rem; margin-bottom:6px; }}
dl.stats.tight div {{ padding:6px 10px; }}

/* 한글 요약 + 영어 원문 */
ul.ko-list {{ list-style:none; padding-left:0; }}
li.ko, div.ko {{ padding:9px 0; border-top:1px solid var(--line); }}
ul.ko-list > li.ko:first-child {{ border-top:none; }}
.ko-line {{ font-size:.92rem; line-height:1.55; }}
.ko-topic {{ display:inline-block; font-size:.72rem; font-weight:700; color:var(--accent);
  background:var(--bg); border:1px solid var(--line); border-radius:999px;
  padding:1px 8px; margin-right:6px; }}
.ko-mark {{ font-size:.68rem; color:var(--muted); border:1px solid var(--line);
  border-radius:4px; padding:0 5px; margin-left:6px; white-space:nowrap; }}
.ko-src, .ko-more {{ margin-top:5px; }}
.ko-src > summary, .ko-more > summary {{ font-size:.74rem; color:var(--muted); cursor:pointer; }}
.ko-src > summary:hover, .ko-more > summary:hover {{ color:var(--accent); }}
.ko-src .quote {{ margin-top:5px; font-size:.82rem; }}

/* 화면 밝기 버튼 */
button.theme {{ border:1px solid var(--line); border-radius:8px; padding:6px 10px;
  background:var(--card); font-size:.82rem; }}
.left-col {{ flex:1 1 460px; min-width:0; }}
.strip {{ margin-top:12px; }}

/* 종목 카드 — 접이식 */
details.stock {{ padding:0; }}
details.stock > summary {{ padding:14px 18px; list-style:none; cursor:pointer;
  display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
details.stock > summary::-webkit-details-marker {{ display:none; }}
details.stock > summary::before {{ content:"▸"; color:var(--muted); font-size:.8rem; }}
details.stock[open] > summary::before {{ content:"▾"; }}
details.stock[open] > summary {{ border-bottom:1px solid var(--line); }}
details.stock > summary:hover {{ background:var(--zebra); }}
.card-body {{ padding:4px 18px 18px; }}
.cname {{ font-weight:400; }}
button.ghost.small {{ font-size:.72rem; padding:0; text-decoration:underline; }}

.scroll {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; background:var(--card); }}
table.summary {{ border-collapse:collapse; width:100%; font-size:.86rem; white-space:nowrap; }}
table.summary th {{ text-align:right; padding:10px 12px; font-size:.74rem; color:var(--muted);
  font-weight:600; border-bottom:1px solid var(--line); background:var(--card); }}
table.summary th:first-child {{ text-align:left; }}
table.summary td {{ padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:middle; }}
table.summary td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
table.summary tbody tr:nth-child(even) {{ background:var(--zebra); }}
table.summary tbody tr:last-child td {{ border-bottom:none; }}
.verdict {{ padding:2px 8px; border-radius:999px; font-size:.78rem; white-space:nowrap; }}
.v-good {{ background:rgba(21,128,61,.14); color:var(--good); }}
.v-fair {{ background:rgba(180,83,9,.14); color:var(--alert); }}
.v-poor {{ background:rgba(185,28,28,.14); color:var(--bad); }}
.v-unknown {{ background:var(--zebra); color:var(--muted); }}

.addform {{ display:flex; gap:8px; margin-top:14px; }}
.addform input, .inline input {{ font:inherit; padding:8px 12px; border-radius:8px;
  border:1px solid var(--line); background:var(--card); color:var(--fg); }}
.addform input {{ flex:0 1 320px; }}
/* 열쇠 칸은 좁게. 넓으면 '저장' 단추가 아래로 밀려 한 줄에 안 들어온다. */
#keys input[type=password] {{ width:9rem; }}
#keys form.inline {{ display:flex; gap:6px; align-items:center; }}
#keys .nowrap {{ white-space:nowrap; }}
.stack {{ display:flex; flex-direction:column; gap:18px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px;
  scroll-margin-top:16px; }}
.card.wide.stock {{ padding:0; }}
.card-head {{ display:flex; justify-content:space-between; align-items:center; gap:10px; }}
.card-head .price {{ margin-left:auto; font-size:1.05rem; font-weight:600;
  font-variant-numeric:tabular-nums; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
.card-head .ext {{ font-size:.78rem; font-weight:500; color:var(--muted); }}
.guidance {{ list-style:none; padding:0; margin:6px 0; }}
.guidance li {{ margin:10px 0; }}
.g-line {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:4px; }}
.gv {{ font-size:.95rem; color:var(--accent); font-variant-numeric:tabular-nums; }}
.line {{ font-size:.88rem; margin:12px 0; }}
.group {{ margin-top:20px; padding-top:14px; border-top:1px solid var(--line); }}
.group-title {{ font-size:.8rem; font-weight:700; color:var(--muted); letter-spacing:.02em;
  margin-bottom:10px; }}
.group h4:first-of-type {{ margin-top:0; }}

.verdict-box {{ border:1px solid var(--line); border-left:4px solid var(--muted);
  border-radius:10px; padding:14px 16px; margin:14px 0; background:var(--zebra); }}
.verdict-box.v-good {{ border-left-color:var(--good); }}
.verdict-box.v-fair {{ border-left-color:var(--alert); }}
.verdict-box.v-poor {{ border-left-color:var(--bad); }}
.verdict-head {{ display:flex; gap:12px; align-items:flex-start; }}
.verdict-head .big {{ font-size:1.6rem; line-height:1; }}
.axes {{ display:grid; gap:10px; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); margin-top:14px; }}
.axis {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }}
.axis-head {{ display:flex; align-items:center; gap:6px; font-size:.9rem; }}
.axis-head .tag {{ margin-left:auto; }}
.axis-line {{ font-size:.82rem; margin:6px 0; }}
.evidence {{ list-style:none; padding:0; margin:0; font-size:.76rem; color:var(--muted); }}
.evidence li {{ padding:1px 0; font-variant-numeric:tabular-nums; }}
.watch {{ margin-top:14px; font-size:.83rem; }}
.watch ul {{ margin:6px 0 0; padding-left:18px; color:var(--muted); }}
.watch li {{ padding:2px 0; }}

.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(116px,1fr)); gap:8px; margin:0; }}
.stats div {{ background:var(--bg); border-radius:8px; padding:7px 10px; }}
.stats dt {{ font-size:.7rem; color:var(--muted); }}
.stats dd {{ margin:0; font-size:.92rem; font-weight:600; font-variant-numeric:tabular-nums; }}
.charts {{ display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }}
.chart-title {{ font-size:.72rem; color:var(--muted); }}
.bars {{ display:flex; align-items:flex-end; gap:5px; height:70px; margin-top:6px; }}
.barwrap {{ flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%; }}
.bar {{ width:100%; background:var(--accent); border-radius:3px 3px 0 0; opacity:.75; }}
.bar.neg {{ background:var(--bad); }}
.barlabel {{ font-size:.6rem; color:var(--muted); margin-top:3px; }}

.checks {{ list-style:none; padding:0; margin:0; font-size:.85rem; }}
.checks li {{ display:flex; gap:6px; padding:7px 0; border-top:1px solid var(--line); flex-wrap:wrap; }}
.checks li:first-child {{ border-top:none; }}
.checks .detail {{ color:var(--muted); flex:1 1 100%; font-size:.8rem; }}
.bullets {{ margin:0; padding-left:18px; font-size:.83rem; color:var(--muted); }}
.bullets li {{ padding:2px 0; }}

details {{ margin:10px 0; }}
summary {{ cursor:pointer; font-size:.85rem; color:var(--accent); padding:4px 0; }}
summary:hover {{ text-decoration:underline; }}
.quote {{ background:var(--quote); border-radius:6px; padding:9px 12px; margin:6px 0;
  font-size:.82rem; line-height:1.65; }}
.quotes {{ list-style:none; padding:0; margin:6px 0; }}
.quotes li {{ background:var(--quote); border-radius:6px; padding:8px 11px; margin:5px 0; font-size:.82rem; }}
.inputs .inline {{ display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end; margin:8px 0; }}
.inputs label {{ display:flex; flex-direction:column; font-size:.75rem; color:var(--muted); gap:3px; }}
.inputs input {{ min-width:200px; }}

ul.filings, ul.plain {{ list-style:none; padding:0; margin:0; }}
ul.filings li, ul.plain li {{ padding:8px 10px; border-bottom:1px solid var(--line); font-size:.88rem;
  display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; }}
ul.filings li.tone-alert {{ border-left:3px solid var(--alert); }}
ul.filings li.tone-good {{ border-left:3px solid var(--good); }}
ul.filings li.tone-bad {{ border-left:3px solid var(--bad); }}
.when {{ color:var(--muted); font-variant-numeric:tabular-nums; font-size:.8rem; min-width:118px; }}
.tag {{ font-size:.7rem; padding:1px 7px; border-radius:999px; background:var(--bg); border:1px solid var(--line); }}
.detail {{ color:var(--muted); }}
.two-col {{ display:grid; gap:24px; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }}

/* 종료 안내 — 가운데 카드 하나만 */
.gate {{ max-width:420px; margin:9vh auto; background:var(--card); border:1px solid var(--line);
  border-radius:16px; padding:30px 28px; text-align:center; }}
.gate h1 {{ font-size:1.25rem; margin-bottom:6px; }}
.gate .sub {{ margin-bottom:18px; }}
.gate-note {{ color:var(--muted); font-size:.8rem; margin-top:12px; line-height:1.6; }}

/* 경제 지표 — 숫자를 크게, 뜻을 바로 밑에 */
.picks {{ display:flex; flex-direction:column; gap:8px; }}
.pk {{ display:flex; align-items:flex-start; gap:8px;
      background:var(--card); border:1px solid var(--line); border-radius:12px;
      padding:4px 12px 4px 4px; }}
.pk-d {{ flex:1 1 auto; min-width:0; }}
.pk-sum {{ display:flex; gap:9px; align-items:baseline; flex-wrap:wrap;
      padding:8px 10px; cursor:pointer; border-radius:8px; }}
.pk-sum:hover {{ background:var(--bg); }}
.pk-rank {{ display:inline-flex; align-items:center; justify-content:center;
      width:20px; height:20px; border-radius:999px; background:var(--line);
      font-size:.7rem; font-weight:700; flex:0 0 auto; }}
.pk-ticker {{ font-weight:700; font-size:1.05rem; }}
.pk-name {{ min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.pk-body {{ padding:2px 10px 10px 39px; display:flex; flex-direction:column; gap:8px; }}
.pk-line {{ margin:0; font-size:.85rem; }}
.pk-why {{ margin:0; }}
.pk-why li {{ margin:2px 0; }}
.pk-care {{ border-top:1px dashed var(--line); padding-top:7px; }}
.pk-note {{ border-top:1px dashed var(--line); padding-top:7px; opacity:.72; }}
.tabs {{ display:flex; gap:6px; margin:0 0 14px; }}
.tab {{ display:inline-flex; align-items:center; gap:6px; padding:7px 15px;
      border:1px solid var(--line); border-radius:999px; background:var(--card);
      color:var(--muted); text-decoration:none; font-size:.87rem; font-weight:600; }}
.tab:hover {{ color:var(--fg); }}
.tab.on {{ background:var(--accent); border-color:transparent; color:#fff; }}
.tab-n {{ font-size:.75rem; opacity:.75; font-variant-numeric:tabular-nums; }}
.tab-open {{ font-size:.72rem; opacity:.85; font-weight:500; }}
.tab-warn {{ font-size:.7rem; padding:1px 6px; border-radius:999px;
      background:rgba(185,28,28,.14); color:var(--bad); }}
.tab.on .tab-warn {{ background:rgba(255,255,255,.22); color:#fff; }}
.f-orig {{ font-size:.74rem; color:var(--muted); margin:2px 0 0 2px; }}
.f-why {{ font-size:.78rem; margin:3px 0 0 2px; }}
.src-parts {{ margin:4px 0 2px 2px; }}
.src-parts > summary {{ cursor:pointer; font-size:.76rem; color:var(--muted); }}
.src-parts > summary:hover {{ color:var(--accent); }}
.pk-care-h {{ font-size:.75rem; font-weight:700; color:var(--muted); margin-bottom:3px; }}
.pk-act {{ flex:0 0 auto; padding-top:9px; }}
.pk-add button {{ font-size:.78rem; padding:4px 10px; }}
.fold > .fold-h {{ display:block; cursor:pointer; list-style:none; }}
.fold > .fold-h::-webkit-details-marker {{ display:none; }}
.fold > .fold-h h2::before {{ content:"▾ "; color:var(--muted); font-weight:400; }}
.fold:not([open]) > .fold-h h2::before {{ content:"▸ "; }}
.macro {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }}
.mi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:13px 15px; }}
.mi-top {{ display:flex; gap:8px; align-items:baseline; justify-content:space-between; }}
.mi-name {{ font-weight:700; font-size:.88rem; }}
.mi-when {{ white-space:nowrap; }}
.mi-val {{ font-size:1.5rem; font-weight:700; font-variant-numeric:tabular-nums;
  display:flex; gap:9px; align-items:baseline; margin:4px 0 2px; }}
.mi-move {{ font-size:.78rem; font-weight:600; }}
.mi-move.good {{ color:var(--good); }}
.mi-move.bad {{ color:var(--bad); }}
.mi-move.flat {{ color:var(--muted); }}
.mi-read {{ font-size:.8rem; font-weight:600; color:var(--muted); }}

.glossary {{ display:grid; gap:18px; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }}
details.g-group {{ border:1px solid var(--line); border-radius:10px; background:var(--card); }}
details.g-group > summary {{ padding:9px 13px; cursor:pointer; font-weight:700;
  font-size:.9rem; color:var(--fg); }}
details.g-group > summary:hover {{ color:var(--accent); }}
details.g-group[open] > summary {{ border-bottom:1px solid var(--line); }}
.g-items {{ padding:10px 12px; display:grid; gap:10px; }}
.g-item {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:12px 14px; margin-bottom:10px; scroll-margin-top:20px; }}
.g-item h4 {{ margin:0 0 6px; color:var(--fg); font-size:.95rem; }}
.g-short {{ font-size:.85rem; margin-bottom:6px; }}
.g-row {{ font-size:.78rem; color:var(--muted); margin:4px 0; }}
.g-row.caution {{ color:var(--alert); }}
.g-key {{ display:inline-block; min-width:52px; font-weight:700; }}
code {{ background:var(--quote); padding:1px 5px; border-radius:4px; font-size:.9em; }}

footer {{ margin-top:36px; padding-top:16px; border-top:1px solid var(--line); font-size:.8rem; }}
@media (max-width:600px) {{
  body {{ padding:14px; }}
  .axes, .charts, .stats {{ grid-template-columns:1fr; }}
}}
</style></head>
<body>
{body}
</body></html>
"""
