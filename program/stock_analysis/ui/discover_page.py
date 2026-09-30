"""발굴 — 공시된 재무제표와 주가를 같은 잣대로 줄 세운 것.

**사라는 뜻이 아니다.** 직접 들여다볼 만한 것을 앞으로 끌어올 뿐이다. 그래서
뽑힌 이유를 숫자와 함께 늘 같이 적고, 확인 못 한 항목도 숨기지 않는다.

갈래를 나눈 건 **묻는 질문이 다르기 때문이다.** '탄탄한가' 와 '커지고 있는가' 와
'시장이 사고 있는가' 는 다른 물음이라 한 줄로 세우면 답이 섞인다.
"""

from __future__ import annotations

from .. import markets, screener
from .kit import bold, card, chip_link, empty, esc, icon, mark, page_head, stock_url
from .shell import with_market

ORDER = (screener.BLUE, screener.GROWTH, screener.MOMENTUM)


def render(ctx, category: str) -> str:
    bot = ctx.bot
    head = page_head("발굴", "눈여겨볼 종목 — 공시된 재무제표와 주가로 고른 것입니다. <b>사라는 뜻이 아닙니다</b>. "
                     "직접 들여다볼 후보를 앞으로 끌어온 것입니다.")
    if not bot.recommend_enabled:
        return head + card(empty("설정에서 발굴(recommend)이 꺼져 있습니다."))

    seen, total = bot.screen_progress(ctx.market)
    source = bot.universe_source(ctx.market)
    who = "DART" if ctx.korean else "SEC"
    groups = {k: v for k, v in (bot.top_picks(market=ctx.market) or {}).items() if v}
    scope = f"후보 {total}개 중 {seen}개 확인"
    missing = ("" if source else
               f'<p class="hint">후보 목록을 {who} 에서 받지 못했습니다. 받을 때까지 감시 목록 안에서만 봅니다 — '
               "대신 쓸 목록을 지어내지 않습니다.</p>")

    if not groups:
        left = max(0, total - seen)
        more = f" 남은 {left}개를 계속 보는 중입니다." if left else ""
        text = f"아직 추천할 만한 종목을 찾지 못했습니다. ({esc(scope)}){esc(more)}" if total else \
            "후보를 모으는 중입니다."
        return head + card(empty(text)) + missing

    category = category if category in groups else next(k for k in ORDER if k in groups)
    chips = "".join(chip_link(screener.CATEGORY_NAME[k], with_market(f"/discover?c={k}", ctx.market),
                              k == category, len(groups[k])) for k in ORDER if k in groups)
    picks = groups[category]
    rows = "".join(_row(ctx, rank, pick, category) for rank, pick in enumerate(picks, 1))
    body = card(
        f'<div class="card-body"><p class="line">{esc(screener.category_how(category, ctx.market))}</p>'
        f'<div class="box-warn">⚠ {bold(screener.CATEGORY_WARNING[category])}</div></div>{rows}',
        esc(screener.CATEGORY_NAME[category]), f"{len(picks)}개 · {esc(scope)}")

    if ctx.korean:
        limits = ("<b>한국은 다섯 축 중 넷으로 봅니다.</b> 성장·수익성·재무 안정성·현금 창출력은 DART 사업보고서에서 "
                  "그대로 읽지만, <b>밸류에이션</b>(PER·PSR)은 시가총액을 구할 발행주식수를 아직 받지 않아 비워 둡니다. "
                  "재무는 <b>연간 확정치</b>라 미국보다 한 걸음 늦습니다.")
    else:
        limits = ("가이던스·컨센서스 대조는 감시 목록 종목에만 있어서 순위에 넣지 않았습니다. 있는 경우 "
                  "<b>[참고]</b> 로 표시만 합니다. ETF 는 추천하지 않습니다 — 줄 세우려면 규모나 보수를 알아야 하는데 "
                  "무료 공개 자료에 그게 없습니다.")
    note = (f'<div class="note"><b>후보 목록:</b> {esc(source or "받지 못함")} — 손으로 적은 목록이 아니라 '
            f"{who} 가 공개한 자료에서 만듭니다.<br><b>갈래끼리는 점수를 견주지 않습니다.</b> "
            f"한 회사가 여러 갈래에 들어갈 수 있습니다.<br>{limits}</div>")
    return head + f'<div class="chips" style="margin-bottom:18px">{chips}</div>' + body + missing + note


def _row(ctx, rank: int, pick, group: str) -> str:
    reasons = "".join(f"<li>{esc(r)}</li>" for r in pick.reasons[:6])
    cautions = "".join(f"<li>{esc(c)}</li>" for c in pick.cautions[:4])
    notes = "".join(f"<li>{esc(n)}</li>" for n in pick.notes[:4])
    if pick.in_watchlist:
        action = f'<a class="tag accent" href="{esc(stock_url(pick.ticker))}">이미 감시 중 ›</a>'
    else:
        action = ('<form method="post" action="/action" class="inline-form pk-add">'
                  '<input type="hidden" name="action" value="add">'
                  f'<input type="hidden" name="ticker" value="{esc(pick.ticker)}">'
                  f'<input type="hidden" name="back" value="{esc(ctx.here)}">'
                  f'<button type="submit" class="btn sm soft">{icon("plus", True)} 감시 목록에 추가</button></form>')
    body = [f"<div><h5>뽑힌 이유</h5><ul>{reasons}</ul></div>"]
    if cautions:
        body.append(f"<div><h5>확인하고 보세요</h5><ul>{cautions}</ul></div>")
    if notes:
        body.append(f"<div><h5>참고 — 판단에는 넣지 않은 값</h5><ul>{notes}</ul></div>")
    return (
        f'<details class="rank-row" data-keep="pick-{esc(group)}-{esc(pick.ticker)}"><summary>'
        f'<span class="rk">{rank}</span>{mark(pick.ticker, pick.name)}'
        f'<div class="rk-name" style="min-width:0"><b>{esc(pick.name or pick.ticker)}</b>'
        f'<span class="rk-t">{esc(markets.display(pick.ticker))}</span>'
        f'<span class="rk-line">{esc(pick.headline)}</span></div>'
        f'<div onclick="event.stopPropagation()">{action}</div></summary>'
        f'<div class="rank-body">{"".join(body)}</div></details>'
    )


__all__ = ["render"]
