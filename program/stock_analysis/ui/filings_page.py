"""공시 — 감시 중인 종목의 새 공시를 날짜별로.

보고 있는 시장 것만 보여준다. 한국 화면에 미국 공시가 섞이면 어느 쪽 이야기인지
알 수 없다. 공시마다 '무엇을 봐야 하는지' 를 함께 적는다 — '유상증자결정' 다섯
글자만으로는 좋은 일인지 나쁜 일인지 알 수 없기 때문이다.
"""

from __future__ import annotations

from .. import markets
from .banners import key_banner
from .kit import action_button, by_day, card, chip_link, empty, esc, filing_item, page_head
from .shell import with_market


def entries_for(ctx) -> list[dict]:
    """감시 중인 이 시장 종목의 공시. 감시 목록에서 뺀 종목의 공시는 빼고 본다."""
    allowed = {t.ticker.upper() for t in ctx.mine}
    return [r for r in ctx.bot.state.recent(200)
            if (r.get("market") or markets.US) == ctx.market      # 표시가 없는 것은 예전 미국 공시
            and str(r.get("ticker", "")).upper() in allowed]


def render(ctx, flt: str, ticker: str) -> str:
    bot = ctx.bot
    config = bot.config
    where = "SEC EDGAR" if ctx.market == markets.US else "금융감독원 DART"
    recent = entries_for(ctx)
    important = [r for r in recent if r.get("tone") in ("alert", "bad")]
    tickers = sorted({str(r.get("ticker", "")).upper() for r in recent})

    chosen = recent
    if flt == "important":
        chosen = important
    if ticker:
        chosen = [r for r in chosen if str(r.get("ticker", "")).upper() == ticker.upper()]

    base = "/filings"
    chips = [chip_link("전체", with_market(base, ctx.market), flt != "important" and not ticker, len(recent)),
             chip_link("중요", with_market(f"{base}?f=important", ctx.market), flt == "important", len(important))]
    chips += [chip_link(markets.display(t), with_market(f"{base}?t={t}", ctx.market), ticker.upper() == t)
              for t in tickers]

    head = page_head(
        "공시",
        f"감시 중인 종목만 · {esc(where)} · 마지막 확인 {esc(bot.state.last_check() or '아직 없음')} · "
        f"{config.poll_interval_sec // 60}분마다 자동 확인",
        action_button("check", "↻ 지금 공시 확인", ctx.here, "btn sm"))
    banner = key_banner(bot, ctx.here) if ctx.korean else ""

    if not chosen:
        text = (f"감시 중인 종목의 새 공시가 올라오면 여기에 뜹니다. (출처: {esc(where)})<br>"
                "처음 켠 날에는 기준선만 잡고, 그 뒤 새로 올라온 것부터 쌓입니다.")
        body = card(empty(text))
    else:
        parts = []
        for label, entries in by_day(chosen, ctx.today):
            parts.append(f'<div class="day-label">{esc(label)}<span class="n">{len(entries)}건</span></div>')
            parts.extend(filing_item(e) for e in entries)
        body = card("".join(parts))
    legend = ('<p class="hint">왼쪽 점 색: 빨강 = 곧바로 확인할 것(실적·인수합병·파산 등), 주황 = 불리할 수 있음, '
              '초록 = 유리할 수 있음, 회색 = 정기 제출. 👉 줄은 그 공시에서 무엇을 봐야 하는지입니다.</p>')
    return head + banner + f'<div class="chips" style="margin-bottom:18px">{"".join(chips)}</div>' + body + legend


__all__ = ["entries_for", "render"]
