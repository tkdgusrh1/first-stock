"""여러 쪽이 같이 쓰는 부품.

같은 것은 늘 같은 모양으로 그린다. 한 쪽에서 '주가' 가 다르게 생기면 사람은
다른 값인 줄 안다.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

from .. import markets, money, visuals
from ..glossary import lookup
from ..news import TIER_NAMES
from ..news import publisher_tier as news_tier
from ..timeutil import ago, clock, kdate

TONE_CLASS = {"alert": "tone-alert", "good": "tone-good", "bad": "tone-bad", "plain": "tone-plain"}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def safe_url(value) -> str:
    """바깥(뉴스 피드·야후)에서 온 주소. http(s) 만 통과 — 'javascript:' 같은 주소가 섞여 와도 링크로 만들지 않는다."""
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""


def plain(text: str | None) -> str:
    """태그를 걷어낸 글. 텔레그램용 답을 화면 알림으로 옮길 때 쓴다."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "완료")).strip()


def bold(text: str) -> str:
    """**강조** 만 굵게. 나머지는 그대로 이스케이프한다."""
    parts = esc(text).split("**")
    return "".join(part if i % 2 == 0 else f"<b>{part}</b>" for i, part in enumerate(parts))


def term(label: str) -> str:
    """용어에 짧은 설명(마우스를 올리면)과 사전 링크를 붙인다."""
    entry = lookup(label)
    if not entry:
        return esc(label)
    return (f'<a class="term" href="/glossary#term-{esc(entry.key)}" '
            f'title="{esc(entry.short)}">{esc(label)}</a>')


# --------------------------------------------------------------------------
# 그림 글자. 선 하나로 그린 단순한 모양만 쓴다(바깥 글꼴·그림을 받지 않는다).
# --------------------------------------------------------------------------
_ICONS = {
    "home": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    "news": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 8h10M7 12h10M7 16h6"/>',
    "file": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "compass": '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "flask": '<path d="M9 3h6M10 3v6l-5.5 9.5A1.6 1.6 0 0 0 6 21h12a1.6 1.6 0 0 0 1.5-2.5L14 9V3"/><path d="M7.5 15h9"/>',
    "settings": ('<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1'
                 'M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>'),
    "book": '<path d="M4 4h6a3 3 0 013 3v13a2 2 0 00-2-2H4z"/><path d="M20 4h-6a3 3 0 00-3 3"/><path d="M20 4v14h-7"/>',
    "power": '<path d="M12 3v9"/><path d="M6.3 7.3a8 8 0 1011.4 0"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "refresh": '<path d="M20 11a8 8 0 00-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0014.6 4.5L20 16"/><path d="M20 20v-4h-4"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "chev-r": '<path d="M9 6l6 6-6 6"/>',
    "chev-l": '<path d="M15 6l-6 6 6 6"/>',
    "ext": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v6H4V6h6"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "bell": '<path d="M6 16V11a6 6 0 1112 0v5l2 2H4z"/><path d="M10 21h4"/>',
    "flame": '<path d="M12 3c1 4 5 5 5 10a5 5 0 01-10 0c0-3 2-4 2-7 2 1 3 3 3 5 1-2 1-5 0-8z"/>',
    "star": '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
}


def icon(name: str, small: bool = False) -> str:
    body = _ICONS.get(name, "")
    cls = "i sm" if small else "i"
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">{body}</svg>'


# --------------------------------------------------------------------------
# 회사 표식 · 시각 설정
# --------------------------------------------------------------------------
# 로고는 야후 등 시세 제공처가 아니라 공개 로고 저장소에서 받는다. 받아오면
# 그 서버에 '이 종목을 보고 있다' 가 남으므로 설정(show_logos)으로 끌 수 있다.
LOGO_URL = "https://assets.parqet.com/logos/symbol/{ticker}?format=png&size=64"
_LOGOS_ON = True
_DISPLAY_TZ = "Asia/Seoul"


def set_logos(enabled: bool) -> None:
    global _LOGOS_ON
    _LOGOS_ON = bool(enabled)


def set_display_tz(name: str) -> None:
    global _DISPLAY_TZ
    _DISPLAY_TZ = str(name or "Asia/Seoul")


def logo_url(ticker: str) -> str:
    """로고 주소. 꺼져 있거나 한국 종목이면 빈 문자열(글자 배지를 쓴다).

    한국 종목 로고는 믿을 만한 무료 저장소가 없다. 엉뚱한 회사 그림을 붙이느니
    글자 배지를 쓴다.
    """
    key = str(ticker or "").strip().upper()
    if not _LOGOS_ON or not key or markets.market_of(key) == markets.KR:
        return ""
    return LOGO_URL.format(ticker=quote(key))


def mark(ticker: str, name: str = "", size: str = "") -> str:
    """종목 배지. size: '' · 'sm' · 'lg'."""
    inner = visuals.badge(ticker, name, logo_url(ticker))
    return f'<span class="b-{size}">{inner}</span>' if size else inner


def stock_url(ticker: str) -> str:
    return "/stock/" + quote(str(ticker or "").upper(), safe="")


def when_ago(moment: datetime | None) -> str:
    return ago(moment) if moment else ""


def when_clock(moment: datetime | None) -> str:
    return clock(moment, _DISPLAY_TZ) if moment else ""


def parse_when(raw) -> datetime | None:
    """저장해둔 ISO 시각 문자열을 되살린다. 형식이 어긋나면 조용히 포기한다."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def day_name(key: str) -> str:
    """'2026-10-01' → '2026-10-01(목)'. 읽을 수 없는 값은 그대로 둔다."""
    try:
        return kdate(date.fromisoformat(key))
    except (TypeError, ValueError):
        return key or "날짜 모름"


def by_day(entries, today: date | None = None, key: str = "date"):
    """날짜별로 묶는다. [(보여줄 날짜 이름, [항목])]. 들어온 순서를 지킨다(이미 새 것이 위).

    '오늘' 은 **설정한 시간대의 오늘**이어야 한다. 서버 시각으로 정하면
    한국이 자정을 넘긴 밤에 어제 것이 '오늘' 로 찍힌다.
    """
    day = today or date.today()
    names = {day.isoformat(): "오늘", (day - timedelta(days=1)).isoformat(): "어제"}
    out: list[tuple[str, list]] = []
    for entry in entries:
        raw = str(entry.get(key) or "")[:10]
        label = names.get(raw) or day_name(raw)
        if out and out[-1][0] == label:
            out[-1][1].append(entry)
        else:
            out.append((label, [entry]))
    return out


# --------------------------------------------------------------------------
# 카드 · 머리 · 단추
# --------------------------------------------------------------------------
def card(body: str, title: str = "", sub: str = "", right: str = "", foot: str = "",
         cls: str = "", id_: str = "", pad: bool = False) -> str:
    head = ""
    if title:
        head = (f'<div class="card-head"><h2>{title}</h2>'
                + (f'<span class="ch-sub">{sub}</span>' if sub else "")
                + (f'<div class="ch-right">{right}</div>' if right else "") + "</div>")
    inner = f'<div class="card-pad">{body}</div>' if pad else body
    footer = f'<div class="card-foot">{foot}</div>' if foot else ""
    ident = f' id="{esc(id_)}"' if id_ else ""
    return f'<section class="card {cls}"{ident}>{head}{inner}{footer}</section>'


def fold_card(body: str, title: str, sub: str = "", key: str = "", open_: bool = False) -> str:
    """접었다 펼치는 카드. 머리를 누르면 열리고, 열어둔 상태는 다음에 와도 기억한다(data-keep)."""
    keep = f' data-keep="{esc(key)}" id="{esc(key)}"' if key else ""
    return (f'<details class="card fold-card"{keep}{" open" if open_ else ""}>'
            f'<summary class="card-head"><h2>{title}</h2>'
            + (f'<span class="ch-sub">{sub}</span>' if sub else "")
            + f'<span class="fold-tip"></span></summary><div class="card-pad">{body}</div></details>')


def page_head(title: str, sub: str = "", actions: str = "") -> str:
    return (f'<div class="page-head"><div class="ph-text"><h1>{title}</h1>'
            + (f'<div class="ph-sub">{sub}</div>' if sub else "") + "</div>"
            + (f'<div class="ph-actions">{actions}</div>' if actions else "") + "</div>")


def action_button(action: str, label: str, back: str, cls: str = "btn sm",
                  fields: dict | None = None, confirm: str = "", title: str = "") -> str:
    """누르면 서버에 일을 시키는 단추. 끝나면 보던 쪽(back)으로 돌아온다."""
    hidden = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">'
                     for k, v in (fields or {}).items())
    ask = (f' onsubmit="return confirm(\'{esc(confirm)}\')"' if confirm else "")
    tip = f' title="{esc(title)}"' if title else ""
    return (f'<form method="post" action="/action" class="inline-form"{ask}>'
            f'<input type="hidden" name="action" value="{esc(action)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">{hidden}'
            f'<button type="submit" class="{cls}"{tip}>{label}</button></form>')


def chip_link(label: str, href: str, on: bool = False, count: int | None = None) -> str:
    n = f' <span class="n">{count}</span>' if count is not None else ""
    return f'<a class="chip{" on" if on else ""}" href="{esc(href)}">{label}{n}</a>'


def empty(text: str) -> str:
    return f'<div class="empty">{text}</div>'


def more_link(label: str, href: str) -> str:
    return f'<a class="more-link" href="{esc(href)}">{esc(label)} {icon("chev-r", True)}</a>'


# --------------------------------------------------------------------------
# 주가 조각. 처음 그릴 때와 몇 초마다 갈아끼울 때 같은 함수를 쓴다.
# --------------------------------------------------------------------------
def price_text(m) -> str:
    return money.price(m.price, m.currency) if m is not None and m.price else "-"


def change_html(m) -> str:
    if m is None or m.price_change_pct is None:
        return '<span class="muted">-</span>'
    return visuals.move(m.price_change_pct)


def trade_time(m, long: bool = False) -> str:
    """이 가격이 **언제 거래된 값인지**. '실시간' 이라고 주장하는 대신 보여준다."""
    when = getattr(m, "price_time", None) if m is not None else None
    if when is None:
        return "거래 시각 모름"
    shown = f"{clock(when, _DISPLAY_TZ)} · {ago(when)}"
    return f"{shown} 거래" if long else shown


def extended_html(m) -> str:
    """장전·장후 가격. 정규장 값과 섞이지 않게 따로 적는다."""
    if m is None or not getattr(m, "extended_price", None):
        return ""
    cls = "up" if (m.extended_change_pct or 0) >= 0 else "down"
    pct = f" {m.extended_change_pct:+.2f}%" if m.extended_change_pct is not None else ""
    return (f'{esc(m.extended_label)} <b class="{cls}">'
            f'{money.price(m.extended_price, m.currency)}{pct}</b>')


def verdict_chip(verdict) -> str:
    if not verdict:
        return ""
    return f'<span class="verdict v-{esc(verdict.level)}">{esc(verdict.label)}</span>'


def spark_for(m) -> str:
    return visuals.spark(getattr(m, "spark", None) or []) if m is not None else ""


# --------------------------------------------------------------------------
# 뉴스 한 줄
# --------------------------------------------------------------------------
SEV_LABEL = {3: "속보", 2: "주목", 1: ""}


def source_chip(entry: dict) -> str:
    publisher = entry.get("publisher") or entry.get("source") or ""
    if not publisher:
        return ""
    tier = int(entry.get("tier") or news_tier(publisher))
    return (f'<span class="src t{tier}" title="{esc(TIER_NAMES.get(tier, ""))}">'
            f'{esc(publisher)}</span>')


def ticker_chips(tickers, known: set[str] | None = None) -> str:
    """기사에 걸린 종목. 감시 중인 것만 종목 화면으로 이어준다(없는 화면으로 보내지 않게)."""
    out = []
    for t in tickers or []:
        label = f"${esc(markets.display(t))}"
        if known is None or str(t).upper() in known:
            out.append(f'<a class="tag ticker" href="{esc(stock_url(t))}">{label}</a>')
        else:
            out.append(f'<span class="tag ticker">{label}</span>')
    return "".join(out)


def news_item(entry: dict, known: set[str] | None = None, compact: bool = False) -> str:
    severity = int(entry.get("severity", 1) or 1)
    moment = parse_when(entry.get("when"))
    when = f'{esc(when_ago(moment))} · {esc(when_clock(moment))}' if moment else ""
    sev = (f'<span class="sev s{severity}">{SEV_LABEL[severity]}</span>'
           if SEV_LABEL.get(severity) else "")
    reasons = "".join(f'<span class="tag">{esc(r)}</span>' for r in entry.get("reasons", [])[:3])
    macro = '<span class="tag down">시장 전체</span>' if entry.get("macro") else ""
    original = entry.get("title", "")
    korean = entry.get("title_ko") or ""
    title = esc(korean or original)
    url = safe_url(entry.get("url"))
    head = (f'<a class="news-title" href="{esc(url)}" target="_blank" rel="noopener">{title}</a>'
            if url else f'<span class="news-title">{title}</span>')
    if korean:
        # 번역은 틀릴 수 있다. 원문 제목을 바로 아래에 그대로 둔다.
        engine = entry.get("ko_engine") or "자동"
        head += (f'<div class="news-orig">{esc(original)} '
                 f'<span class="ko-mark" title="자동 번역이라 틀릴 수 있습니다">{esc(engine)} 번역</span></div>')
    tags = ticker_chips(entry.get("tickers", []), known) + macro + reasons
    cls = "news-item urgent" if severity >= 3 and not compact else "news-item"
    return (f'<article class="{cls}"><div class="news-meta">{source_chip(entry)}{sev}'
            f'<span class="when">{when}</span></div>{head}'
            + (f'<div class="news-tags">{tags}</div>' if tags else "") + "</article>")


# --------------------------------------------------------------------------
# 공시 한 줄 (알림 모양)
# --------------------------------------------------------------------------
def filing_item(entry: dict, show_mark: bool = True) -> str:
    tone = TONE_CLASS.get(entry.get("tone", "plain"), "tone-plain")
    ticker = str(entry.get("ticker", ""))
    report = entry.get("report") or ""
    original = (f'<div class="a-orig">{esc(report)}</div>'
                if report and report != entry.get("title") else "")
    why = f'<div class="a-why">👉 {esc(entry["why"])}</div>' if entry.get("why") else ""
    company = entry.get("company") or ""
    when = str(entry.get("when") or "")
    clock_part = when[11:16] if len(when) >= 16 else ""
    link = safe_url(entry.get("url"))
    source = (f'<a class="more-link" href="{esc(link)}" target="_blank" rel="noopener">'
              f'원문 {icon("ext", True)}</a>') if link else ""
    badge = mark(ticker, company, "sm") if show_mark else ""
    return (
        f'<div class="alert-item {tone}">{badge}<div class="a-body">'
        f'<div class="a-top"><a href="{esc(stock_url(ticker))}">{esc(markets.display(ticker))}</a>'
        f'<span class="muted">·</span><span>{esc(entry.get("form", ""))}</span>'
        + (f'<span class="muted small">{esc(company)}</span>' if company else "")
        + f'</div><div class="a-title">{esc(entry.get("title", ""))}</div>{original}{why}</div>'
        f'<div class="a-right"><span>{esc(clock_part)}</span>{source}</div></div>'
    )


def stars(level: int) -> str:
    level = max(0, min(3, int(level or 0)))
    return f'<span class="stars" title="중요도 {level}/3">{"★" * level}<i>{"★" * (3 - level)}</i></span>'


__all__ = [n for n in dir() if not n.startswith("__")]
