"""내 컴퓨터에서 브라우저로 보는 화면 — 서버와 동작.

외부 라이브러리 없이 파이썬 표준 http.server 만 쓴다. localhost(127.0.0.1)에만
열리므로 다른 기기에서는 접속할 수 없다.

설계 원칙:
  1) 화면은 절대 멈추지 않는다. 오래 걸리는 작업은 백그라운드로 돌리고,
     그 동안에도 화면은 받아둔 값으로 그린다.
  2) 버튼을 누르지 않아도 정보가 다 채워져 있다.
  3) 숫자에는 출처를, 용어에는 설명을 붙인다. 지어낸 문장은 넣지 않는다.

쪽을 그리는 일은 ui/ 아래 파일들이 한다. 여기는 주소를 쪽에 이어주고, 단추를
누르면 일을 시키고, 몇 초마다 바뀌는 값을 보내준다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import markets
from .timeutil import now
from .ui import (
    calendar_page, discover_page, filings_page, frags, glossary_page, home, live, market_page,
    news_page, quant_page, settings_page, shell, stock,
)
from .ui.banners import contact_banner, key_banner, update_banner
from .ui.context import Ctx
from .ui.kit import card, esc, plain, set_display_tz, set_logos, term  # noqa: F401 (term: 다른 곳에서 씀)

log = logging.getLogger(__name__)

LOCK_TIMEOUT = 0.4
AUTOFILL_TRIES = 3      # 자동 채움을 연달아 몇 번까지 다시 해볼지
STATIC_DIR = Path(__file__).resolve().parent / "static"

# 같이 들고 다니는 파일만 내준다. 이름을 받아 아무 파일이나 읽어주면 안 된다.
STATIC_FILES = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "chart.js": "text/javascript; charset=utf-8",
    "lightweight-charts.js": "text/javascript; charset=utf-8",
    "fonts/PretendardVariable.woff2": "font/woff2",
}

PAGES = {
    "/": "home", "/index.html": "home", "/news": "news", "/filings": "filings",
    "/calendar": "calendar", "/discover": "discover", "/market": "market",
    "/settings": "settings", "/glossary": "glossary", "/quant": "quant",
}
TITLES = {"home": "관심 종목", "news": "뉴스", "filings": "공시", "calendar": "캘린더",
          "discover": "발굴", "market": "시장", "settings": "설정", "glossary": "용어 사전",
          "quant": "퀀트 연습장"}

# 옛 이름으로 부르는 곳을 위해 남겨 둔다
SUMMARY_COLUMNS = home.SUMMARY_COLUMNS
chart_data = live.chart_data


QUANT_ACTIONS = {"backtest", "paper_start", "paper_step", "paper_pause", "paper_reset", "journal"}


def _market_of(back: str) -> str:
    """돌아갈 주소의 m= 에서 시장을 읽는다. 모르면 미국."""
    query = parse_qs(urlparse(back or "").query)
    wanted = (query.get("m") or [markets.US])[0]
    return wanted if wanted in (markets.US, markets.KR) else markets.US


def quant_settings(params: dict, market: str) -> dict:
    """화면 입력 → 전략·자본·기간·위험 규칙·비용. % 로 받은 값은 소수로 바꾼다. 이상한 값은 기본값으로."""
    from .quant import strategies as strat
    from .quant.costs import CostModel, default_fee_key, fee_preset
    from .quant.sizing import STAGES, Plan, RiskRules

    one = lambda name: (params.get(name) or [""])[0].strip()  # noqa: E731

    def number(name, default):
        try:
            value = float(one(name).replace(",", ""))
        except ValueError:
            return default
        return value if value == value else default      # NaN 거르기

    strategy = one("strategy") if one("strategy") in strat.STRATEGIES else strat.DEFAULT_STRATEGY
    capital = number("capital", 1_000_000 if market == markets.KR else 1_000)
    capital = min(max(capital, 1.0), 1e12)
    try:
        years = int(one("years") or 0)
    except ValueError:
        years = 0
    years = years if years in (0, 1, 3, 5) else 0
    preset = one("preset") or "custom"
    stage = next((st for st in STAGES if st.key == preset), None)
    if stage:
        rules = stage.rules
    else:
        preset = "custom"
        base = RiskRules()
        rules = RiskRules.from_dict({
            "risk_per_trade": number("risk_per_trade", base.risk_per_trade * 100) / 100,
            "max_weight": number("max_weight", base.max_weight * 100) / 100,
            "max_positions": number("max_positions", base.max_positions),
            "vol_target": one("vol_target") == "1",
            "daily_loss_stop": number("daily_loss_stop", base.daily_loss_stop * 100) / 100,
            "dd_half": number("dd_half", base.dd_half * 100) / 100,
            "dd_stop": number("dd_stop", base.dd_stop * 100) / 100,
        })
    fee = one("fee") or default_fee_key(market)
    costs = fee_preset(market, fee)
    if costs is None:               # '직접' — 칸에 적은 값
        fee = "custom"
        costs = CostModel.from_dict({name: number(name, -1) / 100 if number(name, -1) >= 0 else None
                                     for name in ("commission", "sell_tax", "slippage")}, market)
    days = params.get("check_day") or []
    plan = Plan.from_dict({"monthly_deposit": number("monthly_deposit", 0),
                           "check_days": days or Plan().check_days,
                           "min_hold": number("min_hold", Plan().min_hold)})
    saved = {"strategy": strategy, "capital": capital, "years": str(years), "preset": preset,
             "rules": rules.to_dict(), "costs": costs.to_dict(), "fee": fee, "plan": plan.to_dict()}
    return {"strategy": strategy, "capital": capital, "years": years, "rules": rules, "costs": costs,
            "plan": plan, "saved": saved}


class Dashboard:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.lock = threading.Lock()
        self.notice: str | None = None
        self.busy: str | None = None
        self._last: dict[str, str] = {}        # 쪽마다 마지막으로 그린 본문
        self._market_thread: threading.Thread | None = None
        self._autofill_tries = 0
        self.rejected: str | None = None       # 작업 중에 누른 버튼

    # --- 동작 -----------------------------------------------------------
    def run_action(self, action: str, params: dict) -> str:
        one = lambda name: (params.get(name) or [""])[0].strip()  # noqa: E731
        self._autofill_tries = 0        # 사람이 눌렀으면 자동 채움도 다시 시작한다

        if action == "check":
            return self._background("공시를 확인하는 중…", self._do_check)
        if action == "metrics":
            return self._background("지표를 계산하는 중…", self._do_metrics)
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
            # 한글 회사 이름이면 여기서 종목 코드로 바꾼다. 여섯 자리 숫자를 외우게 하는 건 말이 안 된다.
            if markets.is_korean_name(raw):
                found = self._korean_code(raw)
                if not found.isdigit():
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
        if action == "contact":
            return self._set_contact(one("email"))
        if action in QUANT_ACTIONS:
            return self._quant_action(action, params)
        if action == "quit":
            return self._do_quit()
        return "알 수 없는 동작입니다."

    def _set_contact(self, email: str) -> str:
        """SEC 연락처를 화면에서 받는다. config.yml 의 한 줄만 고치고, 다시 켜지 않고 바로 쓴다."""
        from .http import valid_email
        from .setup_wizard import set_contact

        email = email.strip()
        if not valid_email(email):
            return "이메일 형식이 아닙니다. 'ID@도메인.com' 처럼 빈칸 없이 넣어주세요."
        path = getattr(self.bot.config, "path", None)
        if not path:
            return "설정 파일 위치를 몰라 저장하지 못했습니다."
        try:
            value = set_contact(Path(path), email)
        except OSError as exc:
            return f"설정 파일에 쓰지 못했습니다: {exc}"
        self.bot.config.user_agent = value
        self.bot.http.set_user_agent(value)
        self.bot._targets = None              # SEC 에서 못 찾았던 종목을 다시 찾는다
        self.bot._targets_full = False
        self._background("미국 종목 정보를 불러오는 중…", self._do_fill)
        return "이메일을 저장했습니다. 미국 공시·재무를 불러옵니다."

    # --- 퀀트 연습장 (실제 주문 없음) ------------------------------------------
    def _quant_action(self, action: str, params: dict) -> str:
        from .quant import paper

        one = lambda name: (params.get(name) or [""])[0].strip()  # noqa: E731
        market = _market_of(one("back"))
        store = self.bot.quant
        if action == "journal":
            text = " ".join(one("text").split())[:1000]
            if not text:
                return "적을 내용을 넣어주세요."
            store.add_journal(now(self.bot.config.timezone).date().isoformat(), market, text)
            store.save()
            return "일지에 적었습니다."
        if action == "paper_step":
            return self._background("모의 계좌를 반영하는 중…", lambda: self._do_paper_step(market))
        if action == "paper_pause":
            account = store.paper(market)
            if not account:
                return "모의 계좌가 없습니다."
            account["paused"] = not account.get("paused")
            store.set_paper(market, account)
            store.save()
            return "모의 계좌를 멈췄습니다." if account["paused"] else "모의 계좌를 다시 돌립니다."
        if action == "paper_reset":
            store.set_paper(market, None)
            store.save()
            return "모의 계좌를 지웠습니다. 새로 시작할 수 있습니다."

        chosen = quant_settings(params, market)
        store.set_settings(market, chosen["saved"])
        store.save()
        if action == "paper_start":
            if store.paper(market):
                return "이미 모의 계좌가 있습니다. 초기화한 뒤 다시 시작하세요."
            return paper.start(store, self.bot, market, chosen["capital"], chosen["strategy"],
                               chosen["rules"], chosen["costs"], now(self.bot.config.timezone).date(),
                               plan=chosen["plan"])
        compare = one("mode") == "compare"
        label = "전략을 모두 비교하는 중…" if compare else "백테스트를 돌리는 중…"
        return self._background(label, lambda: self._do_backtest(market, chosen, compare))

    def _do_backtest(self, market: str, chosen: dict, compare: bool) -> str:
        from datetime import timedelta

        from .quant import backtest, paper
        from .quant import strategies as strat

        data = paper.market_data(self.bot, market)
        excluded = paper.leveraged_tickers(self.bot, market)
        if not data:
            return "백테스트할 일봉이 없습니다. 감시 종목의 지표를 먼저 불러와 주세요."
        end = max(b.day for bars in data.values() for b in bars)
        start = end - timedelta(days=int(365.25 * chosen["years"])) if chosen["years"] else None
        store = self.bot.quant
        growth = {}
        wanted = list(strat.STRATEGIES) if compare else [chosen["strategy"]]
        if any(getattr(strat.get(k), "needs_growth", False) for k in wanted):
            from .quant.fundamentals import growth_data

            self.busy = "과거 시점 매출(SEC 제출일 기준)을 정리하는 중…"
            growth = growth_data(self.bot, market)
        if compare:
            rows = []
            for key in strat.STRATEGIES:
                self.busy = f"전략 비교 중… {strat.get(key).name}"
                result = backtest.run(strat.get(key), chosen["rules"], chosen["costs"], chosen["capital"],
                                      data, start, plan=chosen["plan"], growth=growth, market=market,
                                      excluded=excluded)
                rows.append({"strategy": key, "metrics": result["metrics"], "bench": result["bench"],
                             "no_data": bool(result.get("needs_growth") and not result.get("growth_tickers"))})
            store.set_compare(market, rows)
            store.save()
            return f"전략 {len(rows)}개를 같은 조건으로 돌렸습니다. 아래 표를 보세요."
        result = backtest.run(strat.get(chosen["strategy"]), chosen["rules"], chosen["costs"],
                              chosen["capital"], data, start, plan=chosen["plan"], growth=growth, market=market,
                              excluded=excluded)
        store.set_backtest(market, result)
        store.save()
        m = result.get("metrics") or {}
        if not m:
            return "계산할 일봉이 없었습니다."
        cagr = m.get("cagr")
        return (f"백테스트 끝: 거래 {m.get('trades', 0)}번, 연 {cagr * 100:+.1f}%, 최대 낙폭 -{m.get('mdd', 0) * 100:.1f}%"
                if cagr is not None else f"백테스트 끝: 거래 {m.get('trades', 0)}번")

    def _do_paper_step(self, market: str) -> str:
        from .quant import paper

        if not self.bot.quant.paper(market):
            return "모의 계좌가 없습니다."
        days, events = paper.step(self.bot.quant, self.bot, market)
        if not days:
            return "새로 끝난 거래일이 없습니다. 장이 끝난 뒤 지표가 갱신되면 반영됩니다."
        return f"{days}일을 처리했습니다." + (f" 새 기록 {len(events)}건." if events else "")

    def _do_quit(self) -> str:
        """감시를 완전히 끈다.

        창 없이 도는 프로그램이라 끄는 방법이 눈에 안 보인다. 그래서 늘 보고
        있는 이 화면에 종료 버튼을 뒀다. (돌고 있는 프로그램이 폴더를 붙잡고
        있어 폴더를 지우지 못하는 일이 실제로 있었다.)
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
            # 눌렀는데 아무 반응이 없는 것처럼 보이지 않게, 눌렀다는 사실을 따로 들고 있다가 같이 띄운다.
            label = plain(message).rstrip("… .")
            self.rejected = f"{label} — 지금 작업이 끝난 뒤에 다시 눌러주세요."
            return f"이미 실행 중입니다: {self.busy}"

        def worker():
            try:
                with self.lock:
                    result = func()
                self.notice = result
            except Exception as exc:
                log.exception("화면 작업 실패")
                self.notice = f"오류가 났습니다: {exc}"
            finally:
                self.busy = None

        self.busy = message
        threading.Thread(target=worker, daemon=True).start()
        return message

    def _do_check(self) -> str:
        filings = self.bot.check_filings()
        try:
            filings = list(filings) + list(self.bot.check_korean_filings())
        except Exception as exc:
            log.debug("DART 공시 확인 실패: %s", exc)
        return f"새 공시 {len(filings)}건을 찾았습니다." if filings else "새 공시가 없습니다."

    def _progress(self, label: str):
        """어디까지 왔는지 계속 알려준다. 진행 표시가 없으면 멈춘 것과 구분이 안 된다."""
        def report(index: int, total: int, ticker: str) -> None:
            self.busy = f"{label} ({index + 1}/{total}) {ticker}…"
        return report

    def _do_metrics(self, force: bool = True) -> str:
        if not self.bot.targets():
            return self._no_targets_message()
        done, failed = self.bot.ensure_all_metrics(
            force=force, on_progress=self._progress("지표를 계산하는 중"))
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
        return "감시 중인 종목이 없습니다. 위 검색창이나 '관심 종목 추가하기' 로 넣어주세요."

    def _do_fill(self) -> str:
        done, failed = self.bot.ensure_all_metrics(
            force=False, on_progress=self._progress("종목 정보를 불러오는 중"))
        if not self.bot.targets():
            return self._no_targets_message()
        # 가이던스·업종도 같이 채운다 (버튼을 누르지 않아도 보이도록)
        self.busy = "가이던스·업종을 확인하는 중…"
        self.bot.fill_context(limit=3)
        return f"{done}개 종목 정보를 불러왔습니다." + (f" 실패: {', '.join(failed)}" if failed else "")

    def _do_reports(self) -> str:
        loaded, missing, guided, flagged = 0, [], 0, 0
        targets = self.bot.targets()
        for index, target in enumerate(targets):
            self.busy = f"분기보고서를 읽는 중… ({index + 1}/{len(targets)}) {target.ticker}"
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
        return f"속보 {len(items)}건을 찾았습니다." if items else "새 속보가 없습니다."

    def _do_update(self) -> str:
        ok, message = self.bot.apply_update()
        if ok:
            return message + " ← '끄기' 를 누른 뒤 '시작하기' 를 다시 실행하면 새 버전으로 돕니다."
        return message

    def _korean_code(self, name: str) -> str:
        """한글 회사 이름 → 종목 코드. 못 정하면 사람이 읽을 안내를 돌려준다.

        비슷하다고 아무거나 고르지 않는다. 엉뚱한 회사를 감시하게 된다.
        """
        dart = getattr(self.bot, "dart", None)
        if dart is None or not dart.ready:
            return ("DART 인증키가 없어 회사 이름으로는 찾을 수 없습니다. 종목 코드(예: 005930)로 넣거나, "
                    "설정 → 열쇠 보관함에 DART 인증키를 넣어주세요.")
        code, _found, others = dart.resolve_name(name)
        if code:
            return code
        if others:
            candidates = " · ".join(f"{n}({c})" for c, n in others[:6])
            return f"'{name}' 과 비슷한 회사가 여럿입니다. 하나를 골라주세요 → {candidates}"
        if not dart.corp_codes():
            why = dart.last_error
            if why:
                return f"DART 회사 목록을 받지 못했습니다 — {why}"
            return "아직 DART 회사 목록을 받지 못했습니다. 잠시 뒤 다시 시도해주세요."
        return f"'{name}' 을(를) 상장사 목록에서 찾지 못했습니다. 종목 코드로 넣어보세요."

    def _do_add(self, raw: str, ticker: str) -> str:
        reply = plain(self.bot.commands.handle(f"/add {raw}"))
        # 종목당 10초 넘게 걸리는 일이 흔해서, 어디까지 왔는지 계속 알려준다.
        self.bot.ensure_all_metrics(force=False, on_progress=self._progress("종목 정보를 불러오는 중"))
        return reply

    def _do_remove(self, ticker: str) -> str:
        return plain(self.bot.commands.handle(f"/remove {ticker}"))

    def _set_consensus(self, ticker: str, eps: str, revenue: str) -> str:
        parts = []
        if eps:
            parts.append(f"eps={eps}")
        if revenue:
            parts.append(f"rev={revenue}")
        if not ticker or not parts:
            return "컨센서스 값을 입력해주세요."
        with self.lock:
            reply = plain(self.bot.commands.handle(f"/consensus {ticker} {' '.join(parts)}"))
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
        field = {"deepl": "deepl_key", "azure": "azure_key", "papago": "papago_id_key",
                 "google_cloud": "google_cloud_key"}.get(provider)
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
        label = {"deepl": "DeepL", "azure": "Azure 번역기", "papago": "파파고",
                 "google_cloud": "Google 번역 API"}.get(provider, provider)
        return f"{label} 열쇠를 저장했습니다. '번역 시험' 을 눌러 확인해보세요."

    def _do_translate_test(self) -> str:
        """실제로 한 문장을 번역해본다. 되는지 눈으로 확인하는 게 가장 확실하다."""
        sample = "Revenue increased 78% year over year to $213.0 million."
        result = self.bot.translator.translate(sample)
        if not result:
            if not self.bot.translator.available():
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

    # --- 뒤에서 도는 것 ---------------------------------------------------
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

        계속 실패하는 종목이 있으면 '불러오는 중' 이 영원히 반복돼 멈춘 것처럼
        보인다. 몇 번 해보고 안 되면 손을 뗀다 — 실패 이유는 종목 화면에 적힌다.
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
    def render(self, market: str = markets.US, page: str = "home", params: dict | None = None,
               here: str = "") -> str:
        """쪽 하나. 잠금을 못 잡으면(긴 작업 중) 받아둔 값으로 그린다 — 화면은 멈추지 않는다."""
        self.start_market_refresh()      # 화면을 처음 열 때부터 환율이 돌게 한다
        self.autofill_if_needed()
        params = params or {}
        here = here or shell.with_market("/", market)
        key = f"{page}|{here}"
        title, active, charts = TITLES.get(page, "First Stock"), page, False
        locked = self.lock.acquire(timeout=LOCK_TIMEOUT)
        try:
            targets = self.bot.targets() if locked else self.bot.cached_targets()
            today = now(self.bot.config.timezone).date()
            ctx = Ctx.build(self.bot, market, here, today, targets)
            body, title, active, charts = self._page(page, ctx, params)
            self._last[key] = body
        except Exception:
            log.exception("화면을 그리다 멈췄습니다 (%s)", page)
            body = self._last.get(key) or card(
                '<div class="empty">화면을 그리는 중에 문제가 생겼습니다. 잠시 뒤 자동으로 다시 그립니다. '
                "계속되면 program/logs/실행기록.log 를 확인해주세요.</div>")
        finally:
            if locked:
                self.lock.release()
        today = now(self.bot.config.timezone).date()
        return shell.document(title=title, body=body, active=active, market=market, bot=self.bot,
                              here=here, notice=self._take_notice(), charts=charts, today=today)

    def render_path(self, path: str) -> str | None:
        """주소 하나 → 쪽. 모르는 주소면 None. (서버와 시험이 같은 길로 그리게)"""
        parsed = urlparse(path)
        query = parse_qs(parsed.query)
        here = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        wanted = (query.get("m") or [markets.US])[0]
        market = wanted if wanted in (markets.US, markets.KR) else markets.US
        if parsed.path in PAGES:
            return self.render(market, PAGES[parsed.path], query, here)
        if parsed.path.startswith("/stock/"):
            ticker = unquote(parsed.path[len("/stock/"):]).strip()
            target = next((t for t in self.bot.cached_targets()
                           if t.ticker.upper() == ticker.upper()
                           or markets.display(t.ticker) == ticker.upper()), None)
            market = target.market if target else markets.market_of(ticker)
            return self.render(market, "stock", {"t": [ticker]}, here)
        return None

    def _page(self, page: str, ctx: Ctx, params: dict):
        one = lambda name: (params.get(name) or [""])[0]  # noqa: E731
        bot = self.bot
        banners = update_banner(bot.state.known_latest(), ctx.here)
        if not ctx.korean or page == "settings":
            banners += contact_banner(bot, ctx.here)
        if page == "stock":
            ticker = one("t")
            target = ctx.find(ticker)
            if target is None:
                return banners + stock.not_found(ctx, ticker.upper()), ticker.upper() or "종목", "home", False
            name = target.watch.name or target.name or markets.display(target.ticker)
            return banners + stock.render(ctx, target), f"{markets.display(target.ticker)} {name}", "home", True
        if page == "news":
            return banners + news_page.render(ctx, one("f")), TITLES[page], page, False
        if page == "filings":
            return banners + filings_page.render(ctx, one("f"), one("t")), TITLES[page], page, False
        if page == "calendar":
            return banners + calendar_page.render(ctx, one("ym"), one("d"), one("k")), TITLES[page], page, False
        if page == "discover":
            kr = key_banner(bot, ctx.here) if ctx.korean else ""
            return banners + kr + discover_page.render(ctx, one("c")), TITLES[page], page, False
        if page == "market":
            return banners + market_page.render(ctx), TITLES[page], page, False
        if page == "settings":
            return banners + settings_page.render(ctx), TITLES[page], page, False
        if page == "glossary":
            return banners + glossary_page.render(ctx), TITLES[page], page, False
        if page == "quant":
            return banners + quant_page.render(ctx), TITLES[page], page, False
        kr = key_banner(bot, ctx.here) if ctx.korean else ""
        body = home.render(ctx, bot.unresolved_tickers(), bot.metrics_errors())
        return banners + kr + body, TITLES["home"], "home", False

    def _take_notice(self) -> dict | None:
        """화면 아래 알림 한 번. 작업 중이면 진행 상황, 끝났으면 결과."""
        if self.busy:
            waiting, self.rejected = self.rejected, None
            return {"busy": self.busy, "text": waiting or ""}
        self.rejected = None
        if self.notice:
            text, self.notice = self.notice, None
            bad = any(word in text for word in ("❌", "오류", "실패", "거부", "찾지 못", "못했"))
            return {"text": text, "bad": bad}
        return None

    def render_goodbye(self) -> str:
        """종료 직후 마지막 화면. 없는 서버를 두드리지 않게 스크립트를 싣지 않는다."""
        body = shell.gate("⏻ 감시를 멈췄습니다", [
            "이 창은 닫으셔도 됩니다.",
            "다시 보려면 프로그램 폴더의 <b>시작하기</b> 파일을 더블클릭하세요.",
            "이제 프로그램 폴더를 지우거나 옮길 수 있습니다. 돌고 있는 동안에는 윈도우가 폴더를 붙잡고 있어서 "
            "<i>\"사용 중인 폴더\"</i> 라고 나옵니다.",
        ])
        return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>감시를 멈췄습니다</title><link rel="icon" href="data:,">'
                f'{shell.THEME_BOOT}<link rel="stylesheet" href="/static/app.css"></head>'
                f'<body><main class="main">{body}</main></body></html>')

    def status(self) -> dict:
        """화면이 몇 초마다 묻는 것 — 일하는 중인지, 새로 들어온 게 있는지."""
        state = self.bot.state
        news = state.news(1)
        recent = state.recent(1)
        stamp = "|".join([
            str(state.last_check() or ""),
            str(news[0].get("when") if news else ""), str(news[0].get("title") if news else ""),
            str(recent[0].get("when") if recent else ""), str(recent[0].get("title") if recent else ""),
            str(len(self.bot.cached_metrics())), str(len(self.bot.cached_targets())),
        ])
        return {"busy": self.busy, "stamp": stamp}


def stop_process() -> None:
    """이 프로그램을 끝낸다. (테스트에서는 이 함수만 바꿔치기한다)"""
    os._exit(0)


def live_items(bot, market: str) -> dict:
    """옛 모양: 한 시장의 모든 종목. 새 화면은 /live?t=… 로 필요한 것만 받는다."""
    return live.items(bot, [t.ticker for t in bot.cached_targets() if t.market == market])


def _safe_back(raw: str) -> str:
    """단추를 누른 뒤 돌아갈 곳. 이 서버 안의 주소만 받는다(다른 사이트로 튕기지 않게)."""
    text = str(raw or "").strip()
    if not text.startswith("/") or text.startswith("//") or "\\" in text or "\n" in text or "\r" in text:
        return ""
    return text


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

    def _market(self, query: dict) -> str:
        wanted = (query.get("m") or [markets.US])[0]
        return wanted if wanted in (markets.US, markets.KR) else markets.US

    def _target(self, query: dict):
        wanted = (query.get("t") or [""])[0].upper()
        for target in self.dashboard.bot.cached_targets():
            if target.ticker.upper() == wanted:
                return target
        return None

    def do_GET(self):
        if not self._guard():
            return
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        here = path + (f"?{parsed.query}" if parsed.query else "")
        dash = self.dashboard
        bot = dash.bot

        if path in PAGES or path.startswith("/stock/"):
            self._html(dash.render_path(here))
        elif path == "/healthz":            # 살아 있는지 확인용 (시작 스크립트가 쓴다)
            self._text("ok")
        elif path == "/status":
            self._json(dash.status())
        elif path == "/live":
            # 몇 초마다 화면이 받아가는 칸들. 받아둔 값만 쓴다.
            wanted = [t for t in (query.get("t") or [""])[0].split(",") if t]
            self._json({"items": live.items(bot, wanted),
                        "tape": shell.tape(bot.market_snapshot(), self._market(query))})
        elif path == "/bars":
            self._json(live.chart_data(bot, (query.get("t") or [""])[0]))
        elif path.startswith("/frag/"):
            self._fragment(path[len("/frag/"):], query)
        elif path.startswith("/static/") and path[len("/static/"):] in STATIC_FILES:
            name = path[len("/static/"):]
            self._static(name, STATIC_FILES[name])
        else:
            self.send_error(404)

    def _fragment(self, kind: str, query: dict) -> None:
        """쪽이 뜬 뒤 브라우저가 따로 받아 끼우는 조각. 여기서는 바깥에 물어봐도 된다."""
        bot = self.dashboard.bot
        known = {t.ticker.upper() for t in bot.cached_targets()}
        if kind not in ("headlines", "news", "analyst", "intraday", "company", "short"):
            self.send_error(404)
            return
        try:
            if kind == "headlines":
                self._html(frags.headlines(bot, known))
                return
            target = self._target(query)
            if target is None:
                self._html('<div class="empty">감시 목록에 없는 종목입니다.</div>')
                return
            m = bot.cached_metrics().get(target.cik)
            if kind == "news":
                html = frags.stock_news(bot, target, known)
            elif kind == "analyst":
                html = frags.analyst(bot, target, m)
            elif kind == "intraday":
                html = frags.intraday(bot, target, m)
            elif kind == "company":
                html = frags.company(bot, target, m)
            else:
                html = frags.short_interest(bot.profile_for(target), getattr(m, "currency", "USD"))
        except Exception as exc:
            log.exception("조각을 그리지 못했습니다 (%s)", kind)
            html = f'<div class="empty">불러오지 못했습니다: {esc(exc)}</div>'
        self._html(html)

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
        # 보던 쪽으로 되돌려 보낸다. 무조건 '/' 로 보내면 누를 때마다 처음 화면으로 튕긴다.
        back = _safe_back((params.get("back") or [""])[0])
        if not back:
            m = (params.get("m") or [""])[0]
            back = f"/?m={m}" if m in (markets.US, markets.KR) else "/"
        if action == "remove":
            ticker = (params.get("ticker") or [""])[0].upper()
            if back.startswith("/stock/") and unquote(back[len("/stock/"):].split("?")[0]).upper() == ticker:
                back = "/"      # 뺀 종목의 화면으로 돌아가면 '없는 종목' 이 뜬다
        self.send_response(303)
        self.send_header("Location", back)
        self.end_headers()

    def _json(self, payload) -> None:
        self._respond(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      "application/json; charset=utf-8")

    def _static(self, name: str, content_type: str) -> None:
        """같이 들고 다니는 파일. 바깥 CDN 에서 받지 않는다 — 인터넷이 끊겨도
        화면·차트·글꼴이 떠야 하고, 그릴 때마다 남의 서버에 흔적을 남길 이유가 없다."""
        try:
            payload = (STATIC_DIR / name).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        # 새 버전을 받으면 곧바로 새 파일을 쓰게 짧게 둔다(글꼴만 길게).
        self.send_header("Cache-Control", "max-age=604800" if name.startswith("fonts/") else "no-cache")
        self.end_headers()
        self.wfile.write(payload)

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
    # 로고는 기본으로 켠다(미국 종목만). 끄려면 config.yml 의 show_logos: false.
    set_logos(bool(bot.config.raw.get("show_logos", True)))
    set_display_tz(bot.config.timezone)
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
