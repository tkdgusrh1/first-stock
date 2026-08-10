"""내 컴퓨터에서 브라우저로 보는 대시보드.

외부 라이브러리 없이 파이썬 표준 http.server 만 쓴다.
localhost(127.0.0.1)에만 열리므로 다른 기기에서는 접속할 수 없다.
"""

from __future__ import annotations

import html
import logging
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .econ_calendar import parse_extra_events, upcoming_events
from .market_calendar import upcoming_market_days
from .metrics import STATUS_ICON, _money, _pct
from .timeutil import dday, kdate, now

log = logging.getLogger(__name__)

TONE_CLASS = {"alert": "tone-alert", "good": "tone-good", "bad": "tone-bad", "plain": "tone-plain"}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


class Dashboard:
    """봇 상태를 HTML 로 만들어 주는 쪽. 봇 접근은 lock 으로 직렬화한다."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self.lock = threading.Lock()
        self.notice: str | None = None
        self.busy: str | None = None      # 오래 걸리는 작업 진행 표시

    # --- 동작 -----------------------------------------------------------
    def run_action(self, action: str, params: dict) -> str:
        """버튼 처리. 오래 걸리는 건 백그라운드로 돌리고 바로 응답한다."""
        if action == "check":
            return self._background("공시를 확인하는 중…", self._do_check)
        if action == "metrics":
            return self._background("지표를 계산하는 중… (처음엔 30초 정도 걸립니다)", self._do_metrics)
        if action == "brief":
            return self._background("브리핑을 보내는 중…", self._do_brief)
        if action == "add":
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            if not ticker:
                return "티커를 입력해주세요."
            with self.lock:
                return _plain(self.bot.commands.handle(f"/add {ticker}"))
        if action == "remove":
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            with self.lock:
                return _plain(self.bot.commands.handle(f"/remove {ticker}"))
        return "알 수 없는 동작입니다."

    def _background(self, message: str, func) -> str:
        if self.busy:
            return f"이미 실행 중입니다: {self.busy}"

        def worker():
            try:
                with self.lock:
                    self.notice = func()
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

    def _do_metrics(self) -> str:
        count = 0
        for target in self.bot.targets():
            self.bot.metrics_for(target, with_peers=False)
            count += 1
        return f"{count}개 종목 지표를 새로 계산했습니다."

    def _do_brief(self) -> str:
        self.bot.daily_brief(force=True)
        return "텔레그램으로 브리핑을 보냈습니다."

    # --- 화면 -----------------------------------------------------------
    def render(self) -> str:
        with self.lock:
            bot = self.bot
            config = bot.config
            today = now(config.timezone).date()
            targets = bot.targets()
            market_days = upcoming_market_days(today, config.holiday_lookahead_days)
            events = upcoming_events(
                today,
                max(config.econ_lookahead_days, 14),
                min_importance=int(config.raw.get("econ_min_importance", 2)),
                extra=parse_extra_events(config.raw.get("econ_extra_events")),
                include_weekly=bool(config.raw.get("econ_include_weekly", False)),
            )
            recent = bot.state.recent(30)
            last_check = bot.state.last_check()
            cached_metrics = bot.cached_metrics()
            warning = bot.calendar_warning()

        body = [
            _header(today, market_days, last_check, config),
            self._notice_block(),
            _cards(targets, cached_metrics, config),
            _filings(recent),
            _schedule(today, market_days, events),
            _footer(warning),
        ]
        return _PAGE.format(body="\n".join(body), refresh=60)

    def _notice_block(self) -> str:
        if self.busy:
            return f'<div class="notice busy">⏳ {esc(self.busy)}</div>'
        if self.notice:
            text, self.notice = self.notice, None
            return f'<div class="notice">✅ {esc(text)}</div>'
        return ""


def _plain(text: str | None) -> str:
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", text or "완료")).strip()


# --------------------------------------------------------------------------
# 조각들
# --------------------------------------------------------------------------
def _header(today: date, market_days, last_check, config) -> str:
    todays = [d for d in market_days if d.day == today]
    if todays:
        status, cls = f"{todays[0].kind} — {todays[0].name}", "closed"
    elif today.weekday() >= 5:
        status, cls = "주말 휴장", "closed"
    else:
        status, cls = "정상 개장 (한국시간 22:30~05:00)", "open"

    return f"""
<header>
  <div>
    <h1>📈 관심 종목 감시</h1>
    <p class="sub">{esc(kdate(today))} · 미국 증시 <span class="badge {cls}">{esc(status)}</span></p>
    <p class="sub">마지막 확인 {esc(last_check or "아직 없음")} · 확인 주기 {config.poll_interval_sec // 60}분</p>
  </div>
  <div class="actions">
    <form method="post" action="/action"><input type="hidden" name="action" value="check">
      <button type="submit">🔄 지금 공시 확인</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="metrics">
      <button type="submit">📊 지표 새로고침</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="brief">
      <button type="submit">✉️ 브리핑 보내기</button></form>
  </div>
</header>"""


def _cards(targets, cached_metrics, config) -> str:
    cards = []
    for target in targets:
        metrics = cached_metrics.get(target.cik)
        cards.append(_card(target, metrics))
    if not cards:
        cards.append('<div class="card"><p>감시 중인 종목이 없습니다. 아래에서 추가해보세요.</p></div>')

    return f"""
<section>
  <h2>관심 종목 <span class="count">{len(targets)}</span></h2>
  <div class="cards">{"".join(cards)}</div>
  <form method="post" action="/action" class="addform">
    <input type="hidden" name="action" value="add">
    <input type="text" name="ticker" placeholder="티커 입력 (예: TSLA)" maxlength="10" required>
    <button type="submit">+ 종목 추가</button>
  </form>
</section>"""


def _card(target, metrics) -> str:
    watch = target.watch
    lines = [f'<div class="card"><div class="card-head"><h3>{esc(target.ticker)}</h3>']
    lines.append(
        '<form method="post" action="/action" onsubmit="return confirm(\'감시 목록에서 뺄까요?\')">'
        '<input type="hidden" name="action" value="remove">'
        f'<input type="hidden" name="ticker" value="{esc(target.ticker)}">'
        '<button type="submit" class="ghost" title="빼기">✕</button></form></div>'
    )
    if target.name:
        lines.append(f'<p class="sub">{esc(target.name)}</p>')

    if metrics is None:
        lines.append('<p class="muted">지표 미계산 — 위 <b>지표 새로고침</b> 버튼을 눌러주세요.</p>')
    else:
        state = "흑자" if metrics.profitable else ("적자" if metrics.profitable is False else "판단 불가")
        head = [f'<span class="badge {"open" if metrics.profitable else "closed"}">{state}</span>']
        if metrics.price:
            head.append(f"${metrics.price:,.2f}")
        lines.append(f'<p class="row">{" ".join(head)}</p>')

        stats = []
        if metrics.revenue_ttm:
            stats.append(("매출 TTM", _money(metrics.revenue_ttm)))
        if metrics.roe is not None:
            stats.append(("ROE", _pct(metrics.roe)))
        if metrics.roic is not None:
            stats.append(("ROIC", _pct(metrics.roic)))
        if metrics.op_margin is not None:
            arrow = ""
            if metrics.op_margin_prior is not None:
                arrow = " ↑" if metrics.op_margin > metrics.op_margin_prior else " ↓"
            stats.append(("영업이익률", _pct(metrics.op_margin) + arrow))
        if metrics.per:
            stats.append(("PER", f"{metrics.per:.1f}x"))
        if metrics.psr:
            stats.append(("PSR", f"{metrics.psr:.1f}x"))
        if metrics.revenue_growth is not None:
            stats.append(("매출 성장", f"{metrics.revenue_growth:+.0%}"))
        if metrics.runway_years is not None:
            stats.append(("현금 런웨이", f"{metrics.runway_years:.1f}년"))
        lines.append(
            '<dl class="stats">'
            + "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in stats)
            + "</dl>"
        )

        lines.append('<ul class="checks">')
        for check in metrics.priority + metrics.checks:
            icon = STATUS_ICON.get(check.status, "•")
            lines.append(
                f'<li><span class="icon">{icon}</span>'
                f'<b>{esc(check.label)}</b><span class="detail">{esc(check.detail)}</span></li>'
            )
        lines.append("</ul>")

    if watch.earnings_date:
        lines.append(f'<p class="sub">📆 실적 발표 {esc(watch.earnings_date.isoformat())}</p>')
    if watch.milestones:
        lines.append(
            '<p class="sub">🎯 ' + esc(" / ".join(watch.milestones)) + "</p>"
        )
    lines.append("</div>")
    return "".join(lines)


def _filings(recent) -> str:
    if not recent:
        rows = '<p class="muted">아직 받은 공시가 없습니다. 새 공시가 올라오면 여기와 텔레그램에 함께 표시됩니다.</p>'
    else:
        items = []
        for entry in recent:
            tone = TONE_CLASS.get(entry.get("tone", "plain"), "tone-plain")
            items.append(
                f'<li class="{tone}">'
                f'<span class="when">{esc(entry.get("when", ""))}</span>'
                f'<span class="tag">{esc(entry.get("form", ""))}</span>'
                f'<b>{esc(entry.get("ticker", ""))}</b> '
                f'<span class="detail">{esc(entry.get("title", ""))}</span> '
                f'<a href="{esc(entry.get("url", "#"))}" target="_blank" rel="noopener">원문</a>'
                "</li>"
            )
        rows = f'<ul class="filings">{"".join(items)}</ul>'
    return f"<section><h2>최근 공시</h2>{rows}</section>"


def _schedule(today, market_days, events) -> str:
    holiday_items = "".join(
        f"<li><span class='when'>{esc(kdate(d.day))}</span> {esc(d.name)} "
        f"<span class='tag'>{esc(d.kind)}</span> <span class='muted'>{esc(dday(today, d.day))}</span></li>"
        for d in market_days
        if d.day >= today
    ) or "<li class='muted'>예정된 휴장일 없음</li>"

    event_items = []
    for event in events[:20]:
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


def _footer(warning) -> str:
    warn = f'<p class="warn">⚠️ {esc(warning)}</p>' if warning else ""
    return f"""
<footer>
  {warn}
  <p class="muted">데이터: SEC EDGAR·XBRL 공시 + Stooq 종가. 참고용이며 투자 판단의 책임은 본인에게 있습니다.</p>
  <p class="muted">이 화면은 내 컴퓨터에서만 열립니다 (127.0.0.1). 창을 닫아도 봇은 계속 돕니다.</p>
</footer>"""


# --------------------------------------------------------------------------
# HTTP 서버
# --------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    dashboard: Dashboard = None  # 서버 생성 시 주입

    def log_message(self, fmt, *args):  # 접속 로그로 콘솔을 더럽히지 않는다
        log.debug("dashboard %s", fmt % args)

    def _guard(self) -> bool:
        """localhost 외 접속 차단 (바인딩도 127.0.0.1 이지만 이중 확인)."""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self.send_error(403, "localhost only")
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._html(self.dashboard.render())
        elif path == "/healthz":
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
        message = self.dashboard.run_action(action, params)
        if not self.dashboard.busy:
            self.dashboard.notice = message
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def _html(self, text: str):
        payload = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _text(self, text: str):
        payload = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_dashboard(bot, port: int = 8765, open_browser: bool = True) -> ThreadingHTTPServer:
    """대시보드를 백그라운드 스레드로 띄우고 서버 객체를 돌려준다."""
    dashboard = Dashboard(bot)
    handler = type("Handler", (_Handler,), {"dashboard": dashboard})

    server = None
    for candidate in range(port, port + 10):     # 포트가 물려 있으면 옆 번호로
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError as exc:
            log.debug("포트 %s 사용 중: %s", candidate, exc)
    if server is None:
        raise OSError(f"{port}~{port + 9} 포트가 모두 사용 중입니다.")

    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    log.info("대시보드: %s", url)
    if open_browser:
        threading.Timer(1.0, lambda: _open(url)).start()
    return server


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:      # 브라우저가 없는 환경이어도 서버는 살아 있어야 한다
        log.warning("브라우저를 열지 못했습니다(%s). 직접 %s 에 접속하세요.", exc, url)


_PAGE = """<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>관심 종목 감시</title>
<style>
:root {{
  --bg:#f6f7f9; --fg:#1b1d21; --muted:#6b7280; --card:#ffffff; --line:#e5e7eb;
  --accent:#2563eb; --good:#15803d; --bad:#b91c1c; --alert:#b45309;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:#14161a; --fg:#e8eaed; --muted:#9aa1ab; --card:#1c1f24; --line:#2b2f36;
    --accent:#60a5fa; --good:#4ade80; --bad:#f87171; --alert:#fbbf24;
  }}
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; padding:24px; background:var(--bg); color:var(--fg);
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",
              "Noto Sans KR",Segoe UI,sans-serif; line-height:1.55;
}}
h1 {{ font-size:1.5rem; margin:0 0 4px; }}
h2 {{ font-size:1.05rem; margin:28px 0 12px; }}
h3 {{ font-size:1.15rem; margin:0; }}
p {{ margin:2px 0; }}
a {{ color:var(--accent); }}
.sub {{ color:var(--muted); font-size:.85rem; }}
.muted {{ color:var(--muted); }}
.count {{ color:var(--muted); font-weight:400; }}
header {{ display:flex; flex-wrap:wrap; gap:16px; justify-content:space-between; align-items:flex-start; }}
.actions {{ display:flex; flex-wrap:wrap; gap:8px; }}
button {{
  font:inherit; padding:8px 14px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer;
}}
button:hover {{ border-color:var(--accent); color:var(--accent); }}
button.ghost {{ border:none; background:none; color:var(--muted); padding:2px 6px; }}
.badge {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:.8rem; }}
.badge.open {{ background:rgba(21,128,61,.14); color:var(--good); }}
.badge.closed {{ background:rgba(185,28,28,.14); color:var(--bad); }}
.notice {{ margin:16px 0; padding:10px 14px; border-radius:8px; background:var(--card); border:1px solid var(--line); }}
.notice.busy {{ border-color:var(--alert); color:var(--alert); }}
.cards {{
  display:grid; gap:14px; align-items:start;
  grid-template-columns:repeat(auto-fill,minmax(330px,1fr));
}}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }}
.card-head {{ display:flex; justify-content:space-between; align-items:center; }}
.row {{ display:flex; gap:10px; align-items:center; margin:8px 0; }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(90px,1fr)); gap:8px; margin:12px 0; }}
.stats div {{ background:var(--bg); border-radius:8px; padding:6px 8px; }}
.stats dt {{ font-size:.72rem; color:var(--muted); }}
.stats dd {{ margin:0; font-size:.95rem; font-weight:600; }}
.checks {{ list-style:none; padding:0; margin:10px 0 0; font-size:.85rem; }}
.checks li {{ display:flex; gap:6px; padding:4px 0; border-top:1px solid var(--line); flex-wrap:wrap; }}
.checks .detail {{ color:var(--muted); flex:1 1 100%; }}
.addform {{ display:flex; gap:8px; margin-top:14px; }}
.addform input {{
  font:inherit; padding:8px 12px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); flex:0 1 240px;
}}
ul.filings, ul.plain {{ list-style:none; padding:0; margin:0; }}
ul.filings li, ul.plain li {{
  padding:8px 10px; border-bottom:1px solid var(--line); font-size:.9rem;
  display:flex; gap:8px; align-items:baseline; flex-wrap:wrap;
}}
ul.filings li.tone-alert {{ border-left:3px solid var(--alert); }}
ul.filings li.tone-good {{ border-left:3px solid var(--good); }}
ul.filings li.tone-bad {{ border-left:3px solid var(--bad); }}
.when {{ color:var(--muted); font-variant-numeric:tabular-nums; font-size:.82rem; min-width:118px; }}
.tag {{ font-size:.72rem; padding:1px 7px; border-radius:999px; background:var(--bg); border:1px solid var(--line); }}
.detail {{ color:var(--muted); }}
.two-col {{ display:grid; gap:24px; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }}
footer {{ margin-top:32px; padding-top:16px; border-top:1px solid var(--line); font-size:.8rem; }}
.warn {{ color:var(--alert); }}
</style></head>
<body>
{body}
</body></html>
"""
