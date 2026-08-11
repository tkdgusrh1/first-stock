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
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .assessment import LEVEL_ICON, LEVEL_LABEL
from .econ_calendar import parse_extra_events, upcoming_events
from .glossary import BY_KEY, LABEL_TO_KEY, groups
from .market_calendar import upcoming_market_days
from .metrics import STATUS_ICON, Metrics, _money, _pct
from .timeutil import dday, kdate, now

log = logging.getLogger(__name__)

TONE_CLASS = {"alert": "tone-alert", "good": "tone-good", "bad": "tone-bad", "plain": "tone-plain"}
LOCK_TIMEOUT = 0.4


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def term(label: str) -> str:
    """용어에 설명 툴팁과 사전 링크를 붙인다."""
    key = LABEL_TO_KEY.get(label.strip())
    entry = BY_KEY.get(key) if key else None
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

    # --- 동작 -----------------------------------------------------------
    def run_action(self, action: str, params: dict) -> str:
        one = lambda name: (params.get(name) or [""])[0].strip()  # noqa: E731

        if action == "check":
            return self._background("공시를 확인하는 중…", self._do_check)
        if action == "metrics":
            return self._background("지표를 계산하는 중…", self._do_metrics)
        if action == "brief":
            return self._background("브리핑을 보내는 중…", self._do_brief)
        if action == "reports":
            return self._background("분기보고서를 읽는 중… (종목당 20초쯤)", self._do_reports)
        if action == "add":
            raw = " ".join(one("ticker").split())
            if not raw:
                return "티커를 입력해주세요."
            ticker = raw.split(":")[0].split()[0].upper()
            return self._background(f"{ticker} 를 추가하는 중…", lambda: self._do_add(raw, ticker))
        if action == "remove":
            ticker = one("ticker").upper()
            return self._background(f"{ticker} 를 빼는 중…", lambda: self._do_remove(ticker))
        if action == "consensus":
            return self._set_consensus(one("ticker"), one("eps"), one("revenue"))
        if action == "memo":
            return self._set_memo(one("ticker"), one("memo"))
        return "알 수 없는 동작입니다."

    def _background(self, message: str, func) -> str:
        if self.busy:
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

    def _do_metrics(self, force: bool = True) -> str:
        done, failed = self.bot.ensure_all_metrics(force=force)
        if not done and not failed:
            return "새로 계산할 종목이 없습니다."
        message = f"{done}개 종목 지표를 계산했습니다."
        if failed:
            message += f" 실패: {', '.join(failed)} — 잠시 뒤 다시 시도해보세요."
        return message

    def _do_fill(self) -> str:
        done, failed = self.bot.ensure_all_metrics(force=False)
        return f"{done}개 종목 정보를 불러왔습니다." + (f" 실패: {', '.join(failed)}" if failed else "")

    def _do_reports(self) -> str:
        loaded, missing, guided = 0, [], 0
        for target in self.bot.targets():
            report = self.bot.report_for(target, refresh=True)
            if report and report.sections:
                loaded += 1
            else:
                missing.append(target.ticker)
            # 가이던스와 업종도 같이 채운다 (같은 공시를 다시 받지 않도록 한 번에)
            guidance = self.bot.guidance_for(target, refresh=True)
            if guidance and guidance.found:
                guided += 1
            self.bot.industry_for(target, refresh=True)
        message = f"보고서 {loaded}개, 가이던스 {guided}개를 읽었습니다."
        if missing:
            message += f" 본문을 찾지 못한 종목: {', '.join(missing)}"
        return message

    def _do_brief(self) -> str:
        self.bot.daily_brief(force=True)
        if self.bot.notifier.dry_run:
            return "텔레그램이 꺼져 있어 콘솔에만 출력했습니다."
        return "텔레그램으로 브리핑을 보냈습니다."

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

    def autofill_if_needed(self) -> None:
        if self.busy:
            return
        missing = self.bot.missing_metrics()
        if missing:
            names = ", ".join(t.ticker for t in missing[:3])
            more = f" 외 {len(missing) - 3}개" if len(missing) > 3 else ""
            self._background(f"{names}{more} 정보를 불러오는 중…", self._do_fill)

    # --- 화면 -----------------------------------------------------------
    def render(self) -> str:
        self.autofill_if_needed()
        if self.lock.acquire(timeout=LOCK_TIMEOUT):
            try:
                body = self._build_body()
                self._last_body = body
            finally:
                self.lock.release()
        else:
            body = self._last_body or _loading_body()

        page = _PAGE.format(body=body, refresh=4 if self.busy else 90)
        return page.replace("<!--NOTICE-->", self._notice_block(), 1)

    def _build_body(self) -> str:
        bot = self.bot
        config = bot.config
        today = now(config.timezone).date()
        targets = bot.targets()

        metrics = bot.cached_metrics()
        earnings = bot.cached_earnings()
        reports = bot.cached_reports()
        errors = bot.metrics_errors()
        guidance = bot.cached_guidance()
        estimates = bot.cached_estimates()
        industries = bot.cached_industries()
        assessments = {t.cik: bot.assessment_for(t) for t in targets}

        market_days = upcoming_market_days(today, max(config.holiday_lookahead_days, 30))
        events = upcoming_events(
            today,
            max(config.econ_lookahead_days, 21),
            min_importance=int(config.raw.get("econ_min_importance", 2)),
            extra=parse_extra_events(config.raw.get("econ_extra_events")),
            include_weekly=bool(config.raw.get("econ_include_weekly", False)),
        )
        recent = bot.state.recent(40)

        rows = [
            (t, metrics.get(t.cik), earnings.get(t.cik), assessments.get(t.cik))
            for t in targets
        ]
        return "\n".join(
            [
                _header(today, market_days, bot.state.last_check(), config),
                "<!--NOTICE-->",
                _summary_table(rows, today, errors),
                _detail_cards(rows, recent, today, errors, reports, guidance, estimates, industries),
                _filings(recent),
                _schedule(today, market_days, events),
                _glossary_section(),
                _footer(bot.calendar_warning()),
            ]
        )

    def _notice_block(self) -> str:
        if self.busy:
            return (
                f'<div class="notice busy">⏳ {esc(self.busy)} '
                '<span class="muted">— 끝나면 자동으로 새로고침됩니다</span></div>'
            )
        if self.notice:
            text, self.notice = self.notice, None
            bad = any(word in text for word in ("❌", "오류", "실패", "거부", "찾지 못"))
            cls, icon = ("notice bad", "") if bad else ("notice", "✅ ")
            return f'<div class="{cls}">{icon}{esc(text)}</div>'
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
    <p class="sub">마지막 공시 확인 {esc(last_check or "아직 없음")} ·
       {config.poll_interval_sec // 60}분마다 자동 확인 ·
       <a href="#glossary">용어 사전</a></p>
  </div>
  <div class="actions">
    <form method="post" action="/action"><input type="hidden" name="action" value="check">
      <button type="submit">🔄 공시 확인</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="metrics">
      <button type="submit">📊 지표 새로고침</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="reports">
      <button type="submit">📄 보고서 읽기</button></form>
    <form method="post" action="/action"><input type="hidden" name="action" value="brief">
      <button type="submit">✉️ 브리핑 보내기</button></form>
  </div>
</header>"""


# --------------------------------------------------------------------------
# 전 종목 요약 표
# --------------------------------------------------------------------------
SUMMARY_COLUMNS = [
    "종목", "상황", "주가", "시총", "매출(TTM)", "매출성장",
    "영업이익률", "ROE", "ROIC", "PER", "PSR", "런웨이", "체크", "실적발표",
]


def _summary_table(rows, today, errors=None) -> str:
    if not rows:
        return f"""
<section>
  <h2>관심 종목</h2>
  <p class="muted">감시 중인 종목이 없습니다. 아래에서 티커를 입력해 추가해보세요.</p>
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
    <input type="text" name="ticker" placeholder="티커 입력 (예: TSLA, 또는 TSLA:1318605)"
           maxlength="24" autocomplete="off" required>
    <button type="submit">+ 종목 추가</button>
  </form>"""


# --------------------------------------------------------------------------
# 종목별 상세
# --------------------------------------------------------------------------
def _detail_cards(rows, recent, today, errors, reports, guidance, estimates, industries) -> str:
    if not rows:
        return ""
    cards = "".join(
        _detail_card(
            t, m, e, a, recent, today, errors.get(t.cik), reports.get(t.cik),
            guidance.get(t.cik), estimates.get(t.cik), industries.get(t.cik),
        )
        for t, m, e, a in rows
    )
    return f'<section><h2>종목별 상세</h2><div class="stack">{cards}</div></section>'


def _detail_card(target, m, earnings, verdict, recent, today, error, report,
                 guidance=None, estimate=None, industry=None) -> str:
    parts = [f'<article class="card wide" id="{esc(target.ticker)}">']

    title = f'<h3>{esc(target.ticker)}</h3>'
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
        title += f'<span class="price">${m.price:,.2f}{change} {state}{extended}</span>'
    parts.append(f'<div class="card-head">{title}{_remove_button(target.ticker)}</div>')

    subtitle = target.name or (m.company if m else "")
    if subtitle:
        parts.append(f'<p class="sub">{esc(subtitle)} · CIK {esc(target.cik)}</p>')

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
        parts.append("</article>")
        return "".join(parts)

    if m.warnings:
        for warning in m.warnings:
            parts.append(f'<p class="warn">⚠️ {esc(warning)}</p>')

    parts.append(_assessment_block(verdict))
    parts.append('<div class="group"><div class="group-title">📊 숫자와 추이</div>')
    parts.append(_numbers_block(m))
    parts.append(_trends_block(m))
    parts.append("</div>")

    if earnings:
        kind = "추정" if earnings.estimated else "확정"
        parts.append(
            f'<p class="line">📆 <b>실적 발표</b> {esc(kdate(earnings.day))} '
            f'<span class="muted">({esc(dday(today, earnings.day))}, {kind})</span></p>'
        )

    parts.append('<div class="group"><div class="group-title">🎯 메모 기준 판단</div>')
    parts.append(_checks_block(m))
    parts.append(_guidance_block(guidance))
    parts.append(_consensus_block(target, m, estimate))
    parts.append(_milestones_block(target))
    parts.append("</div>")

    parts.append('<div class="group"><div class="group-title">📄 회사가 밝힌 내용</div>')
    parts.append(_report_block(report))
    parts.append("</div>")

    parts.append('<div class="group"><div class="group-title">🔍 비교와 기록</div>')
    parts.append(_peers_block(m, industry))
    parts.append(_filings_for(target, recent))
    parts.append(_memo_block(target))
    parts.append(_inputs_block(target, m))
    parts.append(_sources_block(m))
    parts.append("</div>")

    parts.append("</article>")
    return "".join(parts)


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
        ("EPS TTM", f"${m.eps_ttm:,.2f}" if m.eps_ttm else "-"),
        ("주식수", f"{m.shares / 1e6:,.0f}M" if m.shares else "-"),
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


def _report_block(report) -> str:
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
        items = "".join(f"<li>{esc(sentence)}</li>" for sentence in report.company_words)
        blocks.append(
            '<details open><summary>회사가 직접 밝힌 내용 '
            f'<span class="muted small">{len(report.company_words)}문장</span></summary>'
            f'<ul class="quotes">{items}</ul></details>'
        )

    for section in report.sections:
        preview = section.paragraphs[:6]
        body = "".join(f"<p class='quote'>{esc(p)}</p>" for p in preview)
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
        '<h4>분기보고서 내용 <span class="muted small">원문 발췌 (요약·가공 없음)</span></h4>'
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


def _guidance_block(guidance) -> str:
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
                tags.append(f'<span class="tag">{esc(item.period)}</span>')
            value = f'<b class="gv">{esc(item.range_text)}</b>' if item.range_text else ""
            rows.append(
                f'<li><div class="g-line">{"".join(tags)} {value}</div>'
                f'<p class="quote">{esc(item.sentence)}</p></li>'
            )
        body = f'<ul class="guidance">{"".join(rows)}</ul>'

    results = ""
    if guidance.results:
        items = "".join(f'<li class="quote">{esc(s)}</li>' for s in guidance.results)
        results = (
            f'<details><summary>발표문의 실적 설명 {len(guidance.results)}문장</summary>'
            f'<ul class="quotes">{items}</ul></details>'
        )

    caution = (
        '<p class="hint">⚠️ 가이던스는 회사가 관리할 수 있는 숫자입니다(낮게 부르기·정의 변경 등). '
        '<b>과거에 제시한 가이던스를 실제로 지켰는지</b> 이력과 현금흐름표를 함께 확인하세요.</p>'
    )
    return (
        '<h4>가이던스 <span class="muted small">메모 1순위 · 원문 발췌</span></h4>'
        + header + body + results + caution
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
    """직접 넣어야 정확해지는 값들 (컨센서스·메모)."""
    watch = target.watch
    eps = watch.consensus_eps if watch.consensus_eps is not None else ""
    revenue = watch.consensus_revenue if watch.consensus_revenue is not None else ""
    memo = watch.note or ""

    surprise_hint = ""
    if m.surprise is None:
        surprise_hint = (
            '<p class="hint">컨센서스를 넣어두면 실적 발표 직후 '
            '<b>어닝 서프라이즈(메모 2순위)</b>를 자동으로 계산합니다.</p>'
        )

    return f"""
<details class="inputs"><summary>직접 입력 (컨센서스 · 메모)</summary>
  {surprise_hint}
  <form method="post" action="/action" class="inline">
    <input type="hidden" name="action" value="consensus">
    <input type="hidden" name="ticker" value="{esc(target.ticker)}">
    <label>EPS 컨센서스<input type="text" name="eps" value="{esc(eps)}" placeholder="예: 1.01"></label>
    <label>매출 컨센서스<input type="text" name="revenue" value="{esc(revenue)}" placeholder="예: 45000000000"></label>
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
    """숫자를 어디서 가져왔는지. 직접 확인할 수 있어야 한다."""
    if not m.sources:
        return ""
    items = "".join(f"<li>{esc(v)}</li>" for v in m.sources.values())
    return (
        '<details class="sources"><summary>이 숫자들의 출처</summary>'
        f'<ul class="bullets">{items}</ul>'
        '<p class="muted small">모든 재무 수치는 SEC에 제출된 XBRL 원본에서 계산했습니다. '
        '수정 공시가 있으면 가장 나중에 제출된 값을 씁니다.</p></details>'
    )


def _remove_button(ticker: str) -> str:
    return (
        '<form method="post" action="/action" onsubmit="return confirm(\'감시 목록에서 뺄까요?\')">'
        '<input type="hidden" name="action" value="remove">'
        f'<input type="hidden" name="ticker" value="{esc(ticker)}">'
        '<button type="submit" class="ghost" title="빼기">✕</button></form>'
    )


# --------------------------------------------------------------------------
# 공시 · 일정 · 사전
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
        blocks.append(f'<div class="g-group"><h3>{esc(group)}</h3>{"".join(items)}</div>')

    return f"""
<section id="glossary">
  <h2>용어 사전</h2>
  <p class="hint">화면에 나오는 지표가 무엇이고, 어떻게 계산했고, 무엇을 조심해야 하는지 모아뒀습니다.</p>
  <details open><summary>펼치기 / 접기</summary><div class="glossary">{"".join(blocks)}</div></details>
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
  --quote:#f3f4f6;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg:#14161a; --fg:#e8eaed; --muted:#9aa1ab; --card:#1c1f24; --line:#2b2f36;
    --accent:#60a5fa; --good:#4ade80; --bad:#f87171; --alert:#fbbf24; --zebra:#191c21;
    --quote:#22262c;
  }}
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
button {{ font:inherit; padding:8px 14px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; }}
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
.stack {{ display:flex; flex-direction:column; gap:18px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px;
  scroll-margin-top:16px; }}
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
.checks li.st-fail b {{ color:var(--bad); }}
.checks li.st-pass b {{ color:var(--good); }}
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

.glossary {{ display:grid; gap:18px; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }}
.g-group h3 {{ font-size:.9rem; color:var(--muted); margin-bottom:8px; }}
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
