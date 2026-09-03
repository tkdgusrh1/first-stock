"""'이 종목은 지금 어떤 상황인가' 를 규칙으로 판단한다.

지어낸 말을 넣지 않는다. 모든 문장은 계산된 숫자에서 나오고, 각 항목마다
어떤 값으로 그렇게 판단했는지 근거를 함께 남긴다. 숫자가 없으면
'판단 불가' 로 두고 왜 없는지 밝힌다.

판정은 다섯 축으로 나눈다.
  성장 · 수익성 · 재무 안정성 · 현금 창출력 · 밸류에이션
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import LOSS_GROWTH_TARGET, MIN_RUNWAY_YEARS, ROE_TARGET, Metrics, _money, _pct

GOOD, FAIR, POOR, UNKNOWN = "good", "fair", "poor", "unknown"

LEVEL_LABEL = {GOOD: "양호", FAIR: "보통", POOR: "주의", UNKNOWN: "판단 불가"}
LEVEL_ICON = {GOOD: "🟢", FAIR: "🟡", POOR: "🔴", UNKNOWN: "⚪"}
_SCORE = {GOOD: 2, FAIR: 1, POOR: 0}


@dataclass
class Axis:
    key: str
    name: str
    level: str
    headline: str                       # 한 줄 결론
    evidence: list[str] = field(default_factory=list)   # 근거가 된 숫자들


@dataclass
class Assessment:
    ticker: str
    level: str
    headline: str
    axes: list[Axis] = field(default_factory=list)
    watch_points: list[str] = field(default_factory=list)   # 지금 지켜볼 것
    unknowns: list[str] = field(default_factory=list)       # 확인 못 한 것

    @property
    def icon(self) -> str:
        return LEVEL_ICON[self.level]

    @property
    def label(self) -> str:
        return LEVEL_LABEL[self.level]


# --------------------------------------------------------------------------
def assess(m: Metrics) -> Assessment:
    if m.is_fund and m.fund is not None:
        return assess_fund(m)

    axes = [
        _growth(m),
        _profitability(m),
        _stability(m),
        _cash(m),
        _valuation(m),
    ]
    known = [a for a in axes if a.level != UNKNOWN]

    if not known:
        return Assessment(
            ticker=m.ticker,
            level=UNKNOWN,
            headline="재무 데이터를 가져오지 못해 판단할 수 없습니다.",
            axes=axes,
            unknowns=[a.name for a in axes],
        )

    average = sum(_SCORE[a.level] for a in known) / len(known)
    poor_count = sum(1 for a in known if a.level == POOR)

    if average >= 1.6 and poor_count == 0:
        level = GOOD
    elif average >= 1.0 and poor_count <= 1:
        level = FAIR
    else:
        level = POOR

    return Assessment(
        ticker=m.ticker,
        level=level,
        headline=_headline(m, axes, level),
        axes=axes,
        watch_points=_watch_points(m, axes),
        unknowns=[a.name for a in axes if a.level == UNKNOWN],
    )


# --------------------------------------------------------------------------
# ETF·펀드는 회사가 아니다. 매출·ROE 가 없으니 다른 기준으로 본다.
# --------------------------------------------------------------------------
def assess_fund(m: Metrics) -> Assessment:
    info = m.fund
    axes: list[Axis] = []

    what = [f"성격: {info.risk_label}"]
    if info.name:
        what.append(f"정식 명칭: {info.name}")
    if info.sic_label:
        what.append(f"SEC 분류: {info.sic_label}")
    axes.append(
        Axis("what", "무엇을 담는가", FAIR if info.kind else UNKNOWN,
             info.kind or "상품명만으로는 무엇을 담는지 확정할 수 없습니다.", what)
    )

    # 자세한 경고 문장은 ETF 화면에 한 번만 쓴다. 여기엔 사실만 짧게.
    facts: list[str] = []
    if info.leverage:
        facts.append(f"배수 {info.leverage:g}배")
    if info.inverse:
        facts.append("방향 반대(인버스)")
    if info.daily_reset:
        facts.append("매일 되맞춤")
    if info.single_stock:
        facts.append(f"기초자산 {info.underlying or '단일 종목'}")

    if info.leverage or info.inverse:
        level, headline = POOR, "배수·인버스 상품입니다. 하루 단위로 되맞추므로 오래 들고 있을수록 불리합니다."
    else:
        level, headline = GOOD, "배수를 쓰지 않는 일반 ETF 입니다."
        facts = facts or ["이름에 배수·인버스 표현이 없습니다."]
    axes.append(Axis("structure", "구조 위험", level, headline, facts))

    price_evidence = []
    if m.price:
        price_evidence.append(f"현재가 ${m.price:,.2f}")
    if m.price_change_pct is not None:
        price_evidence.append(f"전일 대비 {m.price_change_pct:+.2f}%")
    if m.extended_price:
        price_evidence.append(f"{m.extended_label} ${m.extended_price:,.2f}")
    axes.append(
        Axis("price", "가격", FAIR if m.price else UNKNOWN,
             "시세는 확인됩니다." if m.price else "시세를 받지 못했습니다.", price_evidence)
    )

    watch = [
        "보수(운용비용)와 실제 추종 오차는 투자설명서(497·485BPOS)에서 확인하세요.",
        "ETF 는 회사가 아니라서 매출·ROE·현금흐름 기준을 적용할 수 없습니다.",
    ]
    if info.high_risk:
        watch.insert(0, "며칠 이상 보유할 계획이라면 기초자산 ETF 를 함께 비교해보세요.")

    return Assessment(
        ticker=m.ticker,
        level=POOR if info.high_risk else FAIR,
        headline=(
            f"{info.risk_label} — " +
            ("구조상 단기 매매용 상품입니다." if info.high_risk else "일반적인 ETF 입니다.")
        ),
        axes=axes,
        watch_points=watch,
        unknowns=[a.name for a in axes if a.level == UNKNOWN],
    )


def _headline(m: Metrics, axes: list[Axis], level: str) -> str:
    state = "흑자" if m.profitable else ("적자" if m.profitable is False else "손익 미확인")
    strong = [a.name for a in axes if a.level == GOOD]
    weak = [a.name for a in axes if a.level == POOR]

    parts = [f"{state} 기업"]
    if strong:
        parts.append(f"{'·'.join(strong)}은(는) 양호")
    if weak:
        parts.append(f"{'·'.join(weak)}은(는) 주의")
    if not strong and not weak:
        parts.append("특별히 두드러지거나 나쁜 축 없음")
    return ", ".join(parts) + "."


def _watch_points(m: Metrics, axes: list[Axis]) -> list[str]:
    """지금 무엇을 지켜봐야 하는지. 메모의 우선순위를 따른다."""
    points: list[str] = []

    if m.profitable is False:
        if m.runway_years is not None and m.runway_years < 3:
            points.append(
                f"현금 런웨이 {m.runway_years:.1f}년 — 증자(주식 추가 발행) 가능성. "
                "S-3·424B 공시가 뜨는지 확인하세요."
            )
        if m.revenue_growth is not None and m.revenue_growth < LOSS_GROWTH_TARGET:
            points.append(
                f"매출 성장률 {m.revenue_growth:+.0%} — 적자 기업의 최소 기준(+30%)에 못 미칩니다. "
                "다음 분기 매출이 회복되는지가 핵심입니다."
            )
    else:
        if m.op_margin is not None and m.op_margin_prior is not None and m.op_margin < m.op_margin_prior:
            points.append(
                f"영업이익률이 {_pct(m.op_margin_prior)} → {_pct(m.op_margin)} 로 하락. "
                "일회성 비용인지 구조적 악화인지 MD&A에서 확인하세요."
            )
        if m.ocf_ttm is not None and m.net_income_ttm and m.ocf_ttm < m.net_income_ttm:
            points.append(
                "영업현금흐름이 순이익보다 적습니다. 매출채권·재고가 쌓이고 있는지 확인하세요."
            )

    if m.share_growth_1y is not None and m.share_growth_1y >= 0.15:
        points.append(
            f"발행주식수가 1년새 {m.share_growth_1y:+.0%} 늘었습니다(희석). "
            "주가가 그대로여도 내 몫은 줄어듭니다. 증자 공시(S-3·424B)를 확인하세요."
        )

    if m.surprise is None:
        points.append(
            "컨센서스가 없어 어닝 서프라이즈(메모 2순위)를 계산할 수 없습니다. "
            "화면에서 예상치를 입력해두면 실적 발표 직후 자동 비교합니다."
        )

    # 가이던스는 실제로 찾았는지에 따라 할 말이 다르다.
    guidance_check = m.priority[0] if m.priority else None
    if guidance_check is not None and guidance_check.status != "na":
        points.append(
            "가이던스(메모 1순위)는 아래 카드에 원문과 과거 이행 이력이 있습니다. "
            "회사 말과 현금흐름표가 같은 방향인지 대조하세요."
        )
    else:
        points.append(
            "가이던스(메모 1순위)를 아직 찾지 못했습니다. 실적 발표(8-K 2.02) 원문에서 "
            "다음 분기 전망과 과거 이행 이력을 직접 확인하세요."
        )
    return points


# --------------------------------------------------------------------------
# 축별 판정
# --------------------------------------------------------------------------
def _growth(m: Metrics) -> Axis:
    if m.revenue_growth is None:
        return Axis("growth", "성장", UNKNOWN, "전년 대비 매출을 비교할 데이터가 부족합니다.",
                    ["최근 8개 분기 매출이 있어야 계산할 수 있습니다."])

    growth = m.revenue_growth
    evidence = [
        f"TTM 매출 {_money(m.revenue_ttm)} (직전 1년 {_money(m.revenue_ttm_prior)})",
        f"성장률 {growth:+.1%}",
    ]

    quarters = m.quarterly_revenue[-4:]
    if len(quarters) >= 2:
        trend = " → ".join(_money(v) for _, v in quarters)
        evidence.append(f"최근 분기 흐름 {trend}")

    if m.profitable is False:
        # 적자 기업은 성장률이 생존 조건이다
        if growth >= LOSS_GROWTH_TARGET:
            return Axis("growth", "성장", GOOD,
                        f"매출이 {growth:+.0%} 늘고 있습니다. 적자 기업 기준(+30%)을 넘습니다.", evidence)
        if growth > 0:
            return Axis("growth", "성장", FAIR,
                        f"매출은 {growth:+.0%} 늘었지만 적자 기업 기준(+30%)에는 못 미칩니다.", evidence)
        return Axis("growth", "성장", POOR,
                    f"매출이 {growth:+.0%} 로 줄고 있습니다. 적자 기업에서는 가장 나쁜 신호입니다.", evidence)

    if growth >= 0.15:
        return Axis("growth", "성장", GOOD, f"매출이 {growth:+.0%} 성장 중입니다.", evidence)
    if growth >= 0:
        return Axis("growth", "성장", FAIR, f"매출 성장이 {growth:+.0%} 로 완만합니다.", evidence)
    return Axis("growth", "성장", POOR, f"매출이 {growth:+.0%} 로 역성장했습니다.", evidence)


def _profitability(m: Metrics) -> Axis:
    evidence: list[str] = []
    if m.net_income_ttm is not None:
        evidence.append(f"TTM 순이익 {_money(m.net_income_ttm)}")
    if m.operating_income_ttm is not None:
        evidence.append(f"TTM 영업이익 {_money(m.operating_income_ttm)}")
    if m.op_margin is not None:
        line = f"영업이익률 {_pct(m.op_margin)}"
        if m.op_margin_prior is not None:
            delta = (m.op_margin - m.op_margin_prior) * 100
            line += f" (전년 동기 {_pct(m.op_margin_prior)}, {delta:+.1f}%p)"
        evidence.append(line)
    from .trust import doubts as _doubts

    unreliable = _doubts(m)
    if m.roe is not None:
        mark = " ← 판단에서 뺌(참고)" if "roe" in unreliable else ""
        evidence.append(f"ROE {_pct(m.roe)}{mark}")
    if m.roic is not None:
        mark = " ← 판단에서 뺌(참고)" if "roic" in unreliable else ""
        evidence.append(f"ROIC {_pct(m.roic)} (기준 {ROE_TARGET:.0%}){mark}")

    if m.profitable is None:
        return Axis("profit", "수익성", UNKNOWN, "손익 데이터를 가져오지 못했습니다.", evidence)

    improving = (
        m.op_margin is not None
        and m.op_margin_prior is not None
        and m.op_margin > m.op_margin_prior
    )

    if m.profitable is False:
        narrowing = (
            m.net_income_ttm is not None
            and m.net_income_ttm_prior is not None
            and m.net_income_ttm > m.net_income_ttm_prior
        )
        if narrowing and improving:
            return Axis("profit", "수익성", FAIR,
                        "아직 적자지만 손실 폭과 마진이 함께 개선되고 있습니다.", evidence)
        if narrowing:
            return Axis("profit", "수익성", FAIR, "아직 적자지만 손실 폭은 줄고 있습니다.", evidence)
        if m.revenue_growth and m.revenue_growth > 0:
            return Axis("profit", "수익성", POOR,
                        "매출이 느는데 적자도 함께 커지고 있습니다. 메모 기준 가장 나쁜 조합입니다.", evidence)
        return Axis("profit", "수익성", POOR, "적자가 확대되고 있습니다.", evidence)

    # 자본 효율은 분모(자기자본·투하자본)가 무너지면 사업 성과와 무관하게
    # 치솟는다. 자사주를 오래 사들인 회사가 대표적이다. 그런 값으로 '양호' 를
    # 주면 **맞는 숫자로 틀린 판단**을 하게 되므로, 여기서는 쓰지 않고
    # 마진 방향으로만 본다. 값 자체는 근거 목록에 참고로 남는다.
    from .trust import doubts as _doubts

    shaky = _doubts(m)
    judge = None
    for name, value in (("roic", m.roic), ("roe", m.roe)):
        if value is not None and name not in shaky:
            judge = value
            break

    if judge is None and (m.roic is not None or m.roe is not None):
        headline = ("흑자지만 자본 효율 수치를 그대로 믿기 어려워"
                    " 마진 방향으로만 봤습니다. (아래 참고)")
        if improving:
            return Axis("profit", "수익성", FAIR,
                        f"{headline} 영업이익률은 개선 중입니다.", evidence)
        return Axis("profit", "수익성", FAIR, headline, evidence)
    if judge is None:
        return Axis("profit", "수익성", FAIR, "흑자지만 자본 효율을 계산할 데이터가 부족합니다.", evidence)
    if judge >= ROE_TARGET and improving:
        return Axis("profit", "수익성", GOOD,
                    f"자본 효율 {_pct(judge)}로 기준(15%)을 넘고, 마진도 개선 중입니다.", evidence)
    if judge >= ROE_TARGET:
        return Axis("profit", "수익성", GOOD, f"자본 효율 {_pct(judge)}로 기준(15%)을 넘습니다.", evidence)
    if judge >= ROE_TARGET * 0.7:
        return Axis("profit", "수익성", FAIR, f"자본 효율 {_pct(judge)}로 기준(15%)에 조금 못 미칩니다.", evidence)
    return Axis("profit", "수익성", POOR, f"자본 효율 {_pct(judge)}로 기준(15%)에 크게 못 미칩니다.", evidence)


def _stability(m: Metrics) -> Axis:
    evidence: list[str] = []
    if m.cash is not None:
        evidence.append(f"보유 현금 {_money(m.cash)}")
    if m.total_debt is not None:
        evidence.append(f"총부채 {_money(m.total_debt)}")
    if m.equity is not None:
        evidence.append(f"자기자본 {_money(m.equity)}")

    net_cash = None
    if m.cash is not None and m.total_debt is not None:
        net_cash = m.cash - m.total_debt
        evidence.append(f"순현금 {_money(net_cash)} (현금 − 총부채)")

    # 희석: 돈이 모자라면 회사는 주식을 더 찍는다. 그 흔적이 주식 수에 남는다.
    if m.share_growth_1y is not None:
        note = f"발행주식수 1년 변화 {m.share_growth_1y:+.1%}"
        if m.share_growth_1y >= 0.15:
            note += " — 주주 몫이 크게 희석됐습니다"
        elif m.share_growth_1y <= -0.02:
            note += " — 자사주 소각 등으로 줄었습니다"
        evidence.append(note)

    if m.runway_years is not None:
        evidence.append(f"현금 런웨이 {m.runway_years:.1f}년 (기준 {MIN_RUNWAY_YEARS:.0f}년)")
        if m.runway_years < MIN_RUNWAY_YEARS:
            return Axis("stability", "재무 안정성", POOR,
                        f"현금으로 {m.runway_years:.1f}년밖에 버티지 못합니다. "
                        "메모 기준(2년)에 미달이며 증자 위험이 큽니다.", evidence)
        if m.runway_years < 3:
            return Axis("stability", "재무 안정성", FAIR,
                        f"현금 런웨이 {m.runway_years:.1f}년으로 기준은 넘지만 여유가 크지 않습니다.", evidence)
        return Axis("stability", "재무 안정성", GOOD,
                    f"현금 런웨이 {m.runway_years:.1f}년으로 자금 여력이 넉넉합니다.", evidence)

    if net_cash is None:
        return Axis("stability", "재무 안정성", UNKNOWN, "현금·부채 데이터를 가져오지 못했습니다.", evidence)

    if net_cash > 0:
        return Axis("stability", "재무 안정성", GOOD, "부채보다 현금이 많습니다(순현금).", evidence)

    # 빚이 많아도 벌어서 갚을 수 있으면 위험이 아니다.
    # 자사주 매입을 많이 한 우량기업은 부채 > 자기자본인 경우가 흔하므로,
    # 자본 대비가 아니라 '순부채를 영업이익 몇 년치로 갚는가' 로 본다.
    net_debt = -net_cash
    if m.operating_income_ttm and m.operating_income_ttm > 0:
        years = net_debt / m.operating_income_ttm
        evidence.append(f"순부채 ÷ 연간 영업이익 = {years:.1f}년치")
        if years <= 1:
            return Axis("stability", "재무 안정성", GOOD,
                        f"순부채가 영업이익 {years:.1f}년치로 부담이 적습니다.", evidence)
        if years <= 3:
            return Axis("stability", "재무 안정성", FAIR,
                        f"순부채가 영업이익 {years:.1f}년치입니다. 감당 가능한 수준입니다.", evidence)
        return Axis("stability", "재무 안정성", POOR,
                    f"순부채가 영업이익 {years:.1f}년치로 부담이 큽니다.", evidence)

    if m.equity and m.total_debt and m.total_debt <= m.equity:
        return Axis("stability", "재무 안정성", FAIR,
                    "부채가 자기자본을 넘지는 않지만, 영업이익으로 갚을 여력은 확인되지 않습니다.", evidence)
    return Axis("stability", "재무 안정성", POOR,
                "부채가 자기자본보다 많고, 영업이익으로 갚을 여력도 확인되지 않습니다.", evidence)


def _cash(m: Metrics) -> Axis:
    evidence: list[str] = []
    if m.ocf_ttm is not None:
        evidence.append(f"영업현금흐름 {_money(m.ocf_ttm)}")
    if m.fcf_ttm is not None:
        evidence.append(f"잉여현금흐름 {_money(m.fcf_ttm)}")
    if m.net_income_ttm is not None:
        evidence.append(f"순이익 {_money(m.net_income_ttm)}")

    if m.ocf_ttm is None:
        return Axis("cash", "현금 창출력", UNKNOWN, "현금흐름 데이터를 가져오지 못했습니다.", evidence)

    if m.ocf_ttm <= 0:
        return Axis("cash", "현금 창출력", POOR,
                    f"본업에서 현금이 빠져나가고 있습니다 ({_money(m.ocf_ttm)}).", evidence)

    if m.net_income_ttm is not None and m.net_income_ttm > 0:
        ratio = m.ocf_ttm / m.net_income_ttm
        evidence.append(f"영업현금흐름 ÷ 순이익 = {ratio:.2f}배")
        if ratio >= 1:
            return Axis("cash", "현금 창출력", GOOD,
                        f"영업현금흐름이 순이익의 {ratio:.2f}배입니다. 이익이 현금으로 잘 들어옵니다.", evidence)
        return Axis("cash", "현금 창출력", POOR,
                    f"영업현금흐름이 순이익의 {ratio:.2f}배에 그칩니다. "
                    "이익은 나는데 현금이 안 들어오는 상태입니다.", evidence)

    if m.fcf_ttm is not None and m.fcf_ttm > 0:
        return Axis("cash", "현금 창출력", GOOD, "적자 상태지만 잉여현금흐름은 플러스입니다.", evidence)
    return Axis("cash", "현금 창출력", FAIR, "영업현금흐름은 플러스입니다.", evidence)


def _valuation(m: Metrics) -> Axis:
    evidence: list[str] = []
    if m.price:
        evidence.append(f"주가 ${m.price:,.2f}")
    if m.market_cap:
        evidence.append(f"시가총액 {_money(m.market_cap)}")

    if m.per:
        evidence.append(f"PER {m.per:.1f}배")
        if m.per_median_5y:
            premium = (m.per / m.per_median_5y - 1) * 100
            evidence.append(f"과거 5년 PER 중앙값 {m.per_median_5y:.1f}배 ({premium:+.0f}%)")
            if premium <= -10:
                return Axis("valuation", "밸류에이션", GOOD,
                            f"PER {m.per:.1f}배로 과거 평균보다 {abs(premium):.0f}% 낮습니다.", evidence)
            if premium <= 30:
                return Axis("valuation", "밸류에이션", FAIR,
                            f"PER {m.per:.1f}배로 과거 평균과 비슷한 수준입니다.", evidence)
            return Axis("valuation", "밸류에이션", POOR,
                        f"PER {m.per:.1f}배로 과거 평균보다 {premium:.0f}% 높습니다.", evidence)
        return Axis("valuation", "밸류에이션", FAIR,
                    f"PER {m.per:.1f}배입니다. 과거 평균과 비교할 주가 이력이 부족합니다.", evidence)

    if m.psr:
        evidence.append(f"PSR {m.psr:.1f}배")
        peers = [p["psr"] for p in m.peers.values() if p.get("psr")]
        if peers:
            import statistics

            median = statistics.median(peers)
            evidence.append(f"동종업계 PSR 중앙값 {median:.1f}배")
            if m.psr <= median:
                return Axis("valuation", "밸류에이션", GOOD,
                            f"PSR {m.psr:.1f}배로 동종업계보다 낮습니다.", evidence)
            return Axis("valuation", "밸류에이션", POOR,
                        f"PSR {m.psr:.1f}배로 동종업계보다 높습니다.", evidence)
        return Axis("valuation", "밸류에이션", FAIR,
                    f"PSR {m.psr:.1f}배입니다. 비교할 동종업계 종목을 지정하면 판단이 정확해집니다.", evidence)

    reason = "적자라 PER을 쓸 수 없고" if (m.eps_ttm or 0) <= 0 else "이익 데이터가 없고"
    if not m.price:
        return Axis("valuation", "밸류에이션", UNKNOWN,
                    "주가를 가져오지 못해 밸류에이션을 계산할 수 없습니다.",
                    evidence + ["시세 제공처에서 이 티커를 찾지 못했습니다."])
    return Axis("valuation", "밸류에이션", UNKNOWN,
                f"{reason} 시가총액도 없어 PSR을 계산할 수 없습니다.", evidence)
