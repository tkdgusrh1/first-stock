"""메모의 판단 기준을 그대로 코드로 옮긴 지표 계산기.

우선순위: 가이던스(1순위) > 어닝 서프라이즈(2순위) > 마진 방향(3순위)
흑자 기업 5개 체크 / 적자 기업 5개 체크는 각각 profit_checks(), loss_checks() 에 있다.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date

from .prices import PriceClient
from .xbrl import CompanyFacts

log = logging.getLogger(__name__)

PASS, WARN, FAIL, NA = "pass", "warn", "fail", "na"
STATUS_ICON = {PASS: "✅", WARN: "⚠️", FAIL: "❌", NA: "➖"}

DEFAULT_TAX_RATE = 0.21
ROE_TARGET = 0.15          # 메모: ROE 15% 이상 유지
LOSS_GROWTH_TARGET = 0.30  # 메모: 적자 기업 매출 성장률 30% 이상
MIN_RUNWAY_YEARS = 2.0     # 메모: 런웨이 2년 미만이면 금지


@dataclass
class Check:
    label: str
    status: str
    detail: str


@dataclass
class Metrics:
    ticker: str
    company: str = ""
    as_of: date | None = None
    profitable: bool | None = None

    price: float | None = None
    price_change_pct: float | None = None
    # 장외(프리·애프터마켓)
    extended_price: float | None = None
    extended_change_pct: float | None = None
    extended_label: str = ""
    market_state: str = ""
    market_cap: float | None = None
    shares: float | None = None

    revenue_ttm: float | None = None
    revenue_ttm_prior: float | None = None
    revenue_growth: float | None = None
    net_income_ttm: float | None = None
    net_income_ttm_prior: float | None = None
    operating_income_ttm: float | None = None
    op_margin: float | None = None
    op_margin_prior: float | None = None
    gross_margin: float | None = None
    ocf_ttm: float | None = None
    fcf_ttm: float | None = None
    eps_ttm: float | None = None
    equity: float | None = None
    cash: float | None = None
    total_debt: float | None = None

    roe: float | None = None
    roic: float | None = None
    per: float | None = None
    per_median_5y: float | None = None
    psr: float | None = None
    runway_years: float | None = None

    quarterly_revenue: list[tuple[date, float]] = field(default_factory=list)
    # 분기별 추이 (방향을 보기 위한 것) — {"revenue": [(분기말, 값)], ...}
    trends: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    # 지표별 출처: 어떤 XBRL 항목을 어느 기간·어느 보고서에서 가져왔는지
    sources: dict[str, str] = field(default_factory=dict)
    price_source: str = ""
    checks: list[Check] = field(default_factory=list)
    priority: list[Check] = field(default_factory=list)
    peers: dict[str, dict] = field(default_factory=dict)
    surprise: dict | None = None
    milestones: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_metrics(
    ticker: str,
    facts: CompanyFacts | None,
    prices: PriceClient | None = None,
    *,
    consensus_eps: float | None = None,
    consensus_revenue: float | None = None,
    milestones: list[str] | None = None,
    peer_metrics: dict[str, "Metrics"] | None = None,
) -> Metrics:
    m = Metrics(ticker=ticker.upper(), milestones=list(milestones or []))
    if facts is None:
        m.warnings.append("SEC XBRL 재무데이터를 가져오지 못했습니다.")
        return m

    m.company = facts.entity_name
    m.sources = collect_sources(facts)

    # --- 손익 -----------------------------------------------------------
    m.revenue_ttm = facts.ttm("revenue")
    m.revenue_ttm_prior = facts.ttm_prior("revenue")
    m.net_income_ttm = facts.ttm("net_income")
    m.net_income_ttm_prior = facts.ttm_prior("net_income")
    m.operating_income_ttm = facts.ttm("operating_income")
    m.ocf_ttm = facts.ttm("ocf")
    capex_ttm = facts.ttm("capex")
    gross_ttm = facts.ttm("gross_profit")

    quarters = facts.quarterly("revenue", limit=8)
    m.quarterly_revenue = [(f.end, f.val) for f in quarters]
    if quarters:
        m.as_of = quarters[-1].end

    if m.revenue_ttm and m.revenue_ttm_prior:
        m.revenue_growth = (m.revenue_ttm - m.revenue_ttm_prior) / abs(m.revenue_ttm_prior)
    if m.revenue_ttm and m.operating_income_ttm is not None:
        m.op_margin = m.operating_income_ttm / m.revenue_ttm
    if m.revenue_ttm and gross_ttm is not None:
        m.gross_margin = gross_ttm / m.revenue_ttm
    if m.ocf_ttm is not None and capex_ttm is not None:
        m.fcf_ttm = m.ocf_ttm - abs(capex_ttm)

    m.trends = build_trends(facts)

    prior_op = _ttm_prior_value(facts, "operating_income")
    if m.revenue_ttm_prior and prior_op is not None:
        m.op_margin_prior = prior_op / m.revenue_ttm_prior

    m.profitable = (m.net_income_ttm or 0) > 0 if m.net_income_ttm is not None else None

    # --- 재무상태 -------------------------------------------------------
    equity = facts.latest_instant("equity")
    m.equity = equity.val if equity else None
    cash = facts.latest_instant("cash")
    sti = facts.latest_instant("short_term_investments")
    m.cash = (cash.val if cash else 0) + (sti.val if sti else 0) or None
    debt_lt = facts.latest_instant("debt_lt")
    debt_st = facts.latest_instant("debt_st")
    m.total_debt = (debt_lt.val if debt_lt else 0) + (debt_st.val if debt_st else 0)

    # --- 효율: ROE / ROIC ----------------------------------------------
    if m.net_income_ttm is not None and m.equity:
        m.roe = m.net_income_ttm / m.equity
    m.roic = _roic(facts, m)

    # --- 밸류에이션 -----------------------------------------------------
    m.shares = facts.shares_outstanding()
    m.eps_ttm = _eps_ttm(facts, m)

    if prices:
        quote = prices.quote(ticker)
        if quote:
            m.price = quote.price
            m.price_source = quote.source
            m.price_change_pct = quote.change_pct
            if m.price_change_pct is None:
                m.price_change_pct = prices.prev_close_change(ticker)
            m.extended_price = quote.extended_price
            m.extended_change_pct = quote.extended_change_pct
            m.extended_label = quote.extended_label
            m.market_state = quote.state_label
            m.sources["price"] = (
                f"{quote.source} · {quote.state_label or '종가'}"
                + (f" ({quote.day})" if quote.day else "")
            )
    if m.price and m.shares:
        m.market_cap = m.price * m.shares
    if m.price and m.eps_ttm and m.eps_ttm > 0:
        m.per = m.price / m.eps_ttm
    if m.market_cap and m.revenue_ttm:
        m.psr = m.market_cap / m.revenue_ttm
    if prices:
        m.per_median_5y = historical_per_median(ticker, facts, prices)

    # --- 런웨이(적자 기업) ----------------------------------------------
    burn = None
    if m.fcf_ttm is not None and m.fcf_ttm < 0:
        burn = -m.fcf_ttm
    elif m.ocf_ttm is not None and m.ocf_ttm < 0:
        burn = -m.ocf_ttm
    if burn and m.cash:
        m.runway_years = m.cash / burn

    # --- 서프라이즈 -----------------------------------------------------
    m.surprise = _surprise(facts, consensus_eps, consensus_revenue)

    # --- 체크리스트 -----------------------------------------------------
    m.peers = {t: _peer_summary(p) for t, p in (peer_metrics or {}).items()}
    m.checks = profit_checks(m) if m.profitable else loss_checks(m)
    m.priority = priority_checks(m)
    return m


# --------------------------------------------------------------------------
# 우선순위 체크: 가이던스 → 어닝 서프라이즈 → 마진 방향
# --------------------------------------------------------------------------
def priority_checks(m: Metrics) -> list[Check]:
    out: list[Check] = []

    out.append(
        Check(
            "1순위 · 가이던스",
            NA,
            "회사 발표 전망은 수치로 자동 추출되지 않습니다. "
            "8-K 2.02/7.01 알림이 오면 원문에서 다음 분기 가이던스와 "
            "'과거 가이던스를 지켰는지' 이력을 직접 확인하세요.",
        )
    )

    surprises = [
        v
        for v in ((m.surprise or {}).get("eps_surprise_pct"), (m.surprise or {}).get("rev_surprise_pct"))
        if v is not None
    ]
    if m.surprise and surprises:
        s = m.surprise
        parts = []
        if s.get("eps_surprise_pct") is not None:
            parts.append(f"EPS {s['actual_eps']:.2f} vs 컨센 {s['consensus_eps']:.2f} ({s['eps_surprise_pct']:+.1f}%)")
        if s.get("rev_surprise_pct") is not None:
            parts.append(
                f"매출 {_money(s['actual_revenue'])} vs 컨센 {_money(s['consensus_revenue'])} ({s['rev_surprise_pct']:+.1f}%)"
            )
        parts.append(f"기준 분기 {s.get('period', '-')}")
        status = PASS if min(surprises) >= 0 else FAIL
        out.append(Check("2순위 · 어닝 서프라이즈", status, " / ".join(parts)))
    else:
        out.append(
            Check(
                "2순위 · 어닝 서프라이즈",
                NA,
                "컨센서스가 없습니다. config 의 consensus_eps / consensus_revenue 에 넣으면 "
                "발표 실적과 자동 비교합니다.",
            )
        )

    if m.op_margin is not None and m.op_margin_prior is not None:
        delta = (m.op_margin - m.op_margin_prior) * 100
        status = PASS if delta > 0.2 else (FAIL if delta < -0.2 else WARN)
        word = "개선" if delta > 0 else ("악화" if delta < 0 else "보합")
        out.append(
            Check(
                "3순위 · 마진 방향",
                status,
                f"영업이익률 {_pct(m.op_margin)} (전년 동기 {_pct(m.op_margin_prior)}, {delta:+.1f}%p {word})",
            )
        )
    elif m.op_margin is not None:
        out.append(Check("3순위 · 마진 방향", NA, f"영업이익률 {_pct(m.op_margin)} (비교할 전년 데이터 부족)"))
    else:
        out.append(Check("3순위 · 마진 방향", NA, "영업이익 데이터 없음"))

    return out


# --------------------------------------------------------------------------
# 흑자 기업 체크리스트 (메모 그대로)
# --------------------------------------------------------------------------
def profit_checks(m: Metrics) -> list[Check]:
    out: list[Check] = []

    # 1. 분기별로 계속 매출이 있는지
    quarters = m.quarterly_revenue[-4:]
    if len(quarters) >= 4:
        yoy = None
        if len(m.quarterly_revenue) >= 8:
            base = m.quarterly_revenue[-5][1]
            if base:
                yoy = (quarters[-1][1] - base) / abs(base)
        detail = "최근 4개 분기 매출: " + " → ".join(_money(v) for _, v in quarters)
        if yoy is not None:
            detail += f" (최근 분기 YoY {yoy:+.1%})"
        status = PASS if all(v > 0 for _, v in quarters) else FAIL
        out.append(Check("① 분기 매출 지속", status, detail))
    else:
        out.append(Check("① 분기 매출 지속", NA, "분기 매출 데이터가 4개 미만"))

    # 2. ROE 15% 이상 (판단은 ROIC 로)
    if m.roe is not None:
        bits = [f"ROE {_pct(m.roe)}"]
        if m.roic is not None:
            bits.append(f"ROIC {_pct(m.roic)}")
        judge = m.roic if m.roic is not None else m.roe
        status = PASS if judge >= ROE_TARGET else (WARN if judge >= ROE_TARGET * 0.7 else FAIL)
        bits.append(f"기준 15% ({'충족' if status == PASS else '미달'})")
        out.append(Check("② 자본 효율 (ROE/ROIC)", status, " · ".join(bits)))
    else:
        out.append(Check("② 자본 효율 (ROE/ROIC)", NA, "자기자본 또는 순이익 데이터 없음"))

    # 3. 영업이익률 방향
    if m.op_margin is not None and m.op_margin_prior is not None:
        delta = (m.op_margin - m.op_margin_prior) * 100
        status = PASS if delta > 0 else FAIL
        out.append(
            Check("③ 영업이익률 방향", status, f"{_pct(m.op_margin_prior)} → {_pct(m.op_margin)} ({delta:+.1f}%p)")
        )
    else:
        out.append(Check("③ 영업이익률 방향", NA, f"현재 {_pct(m.op_margin)} · 전년 비교 불가"))

    # 4. PER 위치 (과거 평균 대비 / 동종업계 대비)
    out.append(_per_check(m))

    # 5. 영업현금흐름 > 순이익
    if m.ocf_ttm is not None and m.net_income_ttm is not None:
        ratio = m.ocf_ttm / m.net_income_ttm if m.net_income_ttm else None
        status = PASS if m.ocf_ttm > m.net_income_ttm else FAIL
        detail = f"영업현금흐름 {_money(m.ocf_ttm)} vs 순이익 {_money(m.net_income_ttm)}"
        if ratio:
            detail += f" (배수 {ratio:.2f}x)"
        if status == FAIL:
            detail += " — 이익은 나는데 현금이 안 들어옴, 매출채권·재고 확인 필요"
        out.append(Check("⑤ 영업현금흐름 > 순이익", status, detail))
    else:
        out.append(Check("⑤ 영업현금흐름 > 순이익", NA, "현금흐름 데이터 없음"))

    return out


# --------------------------------------------------------------------------
# 적자 기업 체크리스트 (메모 그대로)
# --------------------------------------------------------------------------
def loss_checks(m: Metrics) -> list[Check]:
    out: list[Check] = []

    # 1. 매출 성장률 30% 이상 — 무조건 판단
    if m.revenue_growth is not None:
        status = PASS if m.revenue_growth >= LOSS_GROWTH_TARGET else FAIL
        out.append(
            Check(
                "① 매출 성장률 30%+",
                status,
                f"TTM 매출 {_money(m.revenue_ttm)} · 성장률 {m.revenue_growth:+.1%} (기준 +30%)",
            )
        )
    else:
        out.append(Check("① 매출 성장률 30%+", NA, "전년 비교 매출 데이터 없음"))

    # 2. 적자가 줄고 있는지 (매출↑ + 적자↑ = 최악)
    if m.net_income_ttm is not None and m.net_income_ttm_prior is not None:
        improving = m.net_income_ttm > m.net_income_ttm_prior
        detail = f"순손익 {_money(m.net_income_ttm_prior)} → {_money(m.net_income_ttm)}"
        if improving:
            status, detail = PASS, detail + " (적자 축소)"
        else:
            status = FAIL
            detail += " (적자 확대)"
            if m.revenue_growth and m.revenue_growth > 0:
                detail += " ⚠️ 매출은 느는데 적자도 늘어남 — 메모 기준 최악의 조합"
        out.append(Check("② 적자 축소 추세", status, detail))
    else:
        out.append(Check("② 적자 축소 추세", NA, "전년 비교 손익 데이터 없음"))

    # 3. 현금 런웨이 2년
    if m.runway_years is not None:
        status = PASS if m.runway_years >= MIN_RUNWAY_YEARS else FAIL
        detail = f"보유 현금 {_money(m.cash)} / 연간 소진 {_money(_burn(m))} = {m.runway_years:.1f}년"
        if status == FAIL:
            detail += " — 2년 미만, 메모 기준 '금지' (증자 희석 위험)"
        out.append(Check("③ 현금 런웨이 ≥ 2년", status, detail))
    elif m.cash and (m.fcf_ttm or 0) >= 0:
        out.append(Check("③ 현금 런웨이 ≥ 2년", PASS, f"현금 소진 없음(FCF {_money(m.fcf_ttm)}) · 현금 {_money(m.cash)}"))
    else:
        out.append(Check("③ 현금 런웨이 ≥ 2년", NA, "현금 또는 현금흐름 데이터 없음"))

    # 4. PSR 동종업계 대비
    out.append(_psr_check(m))

    # 5. 핵심 마일스톤
    if m.milestones:
        out.append(
            Check(
                "⑤ 핵심 마일스톤",
                NA,
                "직접 확인: " + " / ".join(m.milestones) + " — 8-K 알림이 오면 이 항목부터 대조하세요.",
            )
        )
    else:
        out.append(Check("⑤ 핵심 마일스톤", NA, "config 의 milestones 에 적어두면 매번 같이 띄워줍니다."))

    return out


def _per_check(m: Metrics) -> Check:
    if m.per is None:
        reason = "적자라 PER 산출 불가" if (m.eps_ttm or 0) <= 0 else "주가 또는 EPS 데이터 없음"
        return Check("④ PER 위치", NA, reason)

    bits = [f"PER {m.per:.1f}x"]
    status = WARN
    if m.per_median_5y:
        premium = (m.per / m.per_median_5y - 1) * 100
        bits.append(f"과거 5년 중앙값 {m.per_median_5y:.1f}x ({premium:+.0f}%)")
        status = PASS if premium <= 0 else (WARN if premium <= 30 else FAIL)
    peer_pers = [p["per"] for p in m.peers.values() if p.get("per")]
    if peer_pers:
        peer_median = statistics.median(peer_pers)
        bits.append(f"동종업계 중앙값 {peer_median:.1f}x")
        if m.per > peer_median * 1.3 and status != FAIL:
            status = WARN
    if not m.per_median_5y and not peer_pers:
        bits.append("비교 기준 없음 (peers 설정 권장)")
        status = NA
    return Check("④ PER 위치", status, " · ".join(bits))


def _psr_check(m: Metrics) -> Check:
    if m.psr is None:
        return Check("④ PSR 위치", NA, "시가총액 또는 매출 데이터 없음")
    bits = [f"PSR {m.psr:.1f}x"]
    status = WARN
    peer_psrs = [p["psr"] for p in m.peers.values() if p.get("psr")]
    if peer_psrs:
        peer_median = statistics.median(peer_psrs)
        bits.append(f"동종업계 중앙값 {peer_median:.1f}x")
        status = PASS if m.psr <= peer_median else FAIL
    else:
        bits.append("비교 대상 없음 (config 의 peers 설정 권장)")
        status = NA
    return Check("④ PSR 위치", status, " · ".join(bits))


# --------------------------------------------------------------------------
# 보조 계산
# --------------------------------------------------------------------------
def _roic(facts: CompanyFacts, m: Metrics) -> float | None:
    if m.operating_income_ttm is None:
        return None
    tax_rate = DEFAULT_TAX_RATE
    tax = facts.ttm("tax_expense")
    pretax = facts.ttm("pretax_income")
    if tax is not None and pretax and pretax > 0:
        candidate = tax / pretax
        if 0 <= candidate <= 0.45:
            tax_rate = candidate
    nopat = m.operating_income_ttm * (1 - tax_rate)
    invested = (m.equity or 0) + (m.total_debt or 0) - (m.cash or 0)
    if invested <= 0:
        return None
    return nopat / invested


def _eps_ttm(facts: CompanyFacts, m: Metrics) -> float | None:
    quarters = facts.quarterly("eps", limit=4)
    if len(quarters) == 4:
        return sum(f.val for f in quarters)
    if m.net_income_ttm is not None and m.shares:
        return m.net_income_ttm / m.shares
    annual = facts.annual("eps", limit=1)
    return annual[0].val if annual else None


def _ttm_prior_value(facts: CompanyFacts, key: str) -> float | None:
    quarters = facts.quarterly(key, limit=8)
    if len(quarters) < 8:
        return None
    return sum(f.val for f in quarters[:4])


def _burn(m: Metrics) -> float | None:
    if m.fcf_ttm is not None and m.fcf_ttm < 0:
        return -m.fcf_ttm
    if m.ocf_ttm is not None and m.ocf_ttm < 0:
        return -m.ocf_ttm
    return None


def _surprise(facts: CompanyFacts, consensus_eps: float | None, consensus_revenue: float | None) -> dict | None:
    if consensus_eps is None and consensus_revenue is None:
        return None
    out: dict = {}
    eps_quarters = facts.quarterly("eps", limit=1)
    rev_quarters = facts.quarterly("revenue", limit=1)

    if consensus_eps is not None and eps_quarters:
        actual = eps_quarters[-1].val
        out.update(
            actual_eps=actual,
            consensus_eps=consensus_eps,
            eps_surprise_pct=(actual - consensus_eps) / abs(consensus_eps) * 100 if consensus_eps else None,
            period=eps_quarters[-1].end.isoformat(),
        )
    if consensus_revenue is not None and rev_quarters:
        actual = rev_quarters[-1].val
        out.update(
            actual_revenue=actual,
            consensus_revenue=consensus_revenue,
            rev_surprise_pct=(actual - consensus_revenue) / abs(consensus_revenue) * 100 if consensus_revenue else None,
            period=rev_quarters[-1].end.isoformat(),
        )
    return out or None


def historical_per_median(ticker: str, facts: CompanyFacts, prices: PriceClient, years: int = 5) -> float | None:
    """분기말 주가 ÷ 그 시점 TTM EPS 의 중앙값. '과거 평균 대비 PER' 판단용."""
    eps_quarters = facts.quarterly("eps", limit=years * 4 + 4)
    if len(eps_quarters) < 8:
        return None
    if not prices.history(ticker):
        return None

    pers: list[float] = []
    for i in range(3, len(eps_quarters)):
        window = eps_quarters[i - 3 : i + 1]
        ttm_eps = sum(f.val for f in window)
        if ttm_eps <= 0:
            continue
        # 실적은 분기말 약 40일 뒤에 공개되므로 그 시점 주가와 맞춘다
        as_of = window[-1].filed or window[-1].end
        price = prices.close_on_or_before(ticker, as_of)
        if price:
            pers.append(price / ttm_eps)
    if len(pers) < 4:
        return None
    return statistics.median(pers[-years * 4 :])


def _peer_summary(m: Metrics) -> dict:
    return {
        "ticker": m.ticker,
        "per": m.per,
        "psr": m.psr,
        "roe": m.roe,
        "op_margin": m.op_margin,
        "revenue_growth": m.revenue_growth,
    }


def build_trends(facts: CompanyFacts, limit: int = 8) -> dict[str, list[tuple[date, float]]]:
    """분기별 추이. 현재값만으로는 '방향' 을 알 수 없어서 함께 계산한다."""
    out: dict[str, list[tuple[date, float]]] = {}

    revenue = facts.quarterly("revenue", limit=limit)
    if revenue:
        out["revenue"] = [(f.end, f.val) for f in revenue]

    net_income = facts.quarterly("net_income", limit=limit)
    if net_income:
        out["net_income"] = [(f.end, f.val) for f in net_income]

    operating = {f.end: f.val for f in facts.quarterly("operating_income", limit=limit)}
    margins = [
        (f.end, operating[f.end] / f.val * 100)
        for f in revenue
        if f.end in operating and f.val
    ]
    if margins:
        out["op_margin"] = margins

    return out


def collect_sources(facts: CompanyFacts) -> dict[str, str]:
    """지표마다 '어떤 항목을 어느 기간·어느 보고서에서 가져왔는지' 를 기록한다.

    화면에 그대로 표시해서, 숫자를 믿을 수 있는지 직접 확인할 수 있게 한다.
    """
    out: dict[str, str] = {}

    for key, label in (
        ("revenue", "매출"),
        ("net_income", "순이익"),
        ("operating_income", "영업이익"),
        ("ocf", "영업현금흐름"),
        ("capex", "설비투자"),
        ("eps", "EPS"),
    ):
        quarters = facts.quarterly(key, limit=4)
        if not quarters:
            continue
        last = quarters[-1]
        span = f"{quarters[0].start or quarters[0].end} ~ {last.end}" if len(quarters) > 1 else str(last.end)
        out[key] = f"{label}: {last.concept} · 4개 분기 합산({span}) · {last.form}"

    for key, label in (
        ("equity", "자기자본"),
        ("cash", "현금"),
        ("debt_lt", "장기부채"),
        ("debt_st", "단기부채"),
    ):
        fact = facts.latest_instant(key)
        if fact:
            out[key] = f"{label}: {fact.concept} · {fact.end} 시점 · {fact.form}"

    shares = facts.latest_instant("shares")
    if shares:
        out["shares"] = f"발행주식수: {shares.concept} · {shares.end} 시점 · {shares.form}"
    return out


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    v = abs(value)
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= size:
            return f"{sign}${v / size:,.2f}{unit}"
    return f"{sign}${v:,.0f}"
