"""모든 쪽이 같이 쓰는 틀 — 왼쪽 메뉴, 위 막대(검색·시장·새로고침·밝기), 지수 띠.

틀은 어느 쪽에서나 같은 자리에 같은 모양이다. 메뉴가 쪽마다 움직이면 사람은
매번 찾아야 한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .. import markets, visuals
from ..glossary import groups
from ..timeutil import clock
from .kit import action_button, esc, icon, parse_when

# 화면이 그려지기 전에 밝기를 정한다. 안 그러면 새로 열 때마다 흰 화면이 번쩍인다.
THEME_BOOT = """<script>
(function () {
  var DAY_START = 7, DAY_END = 19;
  function byClock() { var h = new Date().getHours(); return h >= DAY_START && h < DAY_END ? 'light' : 'dark'; }
  var choice = 'auto';
  try { choice = localStorage.getItem('theme') || 'auto'; } catch (e) {}
  var root = document.documentElement;
  if (choice === 'auto') { root.setAttribute('data-theme', byClock()); }
  else if (choice === 'light' || choice === 'dark') { root.setAttribute('data-theme', choice); }
  root.classList.add('js');
})();
</script>"""

# 표식: 장부 칸 위로 올라가는 선 하나. 이 프로그램이 하는 일(기록하고, 숫자로 본다)을 그린다.
BRAND_MARK = ('<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">'
              '<rect x="1.5" y="1.5" width="29" height="29" rx="6"/>'
              '<path class="bm-grid" d="M8 23h16M8 17h16M8 11h16"/>'
              '<path class="bm-line" d="M7 22l6-6 4 3 8-9"/></svg>')

NAV = (
    ("home", "/", "home", "홈"),
    ("news", "/news", "news", "뉴스"),
    ("filings", "/filings", "file", "공시"),
    ("calendar", "/calendar", "calendar", "캘린더"),
    ("discover", "/discover", "compass", "발굴"),
    ("market", "/market", "chart", "시장"),
)


def with_market(path: str, market: str) -> str:
    joiner = "&" if "?" in path else "?"
    return f"{path}{joiner}m={market}"


def document(*, title: str, body: str, active: str, market: str, bot, here: str,
             notice: dict | None = None, charts: bool = False, today=None) -> str:
    """쪽 하나를 완성된 HTML 로."""
    scripts = ['<script src="/static/app.js" defer></script>']
    if charts:
        scripts = ['<script src="/static/lightweight-charts.js" defer></script>',
                   '<script src="/static/chart.js" defer></script>'] + scripts
    boot = {"market": market, "here": here, "notice": notice or None}
    return (
        "<!doctype html>\n"
        '<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{esc(title)} · First Stock</title>"
        '<link rel="icon" href="data:,">'
        f"{THEME_BOOT}"
        '<link rel="stylesheet" href="/static/app.css">'
        "</head><body>"
        '<div class="app">'
        f"{_topbar(market, bot, here)}"
        f"{_rail(active, market, bot, today)}"
        f'<div class="tape" data-live-tape>{tape(bot.market_snapshot(), market)}</div>'
        f'<main class="main">{body}</main>'
        f"{_footer()}"
        "</div>"
        '<div class="toast-zone" id="toasts" aria-live="polite"></div>'
        f'<script type="application/json" id="boot">{_json(boot)}</script>'
        f'<script type="application/json" id="search-data">{_json(_search_data(bot))}</script>'
        + "".join(scripts)
        + "</body></html>"
    )


def _json(value) -> str:
    # </script> 로 끝나는 글이 들어 있으면 스크립트 칸이 거기서 닫혀 버린다.
    # '<' 를 통째로 바꿔 두면 이름에 태그 같은 글이 섞여 와도 칸 밖으로 새지 않는다.
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def _rail(active: str, market: str, bot, today=None) -> str:
    """메뉴 줄. 글자 탭에 밑줄 — 신문 머리처럼 위에 가로로 둔다."""
    counts = _nav_counts(bot, market, today)

    def mark(key: str) -> str:
        return ' class="on" aria-current="page"' if key == active else ""

    items = []
    for key, path, name, label in NAV:
        n = counts.get(key)
        dot = f'<span class="dot-n">{n}</span>' if n else ""
        items.append(f'<a{mark(key)} href="{esc(with_market(path, market))}">'
                     f'{icon(name, True)}<span>{label}</span>{dot}</a>')
    side = [
        f'<a{mark("glossary")} href="{esc(with_market("/glossary", market))}">'
        f'{icon("book", True)}<span>사전</span></a>',
        f'<a{mark("settings")} href="{esc(with_market("/settings", market))}">'
        f'{icon("settings", True)}<span>설정</span></a>',
        '<form method="post" action="/action" class="inline-form" '
        "onsubmit=\"return confirm('감시를 완전히 멈춥니다.\\n\\n다시 보려면 시작하기 파일을 더블클릭하세요. 계속할까요?')\">"
        '<input type="hidden" name="action" value="quit">'
        f'<button type="submit" title="감시를 완전히 종료합니다">{icon("power", True)}<span>종료</span></button></form>',
    ]
    return (f'<nav class="nav" aria-label="메뉴"><div class="nav-main">{"".join(items)}</div>'
            f'<div class="nav-side">{"".join(side)}</div></nav>')


def _nav_counts(bot, market: str, today=None) -> dict:
    """메뉴 옆 숫자 — 오늘 온 공시, 하루 안의 속보. 셀 수 없으면 비워 둔다."""
    out = {}
    try:
        day = (today or datetime.now().date()).isoformat()
        watched = {t.ticker.upper() for t in bot.cached_targets() if t.market == market}
        out["filings"] = sum(
            1 for r in bot.state.recent(60)
            if str(r.get("date") or "")[:10] == day
            and (r.get("market") or markets.US) == market
            and str(r.get("ticker", "")).upper() in watched)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        urgent = 0
        for n in bot.state.news(60):
            moment = parse_when(n.get("when"))
            if moment is not None and moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            if int(n.get("severity", 1) or 1) >= 3 and moment and moment >= cutoff:
                urgent += 1
        out["news"] = urgent
    except Exception:
        return {}
    return out


def _topbar(market: str, bot, here: str) -> str:
    counts = {markets.US: 0, markets.KR: 0}
    for target in bot.cached_targets():
        counts[target.market] = counts.get(target.market, 0) + 1

    path = here.split("?")[0] or "/"
    if path.startswith("/stock/"):
        path = "/"           # 종목 화면에서 시장을 바꾸면 그 시장의 홈으로 간다
    seg = []
    for key in (markets.US, markets.KR):
        state, shown, guessed = bot.market_state(key)
        tip = (f"{markets.hours_text(key)} · 현지 {shown} · "
               + ("시세를 못 받아 시각으로 어림한 값(휴장일 반영 안 됨)" if guessed
                  else "거래소가 알려준 상태"))
        label = markets.STATE_LABEL[state] + (" (어림)" if guessed else "")
        dart = getattr(bot, "dart", None)
        warn = ""
        if key == markets.KR and counts[key] and not (dart and dart.ready):
            warn = ('<span class="tag warn" title="DART 인증키가 없어 재무제표가 비어 있습니다">열쇠 필요</span>')
        seg.append(
            f'<a class="{"on" if key == market else ""}" href="{esc(with_market(path, key))}" '
            f'title="{esc(tip)}"><span class="state-dot {esc(state)}"></span>'
            f'{esc(markets.MARKET_NAME[key])}<span class="n">{counts[key]}</span>'
            f'<span class="sr">{esc(label)}</span>{warn}</a>'
        )

    menu_items = [
        ("news", "속보 확인", "관심 종목·시장 속보를 지금 확인"),
        ("check", "공시 확인", "SEC·DART 새 공시를 지금 확인"),
        ("metrics", "지표 새로 계산", "재무·주가 지표를 처음부터 다시"),
        ("reports", "보고서 읽기", "10-Q/10-K 원문·가이던스·위험 요인 (종목당 20초쯤)"),
    ]
    buttons = "".join(
        '<form method="post" action="/action">'
        f'<input type="hidden" name="action" value="{a}">'
        f'<input type="hidden" name="back" value="{esc(here)}">'
        f'<button type="submit">{icon("refresh", True)}<span>{esc(label)}'
        f'<span class="mp-sub">{esc(sub)}</span></span></button></form>'
        for a, label, sub in menu_items
    )
    return (
        '<header class="topbar">'
        f'<a class="brand" href="{esc(with_market("/", market))}" title="처음 화면">'
        f'{BRAND_MARK}<span class="brand-text"><b>FIRST STOCK</b><span>투자 노트</span></span></a>'
        '<div class="search" data-search>'
        f'{icon("search")}'
        '<input type="search" placeholder="종목·용어 검색, 또는 티커로 추가 (예: TSLA · 삼성전자)" '
        'autocomplete="off" aria-label="검색" maxlength="40">'
        '<kbd>/</kbd><div class="search-menu hide" role="listbox"></div></div>'
        f'<nav class="seg" aria-label="시장">{"".join(seg)}</nav>'
        '<div class="menu" data-menu>'
        f'<button type="button" class="icon-btn" title="지금 새로 받기" aria-haspopup="true">{icon("refresh")}</button>'
        f'<div class="menu-pop hide">{buttons}</div></div>'
        f'<button type="button" class="icon-btn" id="themebtn" title="화면 밝기">{icon("sun")}</button>'
        "</header>"
    )


def tape(snapshot, market: str) -> str:
    """지수·환율 한 줄. 보고 있는 시장의 지수를 앞에 둔다."""
    if snapshot is None or snapshot.empty:
        return '<span class="muted">환율·지수를 불러오는 중입니다… 1분마다 다시 받습니다.</span>'
    indexes = sorted(snapshot.indexes, key=lambda i: getattr(i, "market", "us") != market)
    parts = []
    for index in indexes:
        move = visuals.move(index.change_pct) if index.change_pct is not None else ""
        parts.append(f'<span class="tape-item" title="{esc(index.note)} · {esc(index.source)}">'
                     f'<span class="tape-name">{esc(index.label)}</span><b>{esc(index.text)}</b>{move}</span>')
    if snapshot.rates:
        parts.append('<span class="tape-sep"></span>')
    for rate in snapshot.rates:
        move = visuals.move(rate.change_pct) if rate.change_pct is not None else ""
        parts.append(f'<span class="tape-item" title="1달러당 · {esc(rate.source)}">'
                     f'<span class="tape-name">$1={esc(rate.label)}</span><b>{esc(rate.text)}</b>{move}</span>')
    parts.append(f'<span class="tape-when">{esc(clock(snapshot.fetched_at))} 기준</span>')
    return "".join(parts)


def _search_data(bot) -> dict:
    stocks = [{"t": t.ticker, "d": markets.display(t.ticker), "n": t.watch.name or t.name or "",
               "m": t.market} for t in bot.cached_targets()]
    terms = [{"k": e.key, "n": e.name} for items in groups().values() for e in items]
    return {"stocks": stocks, "terms": terms}


def _footer() -> str:
    from .. import __version__

    return (
        '<footer class="foot">'
        f"<p>First Stock {esc(__version__)} · 재무는 SEC·DART 원본, 주가는 Yahoo Finance(안 되면 Stooq) — "
        "무료 시세라 늦을 수 있어 주가마다 거래 시각을 적습니다.</p>"
        "<p>이 화면은 정보를 모아 보여줄 뿐 매매 신호가 아닙니다. 투자 판단과 그 결과의 책임은 본인에게 있습니다. "
        "이 화면은 내 컴퓨터에서만 열립니다(127.0.0.1).</p></footer>"
    )


def gate(title: str, lines: list[str]) -> str:
    """끝낸 뒤·불러오는 중 같은 한 장짜리 화면."""
    body = "".join(f"<p>{line}</p>" for line in lines)
    return f'<div class="gate"><h1>{title}</h1>{body}</div>'


def banner(text: str, kind: str = "", form: str = "") -> str:
    return f'<div class="banner {kind}"><div class="b-text">{text}</div>{form}</div>'


__all__ = ["banner", "document", "gate", "tape", "with_market", "action_button"]
