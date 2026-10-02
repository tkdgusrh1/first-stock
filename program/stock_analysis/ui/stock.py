"""종목 한 장 — 이 종목에 대해 가진 것을 **하나도 빠짐없이**, 그러나 접어서.

공시 문서처럼 번호 붙은 구역을 위에서 아래로 놓고, 구역마다 **접힌 머리에 결론 한 줄**을
보인다. 펼치지 않아도 무엇이 들어 있는지 알 수 있게.

  맨 위   지금 얼마인가(장외 포함) · 한눈에 숫자 띠 · 새 소식
  01 차트 · 수익률 비교 · 거래량
  02 실적 기대 한 장 — 회사 가이던스 · 전문가 예상 · 지난 결과 · 의견
  03 재무(SEC/DART 원본으로 직접 계산)   04 주요 통계(야후 집계)
  05 옵션 시장   06 보유자 · 내부자   07 회사 · 경영진
  08 뉴스   09 공시   10 과거 데이터(CSV)   11 내 기준

바깥에서 받아야 하는 것은 쪽이 먼저 뜬 뒤 브라우저가 /frag/… 로 따로 받아 끼운다.
접힌 구역 안의 것은 **펼칠 때** 받는다 — 보지도 않을 것을 받지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .. import markets, money, visuals
from ..assessment import LEVEL_ICON, LEVEL_LABEL
from ..korean import guidance_line, note_for, period_ko
from ..metrics import STATUS_ICON, _money, _pct
from ..position import build as build_position
from ..position import krw_rate_from, won
from . import research
from .kit import (
    action_button, card, change_html, esc, extended_html, filing_item, icon, mark, price_text, term, trade_time, verdict_chip,
)
from .shell import with_market


def not_found(ctx, ticker: str) -> str:
    return card(
        f'<div class="empty-box"><b>{esc(ticker)}</b> 는 감시 목록에 없습니다.<br>'
        "관심 종목에 추가하면 이 화면이 채워집니다.</div>"
        '<div class="card-body"><form method="post" action="/action" class="form-row">'
        '<input type="hidden" name="action" value="add">'
        f'<input type="hidden" name="back" value="{esc("/stock/" + quote(ticker, safe=""))}">'
        f'<input type="hidden" name="ticker" value="{esc(ticker)}">'
        f'<button type="submit" class="btn primary">{icon("plus")} {esc(ticker)} 관심 종목에 추가</button>'
        "</form></div>",
        "종목을 찾지 못했습니다",
    )


def render(ctx, target) -> str:
    bot = ctx.bot
    m = ctx.metric(target)
    verdict = ctx.verdict(target)
    back = f'<a class="crumb" href="{esc(with_market("/", target.market))}">{icon("chev-l", True)} 관심 종목</a>'
    head = back + hero(ctx, target, m, verdict)
    if m is None:
        error = bot.metrics_errors().get(target.cik)
        if error:
            body = (f'<div class="empty-box">⚠️ 정보를 가져오지 못했습니다.<br>{esc(error)}</div>'
                    '<div class="card-body">'
                    + action_button("metrics", "다시 시도", ctx.here, "btn primary") + "</div>")
        else:
            body = '<div class="empty-box">정보를 불러오는 중입니다… (10~30초) 끝나면 저절로 채워집니다.</div>'
        return head + card(body)

    recent = [r for r in bot.state.recent(200) if str(r.get("ticker", "")).upper() == target.ticker.upper()]
    sections = build_sections(ctx, target, m, verdict, recent)
    tabs = "".join(f'<a href="#{key}" data-tab="{key}">{esc(short)}</a>' for key, short, *_ in sections)
    tools = ('<span class="tabs-tools"><button type="button" class="chip" data-fold-all="open">모두 펼치기</button>'
             '<button type="button" class="chip" data-fold-all="close">모두 접기</button></span>')
    body = "".join(section(i + 1, key, title, html, line, open_)
                   for i, (key, _short, title, html, line, open_) in enumerate(sections))
    return (head + glance_strip(ctx, target, m) + news_bar(ctx, target, recent)
            + f'<nav class="tabs" data-tabs>{tabs}{tools}</nav>'
            + f'<div class="secs">{body}</div>')


def section(no: int, key: str, title: str, body: str, headline: str = "", open_: bool = False) -> str:
    """번호 붙은 접이식 구역. 접혀 있어도 결론 한 줄(headline)이 보인다. 열고 닫은 상태는 기억한다."""
    line = f'<span class="sec-line">{esc(headline)}</span>' if headline else '<span class="sec-line"></span>'
    return (f'<details class="card sec" id="{esc(key)}" data-keep="{esc(key)}"{" open" if open_ else ""}>'
            f'<summary class="sec-head"><span class="sec-no">{no:02d}</span><h2>{esc(title)}</h2>{line}'
            f'<span class="fold-tip"></span></summary><div class="sec-body">{body}</div></details>')


def lazy(url: str, waiting: str) -> str:
    return f'<div data-lazy="{esc(url)}"><p class="muted small">{esc(waiting)}</p></div>'


def build_sections(ctx, target, m, verdict, recent) -> list[tuple]:
    """(key, 탭 이름, 제목, 본문, 접힌 머리 한 줄, 처음부터 펼칠지)."""
    bot = ctx.bot
    q = quote(target.ticker)
    us = target.market == markets.US
    fund = bool(m.is_fund)
    profile = bot.side_cached("profile", target.ticker)      # 받아둔 것만 — 머리 한 줄에 쓴다
    out = []

    out.append(("sec-chart", "차트", "차트 · 수익률 비교", chart_body(target, m),
                chart_headline(m), True))
    if not fund:
        out.append(("sec-expect", "실적 기대", "실적 기대 한 장", expect_body(ctx, target, m),
                    expect_headline(bot, target, m, profile), True))
    out.append(("sec-fin", "재무", "ETF 정보" if fund else "재무 (공시 원본으로 계산)",
                fin_body(ctx, target, m), fin_headline(m), False))
    out.append(("sec-stats", "통계", "주요 통계", lazy(f"/frag/stats?t={q}", "야후 통계를 받는 중…"),
                research.stats_headline(profile, m) or "가치 평가 · 주가 흐름 · 주식 구조 · 공매도", False))
    if us:
        view = bot.side_cached("options", target.ticker)
        out.append(("sec-options", "옵션", "옵션 시장", lazy(f"/frag/options?t={q}", "옵션 체인을 받는 중…"),
                     research.options_headline(view, m.price) or "예상 움직임 · 내재변동성 · 풋/콜 · 몰린 행사가",
                     False))
    if not fund:
        out.append(("sec-holders", "보유자", "보유자 · 내부자", holders_body(bot, target, m),
                    holders_headline(bot, target, profile), False))
    out.append(("sec-company", "회사", "회사 · 경영진", lazy(f"/frag/company?t={q}", "회사 정보를 받는 중…"),
                company_headline(bot, target, profile), False))
    out.append(("sec-news", "뉴스", "뉴스", lazy(f"/frag/news?t={q}", "이 종목의 최근 기사를 받는 중…"),
                news_headline(bot, target), False))
    out.append(("sec-filings", "공시", "공시", filings_body(target, recent, ctx.today),
                filings_headline(recent), False))
    out.append(("sec-history", "과거 데이터", "과거 데이터", history_body(target, m),
                history_headline(m), False))
    out.append(("sec-mine", "내 기준", "내 기준 · 메모", mine_body(ctx, target, m, verdict),
                checks_headline(m) if not fund else "메모 · 직접 입력", False))
    return out


# --------------------------------------------------------------------------
# 1) 지금 얼마인가
# --------------------------------------------------------------------------
def hero(ctx, target, m, verdict) -> str:
    t = target.ticker
    name = target.watch.name or target.name or (m.company if m else "") or markets.display(t)
    sub = [f"<b>{esc(markets.display(t))}</b>", esc(markets.MARKET_NAME[target.market])]
    if target.market == markets.US and target.cik:
        sub.append(f"CIK {esc(target.cik)}")
    industry = ctx.bot.cached_industries().get(target.cik) if hasattr(ctx.bot, "cached_industries") else None
    if industry and getattr(industry, "description", ""):
        sub.append(esc(industry.description))
    chips = [verdict_chip(verdict)]
    fund = getattr(m, "fund", None) if m else None
    if fund is not None:
        chips.append(f'<span class="tag {"down" if fund.high_risk else "warn"}">{esc(fund.risk_label)}</span>')
    elif m is not None and m.profitable is not None:
        chips.append('<span class="tag up">흑자</span>' if m.profitable else '<span class="tag down">적자</span>')
    if m is not None and getattr(m, "market_open", None):
        chips.append('<span class="tag accent">장중</span>')
    chips.append('<span class="tag" title="무료 시세라 거래소보다 늦을 수 있습니다. 거래 시각을 함께 적습니다">'
                 '지연 시세</span>')

    key = esc(t)
    price = ""
    if m is not None and m.price:
        price = (
            f'<div class="h-price"><div class="hp-val" data-live="price" data-t="{key}">{esc(price_text(m))}</div>'
            f'<div class="hp-move" data-live="change" data-t="{key}">{change_html(m)}</div>'
            f'<div class="hp-time" data-live="timeLong" data-t="{key}">{esc(trade_time(m, long=True))}</div>'
            f'<div class="hp-ext" data-live="ext" data-t="{key}">{extended_html(m)}</div></div>'
        )
    ranges = _ranges(m) if m is not None else ""
    return (
        f'<section class="card hero">{mark(t, name, "lg")}'
        f'<div><h1>{esc(name)}</h1><div class="h-sub">{" · ".join(sub)}</div>'
        f'<div class="h-chips">{"".join(c for c in chips if c)}</div></div>'
        f'{price}{ranges}</section>'
    )


def _range_row(label: str, low, high, now, currency: str) -> str:
    if not (low and high and now) or high <= low:
        return ""
    pos = max(0.0, min(100.0, (now - low) / (high - low) * 100))
    return (f'<div class="range52"><span>{esc(label)} <b>{money.price(low, currency)}</b></span>'
            f'<div class="bar" title="지금 {money.price(now, currency)}"><i style="left:{pos:.1f}%"></i></div>'
            f'<span><b>{money.price(high, currency)}</b></span></div>')


def _ranges(m) -> str:
    out = []
    last = (m.bars or [None])[-1]
    if last is not None and m.price:
        out.append(_range_row(f"1일 범위({last.day.month}/{last.day.day})", last.low, last.high,
                              m.price, m.currency))
    out.append(_range_row("52주 범위", m.low_52w, m.high_52w, m.price, m.currency))
    facts = []      # 시가총액은 바로 아래 '한눈에' 띠에 있다 — 두 번 적지 않는다
    if last is not None and last.volume is not None:
        facts.append(f"거래량({last.day.month}/{last.day.day}) <b>{esc(_count(last.volume))}</b>")
    if m.pct_from_high is not None:
        facts.append(f"52주 고점 대비 <b>{m.pct_from_high:+.0f}%</b>")
    if facts:
        out.append(f'<div class="range52" style="grid-template-columns:1fr">'
                   f'<span>{" · ".join(facts)}</span></div>')
    return "".join(out)


def _count(value: float | None) -> str:
    """거래량·주식 수. 1.2억 · 3,450만 처럼 읽기 쉽게."""
    if value is None:
        return "-"
    if value >= 1e8:
        return f"{value / 1e8:.2f}억"
    if value >= 1e4:
        return f"{value / 1e4:,.1f}만"
    return f"{value:,.0f}"


# --------------------------------------------------------------------------
# 2) 새 소식 — 요 며칠 무슨 일이 있었나
# --------------------------------------------------------------------------
def news_bar(ctx, target, recent) -> str:
    since = (ctx.today - timedelta(days=3)).isoformat()
    filings = [r for r in recent if str(r.get("date") or "") >= since]
    chips = []
    if filings:
        forms = " · ".join(dict.fromkeys(str(r.get("form", "")) for r in filings))
        chips.append(f'<a class="tag accent" href="#sec-filings">공시 {len(filings)}건 · {esc(forms)} ›</a>')
    news = ctx.bot.side_cached("news", target.ticker) or []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    fresh = [n for n in news if n.published and _aware(n.published) >= cutoff]
    if fresh:
        chips.append(f'<a class="tag" href="#sec-news">뉴스 {len(fresh)}건 ›</a>')
    profile = ctx.bot.side_cached("profile", target.ticker)
    if profile is not None:
        changes = [r for r in profile.ratings if r.day >= ctx.today - timedelta(days=7)]
        if changes:
            chips.append(f'<a class="tag warn" href="#sec-analyst">의견 변경 {len(changes)}건 ›</a>')
    total = len(filings) + len(fresh)
    if not chips:
        body = '<span class="muted small">요 사흘 새 공시가 없습니다. 뉴스는 아래에서 받는 중입니다.</span>'
    else:
        body = "".join(chips)
    return (f'<div class="card" style="padding:12px 18px;margin-top:14px;display:flex;gap:10px;'
            f'align-items:center;flex-wrap:wrap"><b class="up" style="color:var(--accent)">새 소식</b>'
            f'<span class="muted small">{total}건 · 최근 3일</span>{body}</div>')


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# 맨 위 숫자 띠 — 한눈에
# --------------------------------------------------------------------------
def glance_strip(ctx, target, m) -> str:
    """시가총액·PER·성장·의견·목표가·다음 실적·52주·공매도. 야후 값을 받아뒀으면 바로, 아니면 받아 끼운다."""
    if ctx.bot.side_cached("profile", target.ticker) is not None:
        html = research.glance(ctx.bot, target, m, ctx.today, ctx.earnings.get(target.cik))
        return f'<div class="glance-wrap">{html}</div>'
    return (f'<div class="glance-wrap" data-lazy="/frag/glance?t={esc(quote(target.ticker))}">'
            f'<div class="glance"><div class="gl"><span class="k">한눈에</span><span class="v muted">받는 중…</span></div></div></div>')


# --------------------------------------------------------------------------
# 01 차트 · 수익률 · 거래량
# --------------------------------------------------------------------------
def chart_body(target, m) -> str:
    return (chart_block(m)
            + '<h4>수익률 비교</h4>'
            + lazy(f"/frag/perf?t={quote(target.ticker)}", "지수와 견줄 일봉을 받는 중…")
            + fold("x-volume", "거래량 · 평소 대비 · 장중", volume_block(target, m), volume_headline(m)))


def chart_headline(m) -> str:
    bits = []
    if m.pct_from_high is not None:
        bits.append(f"52주 고점 대비 {m.pct_from_high:+.0f}%")
    bars = [b for b in (m.bars or []) if b.volume is not None]
    if len(bars) >= 31:
        average = sum(b.volume for b in bars[-31:-1]) / 30
        if average:
            bits.append(f"거래량 30일 평균의 {bars[-1].volume / average * 100:.0f}%")
    return " · ".join(bits) or "일봉 · 이동평균 · 지수 대비 수익률"


def chart_block(m) -> str:
    """TradingView 차트. 못 불러오면 서버가 그린 캔들 그림(SVG)이 남는다."""
    live = getattr(m, "market_open", None)
    bars = getattr(m, "bars", []) or []
    fallback = visuals.candles(bars[-visuals.CANDLE_MAX:], live=live)
    if not fallback:
        return '<div class="empty">일봉이 다섯 개도 안 돼 차트를 그리지 않습니다.</div>'
    span = f"{bars[0].day.isoformat()} ~ {bars[-1].day.isoformat()} · {len(bars)}거래일"
    ranges = ('<div class="seg range-btns" data-range>'
              '<button type="button" data-days="22">1개월</button>'
              '<button type="button" data-days="66">3개월</button>'
              '<button type="button" data-days="132">6개월</button>'
              '<button type="button" class="on" data-days="252">1년</button>'
              '<button type="button" data-days="1260">5년</button>'
              '<button type="button" data-days="0">전체</button></div>')
    mas = ('<div class="chips" data-ma>'
           '<button type="button" class="chip on" data-ma-key="ma20"><span class="tv-ma20">●</span> MA20</button>'
           '<button type="button" class="chip on" data-ma-key="ma60"><span class="tv-ma60">●</span> MA60</button>'
           '<button type="button" class="chip" data-ma-key="ma120"><span style="color:#a855f7">●</span> MA120</button>'
           '</div>')
    return (
        f'<div class="chart-card"><div class="cc-head">{mas}<span class="muted small">{esc(span)}</span>{ranges}</div>'
        f'<div class="tv-chart" data-ticker="{esc(m.ticker)}" data-live="{"1" if live else "0"}">'
        '<div class="tv-legend"></div><div class="tv-canvas"></div>'
        f'<div class="tv-fallback">{fallback}</div>'
        '<p class="tv-help">휠로 확대·축소 · 끌어서 이동 · 봉에 올리면 시·고·저·종·거래량</p></div></div>'
    )


def volume_headline(m) -> str:
    bars = [b for b in (m.bars or []) if b.volume is not None]
    if not bars:
        return ""
    return f"{bars[-1].day.month}/{bars[-1].day.day} 거래량 {_count(bars[-1].volume)}"


def volume_block(target, m) -> str:
    """거래량. 일봉으로 '평소 대비' 를, 장중 5분봉으로 '같은 시각 대비' 를 본다."""
    bars = [b for b in (m.bars or []) if b.volume is not None]
    tiles = []
    note = ""
    if len(bars) >= 4:
        last = bars[-1]
        for days in (3, 7, 30):
            window = bars[-days - 1:-1]
            if len(window) < days:
                continue
            average = sum(b.volume for b in window) / len(window)
            if average:
                ratio = last.volume / average * 100
                cls = "up" if ratio >= 100 else "down"
                tiles.append(f'<div class="stat"><dt>{days}일 평균 대비</dt>'
                             f'<dd class="{cls}">{ratio:.0f}%<br><small>평균 {_count(average)}</small></dd></div>')
        head = f'<div class="stat"><dt>{last.day.month}/{last.day.day} 거래량</dt><dd>{_count(last.volume)}</dd></div>'
        tiles.insert(0, head)
        if getattr(m, "market_open", False):
            note = '<p class="src">장중이라 오늘 거래량은 아직 다 차지 않았습니다. 같은 시각끼리는 아래 그림.</p>'
    daily = (f'<dl class="stat-grid">{"".join(tiles)}</dl>{note}' if tiles
             else '<p class="muted small">거래량 자료가 없습니다.</p>')
    return daily + lazy(f"/frag/intraday?t={quote(target.ticker)}", "장중 거래량(5분봉)을 받는 중…")


# --------------------------------------------------------------------------
# 02 실적 기대 한 장
# --------------------------------------------------------------------------
def expect_body(ctx, target, m) -> str:
    """위: 야후 집계(전문가 예상·의견)와 회사 가이던스를 한 표로. 아래: 회사 원문·이행 이력·지난 분기 대조."""
    bot = ctx.bot
    cik = target.cik
    korean = bot.cached_korean().get(cik)
    guidance = bot.cached_guidance().get(cik)
    track = bot.cached_track_records().get(cik)
    recap = bot.recap_for(target)
    top = lazy(f"/frag/expect?t={quote(target.ticker)}", "애널리스트 집계를 받는 중…")
    recap_fold = fold("x-recap", "지난 분기: 실제 vs 전문가 vs 회사", recap_block(recap),
                      recap.summary if recap is not None and not recap.empty else "")
    if target.market != markets.US:
        # 가이던스 원문·이행 이력은 SEC 8-K 에서 읽는다. 한국 종목에는 없는 칸이라 보이지 않는다.
        return top + recap_fold
    folds = [
        recap_fold,
        fold("x-guidance", "회사 가이던스 원문 (8-K 실적 발표문)", guidance_block(guidance, korean),
             guidance_headline(guidance, None, None)),
        fold("x-track", "회사가 약속을 지켜왔나 (가이던스 이행)", track_block(track, korean),
             track.summary if track is not None else "아직 확인하지 않았습니다"),
    ]
    if target.market == markets.US:
        folds.append(fold("x-where", "숫자를 직접 확인할 곳", where_block(target)))
    return top + "".join(f for f in folds if f)


def expect_headline(bot, target, m, profile) -> str:
    if profile is not None:
        guidance = bot.cached_guidance().get(target.cik)
        chips = research.expect_chips(profile, guidance, m.price)
        if chips:
            return " · ".join(text for text, _ in chips[:3])
    guidance = bot.cached_guidance().get(target.cik)
    track = bot.cached_track_records().get(target.cik)
    return guidance_headline(guidance, track, None) if (guidance or track) else "회사 가이던스 · 전문가 예상 · 의견 · 목표가"


def where_block(target) -> str:
    from ..estimates import links_for

    links = "".join(f'<li><a href="{esc(url)}" target="_blank" rel="noopener">{esc(name)}</a> '
                    f'<span class="muted">— {esc(hint)}</span></li>'
                    for name, url, hint in links_for(markets.display(target.ticker)))
    return f'<ul class="bullets small">{links}</ul>'


# --------------------------------------------------------------------------
# 03 재무 — SEC/DART 원본으로 직접 계산
# --------------------------------------------------------------------------
def fin_body(ctx, target, m) -> str:
    if m.is_fund:
        return fund_block(m, target)
    span = "연간" if money.is_won(m.currency) else "TTM"
    tiles = [
        ("PER", f"{m.per:.1f}x" if m.per else "-", ""),
        ("PSR", f"{m.psr:.1f}x" if m.psr else "-", ""),
        (f"EPS {span}", money.price(m.eps_ttm, m.currency) if m.eps_ttm else "-", ""),
        (f"매출({span})", _money(m.revenue_ttm, m.currency),
         f"{m.revenue_growth:+.0%} 전년 대비" if m.revenue_growth is not None else ""),
        ("영업이익률", _pct(m.op_margin),
         (f"직전 {_pct(m.op_margin_prior)}" if m.op_margin_prior is not None else "")),
        ("ROE", _pct(m.roe), ""),
        ("ROIC", _pct(m.roic), "메모 기준에서 ROE 보다 먼저"),
        ("런웨이", f"{m.runway_years:.1f}년" if m.runway_years is not None else "-",
         "현금 ÷ 한 해 소진액 (적자 기업)"),
    ]
    grid = "".join(
        f'<div class="stat"><dt>{term(k)}</dt><dd>{esc(v)}'
        + (f'<br><small>{esc(n)}</small>' if n else "") + "</dd></div>"
        for k, v, n in tiles)
    as_of = f"기준 {'연도' if span == '연간' else '분기'} {m.as_of.isoformat()}" if m.as_of else ""
    industry = ctx.bot.cached_industries().get(target.cik)
    folds = [
        fold("x-trend", "분기 추이 (최근 8개 분기)", trends_block(m), "매출 · 영업이익률 · 순이익 · 주식 수", open_=True),
        fold("x-numbers", "핵심 숫자 전체", numbers_block(m), "현금 · 부채 · 자기자본 · 현금흐름 · 희석"),
        fold("x-peers", "동종업계 비교", peers_block(m, industry),
             (industry.description or "") if industry else ""),
        fold("x-sources", "숫자의 출처 · 원문 대조", sources_block(m), "SEC XBRL" if not money.is_won(m.currency) else "DART"),
    ]
    stamp = f'<p class="src">{esc(as_of)}</p>' if as_of else ""
    return f'<dl class="stat-grid">{grid}</dl>{stamp}' + "".join(f for f in folds if f)


def fin_headline(m) -> str:
    if m.is_fund:
        info = m.fund
        return info.risk_label if info is not None else "ETF"
    bits = []
    span = "연간" if money.is_won(m.currency) else "TTM"
    if m.revenue_ttm:
        growth = f" ({m.revenue_growth:+.0%})" if m.revenue_growth is not None else ""
        bits.append(f"매출 {span} {_money(m.revenue_ttm, m.currency)}{growth}")
    if m.op_margin is not None:
        bits.append(f"영업이익률 {_pct(m.op_margin)}")
    if m.roic is not None:
        bits.append(f"ROIC {_pct(m.roic)}")
    elif m.runway_years is not None:
        bits.append(f"런웨이 {m.runway_years:.1f}년")
    return " · ".join(bits)


def numbers_block(m) -> str:
    span_label = "연간" if money.is_won(m.currency) else "TTM"
    stats = [
        ("시가총액", _money(m.market_cap, m.currency)),
        (f"매출 {span_label}", _money(m.revenue_ttm, m.currency)),
        (f"순이익 {span_label}", _money(m.net_income_ttm, m.currency)),
        (f"영업이익 {span_label}", _money(m.operating_income_ttm, m.currency)),
        ("영업현금흐름", _money(m.ocf_ttm, m.currency)),
        ("잉여현금흐름", _money(m.fcf_ttm, m.currency)),
        ("보유 현금", _money(m.cash, m.currency)),
        ("총부채", _money(m.total_debt, m.currency)),
        ("자기자본", _money(m.equity, m.currency)),
        ("52주 범위", money.span(m.low_52w, m.high_52w, m.currency)),
        ("주식수", money.shares(m.shares, m.currency)),
        ("희석", f"{m.share_growth_1y:+.1%}" if m.share_growth_1y is not None else "-"),
    ]
    cells = "".join(f'<div class="stat"><dt>{term(k)}</dt><dd>{esc(v)}</dd></div>' for k, v in stats)
    return f'<h4 style="margin-top:22px">핵심 숫자</h4><dl class="stat-grid">{cells}</dl>'


def trends_block(m) -> str:
    charts = []
    revenue = m.trends.get("revenue") or m.quarterly_revenue
    if len(revenue) >= 2:
        charts.append(_bars("분기 매출", revenue, lambda v: _money(v, m.currency)))
    margin = m.trends.get("op_margin")
    if margin and len(margin) >= 2:
        charts.append(_bars("분기 영업이익률", margin, lambda v: f"{v:.1f}%"))
    income = m.trends.get("net_income")
    if income and len(income) >= 2:
        charts.append(_bars("분기 순이익", income, lambda v: _money(v, m.currency)))
    shares = m.trends.get("shares")
    if shares and len(shares) >= 2:
        # 오른쪽으로 갈수록 막대가 높아지면 주식이 늘어난 것 = 내 몫이 줄었다
        charts.append(_bars("발행주식수", shares, lambda v: money.shares(v, m.currency)))
    if not charts:
        return ""
    return ('<h4 style="margin-top:22px">추이 <span class="muted small">최근 8개 분기 · 방향이 중요합니다</span></h4>'
            f'<div class="charts">{"".join(charts)}</div>')


def _bars(title: str, series, fmt) -> str:
    values = [v for _, v in series][-8:]
    labels = [d for d, _ in series][-8:]
    peak = max((abs(v) for v in values), default=0)
    if not peak:
        return ""
    bars = []
    for day, value in zip(labels, values):
        height = max(3, round(abs(value) / peak * 100))
        cls = "neg" if value < 0 else ""
        bars.append(f'<div class="mbar" title="{esc(day.isoformat())} · {esc(fmt(value))}">'
                    f'<i class="{cls}" style="height:{height}%"></i>'
                    f'<span>{esc(str(day.year)[2:])}.{day.month:02d}</span></div>')
    return (f'<div class="mchart"><div class="mc-title">{esc(title)}<b>{esc(fmt(values[-1]))}</b></div>'
            f'<div class="mbars">{"".join(bars)}</div></div>')


def peers_block(m, industry=None) -> str:
    if industry and not m.peers:
        note = (f'<p class="sub">SEC 업종 분류: {esc(industry.description or industry.sic)} '
                f'(SIC {esc(industry.sic)})</p>')
        if industry.peers:
            return ('<h4 style="margin-top:22px">동종업계</h4>' + note
                    + f'<p class="muted small">비교 대상: {esc(", ".join(industry.peers))} — '
                    "지표를 새로 계산하면 수치가 채워집니다.</p>")
        return ('<h4 style="margin-top:22px">동종업계</h4>' + note
                + '<p class="muted small">같은 업종에서 티커가 있는 회사를 찾지 못했습니다.</p>')
    if not m.peers:
        return ""
    def ratio(value) -> str:
        return f"{value:.1f}x" if value else "-"

    def growth(value) -> str:
        return f"{value:+.0%}" if value is not None else "-"

    rows = [
        f"<tr><td><b>{esc(ticker)}</b></td><td>{ratio(peer.get('per'))}</td>"
        f"<td>{ratio(peer.get('psr'))}</td>"
        f"<td>{_pct(peer['op_margin']) if peer.get('op_margin') is not None else '-'}</td>"
        f"<td>{growth(peer.get('revenue_growth'))}</td></tr>"
        for ticker, peer in m.peers.items()
    ]
    own = (f"<tr><td><b>{esc(markets.display(m.ticker))} (이 종목)</b></td>"
           f"<td>{ratio(m.per)}</td><td>{ratio(m.psr)}</td><td>{_pct(m.op_margin)}</td>"
           f"<td>{growth(m.revenue_growth)}</td></tr>")
    note = ""
    if industry:
        source = "직접 지정" if not industry.peers else f"SEC 업종 자동 탐색 · SIC {industry.sic}"
        note = f'<p class="sub">{esc(industry.description or "")} ({esc(source)})</p>'
    return ('<h4 style="margin-top:22px">동종업계 비교</h4>' + note
            + '<div class="table-wrap"><table class="tbl"><thead><tr><th>종목</th><th>PER</th><th>PSR</th>'
            f'<th>영업이익률</th><th>매출성장</th></tr></thead><tbody>{own}{"".join(rows)}</tbody></table></div>')


def sources_block(m) -> str:
    """숫자를 어디서 가져왔는지. **원문까지 짚어 준다.** 더한 분기를 하나씩 펼쳐 검산할 수 있게."""
    if not m.sources:
        return ""
    rows = []
    for source in m.sources.values():
        head = f"<b>{esc(source.label)}</b>"
        if source.note:
            rows.append(f'<li>{head} <span class="muted">{esc(source.note)}</span></li>')
            continue
        total = (f" = <b>{esc(_money(source.total, m.currency))}</b>" if source.total is not None else "")
        line = f'{head} <code>{esc(source.concept)}</code> <span class="muted">{esc(source.how)}</span>{total}'
        if source.checkable:
            parts = "".join(
                f"<li>{esc(part.when)} · {esc(part.shown)}"
                + (f' <a href="{esc(part.url)}" target="_blank" rel="noopener">{esc(part.form or "공시")} 원문</a>'
                   if part.url else "") + "</li>"
                for part in source.parts)
            rows.append(f'<li>{line}<details class="ko-src"><summary>더한 분기 {len(source.parts)}개 보기</summary>'
                        f'<ul class="bullets small">{parts}</ul></details></li>')
        else:
            link = (f' <a href="{esc(source.url)}" target="_blank" rel="noopener">공시 원문</a>'
                    if source.url else "")
            rows.append(f"<li>{line}{link}</li>")
    if money.is_won(m.currency):
        where = ("모든 재무 수치는 금융감독원 <b>DART</b> 에 제출된 사업보고서 원본에서 계산했습니다. "
                 "단위는 원화이고, 미국(최근 4개 분기 합산)과 달리 <b>연간 확정치</b>입니다.")
        how = ""
    else:
        where = ("모든 재무 수치는 <b>SEC</b> 에 제출된 XBRL 원본에서 계산했습니다. "
                 "수정 공시가 있으면 가장 나중에 제출된 값을 씁니다.")
        how = "터미널에서 <code>python main.py verify 티커</code> 로 한 번에 볼 수도 있습니다."
    return ('<details class="fold" data-keep="sources"><summary>이 숫자들의 출처 · 원문 대조</summary>'
            f'<div class="fold-body"><ul class="bullets small">{"".join(rows)}</ul>'
            f'<p class="hint">{where} <b>숫자가 미심쩍으면 원문을 열어 직접 대조해보세요.</b> {how}</p>'
            "</div></details>")


def fund_block(m, target) -> str:
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
    cells = "".join(f'<div class="stat"><dt>{esc(k)}</dt><dd style="font-size:14px">{esc(v)}</dd></div>'
                    for k, v in rows)
    warnings = "".join(f"<li>{esc(w)}</li>" for w in info.warnings)
    warn_block = (f'<div class="box-warn"><b>⚠️ 구조상 알아둘 것</b><ul>{warnings}</ul></div>'
                  if warnings else "")
    notes = "".join(f"<li>{esc(n)}</li>" for n in info.notes)
    note_block = f'<ul class="bullets small" style="margin-top:10px">{notes}</ul>' if notes else ""
    checks = "".join(check_item(c) for c in m.checks)
    return (f'<dl class="stat-grid">{cells}</dl>{warn_block}{note_block}'
            f'<h4 style="margin-top:18px">ETF 체크리스트</h4><ul class="checks">{checks}</ul>'
            '<p class="hint">ETF 는 회사가 아니라 여러 자산을 담아둔 그릇입니다. 매출·ROE·영업이익률 같은 '
            '기업 지표가 존재하지 않아 <b>메모의 5체크 대신 ETF 기준</b>으로 봅니다.</p>')


# --------------------------------------------------------------------------
# 06 보유자 · 내부자
# --------------------------------------------------------------------------
def holders_body(bot, target, m) -> str:
    parts = [lazy(f"/frag/holders?t={quote(target.ticker)}", "기관·내부자 보유 비중을 받는 중…")]
    if target.market == markets.US:
        insider = bot.cached_insiders().get(target.cik)
        if insider is None:
            parts.append('<h4>내부자 매매 (SEC Form 4)</h4>'
                         '<p class="muted small">아직 확인하지 않았습니다. 감시 주기마다 채웁니다.</p>')
        else:
            tiles = (f'<dl class="stat-grid">'
                     f'<div class="stat"><dt>매수</dt><dd class="up">{len(insider.buys)}건'
                     f'<small> · {esc(_money(insider.buy_value))}</small></dd></div>'
                     f'<div class="stat"><dt>매도</dt><dd class="down">{len(insider.sells)}건'
                     f'<small> · {esc(_money(insider.sell_value))}</small></dd></div></dl>')
            parts.append(f'<h4>내부자 매매 <span class="muted small">SEC Form 4 · 최근 {insider.days}일 · '
                         f'자기 돈으로 한 매매만</span></h4>{tiles}'
                         + fold("mine-insider", "내부자 매매 전체 표", insider_block(insider), insider.summary))
    return "".join(parts)


def holders_headline(bot, target, profile) -> str:
    bits = []
    if profile is not None:
        if profile.held_institutions is not None:
            bits.append(f"기관 {profile.held_institutions * 100:.0f}%")
        if profile.held_insiders is not None:
            bits.append(f"내부자 {profile.held_insiders * 100:.1f}%")
    insider = bot.cached_insiders().get(target.cik) if target.market == markets.US else None
    if insider is not None:
        bits.append(insider.summary)
    return " · ".join(bits) or "기관 · 내부자 보유 비중 · 내부자 매매"


# --------------------------------------------------------------------------
# 07~09 회사 · 뉴스 · 공시
# --------------------------------------------------------------------------
def company_headline(bot, target, profile) -> str:
    bits = []
    if profile is not None:
        bits += [x for x in (profile.sector, profile.industry) if x]
        if profile.employees:
            bits.append(f"직원 {profile.employees:,}명")
    if not bits:
        industry = bot.cached_industries().get(target.cik)
        if industry is not None and getattr(industry, "description", ""):
            bits.append(industry.description)
    return " · ".join(bits) or "사업 설명 · 경영진 · 연락처"


def news_headline(bot, target) -> str:
    news = bot.side_cached("news", target.ticker) or []
    if not news:
        return "펼치면 최근 기사를 받습니다"
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    fresh = [n for n in news if n.published and _aware(n.published) >= cutoff]
    return f"최근 3일 {len(fresh)}건 · 받아둔 기사 {len(news)}건"


def filings_body(target, recent, today) -> str:
    """이 종목 공시 전부(받아둔 만큼). 날짜별로 묶는다."""
    from .kit import by_day

    if target.market == markets.US and target.cik:
        link = (f'<a class="more-link" href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany'
                f'&CIK={esc(target.cik)}&owner=include&count=40" target="_blank" rel="noopener">'
                f'SEC EDGAR 전체 {icon("ext", True)}</a>')
    else:
        link = ('<a class="more-link" href="https://dart.fss.or.kr" target="_blank" rel="noopener">'
                f'DART 전자공시 {icon("ext", True)}</a>')
    if not recent:
        body = ('<p class="muted small">이 프로그램을 켠 뒤로 들어온 공시가 아직 없습니다. '
                '첫 확인에서는 기준선만 잡고, 그 뒤 새로 올라온 것부터 쌓입니다.</p>')
    else:
        parts = []
        for label, entries in by_day(recent[:30], today):
            parts.append(f'<div class="day-label">{esc(label)}<span class="n">{len(entries)}건</span></div>')
            parts.extend(filing_item(e, show_mark=False) for e in entries)
        body = f'<div class="flush">{"".join(parts)}</div>'
    return body + f'<p class="src">{link}</p>'


def filings_headline(recent) -> str:
    if not recent:
        return "아직 새 공시 없음"
    first = recent[0]
    return f"{len(recent)}건 · 최근 {first.get('date', '')} {first.get('form', '')}"


# --------------------------------------------------------------------------
# 10 과거 데이터 — 일봉 표 · 월별 · CSV
# --------------------------------------------------------------------------
HISTORY_ROWS = 20


def history_body(target, m) -> str:
    bars = list(m.bars or [])
    if not bars:
        return '<p class="muted small">일봉을 받지 못했습니다. 빈 표를 만들지 않습니다.</p>'
    currency = m.currency

    def table(rows) -> str:
        return ('<div class="table-wrap"><table class="tbl"><thead><tr><th class="l">날짜</th><th>시가</th>'
                '<th>고가</th><th>저가</th><th>종가</th><th>등락</th><th>거래량</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>')

    def row(label, bar, prev) -> str:
        move = (bar.close / prev - 1) if prev else None
        return (f'<tr><td class="l">{label}</td><td>{esc(money.price(bar.open, currency))}</td>'
                f'<td>{esc(money.price(bar.high, currency))}</td><td>{esc(money.price(bar.low, currency))}</td>'
                f'<td><b>{esc(money.price(bar.close, currency))}</b></td>'
                f'<td class="{research.cls_of(move)}">{research.signed(move, 2)}</td>'
                f'<td>{_count(bar.volume)}</td></tr>')

    daily = []
    for i in range(len(bars) - 1, -1, -1):
        prev = bars[i - 1].close if i > 0 else None
        daily.append(row(bars[i].day.isoformat(), bars[i], prev))
    months = _monthly(bars)
    monthly = []
    for i in range(len(months) - 1, -1, -1):
        prev = months[i - 1].close if i > 0 else None
        monthly.append(row(f"{months[i].day.year}.{months[i].day.month:02d}", months[i], prev))
    csv = (f'<a class="btn sm" href="/download/history?t={esc(quote(target.ticker))}" download>'
           f'{icon("table", True)} 일봉 전체 CSV 내려받기</a>')
    return (f'<div class="row-tools">{csv}<span class="muted small">최대 10년 · 엑셀에서 바로 열림</span></div>'
            + table(daily[:HISTORY_ROWS])
            + fold("x-daily", "일별 더 보기 (최근 1년)", table(daily[HISTORY_ROWS:252]), f"{min(len(daily), 252)}거래일")
            + fold("x-monthly", "월별 (전체)", table(monthly), f"{len(monthly)}개월")
            + '<p class="src">Yahoo Finance 일봉(액면분할 반영 · 배당 미반영 종가) · 등락은 직전 봉 종가 대비</p>')


def _monthly(bars):
    """일봉 → 월봉(시가=첫날 시가, 고가·저가=그달 최고·최저, 종가=마지막 날, 거래량=합)."""
    from ..prices import Candle

    out, current, key = [], None, None
    for bar in bars:
        month = (bar.day.year, bar.day.month)
        if month != key:
            if current is not None:
                out.append(current)
            key = month
            current = Candle(bar.day, bar.open, bar.high, bar.low, bar.close, bar.volume)
            continue
        current.high = max(current.high, bar.high)
        current.low = min(current.low, bar.low)
        current.close = bar.close
        current.day = bar.day
        if bar.volume is not None:
            current.volume = (current.volume or 0) + bar.volume
    if current is not None:
        out.append(current)
    return out


def history_headline(m) -> str:
    bars = m.bars or []
    if not bars:
        return ""
    return f"{bars[0].day.isoformat()} ~ {bars[-1].day.isoformat()} · 일봉 {len(bars)}개 · CSV"


# --------------------------------------------------------------------------
# 11 내 기준 — 메모 기준 판단 · 위험 · 원문 · 내 보유
# --------------------------------------------------------------------------
def position_block(ctx, target, m) -> str:
    snapshot = ctx.bot.market_snapshot()
    rate = krw_rate_from(snapshot)
    position = build_position(target.watch, m, rate)
    if position is None or position.value is None:
        return ""
    unit = position.currency
    sign = "+" if position.profit >= 0 else "−"
    won_line = ""
    if position.profit_krw is not None and not position.in_won:
        won_line = (f'<div class="stat"><dt>원화 손익 (지금 환율 ₩{rate:,.2f})</dt>'
                    f'<dd class="{position.direction}">{esc(won(position.profit_krw))}</dd></div>')
    return (
        '<h4>💼 내 보유</h4><dl class="stat-grid">'
        f'<div class="stat"><dt>평가 손익</dt><dd class="{position.direction}">{sign}'
        f'{esc(money.exact(abs(position.profit), unit))}<small> {position.profit_pct:+.2f}%</small></dd></div>'
        f'<div class="stat"><dt>현재 평가</dt><dd>{esc(money.exact(position.value, unit))}</dd></div>'
        f'<div class="stat"><dt>매수가 × 수량</dt><dd style="font-size:14px">'
        f'{esc(money.price(position.buy_price, unit))} × {position.shares:,.4g}주</dd></div>'
        f'<div class="stat"><dt>투자 원금</dt><dd>{esc(money.exact(position.cost, unit))}</dd></div>'
        f'{won_line}</dl>')


def mine_body(ctx, target, m, verdict) -> str:
    bot = ctx.bot
    cik = target.cik
    korean = bot.cached_korean().get(cik)
    remove = action_button("remove", f"{icon('trash', True)} 감시 목록에서 빼기", with_market("/", target.market),
                           "btn sm danger", {"ticker": target.ticker}, confirm="감시 목록에서 뺄까요?")
    tail = f'<div class="row-tools" style="margin-top:14px">{remove}<span class="muted small">빼도 받아둔 공시 기록은 남습니다</span></div>'
    position = position_block(ctx, target, m)
    if m.is_fund:
        return position + fold("mine-memo", "📝 내 메모 · 직접 입력", memo_block(target) + inputs_block(ctx, target, m),
                               open_=True) + tail
    risk = bot.cached_risks().get(cik)
    report = bot.cached_reports().get(cik)
    folds = [
        fold("mine-verdict", "🎯 메모 기준 판단", assessment_block(verdict) + checks_block(m)
             + milestones_block(target), checks_headline(m), open_=True),
        fold("mine-risk", "⚠️ 위험 요인 변화", risk_block(risk, korean),
             risk.summary if risk else "아직 확인하지 않았습니다."),
        fold("mine-report", "📄 회사가 밝힌 내용 (10-Q/10-K 원문)", report_block(report, korean),
             report_headline(report)),
        fold("mine-memo", "💼 내 메모 · 직접 입력", memo_block(target) + inputs_block(ctx, target, m),
             "컨센서스 · 내 매수가 · 메모"),
    ]
    return position + "".join(folds) + tail


def fold(key: str, title: str, body: str, headline: str = "", open_: bool = False) -> str:
    """접힌 상태에서도 **결론 한 줄**이 보여야 무엇을 펼칠지 고를 수 있다."""
    if not body.strip():
        return ""
    note = f'<span class="muted small" style="font-weight:500">{esc(headline)}</span>' if headline else ""
    return (f'<details class="fold" id="{esc(key)}" data-keep="{esc(key)}"{" open" if open_ else ""}>'
            f'<summary><span>{esc(title)}</span>{note}</summary><div class="fold-body">{body}</div></details>')


def checks_headline(m) -> str:
    checks = m.checks + m.priority
    passes = sum(1 for c in checks if c.status == "pass")
    fails = sum(1 for c in checks if c.status == "fail")
    state = "흑자" if m.profitable else ("적자" if m.profitable is False else "손익 미확인")
    return f"{state} 기업 · 통과 {passes} · 미달 {fails}"


def guidance_headline(guidance, track, recap) -> str:
    parts = []
    if guidance is not None and getattr(guidance, "found", False) and guidance.items:
        parts.append(f"회사 제시 {guidance.items[0].range_text or '문장 참조'}")
    if track is not None and track.judged:
        parts.append(track.summary)
    if recap is not None and not recap.empty:
        parts.append(recap.summary)
    return " · ".join(parts) or "가이던스 원문과 과거 이행 이력"


def report_headline(report) -> str:
    if report is None:
        return "아직 읽지 않았습니다."
    if not report.sections:
        return f"{report.form} {report.filing_date} · 표준 항목을 찾지 못함"
    return f"{report.form} {report.filing_date} · {len(report.sections)}개 항목 원문 발췌"


def assessment_block(verdict) -> str:
    """지금 이 종목이 어떤 상황인지. 모든 문장이 숫자에서 나온다."""
    if verdict is None:
        return ""
    axes = []
    for axis in verdict.axes:
        evidence = "".join(f"<li>{esc(item)}</li>" for item in axis.evidence)
        axes.append(
            f'<div class="axis a-{esc(axis.level)}"><div class="axis-head">{LEVEL_ICON[axis.level]} '
            f'<b>{esc(axis.name)}</b><span class="tag">{esc(LEVEL_LABEL[axis.level])}</span></div>'
            f'<p class="axis-line">{esc(axis.headline)}</p><ul class="evidence">{evidence}</ul></div>')
    watch = "".join(f"<li>{esc(point)}</li>" for point in verdict.watch_points)
    watch_html = f'<div class="watch"><b>지금 확인할 것</b><ul>{watch}</ul></div>' if watch else ""
    return (f'<div class="verdict-box v-{esc(verdict.level)}"><div class="vb-head">'
            f'<span class="big">{verdict.icon}</span><div><b>지금 상황: {esc(verdict.label)}</b>'
            f'<p class="sub">{esc(verdict.headline)}</p></div></div>'
            f'<div class="axes">{"".join(axes)}</div>{watch_html}</div>')


def checks_block(m) -> str:
    state = "흑자" if m.profitable else ("적자" if m.profitable is False else "판단 불가")
    priority = "".join(check_item(c) for c in m.priority)
    checks = "".join(check_item(c) for c in m.checks)
    return ('<h4 style="margin-top:18px">우선순위 판단 <span class="muted small">가이던스 → 어닝 서프라이즈 → 마진</span></h4>'
            f'<ul class="checks">{priority}</ul>'
            f'<h4 style="margin-top:18px">{esc(state)} 기업 체크리스트</h4><ul class="checks">{checks}</ul>')


def check_item(check) -> str:
    icon_ = STATUS_ICON.get(check.status, "•")
    return (f'<li class="st-{esc(check.status)}"><span class="icon">{icon_}</span>'
            f'<b>{esc(check.label)}</b><span class="detail">{esc(check.detail)}</span></li>')


def milestones_block(target) -> str:
    milestones = target.watch.milestones
    if not milestones:
        return ""
    items = "".join(f"<li>{esc(x)}</li>" for x in milestones)
    return ('<h4 style="margin-top:18px">핵심 마일스톤 <span class="muted small">공시가 뜨면 이 항목부터 대조하세요</span></h4>'
            f"<ul class='bullets'>{items}</ul>")


def memo_block(target) -> str:
    if not target.watch.note:
        return ""
    return f'<h4>내 메모</h4><p class="quote">{esc(target.watch.note)}</p>'


# --- 영어 원문에 한글 얹기 --------------------------------------------------
#   한글이 위, 영어 원문은 접어서 아래. 원문을 지우지는 않는다.
#   기계 번역은 반드시 '기계 번역' 이라고 밝힌다 — 틀릴 수 있기 때문이다.
def ko(text: str, table: dict | None = None, kind: str = "sentence"):
    if table and text in table:
        return table[text]
    return note_for(text, kind)


def quote_ko(text: str, table: dict | None = None, kind: str = "sentence",
             tag: str = "li", topic: bool = True, rule: bool = True) -> str:
    note = ko(text, table, kind)
    head = ""
    if note.topic and topic:
        head += f'<span class="ko-topic">{esc(note.topic)}</span>'
    if note.line and rule:
        head += f'<b class="ko-line">{esc(note.line)}</b>'
    elif note.machine:
        engine = note.engine or "기계 번역"
        head += (f'<span class="ko-line">{esc(note.machine)}</span>'
                 f'<span class="ko-mark" title="{esc(engine)} 자동 번역입니다. 틀릴 수 있으니 원문을 함께 보세요">'
                 f'{esc(engine)} 번역</span>')
    if note.meaning and topic:
        head += f'<p class="sub">{esc(note.meaning)}</p>'
    if note.line and rule and note.machine:
        head += ('<details class="ko-more"><summary>번역문 더 보기</summary>'
                 f'<p class="sub">{esc(note.machine)} <span class="ko-mark">'
                 f'{esc(note.engine or "기계 번역")} 번역</span></p></details>')
    original = f'<details class="ko-src"><summary>영어 원문</summary><p class="quote">{esc(text)}</p></details>'
    if not head and rule:
        # 옮길 말이 없으면 원문을 그대로 펼쳐 둔다 (숨기면 정보가 사라진다)
        return f'<{tag} class="ko"><p class="quote">{esc(text)}</p></{tag}>'
    return f'<{tag} class="ko">{head}{original}</{tag}>'


def report_block(report, korean=None) -> str:
    if report is None:
        return ('<p class="muted">아직 읽지 않았습니다. 위 막대의 <b>↻ → 보고서 읽기</b>를 누르면 최신 10-Q/10-K '
                '원문에서 사업 설명과 경영진 논의를 가져옵니다.</p>')
    header = (f'<p class="sub">출처: {esc(report.form)} · 제출 {esc(report.filing_date)}'
              + (f" · 기준일 {esc(report.period)}" if report.period else "")
              + f' · <a href="{esc(report.url)}" target="_blank" rel="noopener">원문 보기</a></p>')
    if not report.sections:
        return (header + '<p class="muted">이 보고서에서는 표준 항목(Item 1 / MD&A)을 찾지 못했습니다. '
                '원문을 직접 확인해주세요.</p>')
    blocks = []
    if report.company_words:
        items = "".join(quote_ko(s, korean) for s in report.company_words)
        blocks.append(f'<h4>회사가 직접 밝힌 내용 <span class="muted small">{len(report.company_words)}문장</span></h4>'
                      f'<ul class="ko-list">{items}</ul>')
    for section in report.sections:
        preview = section.paragraphs[:4]
        body = "".join(quote_ko(p, korean, "section", tag="div") for p in preview)
        more = ""
        if len(section.paragraphs) > len(preview):
            more = (f'<p class="muted small">… 이 항목에는 문단이 {len(section.paragraphs)}개 있습니다. '
                    f'전체는 <a href="{esc(report.url)}" target="_blank" rel="noopener">원문</a>에서 보세요.</p>')
        blocks.append(f'<details class="fold"><summary>{esc(section.title)}</summary>'
                      f'<div class="fold-body">{body}{more}</div></details>')
    return ('<p class="hint" style="margin-top:0">한글은 자동 요약·번역, 영어가 원문입니다.</p>'
            + header + "".join(blocks))


def guidance_block(guidance, korean=None) -> str:
    if guidance is None:
        return ('<h4>가이던스 <span class="muted small">메모 1순위</span></h4>'
                '<p class="muted">아직 읽지 않았습니다. ↻ → <b>보고서 읽기</b>를 누르면 최근 실적 발표(8-K 2.02)의 '
                '보도자료에서 전망 문장을 찾아옵니다.</p>')
    header = (f'<p class="sub">출처: {esc(guidance.form)} · 제출 {esc(guidance.filing_date)} · '
              f'<a href="{esc(guidance.url)}" target="_blank" rel="noopener">원문 보기</a></p>')
    if not guidance.items:
        body = ('<p class="muted">이 발표문에서는 전망 문장을 찾지 못했습니다. 표현 방식이 회사마다 달라 '
                '놓칠 수 있으니 원문을 직접 확인해주세요.</p>')
    else:
        rows = []
        for item in guidance.items:
            tags = []
            if item.metric:
                tags.append(f'<span class="tag">{esc(item.metric)}</span>')
            if item.period:
                tags.append(f'<span class="tag">{esc(period_ko(item.period))}</span>')
            value = f' <b>{esc(item.range_text)}</b>' if item.range_text else ""
            headline = guidance_line(item.metric, item.period, item.range_text)
            korean_line = f'<b class="ko-line">{esc(headline)}</b>' if headline else ""
            rows.append(f'<li class="ko"><div>{"".join(tags)}{value}</div>{korean_line}'
                        + quote_ko(item.sentence, korean, tag="div", rule=not headline) + "</li>")
        body = f'<ul class="ko-list">{"".join(rows)}</ul>'
    results = ""
    if guidance.results:
        items = "".join(quote_ko(s, korean) for s in guidance.results)
        results = (f'<details class="fold"><summary>발표문의 실적 설명 {len(guidance.results)}문장</summary>'
                   f'<div class="fold-body"><ul class="ko-list">{items}</ul></div></details>')
    caution = ('<p class="hint">⚠️ 가이던스는 회사가 관리할 수 있는 숫자입니다(낮게 부르기·정의 변경 등). '
               '<b>과거에 제시한 가이던스를 실제로 지켰는지</b> 이력과 현금흐름표를 함께 확인하세요.</p>')
    return ('<h4>가이던스 <span class="muted small">메모 1순위 · 원문 발췌</span></h4>'
            + header + body + results + caution)


def recap_block(recap) -> str:
    """실적 · 컨센서스 · 가이던스 3자 대조."""
    if recap is None or recap.empty:
        return ""
    rows = []
    for line in recap.known:
        gap = ""
        if line.gap_pct is not None:
            gap = f' <span class="{"up" if line.gap_pct >= 0 else "down"}">({line.gap_pct:+.1f}%)</span>'
        rows.append(f"<tr><td>{esc(line.label)}</td><td><b>{esc(line.actual_text)}</b></td>"
                    f"<td>{esc(line.expected_text)}{gap}</td><td>{line.icon} {esc(line.verdict)}</td></tr>")
    period = f' <span class="muted small">기준 분기 {esc(recap.period)}</span>' if recap.period else ""
    source = ""
    if recap.guidance_url:
        source = (f'<p class="sub">가이던스 출처: {esc(recap.guidance_date)} · '
                  f'<a href="{esc(recap.guidance_url)}" target="_blank" rel="noopener">원문</a></p>')
    return (f"<h4>실적 3자 대조{period}</h4>"
            '<div class="table-wrap"><table class="tbl plain"><thead><tr><th>비교</th><th>실제</th>'
            f'<th>기대·약속</th><th>결과</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>{source}'
            '<p class="hint">회사가 약속한 값(가이던스)과 시장이 기대한 값(컨센서스)을 실제와 나란히 놓은 것입니다.</p>')


def track_block(track, korean=None) -> str:
    head = '<h4 style="margin-top:18px">과거 가이던스 이행 <span class="muted small">약속을 지켜온 회사인가</span></h4>'
    if track is None:
        return head + ('<p class="muted">아직 확인하지 않았습니다. ↻ → <b>보고서 읽기</b>를 누르면 과거 실적 '
                       '발표문에서 제시했던 매출 범위를 찾아 실제 실적과 맞춰봅니다.</p>')
    level_class = {"good": "v-good", "fair": "v-fair", "poor": "v-poor"}.get(track.level, "v-unknown")
    head += f'<p class="line"><span class="verdict {level_class}">{esc(track.summary)}</span></p>'
    if not track.items:
        return head + ('<p class="muted">과거 실적 발표문에서 전망 문장을 찾지 못했습니다. '
                       '회사가 수치 전망을 내지 않는 경우도 흔합니다.</p>')
    judged = [i for i in track.items if i.verdict != "확인 불가"]
    rows = []
    for item in judged[:8]:
        gap = ""
        if item.gap_pct is not None:
            gap = f' <span class="{"up" if item.gap_pct >= 0 else "down"}">({item.gap_pct:+.1f}%)</span>'
        period = item.target_end.isoformat() if item.target_end else "-"
        rows.append(f"<tr><td>{esc(item.filed)}</td><td>{esc(period)}</td><td>{esc(item.promised_text)}</td>"
                    f"<td>{esc(item.actual_text)}{gap}</td><td><b>{item.icon} {esc(item.verdict)}</b></td></tr>")
    table = ""
    if rows:
        table = ('<div class="table-wrap"><table class="tbl plain"><thead><tr><th>발표일</th><th>대상 분기</th>'
                 f"<th>{term('가이던스')}</th><th>실제 매출</th><th>결과</th></tr></thead>"
                 f"<tbody>{''.join(rows)}</tbody></table></div>")
    quotes = []
    for item in track.items[:6]:
        note = f'<p class="sub">{esc(item.reason)}</p>' if item.reason else ""
        quotes.append(f'<li class="ko"><p class="sub">{esc(item.filed)} · '
                      f'<a href="{esc(item.url)}" target="_blank" rel="noopener">원문 공시</a></p>'
                      + quote_ko(item.sentence, korean, tag="div") + note + "</li>")
    detail = (f'<details class="fold"><summary>회사가 쓴 문장 {len(track.items)}개 보기</summary>'
              f'<div class="fold-body"><ul class="ko-list">{"".join(quotes)}</ul></div></details>')
    caution = ('<p class="hint">매출 가이던스만 자동으로 맞춰봅니다. 조정 EPS·EBITDA 는 회사가 정의를 정하는 '
               '숫자라 SEC 제출 실적과 바로 비교할 수 없어 판정하지 않습니다.</p>')
    return head + table + detail + caution


def risk_block(risk, korean=None) -> str:
    if risk is None:
        return ('<p class="muted">아직 확인하지 않았습니다. ↻ → <b>보고서 읽기</b>를 누르면 이번 10-Q/10-K 의 '
                '위험 요인을 직전 보고서와 맞춰봅니다.</p>')
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
        items = "".join(f"<li><b>{esc(f.label)}</b> — {esc(f.meaning)}"
                        + quote_ko(f.sentence, korean, "risk", tag="div", topic=False) + "</li>"
                        for f in risk.flags)
        flags = f'<div class="box-warn"><b>⚠️ 무겁게 볼 표현</b><ul>{items}</ul></div>'
    if risk.added:
        quotes = "".join(quote_ko(p, korean, "risk") for p in risk.added)
        more = ""
        if risk.added_total > len(risk.added):
            more = f'<p class="muted small">… 새 문단이 모두 {risk.added_total}개 있습니다.</p>'
        open_attr = "" if risk.flags else " open"
        added = (f'<details class="fold"{open_attr}><summary>직전에 없던 위험 문단 {risk.added_total}개 '
                 '<span class="muted small">한글 요약 + 원문</span></summary>'
                 f'<div class="fold-body"><ul class="ko-list">{quotes}</ul>{more}</div></details>')
    elif risk.compared:
        added = '<p class="muted">직전 보고서와 견줘 새로 추가된 문단이 없습니다.</p>'
    elif risk.no_material_changes:
        added = ('<p class="muted">이 보고서는 위험 요인을 다시 싣지 않고 \'중요한 변화 없음\' 이라고만 '
                 '밝혔습니다. 전체 목록은 최신 10-K 에 있습니다.</p>')
    else:
        added = '<p class="muted">비교할 직전 보고서의 위험 요인을 찾지 못했습니다.</p>'
    removed = ""
    if risk.removed_total:
        removed = (f'<p class="sub">직전에 있다가 빠진 문단이 {risk.removed_total}개 있습니다. '
                   "위험이 해소됐을 수도, 서술을 합친 것일 수도 있어 판단하지 않습니다.</p>")
    return header + flags + added + removed


def insider_block(insider) -> str:
    if insider is None:
        return '<p class="muted">아직 확인하지 않았습니다. 감시 주기마다 자동으로 채워집니다.</p>'
    lines = [f'<p class="line"><b>{esc(insider.summary)}</b></p>']
    if insider.note:
        lines.append(f'<p class="hint">{esc(insider.note)}</p>')
    if insider.trades:
        rows = "".join(
            f"<tr><td>{esc(t.day)}</td><td>{esc(t.person)}<br><span class='muted small'>{esc(t.title)}</span></td>"
            f'<td><span class="{"up" if t.is_buy else "down"}">{"매수" if t.is_buy else "매도"}</span></td>'
            f"<td>{t.shares:,.0f}주</td><td>{money.price(t.price) if t.price else '-'}</td>"
            f"<td>{esc(_money(t.value))}</td>"
            f'<td><a href="{esc(t.url)}" target="_blank" rel="noopener">원문</a></td></tr>'
            for t in insider.trades[:20])
        lines.append('<div class="table-wrap"><table class="tbl plain"><thead><tr><th>날짜</th><th>사람</th>'
                     "<th>구분</th><th>수량</th><th>단가</th><th>금액</th><th></th></tr></thead>"
                     f"<tbody>{rows}</tbody></table></div>")
    lines.append('<p class="hint">P(자기 돈으로 매수)와 S(시장 매도)만 셉니다. RSU 수령·세금 납부용 반납·'
                 '옵션 행사는 매매 의사와 무관해 합계에서 뺐습니다.</p>')
    return "".join(lines)


def inputs_block(ctx, target, m) -> str:
    """직접 넣어야 정확해지는 값들 (컨센서스·매수가·메모)."""
    watch = target.watch
    eps = watch.consensus_eps if watch.consensus_eps is not None else ""
    revenue = watch.consensus_revenue if watch.consensus_revenue is not None else ""
    memo = watch.note or ""
    buy_price = watch.buy_price if watch.buy_price is not None else ""
    buy_shares = watch.buy_shares if watch.buy_shares is not None else ""
    # 한국 종목에 '$' 라고 적어두면 달러로 넣게 된다. 그러면 손익이 통째로 틀린다.
    korean = money.is_won(m.currency)
    buy_unit = "원" if korean else "$"
    buy_hint = "예: 75000" if korean else "예: 48.20"
    rev_hint = "예: 300000000000000" if korean else "예: 45000000000"
    back = esc(ctx.here)
    t = esc(target.ticker)
    hint = ""
    if m.surprise is None and not m.is_fund:
        hint = ('<p class="hint" style="margin-top:0">컨센서스를 넣어두면 실적 발표 직후 '
                '<b>어닝 서프라이즈(메모 2순위)</b>를 자동으로 계산합니다.</p>')
    consensus = "" if m.is_fund else f"""
  <form method="post" action="/action" class="form-row">
    <input type="hidden" name="action" value="consensus"><input type="hidden" name="ticker" value="{t}">
    <input type="hidden" name="back" value="{back}">
    <label>EPS 컨센서스<input class="field" type="text" name="eps" value="{esc(eps)}" placeholder="예: 1.01"></label>
    <label>매출 컨센서스<input class="field" type="text" name="revenue" value="{esc(revenue)}" placeholder="{rev_hint}"></label>
    <button type="submit" class="btn">저장</button></form>"""
    return f"""<h4 style="margin-top:14px">직접 입력</h4>{hint}{consensus}
  <form method="post" action="/action" class="form-row">
    <input type="hidden" name="action" value="position"><input type="hidden" name="ticker" value="{t}">
    <input type="hidden" name="back" value="{back}">
    <label>내 매수가({buy_unit})<input class="field" type="text" name="price" value="{esc(buy_price)}" placeholder="{buy_hint}"></label>
    <label>수량<input class="field" type="text" name="shares" value="{esc(buy_shares)}" placeholder="예: 10"></label>
    <button type="submit" class="btn">저장</button></form>
  <form method="post" action="/action" class="form-row">
    <input type="hidden" name="action" value="memo"><input type="hidden" name="ticker" value="{t}">
    <input type="hidden" name="back" value="{back}">
    <label>내 메모<input class="field" type="text" name="memo" value="{esc(memo)}" placeholder="왜 담았는지, 무엇을 지켜볼지"></label>
    <button type="submit" class="btn">저장</button></form>
  <p class="hint">비워서 저장하면 지워집니다.</p>"""


__all__ = ["not_found", "render"]
