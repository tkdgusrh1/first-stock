"""용어 사전.

화면에 나오는 모든 지표에 대해 '무엇인지 / 어떻게 계산하는지 / 어떻게 읽는지 /
무엇을 조심할지' 를 한 곳에 모았다. 대시보드에서 용어를 누르면 여기 설명이 뜬다.

숫자를 지어내지 않듯 설명도 지어내지 않는다. 계산식은 이 프로그램이 실제로
쓰는 식을 그대로 적었다 (metrics.py 와 일치해야 한다).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Term:
    key: str
    name: str
    short: str                       # 한 줄 설명 (툴팁)
    formula: str | None = None       # 이 프로그램이 쓰는 계산식
    how_to_read: str = ""            # 어떻게 해석하는지
    caution: str = ""                # 함정·주의점
    source: str = ""                 # 데이터 출처
    tags: tuple[str, ...] = field(default_factory=tuple)


TERMS: list[Term] = [
    # --- 메모의 우선순위 ---------------------------------------------------
    Term(
        key="guidance",
        name="가이던스",
        short="회사가 직접 발표하는 다음 분기·연간 실적 전망.",
        how_to_read=(
            "컨센서스(시장 예상치)보다 회사 내부 사정을 더 잘 반영하므로 가장 먼저 봅니다. "
            "숫자 자체보다 '이번에 올렸는가 내렸는가' 방향이 중요합니다."
        ),
        caution=(
            "회사가 관리할 수 있는 숫자입니다. 일부러 낮게 불러 나중에 초과 달성하거나, "
            "정의를 바꾸거나, 강조점을 옮기는 식입니다. 반드시 과거에 제시한 가이던스를 "
            "실제로 지켰는지 이력을 함께 확인하고, 현금흐름표와 대조하세요."
        ),
        source="8-K 항목 2.02 / 7.01 원문, 실적 발표 자료",
        tags=("우선순위",),
    ),
    Term(
        key="consensus",
        name="컨센서스",
        short="증권사 애널리스트들의 실적 예상치 평균.",
        how_to_read=(
            "미래에 대한 기댓값입니다. 컨센서스가 계속 올라간다는 건 회사가 잘하고 있다는 신호입니다. "
            "다만 기댓값이 높을수록 그 값을 못 넘겼을 때 하락 폭도 큽니다."
        ),
        caution="무료로 받을 수 있는 공개 API가 없어 직접 입력해야 합니다.",
        source="직접 입력 (증권사 리포트·포털 참고)",
        tags=("우선순위",),
    ),
    Term(
        key="surprise",
        name="어닝 서프라이즈",
        short="실제 발표 실적이 컨센서스와 얼마나 차이 났는지.",
        formula="(실제 EPS − 컨센서스 EPS) ÷ |컨센서스 EPS| × 100",
        how_to_read="플러스면 예상보다 잘한 것, 마이너스면 못한 것입니다.",
        caution=(
            "EPS는 자사주 매입으로도 올릴 수 있습니다. 매출 서프라이즈와 함께 보세요. "
            "서프라이즈가 나도 가이던스를 낮추면 주가는 내릴 수 있습니다."
        ),
        source="실제치는 SEC XBRL, 컨센서스는 직접 입력",
        tags=("우선순위",),
    ),
    # --- 수익성·효율 -------------------------------------------------------
    Term(
        key="revenue",
        name="매출 (TTM)",
        short="최근 4개 분기를 합친 1년치 매출.",
        formula="최근 4개 분기 매출의 합",
        how_to_read=(
            "TTM(Trailing Twelve Months)은 '최근 12개월'이라는 뜻입니다. "
            "분기 하나만 보면 계절성에 속기 쉬워서 4개 분기를 합쳐 봅니다."
        ),
        caution="10-K만 있는 4분기는 '연간 − 앞선 3개 분기'로 역산합니다.",
        source="SEC XBRL (Revenues 등)",
        tags=("수익성",),
    ),
    Term(
        key="revenue_growth",
        name="매출 성장률",
        short="1년 전 같은 기간 대비 매출이 얼마나 늘었는지.",
        formula="(최근 TTM 매출 − 직전 TTM 매출) ÷ |직전 TTM 매출| × 100",
        how_to_read="적자 기업이라면 이 항목이 가장 중요합니다. 30% 이상을 기준으로 봅니다.",
        caution="인수합병으로 늘어난 매출은 자체 성장이 아닙니다. 공시 원문에서 확인하세요.",
        source="SEC XBRL",
        tags=("수익성", "성장"),
    ),
    Term(
        key="op_margin",
        name="영업이익률",
        short="매출 100원을 팔아 본업에서 얼마를 남기는지.",
        formula="영업이익(TTM) ÷ 매출(TTM) × 100",
        how_to_read=(
            "절대값보다 '방향'이 중요합니다. 오르고 있으면 가격 결정력이나 비용 통제가 "
            "좋아지고 있다는 뜻입니다."
        ),
        caution="일회성 비용(구조조정·소송)이 섞이면 한 분기만 튈 수 있습니다.",
        source="SEC XBRL (OperatingIncomeLoss)",
        tags=("수익성",),
    ),
    Term(
        key="roe",
        name="ROE (자기자본이익률)",
        short="주주 돈으로 얼마나 벌었는지.",
        formula="순이익(TTM) ÷ 자기자본 × 100",
        how_to_read="15% 이상을 꾸준히 유지하면 효율이 좋은 기업으로 봅니다.",
        caution=(
            "빚을 많이 쓰면 자기자본이 작아져 ROE가 커 보입니다. "
            "그래서 부채까지 포함한 ROIC로 함께 판단해야 합니다."
        ),
        source="SEC XBRL (NetIncomeLoss ÷ StockholdersEquity)",
        tags=("효율",),
    ),
    Term(
        key="roic",
        name="ROIC (투하자본이익률)",
        short="빚까지 포함해 실제로 굴린 돈 대비 수익률.",
        formula="세후영업이익 ÷ (자기자본 + 총부채 − 현금)",
        how_to_read=(
            "ROE보다 정확한 효율 지표입니다. 빌린 돈으로 부풀린 수익을 걸러냅니다. "
            "이 프로그램은 ROIC를 우선 기준으로 판정합니다."
        ),
        caution="세후영업이익은 실효세율로 계산하며, 세율을 구할 수 없으면 21%를 씁니다.",
        source="SEC XBRL",
        tags=("효율",),
    ),
    # --- 현금 -------------------------------------------------------------
    Term(
        key="ocf",
        name="영업현금흐름",
        short="본업으로 실제 들어온 현금.",
        how_to_read=(
            "순이익보다 커야 건강합니다. 이익은 회계상 숫자지만 현금은 실제로 통장에 "
            "들어온 돈이라 조작이 훨씬 어렵습니다."
        ),
        caution=(
            "순이익보다 작으면 매출채권(못 받은 돈)이나 재고가 쌓이고 있을 수 있습니다. "
            "'이익은 나는데 현금이 없는' 위험 신호입니다."
        ),
        source="SEC XBRL (NetCashProvidedByUsedInOperatingActivities)",
        tags=("현금",),
    ),
    Term(
        key="fcf",
        name="잉여현금흐름 (FCF)",
        short="영업으로 번 현금에서 설비투자를 뺀, 진짜 남는 돈.",
        formula="영업현금흐름 − 설비투자(CapEx)",
        how_to_read="배당·자사주·빚 상환에 쓸 수 있는 여유 자금입니다.",
        caution="성장기 기업은 투자 때문에 마이너스인 게 정상일 수 있습니다.",
        source="SEC XBRL",
        tags=("현금",),
    ),
    Term(
        key="runway",
        name="현금 런웨이",
        short="지금 속도로 돈을 태우면 보유 현금으로 몇 년을 버티는지.",
        formula="(현금 + 단기투자) ÷ 연간 현금 소진액",
        how_to_read="적자 기업의 생존 지표입니다. 2년 미만이면 위험 구간으로 봅니다.",
        caution=(
            "2년 미만이면 증자(주식을 새로 찍어 파는 것) 가능성이 큽니다. "
            "증자하면 기존 주주 지분이 희석돼 주가에 불리합니다."
        ),
        source="SEC XBRL",
        tags=("현금", "안정성"),
    ),
    # --- 밸류에이션 --------------------------------------------------------
    Term(
        key="per",
        name="PER (주가수익비율)",
        short="주가가 순이익의 몇 배인지.",
        formula="주가 ÷ 주당순이익(EPS, TTM)",
        how_to_read=(
            "절대값만으로는 의미가 없습니다. ① 이 회사의 과거 평균과 비교, "
            "② 같은 업종 회사들과 비교 — 이 두 가지로 봐야 합니다."
        ),
        caution="적자면 계산되지 않습니다. 이익이 일시적으로 튀면 PER이 왜곡됩니다.",
        source="주가는 시세 제공처, EPS는 SEC XBRL",
        tags=("밸류에이션",),
    ),
    Term(
        key="psr",
        name="PSR (주가매출비율)",
        short="시가총액이 연 매출의 몇 배인지.",
        formula="시가총액 ÷ 매출(TTM)",
        how_to_read="적자라 PER을 못 쓰는 성장 기업의 밸류에이션 지표입니다.",
        caution="업종마다 정상 범위가 크게 달라 반드시 동종업계와 비교해야 합니다.",
        source="시가총액 = 주가 × 발행주식수",
        tags=("밸류에이션",),
    ),
    Term(
        key="market_cap",
        name="시가총액",
        short="회사 전체의 시장 가격.",
        formula="주가 × 발행주식수",
        how_to_read="'이 회사를 통째로 사려면 얼마인가'를 뜻합니다.",
        source="주가는 시세 제공처, 주식수는 SEC 공시",
        tags=("밸류에이션",),
    ),
    # --- 공시 -------------------------------------------------------------
    Term(
        key="8k",
        name="8-K (수시공시)",
        short="주주가 알아야 할 중요한 일이 생겼을 때 즉시 내는 보고서.",
        how_to_read=(
            "항목 번호로 내용이 정해져 있습니다. 2.02는 실적 발표, 7.01은 Reg FD 공개, "
            "5.02는 임원 변경, 4.02는 과거 재무제표를 믿을 수 없다는 뜻(매우 나쁨)입니다."
        ),
        source="SEC EDGAR",
        tags=("공시",),
    ),
    Term(
        key="form4",
        name="Form 4 (내부자 거래)",
        short="임원·이사·10% 이상 주주가 주식을 사고팔면 이틀 안에 내는 보고서.",
        how_to_read=(
            "코드 P는 공개시장 매수(자기 돈으로 산 것, 긍정 신호), S는 공개시장 매도입니다. "
            "A는 보상으로 받은 것, F는 세금 납부용 반납, M은 옵션 행사라 매매 의사와는 다릅니다."
        ),
        caution=(
            "매도는 미리 짜둔 계획(10b5-1)일 수 있어 그 자체로 악재는 아닙니다. "
            "원문에서 사전계획 여부를 확인하세요."
        ),
        source="SEC EDGAR Form 4 원문",
        tags=("공시",),
    ),
    Term(
        key="10q",
        name="10-Q (분기보고서)",
        short="분기마다 내는 정식 재무보고서.",
        how_to_read=(
            "재무제표와 함께 경영진이 직접 쓴 설명(MD&A)이 들어 있습니다. "
            "숫자가 왜 그렇게 나왔는지, 앞으로 무엇을 계획하는지가 여기 담깁니다."
        ),
        source="SEC EDGAR",
        tags=("공시",),
    ),
    Term(
        key="10k",
        name="10-K (연간보고서)",
        short="1년에 한 번 내는 가장 상세한 보고서.",
        how_to_read="사업 구조, 위험 요인, 연간 실적 분석이 모두 들어 있습니다.",
        source="SEC EDGAR",
        tags=("공시",),
    ),
    Term(
        key="mdna",
        name="MD&A (경영진 논의)",
        short="경영진이 직접 쓴 실적 설명과 향후 계획.",
        how_to_read=(
            "숫자 뒤의 맥락이 담긴 부분입니다. 매출이 왜 늘었는지, 비용이 왜 증가했는지, "
            "앞으로 무엇에 투자할 것인지가 회사 본인의 말로 적혀 있습니다."
        ),
        caution="회사가 쓴 글이라 유리한 쪽으로 서술될 수 있습니다. 숫자와 대조하며 읽으세요.",
        source="10-Q Item 2 / 10-K Item 7",
        tags=("공시",),
    ),
    Term(
        key="sc13d",
        name="SC 13D / 13G (대량보유)",
        short="지분 5% 이상을 확보한 투자자가 내는 보고서.",
        how_to_read=(
            "13D는 경영 참여 목적(행동주의 투자자일 수 있음), "
            "13G는 단순 투자 목적입니다."
        ),
        source="SEC EDGAR",
        tags=("공시",),
    ),
    Term(
        key="s3",
        name="S-3 / 424B (증자·유상증자)",
        short="주식이나 채권을 새로 발행해 자금을 조달하겠다는 신고서.",
        how_to_read="적자 기업이 현금이 부족할 때 냅니다.",
        caution="주식 수가 늘면 기존 주주 지분이 희석됩니다. 런웨이가 짧은 기업에서 특히 주의.",
        source="SEC EDGAR",
        tags=("공시", "위험"),
    ),
    Term(
        key="cik",
        name="CIK",
        short="SEC가 회사마다 붙인 고유 번호.",
        how_to_read="티커는 바뀔 수 있지만 CIK는 바뀌지 않아, 공시를 찾을 때 기준이 됩니다.",
        source="SEC EDGAR",
        tags=("기타",),
    ),
    Term(
        key="ttm",
        name="TTM",
        short="Trailing Twelve Months, 최근 12개월.",
        how_to_read="최근 4개 분기를 합친 값입니다. 계절성을 지우고 현재 실력을 보기 위한 방식입니다.",
        tags=("기타",),
    ),
]

BY_KEY: dict[str, Term] = {t.key: t for t in TERMS}

# 화면의 항목 이름 → 사전 항목. 표 머리글과 카드 라벨에 물음표를 붙이는 데 쓴다.
LABEL_TO_KEY: dict[str, str] = {
    "매출(TTM)": "revenue",
    "매출 TTM": "revenue",
    "매출성장": "revenue_growth",
    "매출 성장": "revenue_growth",
    "영업이익률": "op_margin",
    "ROE": "roe",
    "ROIC": "roic",
    "PER": "per",
    "PSR": "psr",
    "시총": "market_cap",
    "시가총액": "market_cap",
    "런웨이": "runway",
    "현금 런웨이": "runway",
    "영업현금흐름": "ocf",
    "잉여현금흐름": "fcf",
}


def lookup(label: str) -> Term | None:
    key = LABEL_TO_KEY.get(label.strip())
    return BY_KEY.get(key) if key else None


def groups() -> dict[str, list[Term]]:
    """태그별로 묶어서 사전 화면에 보여준다."""
    order = ["우선순위", "수익성", "효율", "현금", "밸류에이션", "공시", "기타"]
    out: dict[str, list[Term]] = {name: [] for name in order}
    for term in TERMS:
        tag = next((t for t in term.tags if t in out), "기타")
        out[tag].append(term)
    return {k: v for k, v in out.items() if v}
