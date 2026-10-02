"""종목 화면의 '바깥 집계' 조각들 — 실적 기대 한 장 · 주요 통계 · 옵션 · 보유자 · 수익률 비교 · 한눈에.

원칙은 frags.py 와 같다. 받은 값만 보여주고, 못 받으면 못 받았다고 적는다.
줄글 대신 표와 숫자 칩으로 보여주고, 길어질 것은 접어 둔다.
"""

from __future__ import annotations

import re
from datetime import date

from .. import money, performance
from ..estimates import RECOMMENDATION_KO
from ..metrics import _money
from ..timeutil import clock, dday
from .frags import _count, _side_cls, opinion_block, ratings_block
from .kit import empty, esc, term


# --------------------------------------------------------------------------
# 작은 도구
# --------------------------------------------------------------------------
def signed(value: float | None, digits: int = 1) -> str:
    """0.123 → +12.3%."""
    return "-" if value is None else f"{value * 100:+.{digits}f}%"


def plain_pct(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value * 100:.{digits}f}%"


def eps_text(value: float | None, currency: str) -> str:
    """주당 값. 1달러 미만이면 셋째 자리까지(-0.077 이 -0.08 로 뭉개지면 상회·하회가 뒤집혀 보인다)."""
    if value is None:
        return "-"
    if money.is_won(currency) or abs(value) >= 1 or round(value, 2) == round(value, 3):
        return money.price(value, currency)
    return f"-${-value:.3f}" if value < 0 else f"${value:.3f}"


def times(value: float | None) -> str:
    return "-" if value is None else f"{value:,.1f}x"


def cls_of(value: float | None) -> str:
    if value is None:
        return ""
    return "up" if value > 0 else ("down" if value < 0 else "")


def kv(rows) -> str:
    """(이름, 값 html, 덧붙임) → 두 칸 목록. 값이 없는 줄은 뺀다."""
    out = []
    for label, value, note in rows:
        if value in (None, "", "-"):
            continue
        extra = f' <small class="muted">{note}</small>' if note else ""
        out.append(f'<div><dt>{term(label)}</dt><dd>{value}{extra}</dd></div>')
    return f'<dl class="kv">{"".join(out)}</dl>' if out else ""


def group(title: str, body: str) -> str:
    return f'<section class="kv-group"><h4>{esc(title)}</h4>{body}</section>' if body else ""


def source_line(profile, extra: str = "") -> str:
    stamp = clock(profile.fetched_at) if profile is not None and profile.fetched_at else ""
    tail = f" · {esc(extra)}" if extra else ""
    return f'<p class="src">자료: Yahoo Finance 집계 · {esc(stamp)} 받음{tail}</p>'


def blocked(what: str) -> str:
    return empty(f"{what}을(를) 받지 못했습니다(야후가 막았거나 집계가 없는 종목). 빈칸을 추정으로 채우지 않습니다.")


# --------------------------------------------------------------------------
# 가이던스 ↔ 컨센서스 맞추기
# --------------------------------------------------------------------------
_YEAR = re.compile(r"full[- ]?year|fiscal(?:\s+year)?|annual|\byear\b", re.IGNORECASE)
_QUARTER = re.compile(r"quarter|\bQ[1-4]\b", re.IGNORECASE)


def period_kind(text: str | None) -> str | None:
    """가이던스 문장의 기간 → 0q(이번 분기) · 0y(올해). 모르면 None."""
    if not text:
        return None
    if re.search(r"full[- ]?year|fiscal year|annual", text, re.IGNORECASE):
        return "0y"
    if _QUARTER.search(text):
        return "0q"
    if _YEAR.search(text):
        return "0y"
    return None


def guided(guidance, metric: str, period: str, last_reported: date | None):
    """회사가 그 기간·항목에 대해 제시한 범위(GuidanceItem). 지난 분기 이야기면 쓰지 않는다."""
    if guidance is None or not getattr(guidance, "items", None):
        return None
    try:
        filed = date.fromisoformat(str(guidance.filing_date)[:10])
    except ValueError:
        return None
    # 마지막으로 발표된 분기보다 앞서 낸 가이던스는 그 분기에 대한 것이다(이미 지나감).
    if last_reported is not None and filed <= last_reported:
        return None
    for item in guidance.items:
        if item.metric != metric or item.low is None or item.unit != "$":
            continue
        if metric == "매출" and item.low < 1e5:
            continue
        if period_kind(item.period) == period:
            return item
    return None


def _mid(item) -> float:
    return (item.low + item.high) / 2 if item.high is not None else item.low


def versus(item, consensus: float | None) -> tuple[str, str] | None:
    """(문구, 색). 전문가 평균이 회사 가이던스 범위의 어디에 있나."""
    if item is None or consensus is None:
        return None
    if item.high is not None and item.low <= consensus <= item.high:
        return "가이던스 범위 안", ""
    gap = consensus / _mid(item) - 1 if _mid(item) else None
    if gap is None:
        return None
    if consensus > (item.high if item.high is not None else item.low):
        return f"가이던스보다 {gap * 100:+.1f}% 높게 예상", "up"
    return f"가이던스보다 {gap * 100:+.1f}% 낮게 예상", "down"


# --------------------------------------------------------------------------
# 실적 기대 한 장 — 회사가 말한 것 · 전문가 예상 · 지난 결과 · 의견
# --------------------------------------------------------------------------
def expect_chips(profile, guidance, price) -> list[tuple[str, str]]:
    """접힌 머리와 카드 맨 위에 쓰는 결론 칩. 숫자에서만 나온다."""
    chips: list[tuple[str, str]] = []
    if profile is None:
        return chips
    counts = profile.opinion_counts
    if counts:
        buy, hold, sell = counts
        overall = RECOMMENDATION_KO.get(profile.recommendation, "")
        label = f"의견 {overall} · " if overall else "의견 "
        chips.append((f"{label}매수 {buy}·보유 {hold}·매도 {sell}", _side_cls(profile.recommendation)))
    if profile.target_mean is not None and price:
        gap = profile.target_mean / price - 1
        chips.append((f"평균 목표가 {signed(gap)}", cls_of(gap)))
    history = [r for r in profile.eps_history if r.beat is not None]
    if history:
        beats = sum(1 for r in history if r.beat)
        chips.append((f"최근 {len(history)}분기 EPS {beats}번 상회", "up" if beats * 2 > len(history) else "down"))
    current = profile.estimate("0q")
    if current is not None and (current.up_30d or current.down_30d):
        up, down = current.up_30d or 0, current.down_30d or 0
        chips.append((f"30일 추정치 상향 {up}·하향 {down}", "up" if up > down else ("down" if down > up else "")))
    if current is not None:
        last = profile.eps_history[-1].quarter if profile.eps_history else None
        found = versus(guided(guidance, "매출", "0q", last), current.revenue)
        if found:
            chips.append((f"이번 분기 매출: 전문가 평균이 {found[0]}", found[1]))
    return chips


def chips_html(chips) -> str:
    return "".join(f'<span class="tag {cls}">{esc(text)}</span>' for text, cls in chips)


def expect(bot, target, m, today: date) -> str:
    profile = bot.profile_for(target)
    guidance = bot.cached_guidance().get(target.cik) if hasattr(bot, "cached_guidance") else None
    if profile is None:
        return blocked("애널리스트 집계")
    currency = getattr(m, "currency", money.USD) if m else money.USD
    price = getattr(m, "price", None) if m else None
    parts = []

    head = []
    if profile.earnings_dates:
        day = profile.earnings_dates[0]
        head.append(f'<span class="tag accent">다음 실적 {esc(day.isoformat())} · {esc(dday(today, day))}</span>')
    head.append(chips_html(expect_chips(profile, guidance, price)))
    parts.append(f'<div class="chip-row">{"".join(head)}</div>')

    table = expect_table(profile, guidance, currency)
    if table:
        parts.append(table)
    else:
        parts.append('<p class="muted small">애널리스트 예상치(분기·연간)가 집계되지 않은 종목입니다.</p>')

    left = eps_dots(profile, currency)
    right = opinion_block(profile, price, currency) if profile.has_analysts else ""
    if left or right:
        parts.append('<div class="two">'
                     + (f'<div><h4>지난 4분기 EPS · 실제 vs 예상</h4>{left}</div>' if left else "")
                     + (f'<div><h4>전문가 의견 · 목표가</h4>{right}</div>' if right else "")
                     + "</div>")

    folds = []
    revisions = revisions_table(profile)
    if revisions:
        folds.append(sub_fold("x-rev", "예상치 변화 (최근 90일)", revisions,
                              "실적 발표 전 추정치가 오르는 중인지 내리는 중인지"))
    yearly = yearly_table(profile, currency)
    if yearly:
        folds.append(sub_fold("x-year", "연간 매출 · 순이익", yearly, f"{len(profile.yearly)}년"))
    if profile.ratings:
        folds.append(sub_fold("x-ratings", "증권사별 최근 의견 변경",
                              ratings_block(profile, price, currency, limit=15),
                              f"{len(profile.ratings)}건 · 최근 {profile.ratings[0].day.isoformat()}"))
    parts.append("".join(folds))
    parts.append(source_line(profile, "회사 가이던스는 SEC 8-K 실적 발표문" if target.market == "us" else ""))
    return "".join(parts)


def sub_fold(key: str, title: str, body: str, headline: str = "", open_: bool = False) -> str:
    note = f'<span class="muted small">{esc(headline)}</span>' if headline else ""
    return (f'<details class="fold" data-keep="{esc(key)}"{" open" if open_ else ""}>'
            f'<summary><span>{esc(title)}</span>{note}</summary><div class="fold-body">{body}</div></details>')


def expect_table(profile, guidance, currency: str) -> str:
    last = profile.eps_history[-1].quarter if profile.eps_history else None
    rows = []
    for est in profile.estimates:
        lines = []
        if est.revenue is not None:
            lines.append(("매출", est.revenue, est.revenue_low, est.revenue_high, est.revenue_year_ago,
                          est.revenue_growth, est.revenue_analysts,
                          lambda v: _money(v, currency), guided(guidance, "매출", est.period, last)))
        if est.eps is not None:
            lines.append(("EPS", est.eps, est.eps_low, est.eps_high, est.eps_year_ago,
                          est.eps_growth, est.eps_analysts,
                          lambda v: eps_text(v, currency), guided(guidance, "EPS", est.period, last)))
        for i, (name, avg, low, high, ago, growth, n, fmt, item) in enumerate(lines):
            when = ""
            if i == 0:
                end = f"<small>{est.end.year}.{est.end.month:02d} 말</small>" if est.end else ""
                when = f'<td class="l" rowspan="{len(lines)}"><b>{esc(est.label)}</b><br>{end}</td>'
            guide = "-"
            if item is not None:
                guide = esc(item.range_text or "-")
                found = versus(item, avg)
                if found:
                    guide += f'<br><small class="{found[1]}">평균이 {esc(found[0])}</small>'
            span = f"{fmt(low)} ~ {fmt(high)}" if low is not None and high is not None else "-"
            count = f"<small> · {n}명</small>" if n else ""
            change = (f'<td class="{cls_of(growth)}">{signed(growth)}</td>' if name == "매출"
                      else eps_change(avg, ago, growth))
            rows.append(f"<tr>{when}<td class=\"l\">{name}</td><td>{guide}</td>"
                        f"<td><b>{esc(fmt(avg))}</b>{count}</td><td>{esc(span)}</td>"
                        f"<td>{esc(fmt(ago)) if ago is not None else '-'}</td>{change}</tr>")
    if not rows:
        return ""
    return ('<div class="table-wrap"><table class="tbl x-table"><thead><tr><th class="l">기간</th>'
            f'<th class="l">항목</th><th>회사 {term("가이던스")}</th><th>{term("전문가 평균")}</th>'
            '<th>전문가 범위</th><th>1년 전 실제</th><th>예상 성장</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def eps_change(now: float | None, before: float | None, growth: float | None) -> str:
    """EPS 성장률은 한쪽이라도 적자면 % 가 거꾸로 읽힌다(-0.03 → -0.07 이 '+133%' 로 나오는 식).
    그때는 % 대신 무슨 일이 일어나는지 말로 적는다."""
    if now is None or before is None:
        return f'<td class="{cls_of(growth)}">{signed(growth)}</td>'
    if before > 0 and now > 0:
        return f'<td class="{cls_of(now / before - 1)}">{signed(now / before - 1)}</td>'
    if before < 0 <= now:
        return '<td class="up">흑자 전환</td>'
    if before >= 0 > now:
        return '<td class="down">적자 전환</td>'
    if now > before:
        return '<td class="up">적자 축소</td>'
    if now < before:
        return '<td class="down">적자 확대</td>'
    return "<td>변화 없음</td>"


def eps_dots(profile, currency: str) -> str:
    """지난 분기들의 EPS: 속 빈 원 = 예상, 찬 원 = 실제(초록 상회·빨강 하회). 마지막은 다음 분기 예상."""
    history = [r for r in profile.eps_history if r.estimate is not None or r.actual is not None]
    upcoming = profile.estimate("0q")
    points = list(history)
    if not points:
        return ""
    values = [v for r in points for v in (r.actual, r.estimate) if v is not None]
    if upcoming is not None and upcoming.eps is not None:
        values.append(upcoming.eps)
    low, high = min(values), max(values)
    pad = (high - low) * 0.2 or abs(high) * 0.2 or 0.1
    low, high = low - pad, high + pad
    slots = len(points) + (1 if upcoming is not None and upcoming.eps is not None else 0)
    width, height, top, bottom = 460, 170, 14, 48
    step = width / slots

    def y(v):
        return top + (height - top - bottom) * (1 - (v - low) / (high - low))

    marks = []
    for i, r in enumerate(points):
        x = step * (i + 0.5)
        if r.estimate is not None:
            marks.append(f'<circle cx="{x:.1f}" cy="{y(r.estimate):.1f}" r="7" class="d-est"/>')
        if r.actual is not None:
            cls = "d-up" if r.beat else ("d-down" if r.beat is False else "d-na")
            marks.append(f'<circle cx="{x:.1f}" cy="{y(r.actual):.1f}" r="7" class="{cls}">'
                         f'<title>실제 {r.actual:.2f} · 예상 {r.estimate if r.estimate is not None else "-"}</title></circle>')
        label = f"{str(r.quarter.year)[2:]}.{r.quarter.month:02d}"
        verdict = ""
        if r.beat is not None:
            diff = r.actual - r.estimate
            verdict = (f'<text x="{x:.1f}" y="{height - 16}" class="{"d-tu" if r.beat else "d-td"}">'
                       f'{"상회" if r.beat else "하회"}</text>'
                       f'<text x="{x:.1f}" y="{height - 3}" class="d-tx">{diff:+.3g}</text>')
        marks.append(f'<text x="{x:.1f}" y="{height - 30}" class="d-tl">{label}</text>{verdict}')
    if slots > len(points):
        x = step * (slots - 0.5)
        marks.append(f'<circle cx="{x:.1f}" cy="{y(upcoming.eps):.1f}" r="7" class="d-est"/>'
                     f'<text x="{x:.1f}" y="{height - 30}" class="d-tl">다음</text>'
                     f'<text x="{x:.1f}" y="{height - 16}" class="d-tx">예상 {upcoming.eps:.2f}</text>')
    zero = ""
    if low < 0 < high:
        zero = f'<line x1="0" x2="{width}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="d-zero"/>'
    return (f'<svg class="eps-dots" viewBox="0 0 {width} {height}" role="img" aria-label="EPS 실제와 예상">'
            f'{zero}{"".join(marks)}</svg>'
            '<p class="legend"><span class="o"></span> 예상 <span class="f up"></span> 실제(상회) '
            '<span class="f down"></span> 실제(하회) · 단위: 주당 순이익</p>')


def revisions_table(profile) -> str:
    rows = []
    for est in profile.estimates:
        trend = est.eps_trend
        if not trend:
            continue
        cells = "".join(f"<td>{trend[k]:.2f}</td>" if k in trend else "<td>-</td>"
                        for k in ("90d", "60d", "30d", "7d", "now"))
        change = ""
        if "90d" in trend and "now" in trend and trend["90d"]:
            moved = trend["now"] - trend["90d"]
            change = f'<td class="{cls_of(moved)}">{moved:+.2f}</td>'
        else:
            change = "<td>-</td>"
        updown = (f"<td><span class=\"up\">▲{est.up_30d or 0}</span> "
                  f"<span class=\"down\">▼{est.down_30d or 0}</span></td>")
        rows.append(f'<tr><td class="l">{esc(est.label)}</td>{cells}{change}{updown}</tr>')
    if not rows:
        return ""
    return ('<div class="table-wrap"><table class="tbl"><thead><tr><th class="l">예상 EPS</th>'
            '<th>90일 전</th><th>60일 전</th><th>30일 전</th><th>7일 전</th><th>지금</th>'
            f'<th>90일 변화</th><th>30일 고친 수</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def yearly_table(profile, currency: str) -> str:
    rows = []
    for year, revenue, earnings in profile.yearly:
        margin = earnings / revenue if revenue and earnings is not None else None
        rows.append(f'<tr><td class="l">{year}</td><td>{esc(_money(revenue, currency))}</td>'
                    f'<td class="{cls_of(earnings)}">{esc(_money(earnings, currency))}</td>'
                    f'<td class="{cls_of(margin)}">{plain_pct(margin)}</td></tr>')
    if not rows:
        return ""
    return ('<div class="table-wrap"><table class="tbl"><thead><tr><th class="l">회계연도</th><th>매출</th>'
            f'<th>순이익</th><th>순이익률</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


# --------------------------------------------------------------------------
# 주요 통계 (야후 Statistics 에서 판단에 쓰는 것만)
# --------------------------------------------------------------------------
def stats(bot, target, m) -> str:
    profile = bot.profile_for(target)
    currency = getattr(m, "currency", money.USD) if m else money.USD
    price = getattr(m, "price", None) if m else None
    bars = getattr(m, "bars", None) or []
    closes = [(b.day, b.close) for b in bars]
    ma50 = (profile.fifty_day if profile else None) or performance.moving_average(closes, 50)
    ma200 = (profile.two_hundred_day if profile else None) or performance.moving_average(closes, 200)

    def vs(avg):
        if not avg or not price:
            return ""
        return f'<span class="{cls_of(price / avg - 1)}">지금 {signed(price / avg - 1)}</span>'

    trading = kv([
        ("50일 평균가", esc(money.price(ma50, currency)) if ma50 else None, vs(ma50)),
        ("200일 평균가", esc(money.price(ma200, currency)) if ma200 else None, vs(ma200)),
        ("베타", f"{profile.beta:.2f}" if profile and profile.beta is not None else None, "5년 월간"),
        ("52주 변화", _colored(profile.week52_change) if profile else None,
         esc(f"S&P 500 {signed(profile.sp52_change)}") if profile and profile.sp52_change is not None else ""),
        ("평균 거래량(3개월)", _count(profile.avg_volume) if profile and profile.avg_volume else None, ""),
        ("평균 거래량(10일)", _count(profile.avg_volume_10d) if profile and profile.avg_volume_10d else None, ""),
    ])
    if profile is None:
        body = group("주가 흐름", trading)
        return (body + blocked("야후 통계")) if body else blocked("야후 통계")

    valuation = kv([
        # 맨 위(현재가 × SEC 주식 수)와 같은 값을 먼저 — 한 화면에 두 시가총액이 보이지 않게
        ("시가총액", esc(_money(getattr(m, "market_cap", None) or profile.market_cap, currency)), ""),
        ("기업가치(EV)", esc(_money(profile.enterprise_value, currency)) if profile.enterprise_value else None, ""),
        ("PER", times(profile.trailing_pe) if profile.trailing_pe else None, "최근 12개월"),
        ("선행 PER", times(profile.forward_pe) if profile.forward_pe and profile.forward_pe > 0 else None, ""),
        ("PEG", f"{profile.peg:.2f}" if profile.peg else None, ""),
        ("PSR", times(profile.price_to_sales) if profile.price_to_sales else None, ""),
        ("PBR", times(profile.price_to_book) if profile.price_to_book else None, ""),
        ("EV/매출", times(profile.ev_revenue) if profile.ev_revenue else None, ""),
        ("EV/EBITDA", times(profile.ev_ebitda) if profile.ev_ebitda and profile.ev_ebitda > 0 else None, ""),
    ])
    shares = kv([
        ("발행주식", _count(profile.shares_out) + "주" if profile.shares_out else None, ""),
        ("유통주식", _count(profile.float_shares) + "주" if profile.float_shares else None,
         f"발행의 {profile.float_shares / profile.shares_out * 100:.0f}%"
         if profile.float_shares and profile.shares_out else ""),
        ("내부자 보유", plain_pct(profile.held_insiders) if profile.held_insiders is not None else None, ""),
        ("기관 보유", plain_pct(profile.held_institutions) if profile.held_institutions is not None else None, ""),
        ("공매도 비중", plain_pct(profile.short_pct_float, 2) if profile.short_pct_float is not None else None,
         "유통주식 대비"),
        ("공매도 소진일", f"{profile.short_ratio:.1f}일" if profile.short_ratio is not None else None,
         "잔고 ÷ 하루 거래량"),
        ("공매도 잔고", _count(profile.shares_short) + "주" if profile.shares_short else None,
         (f"전월 {signed(profile.shares_short / profile.shares_short_prior - 1)}"
          if profile.shares_short and profile.shares_short_prior else "")),
        ("배당수익률", plain_pct(profile.dividend_yield, 2) if profile.dividend_yield else None, ""),
    ])
    quality = kv([
        ("매출총이익률", plain_pct(profile.gross_margin) if profile.gross_margin is not None else None, ""),
        ("영업이익률", _colored(profile.operating_margin, signed_=False), ""),
        ("순이익률", _colored(profile.profit_margin, signed_=False), ""),
        ("ROE", _colored(profile.roe, signed_=False), ""),
        ("ROA", _colored(profile.roa, signed_=False), ""),
        ("매출 성장", _colored(profile.revenue_growth), "직전 분기, 1년 전 대비"),
        ("보유 현금", esc(_money(profile.total_cash, currency)) if profile.total_cash else None, ""),
        ("총부채", esc(_money(profile.total_debt, currency)) if profile.total_debt else None, ""),
        ("유동비율", f"{profile.current_ratio:.2f}" if profile.current_ratio else None, "1 미만이면 1년 안 갚을 돈이 더 많음"),
        ("부채비율(D/E)", f"{profile.debt_to_equity:.0f}%" if profile.debt_to_equity is not None else None, ""),
        ("잉여현금흐름", _colored_money(profile.free_cashflow, currency), "최근 12개월"),
    ])
    body = (group("가치 평가", valuation) + group("주가 흐름", trading)
            + group("주식 · 공매도", shares) + group("수익성 · 재무 (야후 집계)", quality))
    if not body:
        return blocked("야후 통계")
    when = f" · 공매도 기준일 {profile.short_date.isoformat()}" if profile.short_date else ""
    return (f'<div class="kv-grid">{body}</div>'
            + source_line(profile, "SEC 공시로 직접 계산한 값은 '재무' 칸" + when))


def _colored(value: float | None, signed_: bool = True) -> str | None:
    if value is None:
        return None
    text = signed(value) if signed_ else plain_pct(value)
    return f'<span class="{cls_of(value)}">{text}</span>'


def _colored_money(value: float | None, currency: str) -> str | None:
    if not value:
        return None
    return f'<span class="{cls_of(value)}">{esc(_money(value, currency))}</span>'


def stats_headline(profile, m) -> str:
    if profile is None:
        return ""
    bits = []
    if profile.forward_pe and profile.forward_pe > 0:
        bits.append(f"선행 PER {profile.forward_pe:.1f}x")
    elif profile.trailing_pe:
        bits.append(f"PER {profile.trailing_pe:.1f}x")
    if profile.beta is not None:
        bits.append(f"베타 {profile.beta:.2f}")
    if profile.short_pct_float is not None:
        bits.append(f"공매도 {profile.short_pct_float * 100:.1f}%")
    if profile.held_institutions is not None:
        bits.append(f"기관 {profile.held_institutions * 100:.0f}%")
    return " · ".join(bits)


# --------------------------------------------------------------------------
# 옵션 시장
# --------------------------------------------------------------------------
def options_card(bot, target, m, today: date) -> str:
    view = bot.options_for(target)
    if view is None or view.empty:
        return empty("옵션 체인을 받지 못했습니다(옵션이 없는 종목이거나 야후가 막음). 빈칸을 추정으로 채우지 않습니다.")
    currency = getattr(m, "currency", money.USD) if m else money.USD
    price = (getattr(m, "price", None) if m else None) or view.price
    blocks = []
    for expiry in view.expiries:
        blocks.append(expiry_block(expiry, price, currency, today))
    parts = [f'<div class="opt-grid">{"".join(blocks)}</div>']

    month = view.expiries[-1]
    chain = chain_table(month, price)
    if chain:
        parts.append(sub_fold("o-chain", f"행사가별 표 · 만기 {month.day.isoformat()}", chain,
                              "현재가 위아래 행사가의 거래량·미결제약정·내재변동성"))
    unusual = view.unusual()
    if unusual:
        rows = "".join(
            f'<tr><td class="l"><span class="tag {"up" if u.kind == "콜" else "down"}">{u.kind}</span> '
            f'{esc(money.price(u.contract.strike, currency))}</td><td>{esc(u.expiry.isoformat())}</td>'
            f'<td>{_count(u.contract.volume)}</td><td>{_count(u.contract.open_interest)}</td>'
            f'<td>{u.ratio:.1f}배</td></tr>' if u.ratio else
            f'<tr><td class="l"><span class="tag {"up" if u.kind == "콜" else "down"}">{u.kind}</span> '
            f'{esc(money.price(u.contract.strike, currency))}</td><td>{esc(u.expiry.isoformat())}</td>'
            f'<td>{_count(u.contract.volume)}</td><td>-</td><td>새 계약</td></tr>'
            for u in unusual)
        parts.append(sub_fold("o-unusual", "오늘 몰린 계약 (거래량 > 미결제약정)",
                              '<div class="table-wrap"><table class="tbl"><thead><tr><th class="l">계약</th>'
                              '<th>만기</th><th>거래량</th><th>미결제약정</th><th>배수</th></tr></thead>'
                              f'<tbody>{rows}</tbody></table></div>'
                              '<p class="src">새 자금이 들어온 자리일 수 있지만 산 쪽인지 판 쪽인지는 알 수 없습니다.</p>',
                              f"{len(unusual)}건"))
    parts.append(sub_fold("o-read", "읽는 법", '<ul class="bullets small">'
                          f'<li>{term("예상 움직임")}: 만기까지 옵션 시장이 매긴 움직임 폭(가장 가까운 행사가의 콜+풋 가격).</li>'
                          f'<li>{term("내재변동성")}: 연 단위 흔들림 기대치. 실적 발표 직전에 높아집니다.</li>'
                          f'<li>{term("풋/콜 비율")}: 1보다 크면 하락 대비 수요가 많음. 보유자의 보험일 수도 있습니다.</li>'
                          f'<li>{term("미결제약정")}이 몰린 행사가는 사람들이 주목하는 가격대입니다.</li></ul>'))
    stamp = clock(view.fetched_at) if view.fetched_at else ""
    later = len(view.all_dates)
    parts.append(f'<p class="src">자료: Yahoo Finance 옵션 체인(보통 15분 지연) · {esc(stamp)} 받음 · '
                 f'상장된 만기 {later}개 중 가까운 것과 30일 근처 것</p>')
    return "".join(parts)


def expiry_block(expiry, price, currency: str, today: date) -> str:
    days = expiry.days_left(today)
    move = expiry.straddle(price) if price else None
    iv = expiry.atm_iv(price) if price else None
    rows = []
    if move and price:
        rows.append(("예상 움직임", f"±{esc(money.price(move, currency))} <small>(±{move / price * 100:.1f}%)</small>",
                     f"{esc(money.price(price - move, currency))} ~ {esc(money.price(price + move, currency))}"))
    rows.append(("내재변동성", plain_pct(iv) if iv else None, "현재가에 가장 가까운 행사가"))
    rows.append(("풋/콜(거래량)", _ratio(expiry.pc_volume),
                 f"콜 {_count(expiry.call_volume)} · 풋 {_count(expiry.put_volume)}"))
    rows.append(("풋/콜(미결제)", _ratio(expiry.pc_oi),
                 f"콜 {_count(expiry.call_oi)} · 풋 {_count(expiry.put_oi)}"))
    top_call, top_put = expiry.top_oi(expiry.calls), expiry.top_oi(expiry.puts)
    if top_call:
        rows.append(("미결제약정 최다(콜)", esc(money.price(top_call.strike, currency)),
                     f"{_count(top_call.open_interest)}계약"))
    if top_put:
        rows.append(("미결제약정 최다(풋)", esc(money.price(top_put.strike, currency)),
                     f"{_count(top_put.open_interest)}계약"))
    return (f'<section class="kv-group"><h4>만기 {esc(expiry.day.isoformat())} <small class="muted">D-{days}</small></h4>'
            f'{kv(rows)}</section>')


def _ratio(value: float | None) -> str | None:
    if value is None:
        return None
    cls = "down" if value > 1 else "up"
    return f'<span class="{cls}">{value:.2f}</span>'


def chain_table(expiry, price) -> str:
    strikes = expiry.near(price)
    if not strikes:
        return ""
    atm = expiry.atm_strike(price)
    rows = []
    for k in strikes:
        call, put = expiry.at(expiry.calls, k), expiry.at(expiry.puts, k)

        def cells(c, reverse=False):
            if c is None:
                values = ["-", "-", "-"]
            else:
                values = [_count(c.volume) if c.volume else "-", _count(c.open_interest) if c.open_interest else "-",
                          plain_pct(c.iv, 0) if c.iv else "-"]
            if reverse:
                values.reverse()
            return "".join(f"<td>{v}</td>" for v in values)

        mark = ' class="atm"' if k == atm else ""
        rows.append(f"<tr{mark}>{cells(call)}<td class=\"strike\"><b>{k:,.2f}</b></td>{cells(put, True)}</tr>")
    return ('<div class="table-wrap"><table class="tbl chain"><thead><tr>'
            '<th>콜 거래량</th><th>콜 미결제</th><th>콜 IV</th><th class="strike">행사가</th>'
            '<th>풋 IV</th><th>풋 미결제</th><th>풋 거래량</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="src">굵은 줄 = 현재가에 가장 가까운 행사가</p>')


def options_headline(view, price) -> str:
    if view is None or view.empty:
        return ""
    expiry = view.expiries[-1]
    bits = []
    move = expiry.straddle(price) if price else None
    if move and price:
        bits.append(f"{expiry.day.month}/{expiry.day.day} 만기까지 ±{move / price * 100:.1f}% 예상")
    iv = expiry.atm_iv(price) if price else None
    if iv:
        bits.append(f"IV {iv * 100:.0f}%")
    if expiry.pc_volume is not None:
        bits.append(f"풋/콜 {expiry.pc_volume:.2f}")
    return " · ".join(bits)


# --------------------------------------------------------------------------
# 보유자 (기관 · 내부자 비중)
# --------------------------------------------------------------------------
def holders(bot, target, m) -> str:
    profile = bot.profile_for(target)
    if profile is None:
        return blocked("보유자 집계")
    currency = getattr(m, "currency", money.USD) if m else money.USD
    tiles = kv([
        ("내부자 보유", plain_pct(profile.held_insiders) if profile.held_insiders is not None else None, ""),
        ("기관 보유", plain_pct(profile.held_institutions) if profile.held_institutions is not None else None, ""),
        ("기관 수", f"{profile.institutions_count:,}곳" if profile.institutions_count else None, ""),
        ("유통주식", _count(profile.float_shares) + "주" if profile.float_shares else None, ""),
    ])
    parts = [tiles] if tiles else []
    if profile.institutions:
        rows = "".join(
            f'<tr><td class="l">{esc(h.name)}</td><td>{plain_pct(h.pct, 2)}</td>'
            f'<td>{_count(h.shares) + "주" if h.shares else "-"}</td><td>{esc(_money(h.value, currency))}</td>'
            f'<td class="{cls_of(h.change)}">{signed(h.change)}</td>'
            f'<td>{esc(h.reported.isoformat()) if h.reported else "-"}</td></tr>'
            for h in profile.institutions)
        parts.append('<h4>상위 기관</h4><div class="table-wrap"><table class="tbl"><thead><tr><th class="l">기관</th>'
                     '<th>지분</th><th>주식 수</th><th>가치</th><th>직전 대비</th><th>보고일</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></div>')
    if not parts:
        return empty("보유자 집계가 없는 종목입니다.")
    return "".join(parts) + source_line(profile, "기관 보유는 분기마다 내는 13F 보고 기준이라 늦습니다")


# --------------------------------------------------------------------------
# 수익률 비교 (이 종목 vs 지수)
# --------------------------------------------------------------------------
def perf(bot, target, m, today: date) -> str:
    symbol, name = performance.index_for(target.market, target.price_symbol)
    stock = bot.stock_history(target)
    if not stock and m is not None and m.bars:
        stock = [(b.day, b.close) for b in m.bars]
    if not stock:
        return '<p class="muted small">일봉을 받지 못해 수익률을 계산하지 않았습니다.</p>'
    index = bot.index_history(symbol)
    rows = performance.compare(stock, index, today)
    cells = []
    for row in rows:
        if row.stock is None:
            continue
        idx = (f'<small>{esc(name)} {signed(row.index)}</small>' if row.index is not None
               else f"<small>{esc(name)} -</small>")
        gap = ""
        if row.gap is not None:
            gap = f'<small class="{cls_of(row.gap)}">차이 {row.gap * 100:+.1f}%p</small>'
        cells.append(f'<div class="pf"><span>{esc(row.label)}</span><b class="{cls_of(row.stock)}">'
                     f'{signed(row.stock)}</b>{idx}{gap}</div>')
    if not cells:
        return ""
    note = "" if index else f' · {esc(name)} 일봉을 받지 못해 비교는 비웠습니다'
    return (f'<div class="pf-row">{"".join(cells)}</div>'
            f'<p class="src">종가 기준 주가 수익률(배당 제외) · 기간 첫날이 휴장이면 그 전 거래일{note}</p>')


# --------------------------------------------------------------------------
# 한눈에 — 맨 위 숫자 띠
# --------------------------------------------------------------------------
def glance(bot, target, m, today: date, earnings=None) -> str:
    profile = bot.profile_for(target)
    currency = getattr(m, "currency", money.USD) if m else money.USD
    price = getattr(m, "price", None) if m else None
    tiles = []

    def tile(label, value, note="", cls="", href=""):
        if value in (None, "", "-"):
            return
        # 칸 전체가 고리라 안에 용어 고리(term)를 또 넣지 않는다 — 고리 안의 고리는 화면을 깨뜨린다
        inner = (f'<span class="k">{esc(label)}</span><span class="v {cls}">{value}</span>'
                 + (f"<small>{note}</small>" if note else ""))
        tiles.append(f'<a class="gl" href="{href}">{inner}</a>' if href else f'<div class="gl">{inner}</div>')

    cap = (m.market_cap if m is not None else None) or (profile.market_cap if profile else None)
    tile("시가총액", esc(_money(cap, currency)) if cap else None)
    per = (m.per if m is not None and m.per else None) or (profile.trailing_pe if profile else None)
    fwd = profile.forward_pe if profile and profile.forward_pe and profile.forward_pe > 0 else None
    if per:
        tile("PER", times(per), f"선행 {fwd:.1f}x" if fwd else "", href="#sec-stats")
    elif fwd:
        tile("선행 PER", times(fwd), "지금은 적자라 PER 없음", href="#sec-stats")
    growth = m.revenue_growth if m is not None else None
    tile("매출 성장", _colored(growth), "1년 전 대비", href="#sec-fin")
    margin = m.op_margin if m is not None else None
    tile("영업이익률", _colored(margin, signed_=False), href="#sec-fin")
    if profile is not None:
        counts = profile.opinion_counts
        if counts:
            buy, hold, sell = counts
            overall = RECOMMENDATION_KO.get(profile.recommendation, "") or "의견"
            tile("컨센서스", f'<span class="{_side_cls(profile.recommendation)}">{esc(overall)}</span>',
                 f"매수 {buy}·보유 {hold}·매도 {sell}", href="#sec-expect")
        if profile.target_mean is not None:
            gap = profile.target_mean / price - 1 if price else None
            tile("평균 목표가", esc(money.price(profile.target_mean, currency)),
                 f'<span class="{cls_of(gap)}">{signed(gap)}</span>' if gap is not None else "", href="#sec-expect")
    # 프로그램이 확정 일정을 알면 그것을, 과거 간격으로 추정한 것뿐이면 야후 일정을 먼저 쓴다
    day = None
    if earnings is not None and not earnings.estimated:
        day = earnings.day
    elif profile is not None and profile.earnings_dates:
        day = profile.earnings_dates[0]
    elif earnings is not None:
        day = earnings.day
    if day is not None and day >= today:
        tile("다음 실적", esc(f"{day.month}/{day.day}"), esc(dday(today, day)), href="#sec-expect")
    if profile is not None:
        if profile.week52_change is not None:
            sp = esc(f"S&P {signed(profile.sp52_change)}") if profile.sp52_change is not None else ""
            tile("52주 변화", _colored(profile.week52_change), sp, href="#sec-chart")
        if profile.short_pct_float is not None:
            tile("공매도 비중", plain_pct(profile.short_pct_float), "유통주식 대비", href="#sec-stats")
        if profile.beta is not None:
            tile("베타", f"{profile.beta:.2f}", href="#sec-stats")
    if not tiles:
        return ""
    tail = "" if profile is not None else '<p class="src">야후 집계를 받지 못해 일부 칸이 빠졌습니다.</p>'
    return f'<div class="glance">{"".join(tiles)}</div>{tail}'


def company_extra(profile) -> str:
    """경영진 표 · 주소. 회사 정보 조각 아래에 붙인다."""
    if profile is None:
        return ""
    parts = []
    if profile.officers:
        rows = "".join(
            f'<tr><td class="l"><b>{esc(o.name)}</b></td><td class="l">{esc(o.title)}</td>'
            f'<td>{esc(_money(o.pay)) if o.pay else "-"}</td>'
            f'<td>{esc(_money(o.exercised)) if o.exercised else "-"}</td>'
            f'<td>{o.born or "-"}</td></tr>'
            for o in profile.officers)
        parts.append('<h4>주요 경영진</h4><div class="table-wrap"><table class="tbl plain-l"><thead><tr>'
                     '<th class="l">이름</th><th class="l">직책</th><th>보수</th><th>행사한 옵션</th>'
                     f'<th>출생</th></tr></thead><tbody>{rows}</tbody></table></div>'
                     '<p class="src">보수·옵션은 직전 회계연도, 단위 달러(야후 집계 · 원천은 위임장 DEF 14A)</p>')
    contact = [x for x in (profile.address, profile.phone) if x]
    if contact:
        parts.append(f'<p class="muted small">{esc(" · ".join(contact))}</p>')
    return "".join(parts)


__all__ = ["company_extra", "expect", "glance", "holders", "options_card", "perf", "stats"]
