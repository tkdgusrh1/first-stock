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
    Term("guidance", "가이던스", "회사가 직접 내놓는 다음 분기 전망.",
         how_to_read="숫자보다 방향. 올렸나 내렸나가 핵심.",
         caution="회사가 조절할 수 있다. 과거에 지켰는지 이력을 함께 볼 것.",
         source="8-K 2.02 / 7.01 원문", tags=("우선순위",)),
    Term("consensus", "컨센서스", "증권사들의 실적 예상치 평균.",
         how_to_read="계속 오르면 좋은 신호. 다만 기대가 높을수록 못 미쳤을 때 낙폭도 크다.",
         caution="무료 API가 없어 직접 넣어야 할 때가 많다.",
         source="Yahoo·Nasdaq 등", tags=("우선순위",)),
    Term("surprise", "어닝 서프라이즈", "실제 실적이 예상치를 얼마나 넘었나.",
         formula="(실제 − 예상) ÷ |예상| × 100",
         how_to_read="+면 예상 초과, −면 미달.",
         caution="EPS는 자사주 매입으로도 오른다. 매출과 같이 볼 것.",
         source="실제=SEC XBRL", tags=("우선순위",)),

    # --- 수익성 -----------------------------------------------------------
    Term("revenue", "매출 (TTM)", "최근 4개 분기를 합친 1년치 매출.",
         formula="최근 4개 분기 합",
         how_to_read="분기 하나만 보면 계절성에 속는다.",
         source="SEC XBRL", tags=("수익성",)),
    Term("revenue_growth", "매출 성장률", "1년 전 같은 기간 대비 증가율.",
         formula="(이번 TTM − 직전 TTM) ÷ |직전 TTM| × 100",
         how_to_read="적자 기업은 30% 이상이 기준선.",
         caution="인수합병으로 늘어난 건 자체 성장이 아니다.",
         source="SEC XBRL", tags=("수익성",)),
    Term("op_margin", "영업이익률", "매출 100원당 본업에서 남기는 돈.",
         formula="영업이익 ÷ 매출 × 100",
         how_to_read="절대값보다 방향. 오르면 가격 결정력·비용 통제가 좋아지는 중.",
         caution="일회성 비용이 섞이면 한 분기만 튄다.",
         source="SEC XBRL", tags=("수익성",)),

    # --- 효율 -------------------------------------------------------------
    Term("roe", "ROE", "주주 돈으로 얼마나 벌었나.",
         formula="순이익 ÷ 자기자본 × 100",
         how_to_read="15% 이상을 꾸준히 지키면 우수.",
         caution="빚이 많으면 부풀려 보인다. ROIC로 확인할 것.",
         source="SEC XBRL", tags=("효율",)),
    Term("roic", "ROIC", "빚까지 포함해 굴린 돈 대비 수익률.",
         formula="세후영업이익 ÷ (자기자본 + 총부채 − 현금)",
         how_to_read="ROE보다 정확하다. 이 프로그램의 판정 기준.",
         caution="세율을 못 구하면 21%로 계산한다.",
         source="SEC XBRL", tags=("효율",)),

    # --- 현금 -------------------------------------------------------------
    Term("ocf", "영업현금흐름", "본업으로 실제 들어온 현금.",
         how_to_read="순이익보다 커야 건강하다.",
         caution="작으면 매출채권·재고가 쌓이는 중일 수 있다.",
         source="SEC XBRL", tags=("현금",)),
    Term("fcf", "잉여현금흐름", "번 현금에서 설비투자를 뺀 여윳돈.",
         formula="영업현금흐름 − 설비투자",
         how_to_read="배당·자사주·빚 상환에 쓸 수 있는 돈.",
         caution="성장기 기업은 마이너스가 정상일 수 있다.",
         source="SEC XBRL", tags=("현금",)),
    Term("runway", "현금 런웨이", "지금 속도로 태우면 몇 년 버티나.",
         formula="(현금 + 단기투자) ÷ 연간 소진액",
         how_to_read="적자 기업의 생존 지표. 2년 미만이면 위험.",
         caution="2년 밑이면 증자 가능성이 크고, 증자하면 지분이 희석된다.",
         source="SEC XBRL", tags=("현금",)),

    # --- 밸류에이션 --------------------------------------------------------
    Term("per", "PER", "주가가 순이익의 몇 배인가.",
         formula="주가 ÷ 주당순이익(TTM)",
         how_to_read="절대값은 의미 없다. 과거 평균·동종업계와 비교해야 한다.",
         caution="적자면 계산되지 않는다.",
         source="주가 + SEC XBRL", tags=("밸류에이션",)),
    Term("psr", "PSR", "시가총액이 연 매출의 몇 배인가.",
         formula="시가총액 ÷ 매출(TTM)",
         how_to_read="적자라 PER을 못 쓰는 성장주에 쓴다.",
         caution="업종마다 정상 범위가 크게 다르다.",
         source="주가 + SEC XBRL", tags=("밸류에이션",)),
    Term("market_cap", "시가총액", "회사 전체의 시장 가격.",
         formula="주가 × 발행주식수",
         how_to_read="이 회사를 통째로 사려면 드는 값.",
         source="주가 + SEC 공시", tags=("밸류에이션",)),

    # --- 공시 -------------------------------------------------------------
    Term("8k", "8-K", "중요한 일이 생겼을 때 즉시 내는 수시공시.",
         how_to_read="2.02 실적발표 · 7.01 IR자료 · 5.02 임원변경 · 4.02 재무제표 재작성(매우 나쁨).",
         source="SEC EDGAR", tags=("공시",)),
    Term("form4", "Form 4", "임원·대주주의 주식 매매 신고.",
         how_to_read="P는 자기 돈으로 산 것(긍정), S는 매도. A·F·M은 보상·세금·옵션이라 매매 의사와 다르다.",
         caution="매도는 사전계획(10b5-1)일 수 있어 그 자체로 악재는 아니다.",
         source="SEC EDGAR", tags=("공시",)),
    Term("10q", "10-Q", "분기 정식 재무보고서.",
         how_to_read="재무제표 + 경영진이 직접 쓴 설명(MD&A)이 들어 있다.",
         source="SEC EDGAR", tags=("공시",)),
    Term("10k", "10-K", "연간 보고서. 가장 상세하다.",
         how_to_read="사업 구조·위험 요인·연간 실적이 모두 담긴다.",
         source="SEC EDGAR", tags=("공시",)),
    Term("mdna", "MD&A", "경영진이 쓴 실적 설명과 향후 계획.",
         how_to_read="숫자 뒤의 맥락. 왜 늘었고 앞으로 뭘 할지가 적힌다.",
         caution="회사가 쓴 글이라 유리하게 서술된다. 숫자와 대조할 것.",
         source="10-Q Item 2 / 10-K Item 7", tags=("공시",)),
    Term("sc13d", "SC 13D / 13G", "지분 5% 이상 확보 신고.",
         how_to_read="13D는 경영 참여, 13G는 단순 투자 목적.",
         source="SEC EDGAR", tags=("공시",)),
    Term("s3", "S-3 / 424B", "주식·채권을 새로 발행하겠다는 신고.",
         how_to_read="적자 기업이 현금이 부족할 때 낸다.",
         caution="주식 수가 늘어 기존 주주 지분이 희석된다.",
         source="SEC EDGAR", tags=("공시",)),

    # --- ETF ---------------------------------------------------------------
    Term("etf", "ETF", "여러 자산을 담아 주식처럼 거래되는 그릇.",
         how_to_read="회사가 아니라서 매출·ROE가 없다. 무엇을 담았는지가 전부.",
         caution="같은 지수를 따라가도 보수와 추종 오차가 다르다.",
         source="SEC 펀드 티커 목록", tags=("ETF",)),
    Term("leveraged_etf", "레버리지 ETF", "기초자산 하루 등락의 2~3배를 노리는 상품.",
         formula="매일 배수를 다시 맞춘다(일일 리밸런싱)",
         how_to_read="하루 기준이다. 2배 ETF의 한 달 수익은 지수의 2배가 아니다.",
         caution="오르내림이 반복되면 지수가 제자리여도 원금이 깎인다(변동성 감쇠).",
         source="상품명·투자설명서", tags=("ETF",)),
    Term("inverse_etf", "인버스 ETF", "기초자산과 반대로 움직이게 만든 상품.",
         how_to_read="하락에 베팅하는 도구.",
         caution="레버리지와 같은 이유로 장기 보유에 불리하다.",
         source="상품명·투자설명서", tags=("ETF",)),
    Term("expense_ratio", "보수(운용비용)", "ETF를 들고 있으면 매년 빠져나가는 비용.",
         how_to_read="같은 지수를 따르면 낮은 쪽이 유리하다.",
         caution="이 프로그램은 지어내지 않는다. 투자설명서(497)에서 직접 확인할 것.",
         source="497 / 485BPOS", tags=("ETF",)),

    # --- 기타 -------------------------------------------------------------
    Term("dilution", "희석", "주식 수가 늘어 내 지분 비중이 줄어드는 것.",
         formula="(이번 발행주식수 − 1년 전) ÷ 1년 전 × 100",
         how_to_read="적자 기업이 증자로 돈을 마련하면 여기가 오른다.",
         caution="자사주 매입·소각을 하면 반대로 줄어든다.",
         source="SEC XBRL", tags=("기타",)),
    Term("track_record", "가이던스 이행", "과거에 제시한 전망을 실제로 지켰는지.",
         formula="회사가 제시한 매출 범위 vs 그 분기 SEC 제출 실적",
         how_to_read="메모 기준. 가이던스는 관리될 수 있으니 이력으로 검증한다.",
         caution="조정 EPS·EBITDA는 정의가 회사마다 달라 판정하지 않는다.",
         source="8-K + SEC XBRL", tags=("우선순위",)),
    Term("fx", "환율", "1달러를 다른 나라 돈으로 바꾸면 얼마인지.",
         how_to_read="원화 환율이 오르면 달러가 비싸진 것 = 원화 약세.",
         caution="미국 주식을 원화로 환산할 때 주가와 환율이 같이 움직인다.",
         source="Yahoo Finance / Stooq", tags=("기타",)),
    Term("vix", "VIX", "S&P 500의 향후 30일 변동성 기대치.",
         how_to_read="보통 20 아래면 평온, 30 위면 불안하다는 뜻.",
         caution="방향이 아니라 흔들림의 크기다. 오른다고 꼭 하락은 아니다.",
         source="Yahoo Finance", tags=("기타",)),
    Term("cik", "CIK", "SEC가 회사에 붙인 고유 번호.",
         how_to_read="티커는 바뀌어도 CIK는 안 바뀐다.",
         source="SEC EDGAR", tags=("기타",)),
    Term("ttm", "TTM", "최근 12개월(4개 분기 합).",
         how_to_read="계절성을 지우고 현재 실력을 본다.", tags=("기타",)),
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
    "희석": "dilution",
    "가이던스": "guidance",
    "가이던스 이행": "track_record",
    "환율": "fx",
    "VIX": "vix",
    "ETF": "etf",
}


def lookup(label: str) -> Term | None:
    key = LABEL_TO_KEY.get(label.strip())
    return BY_KEY.get(key) if key else None


def groups() -> dict[str, list[Term]]:
    """태그별로 묶어서 사전 화면에 보여준다."""
    order = ["우선순위", "수익성", "효율", "현금", "밸류에이션", "공시", "ETF", "기타"]
    out: dict[str, list[Term]] = {name: [] for name in order}
    for term in TERMS:
        tag = next((t for t in term.tags if t in out), "기타")
        out[tag].append(term)
    return {k: v for k, v in out.items() if v}
