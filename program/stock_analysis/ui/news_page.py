"""뉴스 — 먼저 알려준 속보와, 지금 매체들이 쓰는 헤드라인.

'속보' 는 걸러낸 것이다(의견·목록 기사는 버리고 실제로 일어난 사건만).
'실시간 헤드라인' 은 거르지 않은 것이다. 둘을 섞으면 무엇이 걸러진 것인지
알 수 없어서 칩으로 나눈다. 제목은 요약하거나 바꾸지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .kit import (
    safe_url,
    action_button, card, chip_link, empty, esc, news_item, page_head, parse_when, source_chip,
    ticker_chips, when_ago,
)
from .shell import with_market

FILTERS = (("all", "속보 전체"), ("urgent", "🚨 긴급"), ("mine", "관심 종목"),
           ("macro", "시장 전체"), ("live", "실시간 헤드라인"))


def _aware(moment):
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def render(ctx, flt: str) -> str:
    flt = flt if flt in dict(FILTERS) else "all"
    stored = sorted(ctx.bot.state.news(150), key=lambda n: str(n.get("when") or ""), reverse=True)
    mine = {t.ticker.upper() for t in ctx.mine}

    def keep(entry, key):
        severity = int(entry.get("severity", 1) or 1)
        tickers = {str(t).upper() for t in entry.get("tickers", [])}
        return {"all": True, "urgent": severity >= 3, "mine": bool(tickers & mine),
                "macro": bool(entry.get("macro"))}.get(key, True)

    counts = {key: sum(1 for n in stored if keep(n, key)) for key, _ in FILTERS if key != "live"}
    chips = "".join(chip_link(label, with_market(f"/news?f={key}", ctx.market), key == flt,
                              counts.get(key)) for key, label in FILTERS)
    head = page_head(
        "뉴스", "관심 종목·시장 속보는 감시 주기마다 자동으로 확인합니다 · 제목은 원문 그대로",
        action_button("news", "↻ 지금 속보 확인", ctx.here, "btn sm"))
    top = f'<div class="chips" style="margin-bottom:18px">{chips}</div>'

    if flt == "live":
        body = card('<div data-lazy="/frag/headlines"><div class="empty">매체들의 지금 헤드라인을 받는 중…</div></div>',
                    "실시간 헤드라인", "Investing.com · MarketWatch · CNBC · 거르지 않음 · 3분마다 새로")
        return head + top + body

    items = [n for n in stored if keep(n, flt)]
    feature = ""
    if flt == "all":
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        hot = [n for n in items if int(n.get("severity", 1) or 1) >= 3
               and (_aware(parse_when(n.get("when"))) or cutoff) >= cutoff][:2]
        if hot:
            cards = "".join(_feature(n, ctx.known) for n in hot)
            feature = ('<h2 style="margin:0 0 12px">🔥 오늘 주요 속보</h2>'
                       f'<div class="feature-grid">{cards}</div>')
    if not items:
        text = {"mine": "관심 종목 이야기가 담긴 속보가 아직 없습니다.",
                "urgent": "긴급으로 분류된 속보가 아직 없습니다.",
                "macro": "시장 전체를 흔드는 사건으로 분류된 속보가 아직 없습니다."}.get(
            flt, "아직 속보가 없습니다. 감시 주기마다 자동으로 확인합니다.")
        body = card(empty(esc(text)))
    else:
        body = card('<div class="news-list">' + "".join(news_item(n, ctx.known) for n in items) + "</div>",
                    "속보", f"{len(items)}건 · 새 것부터")
    note = ('<p class="hint">🚨 속보 = 실적·인수합병·소송·규제·지정학처럼 실제로 일어난 사건. '
            '주목 = 그보다 약한 신호. 매체 칩 색은 공신력입니다(주황 = 통신사·1차 매체, 파랑 = 종합 매체, '
            '회색 = 확인 필요).</p>')
    return head + top + feature + body + note


def _feature(entry, known) -> str:
    moment = parse_when(entry.get("when"))
    url = safe_url(entry.get("url")) or "#"
    return (f'<article class="card feature">'
            f'<div class="news-meta">{source_chip(entry)}<span class="sev s3">속보</span>'
            f'<span class="when">{esc(when_ago(moment))}</span></div>'
            f'<a class="f-title" href="{esc(url)}" target="_blank" rel="noopener">'
            f'{esc(entry.get("title_ko") or entry.get("title", ""))}</a>'
            + (f'<div class="news-orig">{esc(entry.get("title", ""))}</div>' if entry.get("title_ko") else "")
            + f'<div class="f-foot">{ticker_chips(entry.get("tickers", []), known)}'
            + "".join(f'<span class="tag">{esc(r)}</span>' for r in entry.get("reasons", [])[:2])
            + "</div></article>")


__all__ = ["FILTERS", "render"]
