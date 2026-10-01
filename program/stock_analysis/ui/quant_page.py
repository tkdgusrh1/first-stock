"""퀀트 연습장 — 단계 · 백테스트 · 오늘의 신호 · 모의 계좌 · 매매 일지 · 전략 설명.

**실제 주문은 없다.** 화면 어디에도 증권사로 주문을 보내는 단추가 없고, 일부러 넣지 않았다.
숫자는 받은 일봉과 재무로만 계산하고, 못 구한 값은 '-' 로 둔다.
"""

from __future__ import annotations

from .. import markets, money
from ..quant import strategies as strat
from ..quant.costs import default_costs
from ..quant.sizing import STAGES, RiskRules, stage_for
from .kit import action_button, card, esc, page_head, stock_url

YEARS = (("1", "최근 1년"), ("3", "최근 3년"), ("5", "최근 5년"), ("0", "받은 전체"))
PAPER_WEEKS = 8          # 모의 계좌를 통과로 볼 최소 기간
PAPER_TRADES = 20        # 그동안 최소 거래 수


def currency_of(market: str) -> str:
    return money.KRW if market == markets.KR else money.USD


def pct(value, digits: int = 1, sign: bool = False) -> str:
    if value is None:
        return "-"
    if round(value * 100, digits) == 0:
        value = 0.0          # '-0.0%' 처럼 없는 부호를 보이지 않게
    return f"{value * 100:+.{digits}f}%" if sign else f"{value * 100:.{digits}f}%"


def num(value, digits: int = 2) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def tone(value) -> str:
    if value is None:
        return ""
    return "up" if value > 0 else ("down" if value < 0 else "")


def render(ctx) -> str:
    store = ctx.bot.quant
    market = ctx.market
    parts = [
        page_head("퀀트 연습장",
                  f"{markets.MARKET_NAME[market]} · 백테스트 → 모의 계좌 → (나중에) 소액 실전. "
                  "<b>이 화면은 실제 주문을 하지 않습니다.</b>"),
        stages_card(ctx, store),
        backtest_card(ctx, store),
        signals_card(ctx),
        paper_card(ctx, store),
        journal_card(ctx, store),
        rules_card(),
    ]
    return "".join(p for p in parts if p)


# --------------------------------------------------------------------------
# 단계
# --------------------------------------------------------------------------
def _equity_krw(ctx, equity: float | None) -> float | None:
    if equity is None:
        return None
    if ctx.market == markets.KR:
        return equity
    snap = ctx.bot.market_snapshot()
    for rate in getattr(snap, "rates", None) or []:
        if rate.label == "원" and rate.value:
            return equity * rate.value
    return None          # 환율을 못 받았으면 단계를 추측하지 않는다


def stages_card(ctx, store) -> str:
    from ..quant import paper

    view = paper.account_view(store, ctx.bot, ctx.market)
    runs = store.runs
    compared = bool(store.compare(ctx.market))
    weeks = trades = 0
    if view:
        eng = view["engine"]
        weeks = len(eng.curve) // 5
        trades = len(eng.trades)
    paper_ok = view is not None and weeks >= PAPER_WEEKS and trades >= PAPER_TRADES and not eng.halted_on
    steps = [
        ("공부", True, "보고서의 3~7장, 아래 '전략 설명'. 모르는 말은 사전에서."),
        ("백테스트", compared, f"전략 5개 비교를 한 번 이상 · 지금까지 백테스트 {runs}번"
                              + (" — 많이 돌릴수록 과거에만 맞는 규칙이 됩니다" if runs >= 30 else "")),
        ("모의 계좌", paper_ok, f"{PAPER_WEEKS}주 이상 · 거래 {PAPER_TRADES}번 이상 · 멈춤 규칙에 걸리지 않기"
                             + (f" (지금 {weeks}주 · 거래 {trades}번)" if view else " (아직 시작 안 함)")),
        ("소액 실전", False, "주문 기능은 아직 없습니다. 모의 통과 뒤 별도로 결정합니다 — 키는 열쇠 보관함에만."),
        ("자동 주문", False, "소액 실전에서 '예상 밖 주문 0건' 이 확인된 뒤에만."),
    ]
    items = []
    for k, (name, done, note) in enumerate(steps):
        mark = "✓" if done else str(k + 1)
        items.append(f'<li class="qs-step{" done" if done else ""}"><span class="qs-n">{mark}</span>'
                     f'<div><b>{esc(name)}</b><span>{esc(note)}</span></div></li>')
    stage_line = ""
    if view:
        stage = stage_for(_equity_krw(ctx, view["equity"]))
        stage_line = (f'<p class="hint">모의 계좌 크기로 본 지금 단계: <b>{esc(stage.name)}</b> — {esc(stage.note)}</p>'
                      if stage else '<p class="hint">환율을 받지 못해 계좌 단계를 정하지 않았습니다.</p>')
    return card(f'<ol class="qs-steps">{"".join(items)}</ol>{stage_line}', "진행 단계",
                "한 단계를 통과해야 다음으로", cls="quant-stages", pad=True)


# --------------------------------------------------------------------------
# 백테스트
# --------------------------------------------------------------------------
def _field(name: str, label: str, value: str, suffix: str = "", width: str = "", step: str = "any") -> str:
    style = f' style="width:{width}"' if width else ""
    return (f'<label class="qf"><span>{esc(label)}</span><span class="qf-in">'
            f'<input class="field" type="number" name="{esc(name)}" value="{esc(value)}" step="{step}"{style}>'
            + (f'<em>{esc(suffix)}</em>' if suffix else "") + '</span></label>')


def _strategy_select(name: str, chosen: str) -> str:
    opts = "".join(f'<option value="{esc(s.key)}"{" selected" if s.key == chosen else ""}>{esc(s.name)}</option>'
                   for s in strat.STRATEGIES.values())
    return f'<label class="qf"><span>전략</span><select class="field" name="{esc(name)}">{opts}</select></label>'


def _preset_select(chosen: str) -> str:
    opts = [("custom", "아래 값 그대로")] + [(s.key, f"{s.name} 규칙") for s in STAGES]
    return ('<label class="qf"><span>위험 규칙 묶음</span><select class="field" name="preset">'
            + "".join(f'<option value="{esc(k)}"{" selected" if k == chosen else ""}>{esc(v)}</option>'
                      for k, v in opts) + "</select></label>")


def settings_form(ctx, store, action: str, button: str, extra: str = "") -> str:
    """백테스트·모의 계좌가 같이 쓰는 입력 칸. 마지막으로 쓴 값을 기억한다."""
    saved = store.settings(ctx.market)
    rules = RiskRules.from_dict(saved.get("rules"))
    costs = default_costs(ctx.market)
    saved_costs = saved.get("costs") or {}
    capital = saved.get("capital") or (1_000_000 if ctx.market == markets.KR else 1_000)
    unit = "원" if ctx.market == markets.KR else "달러"
    years = str(saved.get("years", "0"))
    year_opts = "".join(f'<option value="{k}"{" selected" if k == years else ""}>{v}</option>' for k, v in YEARS)

    def c(name):
        return f"{float(saved_costs.get(name, getattr(costs, name))) * 100:g}"

    return (
        f'<form method="post" action="/action" class="quant-form">'
        f'<input type="hidden" name="action" value="{esc(action)}">'
        f'<input type="hidden" name="back" value="{esc(ctx.here)}">'
        '<div class="qf-row">'
        + _strategy_select("strategy", saved.get("strategy") or strat.DEFAULT_STRATEGY)
        + _field("capital", "시작 자본", f"{capital:g}", unit, "9em", "1")
        + f'<label class="qf"><span>기간</span><select class="field" name="years">{year_opts}</select></label>'
        + _preset_select(saved.get("preset") or "custom")
        + '</div><details class="qf-more"><summary>위험 규칙 · 비용 직접 정하기</summary><div class="qf-row">'
        + _field("risk_per_trade", "한 번에 잃어도 되는 비율", f"{rules.risk_per_trade * 100:g}", "%", "5em")
        + _field("max_weight", "한 종목 최대 비중", f"{rules.max_weight * 100:g}", "%", "5em")
        + _field("max_positions", "최대 보유 종목", str(rules.max_positions), "개", "4em", "1")
        + _field("daily_loss_stop", "하루 손실 멈춤", f"{rules.daily_loss_stop * 100:g}", "%", "5em")
        + _field("dd_half", "낙폭 → 비중 절반", f"{rules.dd_half * 100:g}", "%", "5em")
        + _field("dd_stop", "낙폭 → 새 매수 멈춤", f"{rules.dd_stop * 100:g}", "%", "5em")
        + f'<label class="qf qf-check"><input type="checkbox" name="vol_target" value="1"'
          f'{" checked" if rules.vol_target else ""}><span>변동성이 크면 비중 줄이기</span></label>'
        + '</div><div class="qf-row">'
        + _field("commission", "수수료(한쪽)", c("commission"), "%", "6em")
        + _field("sell_tax", "매도 세금", c("sell_tax"), "%", "5em")
        + _field("slippage", "체결 차이(한쪽)", c("slippage"), "%", "5em")
        + '</div><p class="hint">0 을 넣으면 그 멈춤 규칙은 꺼집니다. 비용 기본값: '
        + ("국내 온라인 수수료 0.015% + 유관기관 약 0.0036%, 매도 거래세 0.20%(2026~)"
           if ctx.market == markets.KR else "해외주식 기본 수수료 0.25% 수준(증권사마다 다름), 거래세 없음")
        + ' — 내 증권사 값으로 바꾸세요.</p></details>'
        + f'<div class="qf-actions"><button type="submit" class="btn primary">{button}</button>{extra}</div></form>'
    )


def backtest_card(ctx, store) -> str:
    compare_btn = ('<button type="submit" class="btn" name="mode" value="compare" '
                   'title="같은 조건으로 전략 5개를 모두 돌려 한 표로 봅니다">전략 5개 비교</button>')
    form = settings_form(ctx, store, "backtest", "백테스트 실행", compare_btn)
    n = len(ctx.mine)
    lead = (f'<p class="hint">감시 중인 {markets.MARKET_NAME[ctx.market]} 종목 {n}개의 일봉으로 돌립니다. '
            '신호는 장 마감 뒤 종가로 계산하고 <b>다음 날 시가</b>에 체결합니다.</p>')
    body = lead + form + compare_table(ctx, store) + result_block(ctx, store.backtest(ctx.market))
    return card(body, "백테스트", "규칙을 과거에 그대로 적용했다면", id_="q-backtest", pad=True)


def compare_table(ctx, store) -> str:
    rows = store.compare(ctx.market)
    if not rows:
        return ""
    lines = []
    for r in rows:
        m = r.get("metrics") or {}
        s = strat.get(r.get("strategy", ""))
        lines.append(
            f'<tr><td class="l"><b>{esc(s.name)}</b></td>'
            f'<td class="{tone(m.get("cagr"))}">{pct(m.get("cagr"), sign=True)}</td>'
            f'<td class="down">{pct(-(m.get("mdd") or 0)) if m.get("mdd") is not None else "-"}</td>'
            f'<td>{num(m.get("sharpe"))}</td><td>{m.get("trades", "-")}</td>'
            f'<td>{pct(m.get("win_rate"), 0)}</td><td>{num(m.get("payoff"))}</td>'
            f'<td class="l small">{esc(s.advice)}</td></tr>')
    bench = (rows[0].get("bench") or {}) if rows else {}
    foot = (f'<p class="hint">같은 종목을 첫날 똑같이 나눠 사서 그냥 들고 있었다면(비용 전): 연 {pct(bench.get("cagr"), sign=True)}, '
            f'최대 낙폭 {pct(-(bench.get("mdd") or 0))}. 이걸 못 이기면 규칙을 쓸 이유가 없습니다.</p>') if bench else ""
    return ('<h3 class="q-h">전략 비교</h3><div class="table-wrap"><table class="tbl plain q-tbl">'
            '<thead><tr><th class="l">전략</th><th>연수익률</th><th>최대 낙폭</th><th>샤프</th><th>거래</th>'
            '<th>승률</th><th>손익비</th><th class="l">권장</th></tr></thead>'
            f'<tbody>{"".join(lines)}</tbody></table></div>{foot}')


def result_block(ctx, result: dict | None) -> str:
    if not result:
        return '<div class="empty">아직 돌린 백테스트가 없습니다. 위에서 전략을 고르고 실행해보세요.</div>'
    cur = currency_of(ctx.market)
    m, b = result.get("metrics") or {}, result.get("bench") or {}
    if not m:
        return '<div class="empty">계산할 일봉이 없었습니다. 감시 종목의 지표를 먼저 불러와 주세요.</div>'
    s = strat.get(result.get("strategy", ""))
    tiles = [
        ("최종 금액", money.exact(m.get("end"), cur), money.exact(b.get("end"), cur), ""),
        ("연수익률 (CAGR)", pct(m.get("cagr"), sign=True), pct(b.get("cagr"), sign=True), tone(m.get("cagr"))),
        ("최대 낙폭 (MDD)", pct(-(m.get("mdd") or 0)), pct(-(b.get("mdd") or 0)) if b else "-", "down"),
        ("가장 긴 회복 기간", f'{m.get("underwater_days", 0)}일', f'{b.get("underwater_days", 0)}일' if b else "-", ""),
        ("샤프 지수", num(m.get("sharpe")), num(b.get("sharpe")), ""),
        ("변동성(연)", pct(m.get("vol")), pct(b.get("vol")), ""),
    ]
    tile_html = "".join(
        f'<div class="q-tile"><span>{esc(label)}</span><b class="{cls}">{esc(value)}</b>'
        f'<em>그냥 보유 {esc(bench)}</em></div>' for label, value, bench, cls in tiles)
    stats = [
        ("거래", f'{m.get("trades", 0)}번'), ("승률", pct(m.get("win_rate"), 0)),
        ("평균 이익", pct(m.get("avg_win"), sign=True)), ("평균 손실", pct(m.get("avg_loss"), sign=True)),
        ("손익비", num(m.get("payoff"))), ("낸 비용", money.exact(m.get("fees"), cur)),
        ("연 회전율", f'{num(m.get("turnover"), 1)}회' if m.get("turnover") is not None else "-"),
        ("주식 보유 비중", pct(m.get("exposure"), 0)),
    ]
    stat_html = "".join(f'<div><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in stats)
    sp = result.get("split") or {}
    split_html = ""
    if sp:
        h, t = sp.get("head") or {}, sp.get("tail") or {}
        split_html = (f'<div class="q-split"><b>앞 70% / 뒤 30% 나눠 보기</b> (나눈 날 {esc(sp.get("cut", ""))}) — '
                      f'연수익률 {pct(h.get("cagr"), sign=True)} → {pct(t.get("cagr"), sign=True)}, '
                      f'최대 낙폭 {pct(-(h.get("mdd") or 0))} → {pct(-(t.get("mdd") or 0))}. '
                      '뒤 기간이 크게 나쁘면 앞 기간의 운이었을 수 있습니다.</div>')
    warn = "".join(f"<li>{esc(w)}</li>" for w in result.get("warnings") or [])
    head = (f'<div class="q-result-head"><b>{esc(s.name)}</b> · {esc(result.get("start") or "")} ~ '
            f'{esc(result.get("end") or "")} · 종목 {len(result.get("tickers") or [])}개 · '
            f'시작 {money.exact(result.get("capital"), cur)} · 왕복 비용 약 '
            f'{pct(_round_trip(result.get("costs")), 2)}</div>')
    return (head + f'<div class="q-tiles">{tile_html}</div>'
            + curve_svg(result.get("curve") or [])
            + f'<div class="q-stats">{stat_html}</div>{split_html}'
            + (f'<div class="q-warn"><b>이 결과를 믿기 전에</b><ul>{warn}</ul></div>' if warn else "")
            + trades_table(ctx, result.get("trades") or [], cur))


def _round_trip(costs: dict | None) -> float | None:
    if not costs:
        return None
    return costs.get("commission", 0) * 2 + costs.get("sell_tax", 0) + costs.get("slippage", 0) * 2


def curve_svg(points: list, height: int = 180) -> str:
    """계좌 가치(진한 선)와 그냥 보유(옅은 선), 아래에 낙폭. 값은 받은 그대로 — 매끄럽게 다듬지 않는다."""
    pts = [p for p in points if p and p[1] is not None]
    if len(pts) < 2:
        return ""
    w, h, pad_l, pad_r, top_pad, dd_h = 760, height, 8, 8, 14, 50
    values = [p[1] for p in pts] + [p[2] for p in pts if len(p) > 2 and p[2] is not None]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0

    def x(k):
        return pad_l + k * (w - pad_l - pad_r) / (len(pts) - 1)

    def y(v):
        return top_pad + (1 - (v - lo) / span) * (h - top_pad - 10)

    main = " ".join(f"{x(k):.1f},{y(p[1]):.1f}" for k, p in enumerate(pts))
    bench = " ".join(f"{x(k):.1f},{y(p[2]):.1f}" for k, p in enumerate(pts) if len(p) > 2 and p[2] is not None)
    peak, dd = pts[0][1], []
    for k, p in enumerate(pts):
        peak = max(peak, p[1])
        dd.append((k, 1 - p[1] / peak if peak else 0))
    worst = max(d for _, d in dd) or 1.0
    area = (f"{x(0):.1f},{h + 4} " + " ".join(f"{x(k):.1f},{h + 4 + d / worst * dd_h:.1f}" for k, d in dd)
            + f" {x(len(pts) - 1):.1f},{h + 4}")
    total_h = h + dd_h + 22
    return (f'<figure class="q-curve"><svg viewBox="0 0 {w} {total_h}" role="img" aria-label="계좌 가치와 낙폭">'
            f'<polyline points="{bench}" class="q-bench"/><polyline points="{main}" class="q-main"/>'
            f'<polygon points="{area}" class="q-dd"/>'
            f'<text x="{pad_l}" y="11" class="q-ax">{esc(num(hi, 0))}</text>'
            f'<text x="{pad_l}" y="{h - 2}" class="q-ax">{esc(num(lo, 0))}</text>'
            f'<text x="{pad_l}" y="{h + dd_h + 18}" class="q-ax">낙폭 최대 {esc(pct(-worst))}</text>'
            f'<text x="{w - pad_r}" y="{h + dd_h + 18}" class="q-ax" text-anchor="end">'
            f'{esc(str(pts[0][0]))} ~ {esc(str(pts[-1][0]))}</text></svg>'
            '<figcaption><i class="q-key k-main"></i>전략 <i class="q-key k-bench"></i>같은 종목 그냥 보유(비용 전) '
            '<i class="q-key k-dd"></i>고점 대비 낙폭</figcaption></figure>')


def trades_table(ctx, trades: list, cur: str, limit: int = 30) -> str:
    if not trades:
        return ""
    rows = "".join(
        f'<tr><td class="l"><a href="{esc(stock_url(t["ticker"]))}">{esc(markets.display(t["ticker"]))}</a></td>'
        f'<td>{esc(t["entry_day"])}</td><td>{esc(t["exit_day"])}</td><td>{t["shares"]:,}</td>'
        f'<td>{money.price(t["entry_price"], cur)}</td><td>{money.price(t["exit_price"], cur)}</td>'
        f'<td class="{tone(t["pnl"])}">{pct(t["pnl_pct"], sign=True)}</td><td class="l small">{esc(t["reason"])}</td></tr>'
        for t in trades[:limit])
    more = f'<p class="hint">최근 {limit}건만 보여줍니다 (전체 {len(trades)}건 저장).</p>' if len(trades) > limit else ""
    return ('<details class="q-trades"><summary>거래 내역 보기</summary><div class="table-wrap"><table class="tbl plain q-tbl">'
            '<thead><tr><th class="l">종목</th><th>산 날</th><th>판 날</th><th>주수</th><th>산 값</th><th>판 값</th>'
            f'<th>손익</th><th class="l">판 이유</th></tr></thead><tbody>{rows}</tbody></table></div>{more}</details>')


# --------------------------------------------------------------------------
# 오늘의 신호
# --------------------------------------------------------------------------
def signals_card(ctx) -> str:
    from ..quant import paper

    try:
        rows = paper.signals(ctx.bot, ctx.market)
    except Exception:
        rows = []
    if not rows:
        return card('<div class="empty">일봉이 있는 감시 종목이 없습니다.</div>', "오늘의 신호", pad=True)
    lines = []
    for r in rows:
        sig = "".join(f'<span class="tag up">{esc(s)}</span>' for s in r["signals"]) or '<span class="muted">-</span>'
        rot = f'{r["rot_rank"]}위' if r.get("rot_rank") else "-"
        score = "-" if r["score"] is None else f'{r["score"] * 100:.0f}'
        parts = "" if r["score_parts"] == 3 else f' <em class="muted">({r["score_parts"]}/3)</em>'
        surprise = "-" if r["surprise"] is None else f'{r["surprise"]:+.1f}%'
        lines.append(
            f'<tr><td class="l"><a href="{esc(stock_url(r["ticker"]))}"><b>{esc(markets.display(r["ticker"]))}</b></a>'
            f' <span class="muted small">{esc(r["name"] or "")}</span></td>'
            f'<td><b>{score}</b>{parts}</td>'
            f'<td class="{tone(r["growth"])}">{pct(r["growth"], 0, True)}</td><td>{pct(r["roic"], 0)}</td>'
            f'<td class="{tone(r["surprise"])}">{surprise}</td>'
            f'<td class="{tone(r["ret6m"])}">{pct(r["ret6m"], 0, True)}</td><td>{pct(r["from_high"], 0, True)}</td>'
            f'<td>{rot}</td><td class="l">{sig}</td></tr>')
    day = rows[0]["day"]
    note = ('<p class="hint"><b>성장 점수</b> = 매출 성장률 · ROIC · 6개월 수익률의 이 목록 안 순위 평균(0~100). '
            '괄호는 셋 중 몇 개로 매겼는지. <b>지금 재무로 매기는 점수라 백테스트에는 넣지 않았습니다</b> — '
            '과거 시점 재무가 없어서 넣으면 미래 정보가 됩니다. 신호는 '
            f'{esc(day)} 장 마감 기준이고, 매수 추천이 아닙니다.</p>')
    table = ('<div class="table-wrap"><table class="tbl plain q-tbl"><thead><tr><th class="l">종목</th><th>성장 점수</th>'
             '<th>매출 성장</th><th>ROIC</th><th>EPS 서프라이즈</th><th>6개월</th><th>52주 고점 대비</th>'
             '<th>회전 순위</th><th class="l">켜진 신호</th></tr></thead>'
             f'<tbody>{"".join(lines)}</tbody></table></div>')
    return card(table + note, "오늘의 신호", "감시 종목 · 성장 점수 순", id_="q-signals", pad=True)


# --------------------------------------------------------------------------
# 모의 계좌
# --------------------------------------------------------------------------
def paper_card(ctx, store) -> str:
    from ..quant import paper

    view = paper.account_view(store, ctx.bot, ctx.market)
    cur = currency_of(ctx.market)
    if not view:
        intro = ('<p class="hint">가짜 돈으로 규칙을 매일 그대로 따라갑니다. 감시가 돌 때마다 끝난 거래일을 이어서 처리하고, '
                 '주문은 <b>다음 거래일 시가</b>에 체결된 것으로 기록합니다. 백테스트와 같은 엔진이라 규칙이 어긋나지 않습니다.</p>')
        return card(intro + settings_form(ctx, store, "paper_start", "모의 계좌 시작"), "모의 계좌",
                    "아직 없음", id_="q-paper", pad=True)
    eng = view["engine"]
    tiles = [("계좌 가치", money.exact(view["equity"], cur), ""),
             ("수익률", pct(view["return"], sign=True), tone(view["return"])),
             ("고점 대비", pct(-view["drawdown"]), "down" if view["drawdown"] else ""),
             ("현금", money.exact(eng.cash, cur), ""),
             ("거래", f"{len(eng.trades)}번", ""),
             ("처리한 날", f"{len(eng.curve)}일", "")]
    tile_html = "".join(f'<div class="q-tile"><span>{esc(k)}</span><b class="{c}">{esc(v)}</b></div>' for k, v, c in tiles)
    pos_rows = []
    for t, p in eng.positions.items():
        last = eng.last_close.get(t)
        gain = (last / p.cost - 1) if last and p.cost else None
        pos_rows.append(
            f'<tr><td class="l"><a href="{esc(stock_url(t))}">{esc(markets.display(t))}</a></td><td>{p.shares:,}</td>'
            f'<td>{money.price(p.cost, cur)}</td><td>{money.price(last, cur)}</td>'
            f'<td class="{tone(gain)}">{pct(gain, sign=True)}</td><td>{money.price(p.stop, cur)}</td>'
            f'<td>{esc(p.entry_day)}</td></tr>')
    positions = ('<div class="table-wrap"><table class="tbl plain q-tbl"><thead><tr><th class="l">보유</th><th>주수</th>'
                 '<th>평균 단가</th><th>최근 종가</th><th>손익</th><th>손절선</th><th>산 날</th></tr></thead>'
                 f'<tbody>{"".join(pos_rows)}</tbody></table></div>') if pos_rows else '<div class="empty">보유 종목 없음</div>'
    orders = "".join(f'<li><b>{"사기" if o.side == "buy" else "팔기"}</b> {esc(markets.display(o.ticker))} '
                     f'{o.shares:,}주 — {esc(o.reason)} <span class="muted">({esc(o.made)} 종가 기준)</span></li>'
                     for o in eng.orders)
    events = "".join(f'<li><span class="muted">{esc(e["day"])}</span> {esc(e["text"])}</li>'
                     for e in reversed(eng.events[-30:]))
    warn = []
    if view["orphans"]:
        warn.append(f'감시 목록에서 빠진 보유 종목 {", ".join(view["orphans"])} — 일봉이 없어 팔 수 없습니다. 다시 추가하거나 초기화하세요.')
    if eng.halted_on:
        warn.append(f'{eng.halted_on}에 낙폭 한도에 걸려 새 매수를 멈췄습니다. 규칙을 다시 검토하고 초기화하세요.')
    if view["paused"]:
        warn.append("멈춤 상태입니다. 재개하기 전까지 날짜를 처리하지 않습니다.")
    warn_html = "".join(f'<div class="q-warn">{esc(w)}</div>' for w in warn)
    buttons = (action_button("paper_step", "지금 반영", ctx.here, "btn sm",
                             title="끝난 거래일을 지금 바로 이어서 처리합니다")
               + action_button("paper_pause", "재개" if view["paused"] else "멈춤", ctx.here, "btn sm")
               + action_button("paper_reset", "초기화", ctx.here, "btn sm",
                               confirm="모의 계좌를 지우고 처음부터 시작할까요?"))
    s = eng.strategy
    sub = f'{s.name} · {esc(view["started"] or "")} 시작'
    curve = curve_svg([(d, v, None) for d, v in eng.curve]) if len(eng.curve) > 1 else ""
    body = (warn_html + f'<div class="q-tiles">{tile_html}</div>{curve}<h3 class="q-h">보유</h3>{positions}'
            + (f'<h3 class="q-h">다음 시가에 낼 주문</h3><ul class="q-list">{orders}</ul>' if orders else "")
            + (f'<h3 class="q-h">자동 일지</h3><ul class="q-list q-events">{events}</ul>' if events else ""))
    return card(body, "모의 계좌", sub, buttons, id_="q-paper", pad=True)


# --------------------------------------------------------------------------
# 매매 일지 · 전략 설명
# --------------------------------------------------------------------------
def journal_card(ctx, store) -> str:
    entries = [e for e in store.journal() if e.get("market") == ctx.market][:20]
    items = "".join(f'<li><span class="muted">{esc(e["day"])}</span> {esc(e["text"])}</li>' for e in entries)
    form = ('<form method="post" action="/action" class="q-journal">'
            '<input type="hidden" name="action" value="journal">'
            f'<input type="hidden" name="back" value="{esc(ctx.here)}">'
            '<textarea class="field" name="text" rows="2" maxlength="1000" '
            'placeholder="오늘 본 것, 규칙을 어기고 싶었던 순간, 바꾸고 싶은 점 — 바꾸기 전에 먼저 적어두세요"></textarea>'
            '<button type="submit" class="btn sm">적기</button></form>')
    return card(form + (f'<ul class="q-list">{items}</ul>' if items else ""), "매매 일지",
                "규칙을 바꾸고 싶을 때 먼저 적기", id_="q-journal", pad=True)


def rules_card() -> str:
    rows = "".join(
        f'<div class="q-rule"><b>{esc(s.name)}</b>'
        f'<div><span class="tag">살 때</span> {esc(s.buy_rule)}</div>'
        f'<div><span class="tag">팔 때</span> {esc(s.sell_rule)}</div>'
        f'<div><span class="tag">보유</span> {esc(s.hold)}</div>'
        f'<div class="muted"><span class="tag">검증</span> {esc(s.evidence)}</div>'
        f'<div><span class="tag up">권장</span> {esc(s.advice)}</div></div>'
        for s in strat.STRATEGIES.values())
    sizing = ('<p class="hint"><b>얼마나 사나</b>: 살 주식 수 = 계좌 × 한 번에 잃어도 되는 비율 ÷ (산 값 − 손절가). '
              '여기에 한 종목 최대 비중과 남은 현금으로 한 번 더 자릅니다. 손절가는 신호가 난 날 종가 − 2×ATR(20일 평균 변동폭)입니다. '
              '1주도 못 사면 건너뛰고 센다 — 소액 계좌에서 실제로 자주 일어납니다.</p>')
    return card(f'<div class="q-rules">{rows}</div>{sizing}', "전략 설명", "규칙 · 근거 · 권장", id_="q-rules", pad=True)
