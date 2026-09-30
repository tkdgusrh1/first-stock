"""시장 — 지수 · 환율 · 경제지표 · 장 상태 · 휴장일.

경제지표는 '언제 나오나(캘린더)' 가 아니라 '지금 얼마인가' 다. 개별 종목 실적과
무관하게 주가 전체를 눌렀다 푸는 배경이라 따로 본다.
"""

from __future__ import annotations

from .. import markets, visuals
from ..macro import FRED_HOME
from ..market_calendar import upcoming_market_days
from ..timeutil import clock, dday, kdate
from .kit import card, empty, esc, page_head, term


def render(ctx) -> str:
    bot = ctx.bot
    snapshot = bot.market_snapshot()
    head = page_head("시장", "지수·환율은 1분마다, 경제지표는 6시간마다 새로 받습니다")

    status = []
    for key in (markets.US, markets.KR):
        state, shown, guessed = bot.market_state(key)
        status.append(
            f'<div class="card idx"><div class="idx-name"><span class="state-dot {esc(state)}"></span>'
            f'{esc(markets.MARKET_NAME[key])} 증시</div>'
            f'<div class="idx-val" style="font-size:18px">{esc(markets.STATE_LABEL[state])}'
            + (' <span class="tag warn">어림</span>' if guessed else "") + "</div>"
            f'<div class="muted small">{esc(markets.hours_text(key))} · 현지 {esc(shown)}</div></div>')
    parts = [f'<div class="idx-grid" style="margin-bottom:20px">{"".join(status)}</div>']

    if snapshot is None or snapshot.empty:
        parts.append(card(empty("지수·환율을 불러오는 중입니다… 받지 못하면 빈칸으로 둡니다."), "지수 · 환율"))
    else:
        indexes = sorted(snapshot.indexes, key=lambda i: getattr(i, "market", "us") != ctx.market)
        cards = []
        for index in indexes:
            closes = list(getattr(index, "closes", ()) or ())
            spark = visuals.spark(closes, 180, 36, label="최근 1개월") if len(closes) >= 4 else ""
            cards.append(
                f'<div class="card idx"><div class="idx-name">{esc(index.label)}'
                f'<span class="muted" style="font-weight:500">{esc(index.note)}</span></div>'
                f'<div class="idx-val">{esc(index.text)}</div>'
                f'<div class="idx-move">{visuals.move(index.change_pct) if index.change_pct is not None else "<span class=muted>등락 모름</span>"}</div>'
                f'{spark}</div>')
        rates = []
        for rate in snapshot.rates:
            rates.append(
                f'<div class="card idx"><div class="idx-name">1달러 = {esc(rate.label)}</div>'
                f'<div class="idx-val">{esc(rate.text)}</div>'
                f'<div class="idx-move">{visuals.move(rate.change_pct) if rate.change_pct is not None else "<span class=muted>등락 모름</span>"}</div></div>')
        sources = sorted({i.source for i in snapshot.indexes} | {r.source for r in snapshot.rates})
        parts.append(f'<h2 style="margin:6px 0 12px">지수</h2><div class="idx-grid">{"".join(cards)}</div>')
        if rates:
            parts.append(f'<h2 style="margin:24px 0 12px">환율 <span class="muted small">모두 1달러 기준</span></h2>'
                         f'<div class="idx-grid">{"".join(rates)}</div>')
        parts.append(f'<p class="hint">{esc(clock(snapshot.fetched_at))} 기준 · 출처 {esc(" · ".join(sources))} · '
                     "등락은 직전 거래일 종가 대비입니다. 한 달 흐름 선은 일봉 종가를 이은 것입니다.</p>")

    parts.append(_macro(bot.macro_snapshot()))
    parts.append(_holidays(ctx))
    return head + "".join(parts)


def _macro(snapshot) -> str:
    if snapshot is None or snapshot.empty:
        return ('<h2 style="margin:28px 0 12px">경제 지표</h2>'
                + card(empty("값을 불러오는 중입니다. 받지 못하면 이 자리는 비워 둡니다 — 추정치를 넣지 않습니다.")))
    cards = []
    for r in snapshot.readings:
        move = ""
        if r.change_text:
            tone = r.tone or "flat"
            arrow = {"up": "▲", "down": "▼"}.get(r.direction, "―")
            move = f'<span class="mi-move {tone}" title="직전 발표 대비">{arrow} {esc(r.change_text)}</span>'
        note = f'<p class="small" style="color:var(--text-2)">{esc(r.note)}</p>' if r.note else ""
        cards.append(
            f'<div class="card macro-card"><div class="mc-top"><span>{term(r.label)}</span>'
            f'<span>{esc(r.when)}</span></div><div class="mc-val">{esc(r.text)}{move}</div>'
            f'{note}<p class="muted small">{esc(r.spec.meaning)}</p></div>')
    return ('<h2 style="margin:28px 0 12px">경제 지표 <span class="muted small">물가·금리·고용, 지금 값</span></h2>'
            f'<div class="macro-grid">{"".join(cards)}</div>'
            '<p class="hint">화살표는 <b>직전 발표 대비</b>. <span class="mi-move good">초록</span>은 주식에 유리한 방향, '
            '<span class="mi-move bad">빨강</span>은 불리한 방향, 해석이 갈리는 값은 <span class="mi-move flat">회색</span>. '
            f'출처 <a href="{FRED_HOME}" target="_blank" rel="noopener">세인트루이스 연준 FRED</a> '
            f'(원자료: 미 노동통계국·상무부·연준) · {esc(clock(snapshot.fetched_at))}에 받음.</p>')


def _holidays(ctx) -> str:
    days = [d for d in upcoming_market_days(ctx.today, 120) if d.day >= ctx.today]
    if not days:
        body = empty("120일 안에 예정된 미국 휴장일이 없습니다.")
    else:
        body = "".join(
            f'<div class="ev"><div class="ev-when">{esc(dday(ctx.today, d.day))}</div>'
            f'<div class="ev-name"><b>{esc(d.name)}</b><span>{esc(kdate(d.day))}</span></div>'
            f'<span class="tag">{esc(d.kind)}</span></div>' for d in days)
    warning = ctx.bot.calendar_warning()
    extra = f'<div class="card-body"><p class="warn-line small">⚠️ {esc(warning)}</p></div>' if warning else ""
    return ('<div style="margin-top:28px"></div>'
            + card(body + extra, "미국 휴장·조기폐장",
                   "한국 휴장일은 음력 명절이 해마다 바뀌어 손으로 적지 않습니다"))


__all__ = ["render"]
