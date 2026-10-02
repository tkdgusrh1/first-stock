"""쪽이 먼저 뜬 뒤 따로 받아 끼우는 조각들 (/frag/…).

여기는 **바깥에 물어봐도 되는 자리**다. 브라우저가 쪽과 따로 부르므로 몇 초
걸려도 쪽이 멈추지 않는다. 받지 못하면 받지 못했다고 적는다 — 빈칸을 추정값으로
메우지 않는다.
"""

from __future__ import annotations

from .. import markets, money
from ..estimates import RECOMMENDATION_KO, links_for
from ..news import as_entry
from ..timeutil import clock
from .kit import card, empty, esc, icon, news_item


# --------------------------------------------------------------------------
# 종목 뉴스
# --------------------------------------------------------------------------
def stock_news(bot, target, known: set[str]) -> str:
    items = bot.ticker_news(target)
    alerted = [n for n in bot.state.news(80)
               if target.ticker.upper() in {str(t).upper() for t in n.get("tickers", [])}]
    parts = []
    if alerted:
        parts.append('<div class="day-label">속보로 알린 것</div>'
                     + "".join(news_item(n, known, compact=True) for n in alerted[:5]))
    if items:
        entries = [as_entry(i) for i in items]
        bot.korean_titles(entries, limit=12)
        parts.append(f'<div class="day-label">최신 뉴스<span class="n">{len(items)}건</span></div>'
                     + "".join(news_item(e, known, compact=True) for e in entries))
    if not parts:
        where = "Google 뉴스(한글)" if target.market == markets.KR else "Yahoo Finance·Google 뉴스"
        return empty(f"최근 기사를 찾지 못했습니다. {esc(where)} 피드가 막혔거나 기사가 없습니다. "
                     "10분 뒤 다시 받습니다.")
    return '<div class="news-list">' + "".join(parts) + "</div>"


def headlines(bot, known: set[str]) -> str:
    items = bot.latest_headlines()
    if not items:
        return empty("지금 헤드라인을 받지 못했습니다(매체 피드가 막혔을 수 있습니다). 3분 뒤 다시 받습니다.")
    entries = [as_entry(i) for i in items]
    bot.korean_titles(entries, limit=20)
    return '<div class="news-list">' + "".join(news_item(e, known) for e in entries) + "</div>"


# --------------------------------------------------------------------------
# 목표가 · 투자의견 (야후 집계)
# --------------------------------------------------------------------------
def analyst(bot, target, m) -> str:
    profile = bot.profile_for(target)
    links = "".join(f'<li><a href="{esc(url)}" target="_blank" rel="noopener">{esc(name)}</a></li>'
                    for name, url, _hint in links_for(markets.display(target.ticker))[:2])
    if profile is None:
        return empty("애널리스트 집계를 받지 못했습니다(야후가 막았거나 집계가 없는 종목). "
                     "목표가를 추정해서 채우지 않습니다.") + (
            f'<div class="card-body"><ul class="bullets small">{links}</ul></div>'
            if target.market == markets.US else "")
    if not profile.has_analysts:
        return empty("이 종목은 애널리스트 집계가 없습니다.")

    currency = getattr(m, "currency", money.USD) if m else money.USD
    price = getattr(m, "price", None) if m else None
    parts = []

    counts = profile.opinion_counts
    if counts:
        buy, hold, sell = counts
        total = buy + hold + sell
        overall = RECOMMENDATION_KO.get(profile.recommendation, "")
        parts.append(
            '<div style="display:flex;gap:18px;align-items:center;padding:4px 0 14px">'
            f'{_donut(buy, hold, sell)}<div><div class="muted small">전체 의견 '
            + (f'<span class="tag {_side_cls(profile.recommendation)}">{esc(overall)}</span>' if overall else "")
            + f'</div><div style="margin-top:6px;font-size:13.5px"><span class="up">● 매수 {buy}</span> · '
            f'<span class="warn-line">● 보유 {hold}</span> · <span class="down">● 매도 {sell}</span></div>'
            f'<div class="muted small">이번 달 집계 {total}명</div></div></div>')

    if profile.target_mean is not None:
        upside = ""
        if price:
            gap = (profile.target_mean - price) / price * 100
            upside = f'<span class="{"up" if gap >= 0 else "down"}"> ({gap:+.1f}%)</span>'
        parts.append(
            '<dl class="stat-grid" style="grid-template-columns:1fr 1fr">'
            f'<div class="stat"><dt>평균 목표가</dt><dd>{esc(money.price(profile.target_mean, currency))}'
            f'<small>{upside}</small></dd></div>'
            f'<div class="stat"><dt>현재가</dt><dd>{esc(money.price(price, currency)) if price else "-"}</dd></div>'
            "</dl>")
        if profile.target_low and profile.target_high and profile.target_high > profile.target_low:
            span = profile.target_high - profile.target_low

            def pos(value):
                return max(0.0, min(100.0, (value - profile.target_low) / span * 100))

            now_mark = (f'<i style="left:{pos(price):.1f}%" title="현재가"></i>' if price else "")
            parts.append(
                '<div class="range52" style="padding-top:14px">'
                f'<span>최저 <b>{esc(money.price(profile.target_low, currency))}</b></span>'
                f'<div class="bar">{now_mark}<i style="left:{pos(profile.target_mean):.1f}%;'
                'background:var(--up)" title="평균 목표가"></i></div>'
                f'<span>최고 <b>{esc(money.price(profile.target_high, currency))}</b></span></div>'
                '<p class="hint">진한 막대 = 현재가, 초록 막대 = 평균 목표가. '
                f'목표가를 낸 애널리스트 {profile.analysts or "-"}명.</p>')

    if profile.ratings:
        rows = []
        for rating in profile.ratings[:8]:
            target_text = ""
            if rating.target:
                gap = f" ({(rating.target - price) / price * 100:+.1f}%)" if price else ""
                target_text = (f'<div style="text-align:right"><b>{esc(money.price(rating.target, currency))}</b>'
                               f'<div class="small {"up" if price and rating.target >= price else "down"}">'
                               f'{esc(gap)}</div></div>')
            grade = rating.grade_ko or rating.to_grade
            rows.append(
                f'<div class="ev" style="grid-template-columns:minmax(0,1fr) auto">'
                f'<div class="ev-name"><b>{esc(rating.firm)}</b>'
                f'<span><span class="tag {_side_cls(rating.side)}">{esc(grade)}</span> '
                f'{esc(rating.action_ko)} · {esc(rating.day.isoformat())}'
                + (f' · 이전 {esc(rating.from_grade)}' if rating.from_grade and rating.from_grade != rating.to_grade else "")
                + f'<span class="muted"> ({esc(rating.to_grade)})</span></span></div>{target_text}</div>')
        parts.append('<div class="day-label" style="padding-left:0">최근 평가</div>'
                     + f'<div style="margin:0 -22px">{"".join(rows)}</div>')

    stamp = clock(profile.fetched_at) if profile.fetched_at else ""
    parts.append(f'<p class="hint">자료: {esc(profile.source)} 집계 · {esc(stamp)} 받음. '
                 "증권사 의견은 틀릴 수 있고, 목표가는 보통 12개월 뒤를 가리킵니다.</p>")
    return f'<div class="card-body">{"".join(parts)}</div>'


def _side_cls(side: str) -> str:
    return {"buy": "up", "strong_buy": "up", "hold": "warn", "sell": "down",
            "underperform": "down"}.get(side, "")


def _donut(buy: int, hold: int, sell: int) -> str:
    total = buy + hold + sell
    radius, stroke = 34, 10
    length = 2 * 3.14159265 * radius
    segments, offset = [], 0.0
    for count, color in ((buy, "var(--up)"), (hold, "var(--warn)"), (sell, "var(--down)")):
        if not count:
            continue
        part = count / total * length
        segments.append(f'<circle r="{radius}" cx="45" cy="45" fill="none" stroke="{color}" '
                        f'stroke-width="{stroke}" stroke-dasharray="{part:.2f} {length - part:.2f}" '
                        f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 45 45)"/>')
        offset += part
    return (f'<svg width="90" height="90" viewBox="0 0 90 90" role="img" aria-label="투자의견 {total}명">'
            f'<circle r="{radius}" cx="45" cy="45" fill="none" stroke="var(--line)" stroke-width="{stroke}"/>'
            f'{"".join(segments)}<text x="45" y="47" text-anchor="middle" font-size="18" font-weight="800" '
            f'fill="currentColor">{total}</text><text x="45" y="62" text-anchor="middle" font-size="9" '
            'fill="var(--muted)">투자의견</text></svg>')


# --------------------------------------------------------------------------
# 공매도 (야후 집계) — 재무 카드 아래에 붙인다
# --------------------------------------------------------------------------
def short_interest(profile, currency: str) -> str:
    if profile is None or (profile.short_pct_float is None and profile.short_ratio is None):
        return ""
    tiles = []
    if profile.short_pct_float is not None:
        tiles.append(("공매도 비중(유통주식 대비)", f"{profile.short_pct_float * 100:.2f}%", ""))
    if profile.short_ratio is not None:
        tiles.append(("공매도 소진일", f"{profile.short_ratio:.2f}일", "공매도 잔고 ÷ 하루 평균 거래량"))
    if profile.shares_short is not None:
        change = ""
        if profile.shares_short_prior:
            change = f"전월 대비 {(profile.shares_short / profile.shares_short_prior - 1) * 100:+.1f}%"
        tiles.append(("공매도 잔고", f"{_count(profile.shares_short)}주", change))
    if profile.dividend_yield is not None:
        tiles.append(("배당수익률", f"{profile.dividend_yield * 100:.2f}%", ""))
    if profile.beta is not None:
        tiles.append(("베타", f"{profile.beta:.2f}", "시장이 1% 움직일 때 평균 움직임"))
    when = f" · 기준일 {profile.short_date.isoformat()}" if profile.short_date else ""
    cells = "".join(f'<div class="stat"><dt>{esc(k)}</dt><dd>{esc(v)}'
                    + (f'<br><small>{esc(n)}</small>' if n else "") + "</dd></div>" for k, v, n in tiles)
    return (f'<h4 style="margin-top:0">공매도 · 배당 <span class="muted small">Yahoo Finance 집계{esc(when)}</span></h4>'
            f'<dl class="stat-grid">{cells}</dl>')


# --------------------------------------------------------------------------
# 장중 거래량 — 오늘 이 시각까지 vs 지난 거래일들의 같은 시각까지
# --------------------------------------------------------------------------
def intraday(bot, target, m) -> str:
    data = bot.intraday_for(target)
    if data is None or data.today is None or not data.today.minutes:
        return ('<p class="muted small" style="margin-top:14px">장중 5분봉을 받지 못했습니다 '
                "(제공처가 막았거나 거래가 없는 날). 그림을 추정으로 채우지 않습니다.</p>")
    today = data.today
    minute_now = today.minutes[-1]
    cum_now = today.total_until(minute_now) or 0.0
    same, days = data.same_time_average(minute_now)
    full, full_days = data.full_day_average()
    length = max(5, data.close_minute - data.open_minute)

    lines = [f'<div class="stat"><dt>{today.day.month}/{today.day.day} 누적 거래량</dt>'
             f'<dd>{_count(cum_now)}</dd></div>']
    if same:
        gap = (cum_now / same - 1) * 100
        lines.append(f'<div class="stat"><dt>같은 시각 {days}일 평균</dt><dd>{_count(same)}'
                     f'<small class="{"up" if gap >= 0 else "down"}"> {gap:+.0f}%</small></dd></div>')
    if full:
        lines.append(f'<div class="stat"><dt>하루 전체 {full_days}일 평균</dt><dd>{_count(full)}</dd></div>')
    summary = f'<dl class="stat-grid" style="margin-top:16px">{"".join(lines)}</dl>'

    # --- 그림 ---
    width, height, pad_l, pad_b, pad_t, pad_r = 640, 200, 44, 22, 10, 44
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    averages = []
    for minute in range(0, length + 1, 5):
        value, _n = data.same_time_average(minute)
        if value is not None:
            averages.append((minute, value))
    cumulative = today.cumulative()
    top = max([v for _m, v in cumulative] + [v for _m, v in averages] + [1.0])

    def x_of(minute):
        return pad_l + plot_w * min(max(minute, 0), length) / length

    def y_of(value):
        return pad_t + plot_h * (1 - value / top)

    def path(points):
        return " ".join(f"{x_of(mi):.1f},{y_of(v):.1f}" for mi, v in points)

    closes = [(mi, c) for mi, c in zip(today.minutes, today.closes) if c is not None]
    price_line, price_axis = "", ""
    if len(closes) >= 2:
        low, high = min(c for _m, c in closes), max(c for _m, c in closes)
        spread = (high - low) or 1.0
        points = " ".join(f"{x_of(mi):.1f},{pad_t + plot_h * (1 - (c - low) / spread):.1f}" for mi, c in closes)
        price_line = f'<polyline points="{points}" fill="none" stroke="var(--muted)" stroke-width="1" opacity=".7"/>'
        currency = getattr(m, "currency", money.USD)
        price_axis = (f'<text x="{width - 4}" y="{pad_t + 8}" text-anchor="end" font-size="10" fill="var(--muted)">'
                      f'{esc(money.price(high, currency))}</text>'
                      f'<text x="{width - 4}" y="{pad_t + plot_h}" text-anchor="end" font-size="10" '
                      f'fill="var(--muted)">{esc(money.price(low, currency))}</text>')

    def hhmm(minute):
        total = data.open_minute + minute
        return f"{total // 60:02d}:{total % 60:02d}"

    ticks = "".join(
        f'<text x="{x_of(mi):.1f}" y="{height - 6}" text-anchor="{anchor}" font-size="10" fill="var(--muted)">'
        f'{hhmm(mi)}</text>'
        for mi, anchor in ((0, "start"), (length // 2, "middle"), (length, "end")))
    svg = (
        f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:auto;margin-top:12px" role="img" '
        'aria-label="장중 누적 거래량">'
        f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" y2="{pad_t + plot_h}" stroke="var(--line)"/>'
        f'<text x="4" y="{pad_t + 8}" font-size="10" fill="var(--muted)">{esc(_count(top))}</text>'
        f'<text x="4" y="{pad_t + plot_h}" font-size="10" fill="var(--muted)">0</text>'
        f'{price_line}'
        + (f'<polyline points="{path(averages)}" fill="none" stroke="var(--warn)" stroke-width="1.6" '
           'stroke-dasharray="4 3"/>' if len(averages) >= 2 else "")
        + f'<polyline points="{path(cumulative)}" fill="none" stroke="var(--accent)" stroke-width="2.2"/>'
        f'{price_axis}{ticks}</svg>')
    legend = ('<p class="hint" style="margin-top:4px"><span style="color:var(--accent)">━</span> 오늘 누적 · '
              f'<span style="color:var(--warn)">┅</span> 지난 {days}거래일 같은 시각 평균 · '
              '<span class="muted">─</span> 주가(오른쪽 눈금) · 시각은 거래소 현지 시각 · 5분봉 · Yahoo Finance</p>')
    return summary + svg + legend


def _count(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 1e8:
        return f"{value / 1e8:.2f}억"
    if value >= 1e4:
        return f"{value / 1e4:,.1f}만"
    return f"{value:,.0f}"


# --------------------------------------------------------------------------
# 회사 정보
# --------------------------------------------------------------------------
def company(bot, target, m) -> str:
    profile = bot.profile_for(target)
    industry = bot.cached_industries().get(target.cik)
    rows = []
    if profile is not None:
        for label, value in (("거래소", profile.exchange), ("섹터", profile.sector),
                             ("산업", profile.industry)):
            if value:
                rows.append((label, esc(value)))
        if profile.employees:
            rows.append(("직원 수", f"{profile.employees:,}명"))
        if profile.website:
            rows.append(("웹사이트", f'<a href="{esc(profile.website)}" target="_blank" rel="noopener">'
                                   f'{esc(profile.website.replace("https://", "").replace("http://", ""))} '
                                   f'{icon("ext", True)}</a>'))
    if industry is not None and getattr(industry, "sic", ""):
        rows.append(("SEC 업종", f"{esc(industry.description or '')} (SIC {esc(industry.sic)})"))
    if m is not None and m.shares:
        rows.append(("발행주식수", esc(money.shares(m.shares, m.currency))))
    if not rows and (profile is None or not profile.summary):
        return empty("회사 정보를 받지 못했습니다.")
    table = "".join(f'<div class="ev" style="grid-template-columns:90px minmax(0,1fr)">'
                    f'<div class="ev-when">{esc(k)}</div><div class="ev-name">{v}</div></div>'
                    for k, v in rows)
    summary = ""
    if profile is not None and profile.summary:
        text = profile.summary
        machine = ""
        found = bot.translated(f"company:{target.ticker}", text[:1500])
        if found:
            machine = (f'<p style="font-size:13.5px">{esc(found[0])} '
                       f'<span class="ko-mark" title="자동 번역이라 틀릴 수 있습니다">{esc(found[1])} 번역</span></p>')
        summary = (f'<div class="card-body" style="padding-top:12px">{machine}'
                   '<details class="ko-src"' + ("" if machine else " open")
                   + f'><summary>영어 원문</summary><p class="quote">{esc(text)}</p></details></div>')
    return table + summary


__all__ = ["analyst", "company", "headlines", "intraday", "short_interest", "stock_news", "card"]


CATALYST_DAYS = 14
CATALYST_LOOK = 10          # 종합 점수 상위 몇 개의 기사를 볼지(기사 피드를 종목마다 받아서 많이 못 본다)


def catalysts(bot, market: str) -> str:
    """발굴 후보 중 최근 2주 안에 '호재로 읽히는' 기사가 난 종목. 대기업이 아니어도 된다.

    호재라는 판단은 제목의 표현으로만 한다(계약 수주·실적 예상 상회·가이던스 상향·FDA 승인 등).
    방향이 모호하면 넣지 않는다. 뉴스는 이미 주가에 들어갔을 수 있어 '살펴볼 이유' 일 뿐이다.
    """
    from datetime import datetime, timedelta, timezone

    from ..news import as_entry, positive_catalyst

    picks = sorted(bot.all_picks(market), key=lambda p: (-(p.total or 0), p.ticker))
    seen, look = set(), []
    for p in picks:
        if p.ticker not in seen:
            seen.add(p.ticker)
            look.append(p)
        if len(look) >= CATALYST_LOOK:
            break
    if not look:
        return empty("아직 훑어본 후보가 없습니다. 감시가 돌면서 후보를 계속 봅니다.")
    since = datetime.now(timezone.utc) - timedelta(days=CATALYST_DAYS)
    rows = []
    for p in look:
        try:
            items = bot.candidate_news(p.ticker, p.name, market)
        except Exception:
            items = []
        good = []
        for item in items:
            label = positive_catalyst(item.title)
            when = item.published
            if label and when and (when if when.tzinfo else when.replace(tzinfo=timezone.utc)) >= since:
                good.append((label, as_entry(item)))
        if not good:
            continue
        entries = [e for _, e in good[:2]]
        bot.korean_titles(entries, limit=2)
        lines = "".join(
            f'<li><span class="tag up">{esc(label)}</span> '
            f'<a href="{esc(e.get("url") or "#")}" target="_blank" rel="noopener">'
            f'{esc(e.get("title_ko") or e.get("title", ""))}</a>'
            + (f'<div class="news-orig">{esc(e.get("title", ""))}</div>' if e.get("title_ko") else "")
            + f' <span class="muted small">{esc(e.get("publisher") or "")} · {esc(str(e.get("when", ""))[:10])}</span></li>'
            for label, e in good[:2])
        score = f'<span class="pk-score" title="종합 점수(100점 만점)">{p.total:.0f}점</span>' if p.total is not None else ""
        rows.append(f'<div class="cat-row"><div class="cat-head"><b>{esc(p.name or p.ticker)}</b> '
                    f'<span class="muted">{esc(markets.display(p.ticker))}</span>{score}</div><ul>{lines}</ul></div>')
    if not rows:
        return empty(f"종합 점수 상위 {len(look)}개 후보에서 최근 {CATALYST_DAYS}일 안에 호재로 읽히는 기사를 찾지 못했습니다.")
    return ("".join(rows) + '<p class="hint">기사 제목의 표현으로만 고른 것입니다. <b>이미 주가에 반영됐을 수 있고</b>, '
            "제목과 본문이 다를 수 있습니다. 원문·공시를 꼭 확인하세요.</p>")
