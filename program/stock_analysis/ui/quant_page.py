"""퀀트 연습장 — 단계 · 백테스트 · 오늘의 신호 · 모의 계좌 · 매매 일지 · 전략 설명.

**실제 주문은 없다.** 화면 어디에도 증권사로 주문을 보내는 단추가 없고, 일부러 넣지 않았다.
숫자는 받은 일봉과 재무로만 계산하고, 못 구한 값은 '-' 로 둔다.
"""

from __future__ import annotations

from .. import markets, money
from ..quant import exits as exit_rules
from ..quant import profiles
from ..quant import strategies as strat
from ..quant.engine import fmt_shares
from ..quant.basket import UNIVERSES as BASKETS, is_basket
from ..quant.costs import FEE_PRESETS, CostModel, default_costs, default_fee_key
from ..quant.sizing import MARKET_FILTERS, WEEKDAYS, Plan, RiskRules, stage_for
from .kit import action_button, card, esc, fold_card, page_head, stock_url

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
        data_card(ctx, store),
        backtest_card(ctx, store),
        signals_card(ctx),
        paper_card(ctx, store),
        reports_card(ctx, store),
        journal_card(ctx, store),
        rules_card(),
        evidence_card(),
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
        ("백테스트", compared, f"전략 {len(strat.STRATEGIES)}개 비교를 한 번 이상 · 지금까지 백테스트 {runs}번"
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
# 넓은 종목 바구니 — 자료 상태와 '자료 받기'
# --------------------------------------------------------------------------
def data_card(ctx, store) -> str:
    """그 해 매출 상위 바구니의 자료가 얼마나 모였나. 자료를 아직 안 받았으면 무엇을 왜 받는지 설명한다."""
    key = next((k for k, (m, _) in BASKETS.items() if m == ctx.market), None)
    if key is None:
        return ""
    qd = getattr(ctx.bot, "_quant_data", None)
    try:
        st = ctx.bot.quant_data.status(ctx.market, ctx.today) if (qd is not None or _has_files(ctx)) else None
    except Exception:
        st = None
    progress = getattr(ctx.bot._quant_data, "progress", "") if getattr(ctx.bot, "_quant_data", None) else ""
    if ctx.market == markets.US:
        what = ("SEC 가 공개하는 '그 해 모든 제출 기업의 연간 매출'(XBRL frames, 은행은 순영업수익)로 해마다 상위 500개를 고르고, "
                "그 종목들의 10년치 일봉(야후)과 재무(매출·매출총이익·순이익·자산·자본·주식 수, SEC 제출일 포함)를 받습니다. "
                "SEC 연락처(설정)가 있어야 합니다.")
        first = "처음 한 번은 20~30분쯤(시세 약 800종목 + 재무 약 800회사)"
    else:
        what = ("DART 다중회사 주요계정(그 해 사업보고서)으로 해마다 매출 상위 200개를 고르고, 그 종목들의 10년치 일봉(야후)을 "
                "받습니다. 같은 응답의 영업이익·자산(접수일 포함)으로 수익성을 매깁니다. DART 인증키가 있어야 합니다.")
        first = "처음 한 번은 5~10분쯤"
    rows = []
    if st and st["years"]:
        prices = st["prices"]
        rows = [("바구니", f"{st['years'][0]}~{st['years'][-1]}년 매출 순위 · 지금 쓰는 {st['year']}년 순위 "
                          f"{st['members']}종목" if st["year"] else f"{st['years'][0]}~{st['years'][-1]}년 순위(아직 공개 전)"),
                ("시세", f"지금 바구니 {prices['have']}/{prices['total']}종목 받음 · 가장 최근 {prices['newest'] or '-'}"
                         + (f" · 못 받은 {len(prices['failed'])}종목" if prices["failed"] else "")),
                ("지수(국면·비교)", "받음" if st["bench"] else "아직")]
        if ctx.market == markets.US:
            rows.append(("재무", f"지금 바구니 {st['facts']}/{st['members']}회사 받음"))
        rows.append(("어느 해든 한 번이라도 든 종목", f"{st['all']}개 — 백테스트는 이 전부를 그 해 기준으로 바꿔 끼웁니다"))
        dropped = sorted(((v, y, name, why) for y, items in (st.get("dropped") or {}).items()
                          for name, v, why in items), reverse=True)
        if dropped:
            examples = ", ".join(f"{name} {y}년 {_big_money(v, ctx.market)}({why})" for v, y, name, why in dropped[:3])
            rows.append(("뺀 매출(SEC 숫자끼리 안 맞음)",
                         f"{len(dropped)}건 — {examples}. 값을 고쳐 쓰지 않고 그 해 순위에서만 뺐습니다"))
    body = "".join(f'<dt>{esc(k)}</dt><dd>{esc(v)}</dd>' for k, v in rows)
    status = (f'<p class="hint"><b>받는 중:</b> {esc(progress)} — 화면은 계속 쓸 수 있습니다.</p>' if progress else "")
    button = action_button("quant_data", "자료 받기 · 새로고침", ctx.here, "btn sm primary",
                           title="없는 해·새 날·새 분기만 받습니다. 처음 한 번만 오래 걸립니다")
    lead = (f'<p class="hint" style="margin-top:0"><b>관심 종목 몇 개 대신 「{esc(universe_name(key))}」 에서 고르게 합니다.</b> '
            f'{esc(what)} {esc(first)}, 그 뒤에는 새로 생긴 날·새 분기만 받습니다. '
            '<b>지금 큰 회사로 과거를 고르지 않도록</b> 해마다 그 해 순위를 쓰고, 그 매출이 공개된 뒤부터만 씁니다. '
            '다만 그 사이 사라진(상장폐지·합병) 회사는 지금 시세가 없어 빠집니다 — 결과마다 적습니다.</p>')
    mobile = ""
    from .. import mobile as phone_app

    if phone_app.active():
        mobile = '<p class="hint">휴대폰에서는 데이터·배터리를 많이 씁니다. 와이파이에서, 가능하면 PC 에서 받으세요.</p>'
    stats = f'<dl class="q-facts">{body}</dl>' if body else '<div class="empty">아직 받은 자료가 없습니다.</div>'
    return card(lead + mobile + status + stats, "넓은 종목 바구니", universe_name(key), button, id_="q-data", pad=True)


def _big_money(value: float, market: str) -> str:
    unit = "달러" if market == markets.US else "원"
    if value >= 1e12:
        return f"{value / 1e12:,.1f}조 {unit}"
    if value >= 1e8:
        return f"{value / 1e8:,.0f}억 {unit}"
    return f"{value / 1e6:,.0f}백만 {unit}"


def _has_files(ctx) -> bool:
    """자료 파일이 이미 있으면 상태를 읽는다(없으면 저장소를 새로 만들지 않는다)."""
    from pathlib import Path

    cache = Path(ctx.bot.config.cache_dir)
    return (cache / f"quant_basket_{ctx.market}.json").exists()


# --------------------------------------------------------------------------
# 백테스트
# --------------------------------------------------------------------------
def _field(name: str, label: str, value: str, suffix: str = "", width: str = "", step: str = "any") -> str:
    style = f' style="width:{width}"' if width else ""
    return (f'<label class="qf"><span>{esc(label)}</span><span class="qf-in">'
            f'<input class="field" type="number" name="{esc(name)}" value="{esc(value)}" step="{step}"{style}>'
            + (f'<em>{esc(suffix)}</em>' if suffix else "") + '</span></label>')


def _strategy_select(name: str, chosen: str) -> str:
    opts = f'<option value="auto"{" selected" if chosen == "auto" else ""}>성향에 맞게 (추천)</option>'
    opts += "".join(f'<option value="{esc(s.key)}"{" selected" if s.key == chosen else ""}>{esc(s.name)}</option>'
                    for s in strat.STRATEGIES.values())
    return f'<label class="qf"><span>전략</span><select class="field" name="{esc(name)}">{opts}</select></label>'


def _exit_select(chosen: str) -> str:
    opts = f'<option value="auto"{" selected" if chosen == "auto" else ""}>성향에 맞게 (추천)</option>'
    opts += "".join(f'<option value="{esc(p.key)}"{" selected" if p.key == chosen else ""}>{esc(p.name)}</option>'
                    for p in exit_rules.PRESETS)
    return ('<label class="qf" title="사는 규칙(전략)과 따로, 어떻게 팔지 고릅니다">'
            f'<span>청산 규칙</span><select class="field" name="exit">{opts}</select></label>')


def universe_name(key: str) -> str:
    if key in BASKETS:
        return BASKETS[key][1]
    return exit_rules.UNIVERSE_NAME.get(key, key)


def _universe_select(chosen: str, market: str) -> str:
    basket = ", ".join(t if market == markets.US else f"{t} {n.split('(')[0]}"
                       for t, n in exit_rules.BASKETS.get(market, ()))
    keys = list(exit_rules.UNIVERSE_NAME) + [k for k, (m, _) in BASKETS.items() if m == market]
    opts = "".join(f'<option value="{k}"{" selected" if k == chosen else ""}>{esc(universe_name(k))}</option>'
                   for k in keys)
    return (f'<label class="qf" title="방어형 ETF 바구니: {esc(basket)} · 큰 회사 바구니는 아래 \'넓은 종목 바구니\' 에서 '
            f'자료를 먼저 받으세요"><span>종목 범위</span><select class="field" name="universe">{opts}</select></label>')


def _filter_select(chosen: str, market: str) -> str:
    from ..quant.data import BENCH

    opts = "".join(f'<option value="{k}"{" selected" if k == chosen else ""}>{esc(v)}</option>'
                   for k, v in MARKET_FILTERS.items())
    return (f'<label class="qf" title="{esc(BENCH[market][1])} 이 200일 평균 아래로 내려가면 — 시장 전체가 내리막일 때 '
            f'새로 사지 않거나 현금으로 쉽니다"><span>시장 국면 필터</span>'
            f'<select class="field" name="market_filter">{opts}</select></label>')


def exits_table_help() -> str:
    rows = "".join(f'<tr><td class="l"><b>{esc(p.name)}</b></td><td class="l small">{esc(p.summary)}</td></tr>'
                   for p in exit_rules.PRESETS)
    return ('<div class="table-wrap"><table class="tbl plain q-tbl"><thead><tr><th class="l">청산 규칙</th>'
            f'<th class="l">어떻게 파나</th></tr></thead><tbody>{rows}</tbody></table></div>'
            '<p class="hint">R = 산 값 − 처음 손절선(한 번에 잃기로 한 폭). 일봉으로는 장중 순서를 모르니 '
            '<b>손절을 먼저</b> 본다고 가정합니다(나쁜 쪽). 갭 하락이면 손절선이 아니라 시가에 팔려 본전 손절도 손실이 날 수 있습니다.</p>')


def _profile_select(chosen: str) -> str:
    opts = [(p.key, p.name) for p in profiles.PROFILES] + [("custom", "직접 정하기")]
    return ('<label class="qf"><span>투자 성향</span><select class="field" name="profile">'
            + "".join(f'<option value="{esc(k)}"{" selected" if k == chosen else ""}>{esc(v)}</option>'
                      for k, v in opts) + "</select></label>")


def profiles_table(market: str) -> str:
    """세 성향을 한 표로 — 무엇이 다른지, 왜 그렇게 잡았는지."""
    rows = []
    for p in profiles.PROFILES:
        r = p.rules
        rows.append(
            f'<tr><td class="l"><b>{esc(p.name)}</b><div class="muted small">{esc(p.summary)}</div></td>'
            f'<td>{pct(r.risk_per_trade)}</td><td>{r.max_positions}개 · 최대 {pct(r.max_weight, 0)}</td>'
            f'<td>{esc("·".join(WEEKDAYS[d] for d in p.check_days))}</td><td>{p.min_hold}일</td>'
            f'<td>하루 {pct(-p.emergency, 0)}</td><td>{pct(-r.dd_stop, 0)}</td>'
            f'<td class="l small">{esc(strat.get(p.strategy(market)).name)}</td>'
            f'<td class="l small">{esc(exit_rules.get(p.exit_key).name)}</td>'
            f'<td class="l small">{"<br>".join(esc(w) for w in p.why)}</td></tr>')
    return ('<div class="table-wrap"><table class="tbl plain q-tbl q-profiles"><thead><tr><th class="l">성향</th>'
            '<th>한 번 위험</th><th>종목</th><th>점검</th><th>최소 보유</th><th>긴급 매도</th><th>멈춤 낙폭</th>'
            '<th class="l">추천 전략</th><th class="l">청산</th><th class="l">근거</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def field_guide() -> str:
    rows = "".join(f'<tr><td class="l"><b>{esc(label)}</b></td><td class="l small">{esc(what)}</td>'
                   f'<td class="l small">{esc(how)}</td></tr>' for _, label, what, how in profiles.FIELD_GUIDE)
    return ('<div class="table-wrap"><table class="tbl plain q-tbl q-guide"><thead><tr><th class="l">칸</th>'
            f'<th class="l">뜻</th><th class="l">어떻게 고르나</th></tr></thead><tbody>{rows}</tbody></table></div>')


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

    plan = Plan.from_dict(saved.get("plan"))
    fee = saved.get("fee") or default_fee_key(ctx.market)
    fee_rows = FEE_PRESETS["kr" if ctx.market == markets.KR else "us"]
    fee_opts = "".join(f'<option value="{esc(k)}"{" selected" if k == fee else ""}>{esc(label)}</option>'
                       for k, label, _ in fee_rows)
    fee_opts += f'<option value="custom"{" selected" if fee == "custom" else ""}>아래 칸에 직접</option>'
    day_boxes = "".join(
        f'<label class="qf-day"><input type="checkbox" name="check_day" value="{k}"'
        f'{" checked" if k in plan.check_days else ""}><span>{d}</span></label>'
        for k, d in enumerate(WEEKDAYS))

    profile = saved.get("profile") or profiles.DEFAULT_PROFILE
    chosen_strategy = saved.get("strategy") or "auto"
    chosen_exit = saved.get("exit") or "auto"
    return (
        f'<form method="post" action="/action" class="quant-form">'
        f'<input type="hidden" name="action" value="{esc(action)}">'
        f'<input type="hidden" name="back" value="{esc(ctx.here)}">'
        '<div class="qf-row">'
        + _profile_select(profile)
        + _strategy_select("strategy", chosen_strategy)
        + _exit_select(chosen_exit)
        + _universe_select(plan.universe, ctx.market)
        + _filter_select(plan.market_filter, ctx.market)
        + _field("capital", "시작 자본", f"{capital:g}", unit, "9em", "1")
        + _field("monthly_deposit", "매달 넣는 돈", f"{plan.monthly_deposit:g}", unit, "8em", "1")
        + f'<label class="qf"><span>기간</span><select class="field" name="years">{year_opts}</select></label>'
        + f'<label class="qf"><span>수수료</span><select class="field" name="fee">{fee_opts}</select></label>'
        + ('<label class="qf qf-check" title="0.01주 단위로 삽니다. 1주 값이 큰 미국 종목을 소액으로 담을 때">'
           f'<input type="checkbox" name="fractional" value="1"{" checked" if plan.fractional else ""}>'
           '<span>소수점 매수</span></label>' if ctx.market == markets.US else "")
        + ('<label class="qf qf-check" title="기본은 뺍니다. 넣으면 결과에 변동성 감쇠 경고가 붙습니다">'
           f'<input type="checkbox" name="include_leveraged" value="1"{" checked" if plan.include_leveraged else ""}>'
           '<span>레버리지·인버스 포함(실험)</span></label>')
        + '</div>'
        + '<p class="hint" style="margin:0">성향을 고르면 위험 규칙·점검 요일·최소 보유·긴급 매도가 그 성향 값으로 정해집니다. '
          '모든 성향에서 손절은 매일, 긴급 매도(하루 급락)도 매일 확인합니다.'
        + (' 미국은 <b>미국 날짜</b> 기준이라 화·금 장 마감은 한국 시간 수·토 새벽입니다.' if ctx.market == markets.US else "")
        + '</p>'
        + f'<details class="qf-more" data-keep="q-profiles"><summary>성향 비교 보기</summary>{profiles_table(ctx.market)}</details>'
        + f'<details class="qf-more" data-keep="q-exits-help"><summary>청산 규칙 설명</summary>{exits_table_help()}</details>'
        + '<details class="qf-more" data-keep="q-custom"><summary>직접 정하기 (성향을 \'직접 정하기\' 로 골랐을 때만 씀)</summary>'
        + '<div class="qf-row">'
        + f'<div class="qf"><span>점검 요일</span><div class="qf-days">{day_boxes}</div></div>'
        + _field("min_hold", "최소 보유", str(plan.min_hold), "거래일", "4em", "1")
        + _field("emergency", "긴급 매도(하루 하락)", f"{plan.emergency * 100:g}", "%", "5em")
        + '</div><div class="qf-row">'
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
        + '</div>' + field_guide()
        + '<p class="hint">0 을 넣으면 그 규칙은 꺼집니다. 비용 칸은 수수료를 \'아래 칸에 직접\' 으로 골랐을 때만 씁니다. '
        + ("기본 수수료는 가장 싼 쪽(뱅키스 비대면 최초 신규 평생 우대). 매도 거래세 0.20% 는 법정이라 줄일 수 없고, "
           "국내 주식형 ETF 만 면제입니다."
           if ctx.market == markets.KR else "한국투자증권 미국주식 기본 0.25%. 다른 증권사 이벤트로 0.07% 안팎까지 내려갑니다.")
        + '</p></details>'
        + f'<div class="qf-actions"><button type="submit" class="btn primary">{button}</button>{extra}</div></form>'
    )


def backtest_card(ctx, store) -> str:
    compare_btn = ('<button type="submit" class="btn" name="mode" value="compare" '
                   'title="같은 조건으로 전략을 모두 돌려 한 표로 봅니다">'
                   f'전략 {len(strat.STRATEGIES)}개 비교</button>')
    exits_btn = ('<button type="submit" class="btn" name="mode" value="exits" '
                 'title="고른 전략 하나로 청산 규칙을 모두 돌려 \'얼마나 덜 잃나\' 를 한 표로 봅니다">'
                 f'청산 규칙 {len(exit_rules.PRESETS)}개 비교</button>')
    from ..quant.validation import RANDOM_RUNS

    random_btn = ('<button type="submit" class="btn" name="mode" value="random" '
                  'title="같은 바구니·같은 조건·같은 청산 규칙에서 순위만 무작위로 매겨 여러 번 돌리고, 내 전략이 그보다 나았는지 봅니다">'
                  f'운과 비교 (무작위 {RANDOM_RUNS}번)</button>')
    form = settings_form(ctx, store, "backtest", "백테스트 실행", compare_btn + exits_btn + random_btn)
    form += ('<div class="qf-reset">'
             + action_button("quant_settings_reset", "설정값 초기화", ctx.here, "btn sm",
                             confirm="적어둔 설정값을 처음 상태로 되돌릴까요? (모의 계좌·기록은 그대로)")
             + '</div>')
    n = len(ctx.mine)
    universe = Plan.from_dict(store.settings(ctx.market).get("plan")).universe
    if universe == "defense":
        names = ", ".join(t for t, _ in exit_rules.BASKETS.get(ctx.market, ()))
        where = f"방어형 ETF 바구니({esc(names)})의 최대 10년치 일봉으로"
    elif is_basket(universe):
        where = f"{esc(universe_name(universe))}의 받아 둔 일봉으로(해마다 그 해 순위로 바꿔 끼우며)"
    else:
        where = f"감시 중인 {markets.MARKET_NAME[ctx.market]} 종목 {n}개의 일봉으로"
    lead = (f'<p class="hint">{where} 돌립니다. '
            '신호는 장 마감 뒤 종가로 계산하고 <b>다음 날 시가</b>에 체결합니다.</p>')
    body = (lead + form + exits_compare(ctx, store) + compare_table(ctx, store)
            + result_block(ctx, store.backtest(ctx.market)))
    return card(body, "백테스트", "규칙을 과거에 그대로 적용했다면", id_="q-backtest", pad=True)


def exits_compare(ctx, store) -> str:
    """청산 규칙 비교 — '잃지 않기' 를 숫자로 고르는 표."""
    found = store.exits(ctx.market)
    if not found or not found.get("rows"):
        return ""
    lines = []
    for r in found["rows"]:
        m = r.get("metrics") or {}
        p = exit_rules.get(r.get("exit", ""))
        tags = ""
        if p.key == found.get("safest"):
            tags += ' <span class="tag up">번 것 중 가장 덜 흔들림</span>'
        if p.key == found.get("balanced") and p.key != found.get("safest"):
            tags += ' <span class="tag accent">낙폭 대비 수익 최고</span>'
        sp = r.get("split") or {}
        tail = (sp.get("tail") or {}).get("cagr")
        lines.append(
            f'<tr><td class="l"><b>{esc(p.name)}</b>{tags}<div class="muted small">{esc(p.summary)}</div></td>'
            f'<td class="{tone(m.get("cagr"))}">{pct(m.get("cagr"), sign=True)}</td>'
            f'<td class="down">{pct(-(m.get("mdd") or 0)) if m.get("mdd") is not None else "-"}</td>'
            f'<td class="down">{pct(m.get("worst"), sign=True)}</td>'
            f'<td>{pct(m.get("loss_share"), 0)}</td><td>{m.get("loss_streak", "-")}번</td>'
            f'<td>{pct(m.get("avg_win"), sign=True)} / {pct(m.get("avg_loss"), sign=True)}</td>'
            f'<td>{num(m.get("profit_factor"))}</td><td>{m.get("trades", "-")}</td>'
            f'<td class="{tone(tail)}">{pct(tail, sign=True)}</td></tr>')
    s = strat.get(found.get("strategy", ""))
    where = "방어형 ETF 바구니" if found.get("universe") == "defense" else f"종목 {len(found.get('tickers') or [])}개"
    verdict = ""
    if found.get("safest"):
        verdict = (f'<p class="hint"><b>숫자로 고르면:</b> 비용을 빼고도 번 규칙 중 최대 낙폭이 가장 작은 것은 '
                   f'<b>{esc(exit_rules.get(found["safest"]).name)}</b>, 낙폭 1% 당 수익이 가장 큰 것은 '
                   f'<b>{esc(exit_rules.get(found["balanced"]).name)}</b> 입니다. '
                   '차이가 작으면(낙폭 2~3%p 안쪽) 운일 수 있으니 거래 수가 많은 쪽, 뒤 30% 기간도 번 쪽을 고르세요.</p>')
    else:
        verdict = ('<p class="hint"><b>비용을 빼고 번 청산 규칙이 없습니다.</b> 파는 법을 바꿔도 안 되면 사는 규칙(전략)이나 '
                   '종목 범위가 문제입니다 — 방어형 ETF 바구니로도 돌려 보세요.</p>')
    return ('<h3 class="q-h">청산 규칙 비교 <span class="muted small">'
            f'{esc(s.name)} · {esc(where)} · {esc(found.get("start") or "")} ~ {esc(found.get("end") or "")}</span></h3>'
            '<div class="table-wrap"><table class="tbl plain q-tbl"><thead><tr><th class="l">청산 규칙</th>'
            '<th>연수익률</th><th>최대 낙폭</th><th>최악 거래</th><th>손실 거래</th><th>최대 연속 손실</th>'
            '<th>평균 이익/손실</th><th>이익÷손실 합</th><th>거래</th><th>뒤 30% 연수익률</th></tr></thead>'
            f'<tbody>{"".join(lines)}</tbody></table></div>{verdict}'
            '<p class="hint">절반 익절은 판 몫을 거래 한 건으로 셉니다(승률이 높아 보일 수 있음). '
            '그래서 승률 대신 <b>최대 낙폭 · 최악 거래 · 이익÷손실 합</b>을 먼저 보세요. 1 보다 크면 번 돈이 잃은 돈보다 많습니다.</p>')


def compare_table(ctx, store) -> str:
    rows = store.compare(ctx.market)
    if not rows:
        return ""
    lines = []
    for r in rows:
        m = r.get("metrics") or {}
        s = strat.get(r.get("strategy", ""))
        if r.get("no_data"):
            lines.append(f'<tr><td class="l"><b>{esc(s.name)}</b></td><td colspan="6" class="muted">'
                         f'과거 시점 매출 자료가 없어 돌리지 못했습니다{"(한국은 아직 안 됨)" if ctx.korean else ""}</td>'
                         f'<td class="l small">{esc(s.advice)}</td></tr>')
            continue
        lines.append(
            f'<tr><td class="l"><b>{esc(s.name)}</b></td>'
            f'<td class="{tone(m.get("cagr"))}">{pct(m.get("cagr"), sign=True)}</td>'
            f'<td class="down">{pct(-(m.get("mdd") or 0)) if m.get("mdd") is not None else "-"}</td>'
            f'<td>{num(m.get("sharpe"))}</td><td>{dsr_text(r.get("dsr"))}</td><td>{m.get("trades", "-")}</td>'
            f'<td>{pct(m.get("win_rate"), 0)}</td><td>{num(m.get("payoff"))}</td>'
            f'<td class="l small">{esc(s.advice)}</td></tr>')
    bench = (rows[0].get("bench") or {}) if rows else {}
    name = rows[0].get("bench_name") or "같은 종목을 첫날 똑같이 나눠 사서 그냥 보유"
    foot = (f'<p class="hint">{esc(name)}(비용 전): 연 {pct(bench.get("cagr"), sign=True)}, '
            f'최대 낙폭 {pct(-(bench.get("mdd") or 0))}. 이걸 못 이기면 규칙을 쓸 이유가 없습니다. '
            '<b>운이 아닐 확률</b>은 지금까지 돌린 백테스트 횟수까지 감안한 값입니다(95% 이상이면 운과 구별된다고 봄).</p>'
            if bench else "")
    return ('<h3 class="q-h">전략 비교</h3><div class="table-wrap"><table class="tbl plain q-tbl">'
            '<thead><tr><th class="l">전략</th><th>연수익률</th><th>최대 낙폭</th><th>샤프</th><th>운이 아닐 확률</th><th>거래</th>'
            '<th>승률</th><th>손익비</th><th class="l">권장</th></tr></thead>'
            f'<tbody>{"".join(lines)}</tbody></table></div>{foot}')


def dsr_text(dsr: dict | None) -> str:
    if not dsr:
        return "-"
    return f'<span class="{"up" if dsr["p"] >= 0.95 else ""}">{dsr["p"] * 100:.0f}%</span>'


def validation_block(result: dict) -> str:
    """'운이 아닐까' — 시도 횟수 반영 확률과 무작위 비교."""
    parts = []
    dsr = result.get("dsr")
    if dsr:
        verdict = ("운과 구별됩니다" if dsr["p"] >= 0.95 else
                   "운과 구별하기 어렵습니다 — 설정을 더 바꿔 보기보다 모의 계좌로 앞으로를 확인하세요")
        parts.append(f'<b>운이 아닐 확률 {dsr["p"] * 100:.0f}%</b> (이 시장에서 지금까지 백테스트 {dsr["trials"]}번, '
                     f'그만큼 운이 좋았을 때 기대되는 샤프 {dsr["sr0"]:.2f}를 기준선으로) — {verdict}.')
    rnd = result.get("random") or {}
    if rnd.get("skipped"):
        parts.append(esc(rnd["skipped"]))
    elif rnd.get("runs"):
        beat = rnd.get("beat_cagr")
        parts.append(f'<b>무작위 순위 {rnd["runs"]}번과 비교</b> — 같은 바구니·조건·청산 규칙에서 순위만 무작위로 매겼을 때 '
                     f'연수익률 중간값 {pct(rnd.get("median_cagr"), sign=True)}, 최대 낙폭 중간값 {pct(-(rnd.get("median_mdd") or 0))}. '
                     f'이 전략은 연수익률로 {pct(beat, 0)}, 샤프로 {pct(rnd.get("beat_sharpe"), 0)}, '
                     f'낙폭으로 {pct(rnd.get("beat_mdd"), 0)}의 무작위 결과보다 나았습니다'
                     + (" — 순위 매기는 규칙이 운보다 낫다고 보기 어렵습니다." if beat is not None and beat < 0.8 else "."))
    if not parts:
        return ""
    return '<div class="q-split">' + "<br>".join(parts) + '</div>'



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
        ("넣은 돈 합계", money.exact(m.get("deposited"), cur), "같은 금액", ""),
        ("번 돈 (넣은 돈 제외)", money.exact(m.get("profit"), cur), money.exact(b.get("profit"), cur),
         tone(m.get("profit"))),
        ("연수익률 (CAGR)", pct(m.get("cagr"), sign=True), pct(b.get("cagr"), sign=True), tone(m.get("cagr"))),
        ("최대 낙폭 (MDD)", pct(-(m.get("mdd") or 0)), pct(-(b.get("mdd") or 0)) if b else "-", "down"),
        ("가장 긴 회복 기간", f'{m.get("underwater_days", 0)}일', f'{b.get("underwater_days", 0)}일' if b else "-", ""),
        ("샤프 지수", num(m.get("sharpe")), num(b.get("sharpe")), ""),
        ("변동성(연)", pct(m.get("vol")), pct(b.get("vol")), ""),
    ]
    bench_name = result.get("bench_name") or "같은 종목 그냥 보유"
    short = "그냥 보유" if bench_name.startswith("같은 종목") else bench_name.replace(" 그냥 보유", "")
    tile_html = "".join(
        f'<div class="q-tile"><span>{esc(label)}</span><b class="{cls}">{esc(value)}</b>'
        f'<em>{short} {esc(bench)}</em></div>' for label, value, bench, cls in tiles)
    stats = [
        ("거래", f'{m.get("trades", 0)}번'), ("승률", pct(m.get("win_rate"), 0)),
        ("평균 이익", pct(m.get("avg_win"), sign=True)), ("평균 손실", pct(m.get("avg_loss"), sign=True)),
        ("손익비", num(m.get("payoff"))), ("낸 비용", money.exact(m.get("fees"), cur)),
        ("연 회전율", f'{num(m.get("turnover"), 1)}회' if m.get("turnover") is not None else "-"),
        ("주식 보유 비중", pct(m.get("exposure"), 0)),
    ]
    stat_html = "".join(f'<div><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in stats)
    decay_html = ""
    if m.get("cagr") is not None and m["cagr"] > 0:
        from ..quant.evidence import DECAY

        decay_html = (f'<div class="q-split">참고: 논문에 나온 규칙 97개는 발표 뒤 수익이 평균 {DECAY:.0%} 줄었습니다. '
                      f'같은 비율로 깎으면 연 {pct(m["cagr"] * (1 - DECAY), sign=True)} 입니다 — 실전 기대치는 이쪽에 가깝게 잡으세요.</div>')
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
            f'시작 {money.exact(result.get("capital"), cur)}{_plan_text(result.get("plan"), cur)} · 왕복 비용 약 '
            f'{pct(CostModel.from_dict(result["costs"], ctx.market).round_trip if result.get("costs") else None, 2)}</div>')
    return (head + f'<div class="q-tiles">{tile_html}</div>'
            + curve_svg(result.get("curve") or [], result.get("bench_name") or "")
            + f'<div class="q-stats">{stat_html}</div>{validation_block(result)}{split_html}{decay_html}'
            + (f'<div class="q-warn"><b>이 결과를 믿기 전에</b><ul>{warn}</ul></div>' if warn else "")
            + trades_table(ctx, result.get("trades") or [], cur))


def _plan_text(raw: dict | None, cur: str) -> str:
    plan = Plan.from_dict(raw)
    deposit = f" + 매달 {money.exact(plan.monthly_deposit, cur)}" if plan.monthly_deposit else ""
    universe = f" · {universe_name(plan.universe)}" if plan.universe != "watch" else ""
    regime = f" · 시장 국면 필터: {MARKET_FILTERS[plan.market_filter]}" if plan.market_filter != "off" else ""
    return (f"{deposit} · 점검 {plan.days_text} · 최소 보유 {plan.min_hold}일"
            f" · 청산 {exit_rules.describe(plan)}{universe}{regime}")


def curve_svg(points: list, bench_name: str = "", height: int = 180) -> str:
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
            f'<figcaption><i class="q-key k-main"></i>전략 <i class="q-key k-bench"></i>{esc(bench_name or "같은 종목 그냥 보유")}(비용 전) '
            '<i class="q-key k-dd"></i>고점 대비 낙폭</figcaption></figure>')


def trades_table(ctx, trades: list, cur: str, limit: int = 30) -> str:
    if not trades:
        return ""
    rows = "".join(
        f'<tr><td class="l"><a href="{esc(stock_url(t["ticker"]))}">{esc(markets.display(t["ticker"]))}</a></td>'
        f'<td>{esc(t["entry_day"])}</td><td>{esc(t["exit_day"])}</td><td>{fmt_shares(t["shares"])}</td>'
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
             ("넣은 돈", money.exact(eng.capital + eng.deposited, cur), ""),
             ("수익률(넣은 돈 제외)", pct(view["return"], sign=True), tone(view["return"])),
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
            f'<tr><td class="l"><a href="{esc(stock_url(t))}">{esc(markets.display(t))}</a></td><td>{fmt_shares(p.shares)}</td>'
            f'<td>{money.price(p.cost, cur)}</td><td>{money.price(last, cur)}</td>'
            f'<td class="{tone(gain)}">{pct(gain, sign=True)}</td><td>{money.price(p.stop, cur)}</td>'
            f'<td>{esc(p.entry_day)}</td></tr>')
    positions = ('<div class="table-wrap"><table class="tbl plain q-tbl"><thead><tr><th class="l">보유</th><th>주수</th>'
                 '<th>평균 단가</th><th>최근 종가</th><th>손익</th><th>손절선</th><th>산 날</th></tr></thead>'
                 f'<tbody>{"".join(pos_rows)}</tbody></table></div>') if pos_rows else '<div class="empty">보유 종목 없음</div>'
    orders = "".join(f'<li><b>{"사기" if o.side == "buy" else "팔기"}</b> {esc(markets.display(o.ticker))} '
                     f'{fmt_shares(o.shares)}주 — {esc(o.reason)} <span class="muted">({esc(o.made)} 종가 기준)</span></li>'
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
    status_html = f'<p class="hint q-status">{esc(_status_text(view))}</p>'
    buttons = (action_button("paper_step", "지금 반영", ctx.here, "btn sm",
                             title="끝난 거래일을 지금 바로 이어서 처리합니다")
               + action_button("paper_pause", "재개" if view["paused"] else "멈춤", ctx.here, "btn sm")
               + action_button("paper_reset", "초기화", ctx.here, "btn sm",
                               confirm="모의 계좌를 지우고 처음부터 시작할까요?"))
    s = eng.strategy
    sub = f'{s.name} · {esc(view["started"] or "")} 시작{esc(_plan_text(eng.plan.to_dict(), cur))}'
    curve = curve_svg([(d, v, None) for d, v in eng.curve]) if len(eng.curve) > 1 else ""
    body = (warn_html + f'<div class="q-tiles">{tile_html}</div>{status_html}{curve}<h3 class="q-h">보유</h3>{positions}'
            + (f'<h3 class="q-h">다음 시가에 낼 주문</h3><ul class="q-list">{orders}</ul>' if orders else "")
            + (f'<details class="qf-more" data-keep="q-events"><summary>자동 일지 (최근 30건)</summary>'
               f'<ul class="q-list q-events">{events}</ul></details>' if events else ""))
    return card(body, "모의 계좌", sub, buttons, id_="q-paper", pad=True)


def _status_text(view) -> str:
    """'돌아가고 있나?' 에 화면에서 답한다 — 마지막으로 처리한 날, 다음 점검일, 신호가 났는데 안 산 이유."""
    from ..quant import paper

    eng = view["engine"]
    bits = [f"마지막으로 처리한 거래일 {eng.last_day or '없음'}"]
    upcoming = paper.next_check(eng)
    if upcoming:
        bits.append(f"다음 점검 {upcoming}({eng.plan.days_text}, 휴장이면 다음 점검일) 장 마감 뒤")
    bits.append(f"새로 살 수 있는 종목 {len(set(view['universe']) - set(view.get('left_out') or []))}개")
    if view.get("left_out"):
        bits.append(f"레버리지·인버스라 뺀 종목 {', '.join(view['left_out'])}")
    if eng.plan.market_filter != "off" and eng.regime_on is not None:
        bits.append("시장 국면: 지수 200일선 " + ("위" if eng.regime_on else "아래 — " + MARKET_FILTERS[eng.plan.market_filter]))
    missed = [(eng.skipped, "1주도 못 사서"), (eng.no_cash, "현금이 다른 종목에 들어가 있어서"),
              (eng.chased, "그날 급등해서"), (eng.blocked, "손실·낙폭 규칙에 막혀"),
              (eng.regime_blocked, "시장이 200일선 아래라")]
    why = [f"{label} {n}번" for n, label in missed if n]
    bits.append("신호가 났는데 안 산 경우: " + ", ".join(why) if why
                else "점검일에 살 신호가 난 종목이 없으면 거래하지 않습니다 — 거래가 드문 것이 정상일 수 있습니다")
    return " · ".join(bits)


# --------------------------------------------------------------------------
# 주간 성적표
# --------------------------------------------------------------------------
REPORT_ROWS = 12


def backup_line(ctx) -> str:
    """기록을 어디에 따로 남기는지, 마지막으로 언제 남겼는지, 지금 남기는 단추."""
    from datetime import datetime

    from .. import mobile as phone_app
    from ..quant import backup

    place = backup.root()
    made = backup.last_made()
    when = datetime.fromtimestamp(made).strftime("%Y-%m-%d %H:%M") if made else "아직 없음"
    where = ("휴대폰 앱 안(앱을 지우면 같이 지워집니다)" if phone_app.active()
             else f"{place} — 프로그램 폴더를 지워도 남습니다")
    error = (f' <b class="down">마지막 백업 실패: {esc(backup.last_error)}</b>' if backup.last_error else "")
    button = action_button("quant_backup", "지금 백업", ctx.here, "btn sm",
                           title="주간 성적표·거래 내역·계좌 가치·매매 일지(CSV)와 원본(quant.json)을 오늘 날짜 폴더에")
    return (f'<p class="hint">기록 백업: 일주일에 한 번 저절로, 날짜별 폴더로 {esc(where)}. '
            f'마지막 백업 {esc(when)}.{error} {button}</p>')


def reports_card(ctx, store) -> str:
    """모의 계좌를 매주 한 장씩 — 전체 평가(시작 뒤)와 그 주의 세부. 실전 전에 쌓는 데이터."""
    from ..quant.data import BENCH

    account = store.paper(ctx.market)
    everything = store.reports(ctx.market)
    csv_link = (f'<a class="btn sm" href="/download/quant_weekly?m={esc(ctx.market)}">CSV 내려받기</a>'
                if everything else "")
    if not account:
        return fold_card('<div class="empty">모의 계좌를 시작하면 매주 마지막 거래일 장 마감 뒤 한 장씩 쌓입니다.</div>',
                         "주간 성적표", "모의 계좌가 없음", key="q-reports")
    started = account.get("started")
    reports = [r for r in everything if r.get("account") == started]
    if not reports:
        body = ('<div class="empty">첫 성적표는 첫 주가 끝난 뒤(그 주 마지막 거래일을 처리한 뒤) 생깁니다. '
                'PC 에서는 텔레그램으로도 보냅니다.</div>') + backup_line(ctx)
        return card(body, "주간 성적표", f"{esc(started or '')} 시작 계좌", csv_link, id_="q-reports", pad=True)
    bench = BENCH[ctx.market][1]
    r = reports[-1]
    overall = [
        ("시작 뒤 수익률", pct(r.get("total_return"), sign=True), f"{bench} {pct(r.get('bench_total'), sign=True)}",
         tone(r.get("total_return"))),
        ("지수와 차이", pct(_diff(r.get("total_return"), r.get("bench_total")), sign=True), "넣은 돈 제외",
         tone(_diff(r.get("total_return"), r.get("bench_total")))),
        ("최대 낙폭", pct(-(r.get("mdd") or 0)), f"지금 고점 대비 {pct(-(r.get('drawdown') or 0))}", "down"),
        ("거래(판 것)", f"{r.get('trades_total', 0)}번", f"승률 {pct(r.get('win_rate'), 0)} · 이익÷손실 "
                                                       f"{num(r.get('profit_factor'))}", ""),
    ]
    tiles = "".join(f'<div class="q-tile"><span>{esc(k)}</span><b class="{c}">{esc(v)}</b><em>{esc(sub)}</em></div>'
                    for k, v, sub, c in overall)
    expect = r.get("expect")
    if expect:
        expect_html = (f'<p class="hint"><b>백테스트 기대와 비교</b>({expect["days"]}거래일): {esc(expect["verdict"])} '
                       f'(모의 연 {pct(expect["paper_year"], sign=True)} vs 백테스트 연 {pct(expect["backtest_year"], sign=True)}, '
                       f'z = {expect["z"]:+.1f} — ±2 를 넘으면 우연이라 보기 어려운 차이)</p>')
    elif account.get("baseline"):
        expect_html = '<p class="hint">백테스트 기대와 비교는 20거래일이 쌓인 뒤부터 합니다.</p>'
    else:
        expect_html = ('<p class="hint">같은 설정의 백테스트가 없어 \'기대한 만큼 가고 있나\' 는 비교하지 않았습니다. '
                       '모의 계좌와 같은 설정으로 백테스트를 한 번 돌리면 다음 장부터 비교합니다.</p>')
    week_line = (f'<p class="line"><b>{esc(r["week"])}</b> ({esc(r["from"])}~{esc(r["to"])}) — 이번 주 '
                 f'<b class="{tone(r.get("week_return"))}">{pct(r.get("week_return"), sign=True)}</b>, '
                 f'{esc(bench)} {pct(r.get("bench_week"), sign=True)}'
                 + (", 시장 국면: 지수 200일선 " + ("위" if r["regime"] else "아래") if r.get("regime") is not None else "")
                 + '</p>')
    events = "".join(f"<li>{esc(t)}</li>" for t in r.get("events") or [])
    held = r.get("positions")
    held_html = ""
    if held is not None:
        cur = currency_of(ctx.market)
        rows = "".join(
            f'<tr><td class="l">{esc(markets.display(p["ticker"]))}</td><td>{money.price(p.get("cost"), cur)}</td>'
            f'<td>{money.price(p.get("last"), cur)}</td><td class="{tone(p.get("gain"))}">{pct(p.get("gain"), sign=True)}</td>'
            f'<td>{pct(p.get("weight"), 0)}</td><td>{esc(p.get("since") or "")}</td></tr>' for p in held)
        held_html = ('<div class="table-wrap"><table class="tbl plain q-tbl"><thead><tr><th class="l">보유</th><th>산 값</th>'
                     '<th>최근 종가</th><th>손익</th><th>비중</th><th>산 날</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></div>' if rows else '<div class="empty">보유 없음(현금)</div>')
    missed = r.get("missed") or {}
    missed_text = ", ".join(f"{label} {missed[k]}번" for k, label in
                            (("skipped", "1주도 못 사서"), ("no_cash", "현금 부족"), ("chased", "급등일이라"),
                             ("blocked", "손실·낙폭 규칙"),
                             ("regime_blocked", "시장 국면")) if missed.get(k))
    detail = (week_line + (f'<ul class="q-list">{events}</ul>' if events else '<p class="hint">이번 주에 사고판 것 없음.</p>')
              + held_html + (f'<p class="hint">신호가 났는데 안 산 경우(이번 주): {esc(missed_text)}</p>' if missed_text else ""))
    history = "".join(
        f'<tr><td class="l">{esc(x["week"])}</td><td class="{tone(x.get("week_return"))}">{pct(x.get("week_return"), sign=True)}</td>'
        f'<td>{pct(x.get("bench_week"), sign=True)}</td>'
        f'<td class="{tone(_diff(x.get("week_return"), x.get("bench_week")))}">'
        f'{pct(_diff(x.get("week_return"), x.get("bench_week")), sign=True)}</td>'
        f'<td class="{tone(x.get("total_return"))}">{pct(x.get("total_return"), sign=True)}</td>'
        f'<td class="down">{pct(-(x.get("mdd") or 0))}</td><td>{x.get("trades_week", 0)}</td></tr>'
        for x in reversed(reports[-REPORT_ROWS:]))
    table = ('<div class="table-wrap"><table class="tbl plain q-tbl"><thead><tr><th class="l">주</th><th>이번 주</th>'
             f'<th>{esc(bench)}</th><th>차이</th><th>시작 뒤</th><th>최대 낙폭</th><th>거래</th></tr></thead>'
             f'<tbody>{history}</tbody></table></div>')
    body = (f'<h3 class="q-h">전체 평가</h3><div class="q-tiles">{tiles}</div>{expect_html}'
            f'<h3 class="q-h">이번 주 세부</h3>{detail}<h3 class="q-h">지난 주들</h3>{table}'
            '<p class="hint">매주 한 장씩 쌓입니다(실제 주문 없음). 몇 달 쌓이면 \'지수보다 나은 주가 얼마나 되나\', '
            '\'백테스트만큼 가나\' 를 숫자로 볼 수 있습니다. CSV 는 엑셀·구글 시트·코랩에서 바로 열 수 있습니다'
            + (" (휴대폰 앱에서는 내려받기가 안 되니 PC 에서)" if _on_phone() else "") + '.</p>' + backup_line(ctx))
    return card(body, "주간 성적표", f"{len(reports)}주 · {esc(started or '')} 시작 계좌", csv_link,
                id_="q-reports", pad=True)


def _diff(a, b):
    return None if a is None or b is None else a - b


def _on_phone() -> bool:
    from .. import mobile

    return mobile.active()


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
    return fold_card(form + (f'<ul class="q-list">{items}</ul>' if items else ""), "매매 일지",
                     f"규칙을 바꾸고 싶을 때 먼저 적기 · {len(entries)}건", key="q-journal")


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
    return fold_card(f'<div class="q-rules">{rows}</div>{sizing}', "전략 설명",
                     f"전략 {len(strat.STRATEGIES)}개의 규칙 · 근거 · 권장", key="q-rules")


def evidence_card() -> str:
    """표본이 큰 연구와, 그걸 이 프로그램에 어떻게 반영했는지."""
    from ..quant.evidence import STUDIES

    rows = "".join(
        f'<tr><td class="l"><a href="{esc(st.url)}" target="_blank" rel="noopener">{esc(st.name)}</a></td>'
        f'<td class="l small">{esc(st.sample)}</td><td class="l small">{esc(st.finding)}</td>'
        f'<td class="l small"><b>{esc(st.applied)}</b></td></tr>' for st in STUDIES)
    note = ('<p class="hint">숫자는 논문 요약·보고서 안내문 기준입니다. \'돈을 버는 개인 퀀트가 어떻게 하는지\' 를 100명 이상 '
            '조사한 공개 연구는 찾지 못해서, 대신 \'어떻게 하면 잃는지\' 를 수십만 명 단위로 확인한 연구를 규칙으로 옮겼습니다.</p>')
    table = ('<div class="table-wrap"><table class="tbl plain q-tbl q-evidence"><thead><tr><th class="l">연구</th>'
             '<th class="l">표본</th><th class="l">찾은 것</th><th class="l">이 프로그램에 반영</th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>')
    return fold_card(table + note, "근거", f"표본이 큰 연구 {len(STUDIES)}개와 반영한 규칙", key="q-evidence")
