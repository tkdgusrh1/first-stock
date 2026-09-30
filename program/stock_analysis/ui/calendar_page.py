"""캘린더 — 한 달 칸 + 고른 날의 일정.

점 하나가 일정 하나다. 파랑 = 실적 발표, 빨강 = 경제지표, 회색 = 휴장.
'추정' 일정(과거 간격으로 짐작한 실적일, 관행으로 짐작한 지표 발표일)은 반드시
'추정' 이라고 적는다. 확정된 것처럼 보이면 그날에 맞춰 움직이게 된다.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import events as ev
from .kit import card, chip_link, empty, esc, icon, page_head, stars, stock_url
from .shell import with_market

KINDS = (("all", "전체"), (ev.EARN, "실적"), (ev.ECON, "경제지표"), (ev.HOLIDAY, "휴장"))
WEEKDAYS = ("일", "월", "화", "수", "목", "금", "토")


def _month(raw: str, today: date) -> date:
    try:
        year, month = (int(x) for x in str(raw).split("-")[:2])
        return date(year, month, 1)
    except (TypeError, ValueError):
        return today.replace(day=1)


def _shift(first: date, months: int) -> date:
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def render(ctx, ym: str, picked: str, kind: str) -> str:
    kind = kind if kind in dict(KINDS) else "all"
    first = _month(ym, ctx.today)
    # 일요일에서 시작하는 6주. 달 앞뒤가 비면 이웃 달 날짜를 옅게 둔다.
    start = first - timedelta(days=(first.weekday() + 1) % 7)
    end = start + timedelta(days=41)
    items = ev.collect(ctx.bot, start, end, ctx.targets)
    if kind != "all":
        items = [e for e in items if e.kind == kind]
    by_day: dict[date, list] = {}
    for item in items:
        by_day.setdefault(item.day, []).append(item)

    try:
        chosen = date.fromisoformat(picked) if picked else None
    except ValueError:
        chosen = None
    if chosen is None:
        chosen = ctx.today if first <= ctx.today < _shift(first, 1) else first

    def link(**overrides) -> str:
        params = {"ym": f"{first.year}-{first.month:02d}", "d": chosen.isoformat(), "k": kind}
        params.update(overrides)
        query = "&".join(f"{k}={v}" for k, v in params.items() if v)
        return with_market(f"/calendar?{query}", ctx.market)

    prev_m, next_m = _shift(first, -1), _shift(first, 1)
    nav = (f'<div class="cal-head"><a class="icon-btn" href="{esc(link(ym=f"{prev_m.year}-{prev_m.month:02d}", d=""))}" '
           f'aria-label="이전 달">{icon("chev-l")}</a><h2>{first.year}년 {first.month}월</h2>'
           f'<a class="icon-btn" href="{esc(link(ym=f"{next_m.year}-{next_m.month:02d}", d=""))}" '
           f'aria-label="다음 달">{icon("chev-r")}</a>'
           f'<a class="btn sm" href="{esc(link(ym=f"{ctx.today.year}-{ctx.today.month:02d}", d=ctx.today.isoformat()))}">오늘</a></div>')

    rows = []
    day = start
    for _week in range(6):
        cells = []
        for weekday in range(7):
            classes = []
            if day.month != first.month:
                classes.append("other")
            if weekday == 0:
                classes.append("sun")
            if weekday == 6:
                classes.append("sat")
            if day == ctx.today:
                classes.append("today")
            if day == chosen:
                classes.append("picked")
            dots, holiday = [], ""
            for item in by_day.get(day, [])[:6]:
                if item.kind == ev.HOLIDAY:
                    holiday = '<span class="hol">휴장</span>'
                else:
                    dots.append('<i class="e"></i>' if item.kind == ev.EARN else "<i></i>")
            cells.append(
                f'<td class="{" ".join(classes)}"><a href="{esc(link(d=day.isoformat()))}" '
                f'aria-label="{day.isoformat()} 일정 {len(by_day.get(day, []))}개">'
                f'<span class="d">{day.day}</span><span class="dots">{"".join(dots)}</span>{holiday}</a></td>')
            day += timedelta(days=1)
        rows.append(f"<tr>{''.join(cells)}</tr>")
    head_row = "".join(f"<th>{w}</th>" for w in WEEKDAYS)
    grid = (nav + f'<table class="cal"><thead><tr>{head_row}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<p class="cal-note">경제지표 시각은 화면 시간대({esc(ctx.config.timezone)})로 옮겨 적었습니다. '
            '실적 발표·휴장일은 미국 날짜입니다(발표 시각은 SEC 자료에 없어 적지 않습니다).</p>')

    chips = "".join(chip_link(label, link(k=key), key == kind) for key, label in KINDS)
    today_items = by_day.get(chosen, [])
    weekday = WEEKDAYS[(chosen.weekday() + 1) % 7]
    listing = ("".join(_day_event(e) for e in today_items) if today_items
               else '<div class="empty">이 날 잡힌 일정이 없습니다.</div>')
    side = card(f'<div class="day-list">{listing}</div>', f"{chosen.month}월 {chosen.day}일 {weekday}요일",
                f"{len(today_items)}개")

    earnings = [e for e in items if e.kind == ev.EARN and first <= e.day < next_m]
    month_list = ""
    if earnings:
        month_list = card("".join(
            f'<div class="ev"><div class="ev-when">{e.day.month}/{e.day.day}</div>'
            f'<div class="ev-name"><a href="{esc(stock_url(e.ticker))}"><b>{esc(e.name)}</b></a>'
            f'<span>{esc(e.note)}</span></div>'
            f'<span class="tag {"warn" if e.estimated else "up"}">{"추정" if e.estimated else "확정"}</span></div>'
            for e in earnings), "이 달 실적 발표", f"{len(earnings)}개")

    head = page_head("캘린더", "관심 종목 실적 발표 · 미국 경제지표 · 미국 휴장일",
                     f'<div class="chips">{chips}</div>')
    body = (f'<div class="cal-wrap"><div class="card">{grid}</div>'
            f'<div class="col">{side}{month_list}</div></div>')
    if not items:
        body += empty("이 기간에 잡힌 일정이 없습니다.")
    return head + body


def _day_event(event) -> str:
    cls = {"earn": "day-ev earn", "hol": "day-ev hol"}.get(event.kind, "day-ev")
    time = event.time or {"earn": "실적", "hol": "휴장"}.get(event.kind, "시각 미정")
    name = esc(event.name)
    if event.kind == ev.EARN and event.ticker:
        name = f'<a href="{esc(stock_url(event.ticker))}">{name}</a>'
    extra = []
    if event.kind == ev.ECON:
        extra.append('<span class="tag">미국</span>')
        extra.append(stars(event.importance))
    if event.estimated:
        extra.append('<span class="tag warn">추정</span>')
    note = f'<div class="de-sub">{esc(event.note)}</div>' if event.note else ""
    return (f'<div class="{cls}"><div class="de-time">{esc(time)}</div>'
            f'<div><div>{" ".join(extra)}</div><div class="de-name">{name}</div>{note}</div></div>')


__all__ = ["render"]
