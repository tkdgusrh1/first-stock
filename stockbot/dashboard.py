"""내 컴퓨터에서 브라우저로 보는 대시보드.

외부 라이브러리 없이 파이썬 표준 http.server 만 쓴다.
localhost(127.0.0.1)에만 열리므로 다른 기기에서는 접속할 수 없다.

설계 원칙 두 가지:
  1) 화면은 절대 멈추지 않는다. 오래 걸리는 작업은 백그라운드로 돌리고,
     그 동안에도 직전 화면을 그대로 보여준다. (락을 붙잡고 렌더링하지 않는다)
  2) 버튼을 누르지 않아도 정보가 다 채워져 있다. 시작하면 알아서 계산한다.
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
from .metrics import STATUS_ICON, Metrics, _money, _pct
from .timeutil import dday, kdate, now

log = logging.getLogger(__name__)

TONE_CLASS = {"alert": "tone-alert", "good": "tone-good", "bad": "tone-bad", "plain": "tone-plain"}
LOCK_TIMEOUT = 0.4      # 이 시간 안에 못 잡으면 직전 화면을 보여준다 (체감이 즉시여야 한다)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


class Dashboard:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.lock = threading.Lock()
        self.notice: str | None = None
        self.busy: str | None = None
        self._last_body: str | None = None

    # --- 동작 -----------------------------------------------------------
    def run_action(self, action: str, params: dict) -> str:
        if action == "check":
            return self._background("공시를 확인하는 중…", self._do_check)
        if action == "metrics":
            return self._background("지표를 계산하는 중…", self._do_metrics)
        if action == "brief":
            return self._background("브리핑을 보내는 중…", self._do_brief)
        if action == "add":
            raw = " ".join((params.get("ticker") or [""])[0].split()).strip()
            if not raw:
                return "티커를 입력해주세요."
            ticker = raw.split(":")[0].split()[0].upper()
            return self._background(f"{ticker} 를 추가하는 중…", lambda: self._do_add(raw, ticker))
        if action == "remove":
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            return self._background(f"{ticker} 를 빼는 중…", lambda: self._do_remove(ticker))
        return "알 수 없는 동작입니다."

    def _background(self, message: str, func) -> str:
        """오래 걸릴 수 있는 작업은 전부 백그라운드로. 화면은 계속 응답한다."""
        if self.busy:
            return f"이미 실행 중입니다: {self.busy}"

        def worker():
            try:
                # 봇 접근만 락으로 감싼다. 렌더링은 이 락을 기다리지 않는다.
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

    def _do_metrics(self) -> str:
        done, failed = 0, []
        for target in self.bot.targets():
            try:
                self.bot.metrics_for(target, with_peers=False)
                self.bot.earnings_for(target)
                done += 1
            except Exception as exc:
                log.warning("지표 계산 실패 %s: %s", target.ticker, exc)
                failed.append(target.ticker)
        message = f"{done}개 종목 지표를 새로 계산했습니다."
        if failed:
            message += f" (실패: {', '.join(failed)})"
        return message

    def _do_brief(self) -> str:
        self.bot.daily_brief(force=True)
        if self.bot.notifier.dry_run:
            return "텔레그램이 꺼져 있어 콘솔에만 출력했습니다."
        return "텔레그램으로 브리핑을 보냈습니다."

    def _do_add(self, raw: str, ticker: str) -> str:
        reply = _plain(self.bot.commands.handle(f"/add {raw}"))
        for target in self.bot.targets():          # 추가한 종목 지표를 바로 채운다
            if target.ticker == ticker:
                try:
                    self.bot.metrics_for(target, with_peers=False)
                except Exception as exc:
                    log.warning("지표 계산 실패 %s: %s", ticker, exc)
        return reply

    def _do_remove(self, ticker: str) -> str:
        return _plain(self.bot.commands.handle(f"/remove {ticker}"))

    def load_initial(self) -> None:
        """시작하자마자 지표를 채워둔다. 버튼을 누를 필요가 없게."""
        self._background("종목 정보를 불러오는 중…", self._do_metrics)

    # --- 화면 -----------------------------------------------------------
    def render(self) -> str:
        """락을 오래 기다리지 않는다. 바쁘면 직전 화면을 그대로 보여준다."""
        if self.lock.acquire(timeout=LOCK_TIMEOUT):
            try:
                body = self._build_body()
                self._last_body = body
            finally:
                self.lock.release()
        else:
            body = self._last_body or _loading_body()

        # 작업 중일 땐 짧게 새로고침해서 결과가 바로 보이게 한다
        page = _PAGE.format(body=body, refresh=4 if self.busy else 60)
        return page.replace("<!--NOTICE-->", self._notice_block(), 1)

    def _build_body(self) -> str:
        bot = self.bot
        config = bot.config
        today = now(config.timezone).date()
        targets = bot.targets()
        metrics = bot.cached_metrics()
        earnings = {t.cik: bot.cached_earnings().get(t.cik) for t in targets}

        market_days = upcoming_market_days(today, max(config.holiday_lookahead_days, 30))
        events = upcoming_events(
            today,
            max(config.econ_lookahead_days, 21),
            min_importance=int(config.raw.get("econ_min_importance", 2)),
            extra=parse_extra_events(config.raw.get("econ_extra_events")),
            include_weekly=bool(config.raw.get("econ_include_weekly", False)),
        )
        recent = bot.state.recent(40)

        rows = [(t, metrics.get(t.cik), earnings.get(t.cik)) for t in targets]
        return "\n".join(
            [
                _header(today, market_days, bot.state.last_check(), config),
                "<!--NOTICE-->",
                _summary_table(rows, today),
                _detail_cards(rows, recent, today),
                _filings(recent),
                _schedule(today, market_days, events),
                _footer(bot.calendar_warning()),
            ]
        )

    def _notice_block(self) -> str:
        if self.busy:
            return f'<div class="notice busy">⏳ {esc(self.busy)} <span class="muted">— 끝나면 자동으로 새로고침됩니다</span></div>'
        if self.notice:
            text, self.notice = self.notice, None
            bad = any(word in text for word in ("❌", "오류", "실패", "거부", "찾지 못"))
            cls, icon = ("notice bad", "") if bad else ("notice", "✅ ")
            body = esc(text).replace("\n", "<br>")
            return f'<div class="{cls}">{icon}{body}</div>'
        return ""


def _plain(text: str | None) -> str:
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", text or "완료")).strip()


# --------------------------------------------------------------------------
# 머리말
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
    <p class="sub">마지막 공시 확인 {esc(last_check or "아직 없음")} · {config.poll_interval_sec // 60}분마다 자동 확인</p>
  </div>
  <div class="actions">
    <form method="post" action="/action"><input type="hidden" name="action" value="check">
      <button type="submit">🔄 공시 확인</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="metrics">
      <button type="submit">📊 지표 새로고침</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="brief">
      <button type="submit">✉️ 브리핑 보내기</button></form>
  </div>
</header>"""


# --------------------------------------------------------------------------
# 전 종목 요약 표 — 한눈에 비교
# --------------------------------------------------------------------------
SUMMARY_COLUMNS = [
    "종목", "상태", "주가", "시총", "매출(TTM)", "매출성장",
    "영업이익률", "ROE", "ROIC", "PER", "PSR", "런웨이", "체크", "실적발표",
]


def _summary_table(rows, today) -> str:
    if not rows:
        return """
<section>
  <h2>관심 종목</h2>
  <p class="muted">감시 중인 종목이 없습니다. 아래에서 티커를 입력해 추가해보세요.</p>
  {form}
</section>""".format(form=_add_form())

    head = "".join(f"<th>{esc(c)}</th>" for c in SUMMARY_COLUMNS)
    body = "".join(_summary_row(target, metrics, earnings, today) for target, metrics, earnings in rows)

    return f"""
<section>
  <h2>전체 종목 한눈에 <span class="count">{len(rows)}개</span></h2>
  <div class="scroll">
    <table class="summary">
      <thead><tr>{head}</tr></thead>
      <tbody>{body}</tbody>
    </table>
  </div>
  {_add_form()}
</section>"""


def _summary_row(target, m: Metrics | None, earnings, today) -> str:
    ticker = f'<a href="#{esc(target.ticker)}"><b>{esc(target.ticker)}</b></a>'
    if target.name:
        ticker += f'<br><span class="muted small">{esc(target.name)}</span>'

    if m is None:
        empty = "".join("<td class='num muted'>…</td>" for _ in range(len(SUMMARY_COLUMNS) - 2))
        return f"<tr><td>{ticker}</td><td class='muted'>불러오는 중</td>{empty}</tr>"

    state = (
        '<span class="badge open">흑자</span>' if m.profitable
        else ('<span class="badge closed">적자</span>' if m.profitable is False else '<span class="muted">-</span>')
    )

    price = "-"
    if m.price:
        price = f"${m.price:,.2f}"
        if m.price_change_pct is not None:
            cls = "up" if m.price_change_pct >= 0 else "down"
            price += f'<br><span class="small {cls}">{m.price_change_pct:+.2f}%</span>'

    margin = _pct(m.op_margin)
    if m.op_margin is not None and m.op_margin_prior is not None:
        up = m.op_margin > m.op_margin_prior
        margin += f' <span class="{"up" if up else "down"}">{"↑" if up else "↓"}</span>'

    growth = "-"
    if m.revenue_growth is not None:
        cls = "up" if m.revenue_growth >= 0 else "down"
        growth = f'<span class="{cls}">{m.revenue_growth:+.0%}</span>'

    passes = sum(1 for c in m.checks + m.priority if c.status == "pass")
    fails = sum(1 for c in m.checks + m.priority if c.status == "fail")
    warns = sum(1 for c in m.checks + m.priority if c.status == "warn")
    checks = f'<span class="up">✅{passes}</span> <span class="warnmark">⚠️{warns}</span> <span class="down">❌{fails}</span>'

    if earnings:
        when = f"{earnings.day.isoformat()}<br><span class='small muted'>{dday(today, earnings.day)}"
        when += " · 추정" if earnings.estimated else " · 확정"
        when += "</span>"
    elif target.watch.earnings_date:
        when = target.watch.earnings_date.isoformat()
    else:
        when = '<span class="muted">-</span>'

    cells = [
        ticker,
        state,
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
        checks,
        when,
    ]
    tds = "".join(f'<td class="num">{c}</td>' for c in cells[1:])
    return f"<tr><td>{cells[0]}</td>{tds}</tr>"


def _add_form() -> str:
    return """
  <form method="post" action="/action" class="addform">
    <input type="hidden" name="action" value="add">
    <input type="text" name="ticker" placeholder="티커 입력 (예: TSLA, 또는 TSLA:1318605)" maxlength="24"
           autocomplete="off" required>
    <button type="submit">+ 종목 추가</button>
  </form>"""


# --------------------------------------------------------------------------
# 종목별 상세
# --------------------------------------------------------------------------
def _detail_cards(rows, recent, today) -> str:
    if not rows:
        return ""
    cards = "".join(_detail_card(t, m, e, recent, today) for t, m, e in rows)
    return f'<section><h2>종목별 상세</h2><div class="cards">{cards}</div></section>'


def _detail_card(target, m: Metrics | None, earnings, recent, today) -> str:
    parts = [f'<div class="card" id="{esc(target.ticker)}">']

    # 머리
    title = f'<h3>{esc(target.ticker)}</h3>'
    if m and m.price:
        change = ""
        if m.price_change_pct is not None:
            cls = "up" if m.price_change_pct >= 0 else "down"
            change = f' <span class="{cls}">{m.price_change_pct:+.2f}%</span>'
        title += f'<span class="price">${m.price:,.2f}{change}</span>'
    parts.append(f'<div class="card-head">{title}' + _remove_button(target.ticker) + "</div>")

    subtitle = target.name or (m.company if m else "")
    if subtitle:
        parts.append(f'<p class="sub">{esc(subtitle)}</p>')

    if m is None:
        parts.append('<p class="muted">정보를 불러오는 중입니다…</p></div>')
        return "".join(parts)

    if m.warnings:
        for warning in m.warnings:
            parts.append(f'<p class="warn">⚠️ {esc(warning)}</p>')

    # 숫자 묶음
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
        ("EPS TTM", f"${m.eps_ttm:,.2f}" if m.eps_ttm else "-"),
        ("주식수", f"{m.shares / 1e6:,.0f}M" if m.shares else "-"),
        ("기준 분기", m.as_of.isoformat() if m.as_of else "-"),
    ]
    parts.append(
        '<dl class="stats">'
        + "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in stats)
        + "</dl>"
    )

    # 분기 매출 추이
    if len(m.quarterly_revenue) >= 2:
        parts.append(_revenue_bars(m.quarterly_revenue))

    # 실적 발표일
    if earnings:
        kind = "추정" if earnings.estimated else "확정"
        parts.append(
            f'<p class="line">📆 <b>실적 발표</b> {esc(kdate(earnings.day))} '
            f'<span class="muted">({esc(dday(today, earnings.day))}, {kind})</span></p>'
        )

    # 체크리스트
    parts.append('<h4>우선순위 판단</h4><ul class="checks">')
    for check in m.priority:
        parts.append(_check_item(check))
    parts.append("</ul>")

    state = "흑자" if m.profitable else ("적자" if m.profitable is False else "판단 불가")
    parts.append(f'<h4>{esc(state)} 기업 체크리스트</h4><ul class="checks">')
    for check in m.checks:
        parts.append(_check_item(check))
    parts.append("</ul>")

    if m.peers:
        peer_rows = []
        for ticker, peer in m.peers.items():
            bits = []
            if peer.get("per"):
                bits.append(f"PER {peer['per']:.1f}x")
            if peer.get("psr"):
                bits.append(f"PSR {peer['psr']:.1f}x")
            if peer.get("op_margin") is not None:
                bits.append(f"영업이익률 {_pct(peer['op_margin'])}")
            peer_rows.append(f"<li><b>{esc(ticker)}</b> <span class='detail'>{esc(' · '.join(bits) or '데이터 없음')}</span></li>")
        parts.append("<h4>동종업계 비교</h4><ul class='checks'>" + "".join(peer_rows) + "</ul>")

    if target.watch.milestones:
        items = "".join(f"<li>{esc(x)}</li>" for x in target.watch.milestones)
        parts.append(f"<h4>핵심 마일스톤</h4><ul class='bullets'>{items}</ul>")

    mine = [r for r in recent if r.get("ticker") == target.ticker][:3]
    if mine:
        items = "".join(
            f'<li><span class="when">{esc(r.get("when", ""))}</span> '
            f'<span class="tag">{esc(r.get("form", ""))}</span> '
            f'<a href="{esc(r.get("url", "#"))}" target="_blank" rel="noopener">{esc(r.get("title", ""))}</a></li>'
            for r in mine
        )
        parts.append(f"<h4>최근 공시</h4><ul class='bullets'>{items}</ul>")

    parts.append("</div>")
    return "".join(parts)


def _check_item(check) -> str:
    icon = STATUS_ICON.get(check.status, "•")
    return (
        f'<li class="st-{esc(check.status)}"><span class="icon">{icon}</span>'
        f'<b>{esc(check.label)}</b><span class="detail">{esc(check.detail)}</span></li>'
    )


def _remove_button(ticker: str) -> str:
    return (
        '<form method="post" action="/action" onsubmit="return confirm(\'감시 목록에서 뺄까요?\')">'
        '<input type="hidden" name="action" value="remove">'
        f'<input type="hidden" name="ticker" value="{esc(ticker)}">'
        '<button type="submit" class="ghost" title="빼기">✕</button></form>'
    )


def _revenue_bars(quarters) -> str:
    """분기 매출 추이를 간단한 막대로. (외부 라이브러리 없이 CSS 만)"""
    values = [v for _, v in quarters][-8:]
    labels = [d for d, _ in quarters][-8:]
    peak = max((abs(v) for v in values), default=0)
    if not peak:
        return ""
    bars = []
    for day, value in zip(labels, values):
        height = max(4, round(abs(value) / peak * 56))
        cls = "bar" if value >= 0 else "bar neg"
        bars.append(
            f'<div class="barwrap" title="{esc(day.isoformat())} · {esc(_money(value))}">'
            f'<div class="{cls}" style="height:{height}px"></div>'
            f'<span class="barlabel">{esc(str(day.year)[2:])}.{day.month:02d}</span></div>'
        )
    return f'<div class="chart"><span class="chart-title">분기 매출 추이</span><div class="bars">{"".join(bars)}</div></div>'


# --------------------------------------------------------------------------
# 공시 · 일정
# --------------------------------------------------------------------------
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


def _footer(warning) -> str:
    warn = f'<p class="warn">⚠️ {esc(warning)}</p>' if warning else ""
    return f"""
<footer>
  {warn}
  <p class="muted">데이터: SEC EDGAR·XBRL 공시 + Stooq 종가(실시간 아님). 참고용이며 투자 판단의 책임은 본인에게 있습니다.</p>
  <p class="muted">이 화면은 내 컴퓨터에서만 열립니다 (127.0.0.1). 창을 닫아도 봇은 계속 돕니다.</p>
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
        message = self.dashboard.run_action((params.get("action") or [""])[0], params)
        if not self.dashboard.busy:
            self.dashboard.notice = message
        self.send_response(303)
        self.send_header("Location", "/")
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
    """대시보드를 백그라운드 스레드로 띄우고 서버 객체를 돌려준다."""
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


_PAGE = """<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>관심 종목 감시</title>
<style>
:root {{
  --bg:#f6f7f9; --fg:#1b1d21; --muted:#6b7280; --card:#ffffff; --line:#e5e7eb;
  --accent:#2563eb; --good:#15803d; --bad:#b91c1c; --alert:#b45309; --zebra:#fafbfc;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:#14161a; --fg:#e8eaed; --muted:#9aa1ab; --card:#1c1f24; --line:#2b2f36;
    --accent:#60a5fa; --good:#4ade80; --bad:#f87171; --alert:#fbbf24; --zebra:#191c21;
  }}
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; padding:24px; background:var(--bg); color:var(--fg);
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",
              "Noto Sans KR",Segoe UI,sans-serif; line-height:1.55;
}}
h1 {{ font-size:1.5rem; margin:0 0 4px; }}
h2 {{ font-size:1.05rem; margin:30px 0 12px; }}
h3 {{ font-size:1.2rem; margin:0; }}
h4 {{ font-size:.82rem; margin:14px 0 6px; color:var(--muted); font-weight:600;
      text-transform:none; letter-spacing:.02em; }}
p {{ margin:2px 0; }}
a {{ color:var(--accent); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
.sub {{ color:var(--muted); font-size:.85rem; }}
.muted {{ color:var(--muted); }}
.small {{ font-size:.75rem; }}
.count {{ color:var(--muted); font-weight:400; font-size:.85rem; }}
.up {{ color:var(--good); }}
.down {{ color:var(--bad); }}
.warnmark {{ color:var(--alert); }}
.warn {{ color:var(--alert); font-size:.85rem; }}
header {{ display:flex; flex-wrap:wrap; gap:16px; justify-content:space-between; align-items:flex-start; }}
.actions {{ display:flex; flex-wrap:wrap; gap:8px; }}
button {{
  font:inherit; padding:8px 14px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer;
}}
button:hover {{ border-color:var(--accent); color:var(--accent); }}
button.ghost {{ border:none; background:none; color:var(--muted); padding:2px 6px; }}
.badge {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:.78rem; white-space:nowrap; }}
.badge.open {{ background:rgba(21,128,61,.14); color:var(--good); }}
.badge.closed {{ background:rgba(185,28,28,.14); color:var(--bad); }}
.notice {{ margin:16px 0; padding:10px 14px; border-radius:8px; background:var(--card); border:1px solid var(--line); }}
.notice.busy {{ border-color:var(--alert); color:var(--alert); }}
.notice.bad {{ border-color:var(--bad); color:var(--bad); }}

.scroll {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; background:var(--card); }}
table.summary {{ border-collapse:collapse; width:100%; font-size:.86rem; white-space:nowrap; }}
table.summary th {{
  text-align:right; padding:10px 12px; font-size:.74rem; color:var(--muted);
  font-weight:600; border-bottom:1px solid var(--line); position:sticky; top:0; background:var(--card);
}}
table.summary th:first-child {{ text-align:left; }}
table.summary td {{ padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:middle; }}
table.summary td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
table.summary tbody tr:nth-child(even) {{ background:var(--zebra); }}
table.summary tbody tr:hover {{ background:rgba(37,99,235,.06); }}
table.summary tbody tr:last-child td {{ border-bottom:none; }}

.addform {{ display:flex; gap:8px; margin-top:14px; }}
.addform input {{
  font:inherit; padding:8px 12px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); flex:0 1 240px;
}}
.cards {{ display:grid; gap:14px; align-items:start; grid-template-columns:repeat(auto-fill,minmax(370px,1fr)); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; scroll-margin-top:16px; }}
.card-head {{ display:flex; justify-content:space-between; align-items:center; gap:10px; }}
.card-head .price {{ margin-left:auto; font-size:1rem; font-weight:600; font-variant-numeric:tabular-nums; }}
.line {{ font-size:.85rem; margin:10px 0; }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(104px,1fr)); gap:8px; margin:12px 0 0; }}
.stats div {{ background:var(--bg); border-radius:8px; padding:6px 9px; }}
.stats dt {{ font-size:.7rem; color:var(--muted); }}
.stats dd {{ margin:0; font-size:.9rem; font-weight:600; font-variant-numeric:tabular-nums; }}
.chart {{ margin:14px 0 4px; }}
.chart-title {{ font-size:.7rem; color:var(--muted); }}
.bars {{ display:flex; align-items:flex-end; gap:6px; height:74px; margin-top:6px; }}
.barwrap {{ flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%; }}
.bar {{ width:100%; background:var(--accent); border-radius:3px 3px 0 0; opacity:.75; }}
.bar.neg {{ background:var(--bad); }}
.barlabel {{ font-size:.62rem; color:var(--muted); margin-top:3px; }}
.checks {{ list-style:none; padding:0; margin:0; font-size:.84rem; }}
.checks li {{ display:flex; gap:6px; padding:6px 0; border-top:1px solid var(--line); flex-wrap:wrap; }}
.checks li:first-child {{ border-top:none; }}
.checks .detail {{ color:var(--muted); flex:1 1 100%; font-size:.8rem; }}
.checks li.st-fail b {{ color:var(--bad); }}
.checks li.st-pass b {{ color:var(--good); }}
.bullets {{ margin:0; padding-left:18px; font-size:.82rem; color:var(--muted); }}
.bullets li {{ padding:2px 0; }}
ul.filings, ul.plain {{ list-style:none; padding:0; margin:0; }}
ul.filings li, ul.plain li {{
  padding:8px 10px; border-bottom:1px solid var(--line); font-size:.88rem;
  display:flex; gap:8px; align-items:baseline; flex-wrap:wrap;
}}
ul.filings li.tone-alert {{ border-left:3px solid var(--alert); }}
ul.filings li.tone-good {{ border-left:3px solid var(--good); }}
ul.filings li.tone-bad {{ border-left:3px solid var(--bad); }}
.when {{ color:var(--muted); font-variant-numeric:tabular-nums; font-size:.8rem; min-width:118px; }}
.tag {{ font-size:.7rem; padding:1px 7px; border-radius:999px; background:var(--bg); border:1px solid var(--line); }}
.detail {{ color:var(--muted); }}
.two-col {{ display:grid; gap:24px; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }}
footer {{ margin-top:32px; padding-top:16px; border-top:1px solid var(--line); font-size:.8rem; }}
@media (max-width:600px) {{
  body {{ padding:14px; }}
  .cards {{ grid-template-columns:1fr; }}
}}
</style></head>
<body>
{body}
</body></html>
"""
