"""홈 — 관심 종목 한눈에.

사람이 목록에서 찾는 건 셋이다: **어느 회사인가, 올랐나 내렸나, 얼마나.**
그래서 기본은 가격 목록이고, 재무 지표 열네 칸은 '지표 표' 로 바꿔 본다.
오른쪽에는 시장·다가오는 일정·최근 공시·발굴 요약을 둔다 — 각각 전용 쪽이
따로 있고, 여기서는 '지금 볼 것이 있나' 만 알려준다.
"""

from __future__ import annotations

from datetime import timedelta

from .. import markets, screener, visuals
from ..metrics import _money, _pct
from ..news import short_name
from ..timeutil import dday
from . import events as ev
from .kit import (
    news_item,
    card, change_html, esc, filing_item, icon, mark, more_link, page_head, parse_when, price_text, spark_for, stock_url, term, trade_time, verdict_chip,
    when_ago,
)
from .shell import with_market

SUMMARY_COLUMNS = [
    "종목", "상황", "주가", "흐름", "시총", "매출(TTM)", "매출성장",
    "영업이익률", "ROE", "ROIC", "PER", "PSR", "런웨이", "체크", "실적발표",
]


def summary_columns(market: str) -> list[str]:
    """표 머리글. 한국은 DART 사업보고서의 연간 확정치라 TTM 이 아니다."""
    if market != markets.KR:
        return SUMMARY_COLUMNS
    return ["매출(연간)" if c == "매출(TTM)" else c for c in SUMMARY_COLUMNS]


def render(ctx, unresolved: list[str], errors: dict) -> str:
    rows = ctx.mine
    head = page_head(
        "관심 종목",
        f"{markets.MARKET_NAME[ctx.market]} · {len(rows)}개 · 주가는 20초마다 받고 화면은 5초마다 숫자만 바뀝니다",
        '<div class="seg" data-view-switch>'
        f'<button type="button" class="on" data-view="list">{icon("list", True)} 목록</button>'
        f'<button type="button" data-view="table">{icon("table", True)} 지표 표</button></div>',
    )
    main = [watch_card(ctx, rows, unresolved, errors), watch_news_card(ctx)]
    if ctx.korean:
        main.append(korean_limits(ctx.bot))
    side = [market_card(ctx), events_card(ctx), filings_card(ctx), picks_card(ctx)]
    return (head + headline_bar(ctx)
            + f'<div class="grid"><div class="col">{"".join(main)}</div>'
            f'<div class="col">{"".join(s for s in side if s)}</div></div>')


# --------------------------------------------------------------------------
# 주요 속보 띠 — 저장된 속보 가운데 중요한 것만, 새 것부터
# --------------------------------------------------------------------------
def headline_bar(ctx) -> str:
    news = [n for n in ctx.bot.state.news(40) if int(n.get("severity", 1) or 1) >= 2]
    news.sort(key=lambda n: (int(n.get("severity", 1) or 1), str(n.get("when") or "")), reverse=True)
    news = news[:10]
    if not news:
        return ""
    items = []
    for i, entry in enumerate(news):
        moment = parse_when(entry.get("when"))
        publisher = entry.get("publisher") or entry.get("source") or ""
        url = entry.get("url") or with_market("/news", ctx.market)
        items.append(
            f'<div class="hb-item{" on" if i == 0 else ""}">'
            + (f'<span class="hb-src">{esc(publisher)}</span>' if publisher else "")
            + f'<a class="hb-title" href="{esc(url)}" target="_blank" rel="noopener">'
            f'{esc(entry.get("title_ko") or entry.get("title", ""))}</a>'
            f'<span class="hb-when">{esc(when_ago(moment))}</span></div>'
        )
    nav = ""
    if len(items) > 1:
        nav = ('<div class="hb-nav"><button type="button" data-hb="-1" aria-label="이전">'
               f'{icon("chev-l", True)}</button><span data-hb-pos>1 / {len(items)}</span>'
               f'<button type="button" data-hb="1" aria-label="다음">{icon("chev-r", True)}</button></div>')
    return (f'<div class="headline-bar" data-headlines><span class="hb-label">🔥 주요 속보</span>'
            f'{"".join(items)}{nav}</div>')


# --------------------------------------------------------------------------
# 관심 종목 카드 — 목록 보기 · 지표 표 보기
# --------------------------------------------------------------------------
def watch_card(ctx, rows, unresolved, errors) -> str:
    sort = ('<div class="chips" data-sort>'
            '<button type="button" class="chip on" data-sort-by="none">기본</button>'
            '<button type="button" class="chip" data-sort-by="up">상승률</button>'
            '<button type="button" class="chip" data-sort-by="down">하락률</button>'
            '<button type="button" class="chip" data-sort-by="name">이름</button></div>')
    if not rows:
        if unresolved:
            names = ", ".join(esc(t) for t in unresolved[:8])
            body = (f'<div class="empty-box">⚠️ 설정에 있는 <b>{names}</b> 를 SEC 에서 찾지 못했습니다.<br>'
                    '대개 SEC 접속이 막힌 경우입니다(공유기·백신·VPN·회사망). 잠시 뒤 자동으로 다시 시도합니다.<br>'
                    '계속 이러면 <code>program/logs/실행기록.log</code> 를 확인해주세요.</div>')
        else:
            body = '<div class="empty-box">아직 관심 종목이 없어요. 아래에서 티커나 회사 이름으로 추가해보세요.</div>'
        return card(body + add_block(ctx), "관심 종목", cls="watch")

    list_rows = "".join(_row(ctx, t, errors.get(t.cik)) for t in rows)
    table = summary_table(ctx, rows, errors)
    body = (f'<div class="rows" data-view-pane="list">{list_rows}</div>'
            f'<div class="table-wrap hide" data-view-pane="table">{table}</div>')
    return card(body + add_block(ctx), "관심 종목", f"{len(rows)}개", sort, cls="watch")


def _row(ctx, target, error=None) -> str:
    m = ctx.metric(target)
    t = target.ticker
    name = short_name(target.watch.name or target.name or (m.company if m else "")) or markets.display(t)
    sub = markets.display(t)
    fund = getattr(m, "fund", None) if m else None
    if fund is not None:
        sub += f" · {fund.risk_label}"
    elif m is not None and m.profitable is not None:
        sub += " · 흑자" if m.profitable else " · 적자"

    if m is None:
        state = ('<span class="tag down">불러오기 실패</span>' if error
                 else '<span class="tag">불러오는 중…</span>')
        price = '<span class="muted">…</span>'
        return (f'<a class="row pending" href="{esc(stock_url(t))}" data-name="{esc(name)}">'
                f'{mark(t, name)}<div class="r-name"><b>{esc(name)}</b><span>{esc(sub)}</span></div>'
                f'<div class="r-state">{state}</div><div class="r-spark"></div>'
                f'<div class="r-price">{price}</div><span class="r-go">{icon("chev-r", True)}</span></a>')

    key = esc(t)
    chg = "" if m.price_change_pct is None else f"{m.price_change_pct:.6f}"
    tone = ""
    if m.price_change_pct:
        tone = " r-up" if m.price_change_pct > 0 else " r-down"     # 왼쪽 띠 색
    return (
        f'<a class="row{tone}" href="{esc(stock_url(t))}" data-name="{esc(name)}" data-chg="{chg}" '
        f'data-t="{esc(t)}">'
        f'{mark(t, name)}'
        f'<div class="r-name"><b>{esc(name)}</b><span>{esc(sub)}</span></div>'
        f'<div class="r-state">{verdict_chip(ctx.verdict(target))}</div>'
        f'<div class="r-spark" data-live="spark" data-t="{key}">{spark_for(m)}</div>'
        f'<div class="r-price"><b data-live="price" data-t="{key}">{esc(price_text(m))}</b>'
        f'<span class="r-move" data-live="change" data-t="{key}">{change_html(m)}</span>'
        f'<span class="r-time" data-live="time" data-t="{key}" title="이 가격이 거래된 시각">'
        f'{esc(trade_time(m))}</span></div>'
        f'<span class="r-go">{icon("chev-r", True)}</span></a>'
    )


def add_block(ctx) -> str:
    """관심 종목 추가. 큰 단추를 누르면 입력칸이 열린다."""
    return (
        '<div class="card-body" data-add>'
        f'<button type="button" class="btn primary block" data-add-open>{icon("plus")} 관심 종목 추가하기</button>'
        '<form method="post" action="/action" class="form-row hide" data-add-form style="margin-top:12px">'
        '<input type="hidden" name="action" value="add">'
        f'<input type="hidden" name="back" value="{esc(ctx.here)}">'
        '<input class="field" style="flex:1" type="text" name="ticker" '
        'placeholder="티커 또는 회사 이름 (예: TSLA · 삼성전자 · 005930)" maxlength="24" autocomplete="off" required>'
        '<button type="submit" class="btn primary">추가</button></form></div>'
    )


def summary_table(ctx, rows, errors) -> str:
    columns = summary_columns(ctx.market)
    head = "".join(f"<th>{term(c)}</th>" for c in columns)
    body = "".join(_table_row(ctx, t, errors.get(t.cik)) for t in rows)
    return f'<table class="tbl"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _table_row(ctx, target, error=None) -> str:
    m = ctx.metric(target)
    name = short_name(target.watch.name or target.name or "")
    first = (f'<a class="tk-cell" href="{esc(stock_url(target.ticker))}">{mark(target.ticker, name, "sm")}'
             f'<span><b>{esc(markets.display(target.ticker))}</b><span>{esc(name)}</span></span></a>')
    width = len(SUMMARY_COLUMNS) - 1
    if m is None:
        state = "불러오기 실패" if error else "불러오는 중…"
        return f'<tr><td>{first}</td><td class="l muted" colspan="{width}">{state}</td></tr>'

    key = esc(target.ticker)
    price = (f'<b data-live="price" data-t="{key}">{esc(price_text(m))}</b><br>'
             f'<span class="small" data-live="change" data-t="{key}">{change_html(m)}</span>')
    trend = f'<div data-live="spark" data-t="{key}">{spark_for(m)}</div>'
    checks = m.checks + ([] if m.is_fund else m.priority)
    passes = sum(1 for c in checks if c.status == "pass")
    warns = sum(1 for c in checks if c.status == "warn")
    fails = sum(1 for c in checks if c.status == "fail")
    check_cell = (f'<span class="up">✅{passes}</span> <span class="warn-line">⚠️{warns}</span> '
                  f'<span class="down">❌{fails}</span>')
    situation = verdict_chip(ctx.verdict(target)) or '<span class="muted">-</span>'

    if m.is_fund:
        note = m.fund.risk_label if m.fund else "ETF"
        return (f"<tr><td>{first}</td><td>{situation}</td><td>{price}</td><td>{trend}</td>"
                f'<td class="l muted" colspan="9">{esc(note)} · ETF 라 매출·ROE 같은 기업 지표가 없습니다</td>'
                f"<td>{check_cell}</td><td class='muted'>-</td></tr>")

    margin = _pct(m.op_margin)
    if m.op_margin is not None and m.op_margin_prior is not None:
        up = m.op_margin > m.op_margin_prior
        margin += f' <span class="{"up" if up else "down"}">{"↑" if up else "↓"}</span>'
    growth = "-"
    if m.revenue_growth is not None:
        growth = f'<span class="{"up" if m.revenue_growth >= 0 else "down"}">{m.revenue_growth:+.0%}</span>'

    earnings = ctx.earnings.get(target.cik)
    if earnings:
        when = (f"{earnings.day.isoformat()}<br><span class='small muted'>{dday(ctx.today, earnings.day)}"
                f" · {'추정' if earnings.estimated else '확정'}</span>")
    elif target.watch.earnings_date:
        when = target.watch.earnings_date.isoformat()
    else:
        when = '<span class="muted">-</span>'

    cells = [
        situation, price, trend,
        _money(m.market_cap, m.currency), _money(m.revenue_ttm, m.currency), growth, margin,
        _pct(m.roe), _pct(m.roic),
        f"{m.per:.1f}x" if m.per else "-", f"{m.psr:.1f}x" if m.psr else "-",
        f"{m.runway_years:.1f}년" if m.runway_years is not None else "-",
        check_cell, when,
    ]
    return f"<tr><td>{first}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def watch_news_card(ctx) -> str:
    """내 종목 이야기가 담긴 속보. 종목마다 들어가 보지 않아도 여기서 먼저 보인다."""
    mine = {t.ticker.upper() for t in ctx.mine}
    if not mine:
        return ""
    items = [n for n in ctx.bot.state.news(120)
             if mine & {str(t).upper() for t in n.get("tickers", [])}]
    items.sort(key=lambda n: str(n.get("when") or ""), reverse=True)
    if not items:
        body = ('<div class="empty">관심 종목 이야기가 담긴 속보가 아직 없습니다. 종목별 최신 기사는 '
                '종목 화면의 \'뉴스\' 에 있습니다.</div>')
    else:
        body = '<div class="news-list">' + "".join(news_item(n, ctx.known, compact=True) for n in items[:5]) + "</div>"
    return card(body, "관심 종목 소식", "속보로 분류된 것만",
                foot=more_link("뉴스 전체", with_market("/news?f=mine", ctx.market)))


# --------------------------------------------------------------------------
# 오른쪽 기둥
# --------------------------------------------------------------------------
def market_card(ctx) -> str:
    snapshot = ctx.bot.market_snapshot()
    if snapshot is None or snapshot.empty:
        body = '<div class="empty">지수를 불러오는 중입니다… 받지 못하면 빈칸으로 둡니다.</div>'
        return card(body, "시장", foot=more_link("시장 전체 보기", with_market("/market", ctx.market)))
    rows = []
    for index in [i for i in snapshot.indexes if getattr(i, "market", "us") == ctx.market]:
        spark = visuals.spark(list(getattr(index, "closes", ()) or ()), 64, 22, label="최근 1개월")
        rows.append(
            f'<div class="mini-idx"><div><div class="mi-name">{esc(index.label)}</div>'
            f'<div class="mi-note">{esc(index.note)}</div></div>'
            f'<div style="display:flex;gap:10px;align-items:center">{spark}'
            f'<div><div class="mi-val">{esc(index.text)}</div>'
            f'<div class="mi-move">{visuals.move(index.change_pct) if index.change_pct is not None else ""}'
            '</div></div></div></div>')
    won = next((r for r in snapshot.rates if r.label == "원"), None)
    if won:
        rows.append(
            f'<div class="mini-idx"><div><div class="mi-name">원/달러</div>'
            f'<div class="mi-note">1달러당 원화</div></div><div><div class="mi-val">{esc(won.text)}</div>'
            f'<div class="mi-move">{visuals.move(won.change_pct) if won.change_pct is not None else ""}</div>'
            '</div></div>')
    body = "".join(rows) or '<div class="empty">이 시장의 지수를 받지 못했습니다.</div>'
    return card(body, "시장", foot=more_link("지수·환율·경제지표", with_market("/market", ctx.market)))


def events_card(ctx) -> str:
    items = ev.collect(ctx.bot, ctx.today, ctx.today + timedelta(days=21), ctx.mine)
    if ctx.korean:
        # 경제지표·휴장일은 미국 것뿐이다. 한국 화면에 두면 한국 이야기로 읽힌다.
        items = [e for e in items if e.kind == ev.EARN]
    items = [e for e in items if e.kind != ev.ECON or e.importance >= 3][:7]
    if not items:
        body = '<div class="empty">3주 안에 잡힌 일정이 없습니다.</div>'
    else:
        body = "".join(_event_row(ctx, e) for e in items)
    return card(body, "다가오는 일정", "3주 안",
                foot=more_link("캘린더 열기", with_market("/calendar", ctx.market)))


def _event_row(ctx, event) -> str:
    when = f"{event.day.month}/{event.day.day}<br>{esc(dday(ctx.today, event.day))}"
    name = esc(event.name)
    if event.kind == ev.EARN and event.ticker:
        name = f'<a href="{esc(stock_url(event.ticker))}">{name}</a>'
    tag = {"earn": '<span class="tag accent">실적</span>', "hol": '<span class="tag">휴장</span>'}.get(
        event.kind, f'<span class="tag">{esc(event.time or "지표")}</span>')
    parts = [event.note] + (["추정"] if event.estimated and event.kind == ev.ECON else [])
    note = esc(" · ".join(p for p in parts if p))
    return (f'<div class="ev"><div class="ev-when">{when}</div>'
            f'<div class="ev-name"><b>{name}</b><span>{note}</span></div>{tag}</div>')


def filings_card(ctx) -> str:
    watched = {t.ticker.upper() for t in ctx.mine}
    recent = [r for r in ctx.bot.state.recent(40)
              if (r.get("market") or markets.US) == ctx.market
              and str(r.get("ticker", "")).upper() in watched][:5]
    body = ('<div class="compact">' + "".join(filing_item(r) for r in recent) + "</div>" if recent
            else '<div class="empty">감시 중인 종목의 새 공시가 오면 여기에 먼저 뜹니다.</div>')
    return card(body, "최근 공시", foot=more_link("공시 전체", with_market("/filings", ctx.market)))


def picks_card(ctx) -> str:
    bot = ctx.bot
    if not bot.recommend_enabled:
        return ""
    groups = bot.top_picks(market=ctx.market) or {}
    for key in (screener.BLUE, screener.GROWTH, screener.MOMENTUM):
        picks = groups.get(key) or []
        if picks:
            rows = "".join(
                f'<div class="ev"><div class="ev-when">{rank}</div>'
                f'<div class="ev-name"><b>{esc(p.ticker)}</b><span>{esc(p.name)}</span></div>'
                f'<span class="tag accent">{esc(screener.CATEGORY_NAME[key])}</span></div>'
                for rank, p in enumerate(picks[:3], 1))
            return card(rows, "발굴", "지표로 고른 것 · 사라는 뜻 아님",
                        foot=more_link("이유와 함께 보기", with_market("/discover", ctx.market)))
    return ""


def korean_limits(bot) -> str:
    """한국 화면에서 아직 못 하는 것을 그대로 적는다. 고장 난 것과 안 만든 것은 다르다."""
    dart = getattr(bot, "dart", None)
    key_line = (
        "<li><b>재무제표·공시</b> — DART 인증키가 없어 비어 있습니다. 위 띠에 붙여넣으면 채워집니다.</li>"
        if not (dart and dart.ready) else
        "<li><b>재무제표</b> — DART 사업보고서의 <b>연간 확정치</b>를 씁니다. 미국(최근 4개 분기 합산)보다 "
        "한 걸음 늦습니다.</li>"
    )
    body = (
        '<details class="fold" data-keep="kr-limits"><summary>한국 화면에서 아직 안 되는 것</summary>'
        f'<div class="fold-body"><ul class="bullets small">{key_line}'
        "<li><b>PER · PSR</b> — 시가총액을 구할 발행주식수를 아직 받지 않아 '판단 불가' 로 둡니다. 지어내지 않습니다.</li>"
        "<li><b>발굴의 밸류에이션 축</b> — PER·PSR 을 못 구해 나머지 네 축(성장·수익성·재무 안정성·현금 창출력)으로만 봅니다.</li>"
        "<li><b>경제지표 · 휴장일</b> — 미국 기준만 있습니다. 캘린더에 '미국' 이라고 붙여 둡니다.</li>"
        "</ul><p class='hint'>지금 한국 화면에서 되는 것: 주가 · 등락 · 차트 · 52주 위치 · 재무제표 판정 · "
        "체크리스트 · 내 매수가 손익 · DART 공시 감시 · 한글 종목 뉴스 · 발굴.</p></div></details>"
    )
    return card(body, pad=True)


__all__ = ["SUMMARY_COLUMNS", "render", "summary_columns", "summary_table"]
