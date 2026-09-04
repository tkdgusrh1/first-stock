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

    Term("risk_factors", "위험 요인 (Item 1A)", "회사가 스스로 적어둔 위험 목록.",
         how_to_read="해마다 거의 같다. 그래서 '새로 추가된 문단'이 진짜 신호다.",
         caution="10-Q는 '변화 없음'만 쓰고 넘어가는 일이 흔하다.",
         source="10-K / 10-Q Item 1A", tags=("공시",)),
    Term("going_concern", "존속 의문", "1년 안에 회사가 버틸 수 있을지 의심된다는 감사인의 문구.",
         how_to_read="가장 무거운 경고. 자금 조달이 급해졌다는 뜻이다.",
         caution="이 문구가 붙었다고 반드시 파산하지는 않는다. 다만 증자·매각 압박은 커진다.",
         source="10-K / 10-Q 본문", tags=("공시",)),
    Term("insider_buy", "내부자 순매수", "임원·대주주가 자기 돈으로 산 금액 − 판 금액.",
         formula="P(공개시장 매수) − S(공개시장 매도), 최근 90일",
         how_to_read="매수는 드물어 신호가 되고, 매도는 흔해 신호가 약하다.",
         caution="RSU 수령·세금 반납·옵션 행사는 매매 의사가 아니라 합계에서 뺀다.",
         source="SEC Form 4", tags=("공시",)),

    # --- 경제 지표 ---------------------------------------------------------
    # 개별 종목의 실적과 무관하게 시장 전체의 PER 을 눌렀다 푸는 배경 값들.
    Term("cpi", "소비자물가 CPI", "가계가 사는 물건·서비스 값이 1년 전보다 얼마나 올랐나.",
         formula="이번 달 지수 ÷ 작년 같은 달 지수 − 1",
         how_to_read="지수 숫자(310 같은)는 뜻이 없다. 전년 대비 %만 본다. 연준 목표는 2%.",
         caution="발표는 한 달 늦다. 8월에 보는 값은 7월분이다.",
         source="미 노동통계국(BLS), FRED 경유", tags=("경제지표",)),
    Term("core_cpi", "근원 CPI", "식품·에너지를 뺀 물가.",
         how_to_read="유가처럼 출렁이는 항목을 빼서 추세를 본다. 헤드라인보다 천천히 움직인다.",
         caution="주거비 비중이 커서 실제 체감보다 늦게 반영된다.",
         source="미 노동통계국(BLS)", tags=("경제지표",)),
    Term("core_pce", "근원 PCE", "연준이 목표 2%를 재는 바로 그 물가 지표.",
         how_to_read="금리 결정에 직접 쓰이는 값이라 CPI보다 무게가 크다.",
         caution="소비 패턴 변화를 반영해서 보통 CPI보다 낮게 나온다.",
         source="미 상무부(BEA)", tags=("경제지표",)),
    Term("policy_rate", "기준금리", "연준이 정하는 하룻밤 자금 금리의 목표 범위.",
         how_to_read="돈의 값. 오르면 주식에 요구되는 수익률도 같이 올라 밸류에이션이 눌린다.",
         caution="발표된 금리보다 '앞으로 어디로 갈지'가 주가를 움직인다.",
         source="연준(FOMC)", tags=("경제지표",)),
    Term("ust10y", "10년물 국채금리", "미국 정부에 10년 빌려줄 때 받는 이자.",
         how_to_read="모든 자산 가격의 기준선. 오르면 먼 미래 이익의 현재가치가 깎여 성장주가 먼저 눌린다.",
         caution="기준금리와 따로 움직인다. 연준이 내려도 이쪽은 오를 수 있다.",
         source="미 재무부", tags=("경제지표",)),
    Term("yield_curve", "장단기 금리차", "10년물 금리 − 2년물 금리.",
         formula="10년물 − 2년물 (%p)",
         how_to_read="마이너스면 '금리 역전'. 과거 침체 앞에서 반복해 나타났던 신호다.",
         caution="역전이 바로 하락은 아니다. 역전 뒤 침체까지 1년 넘게 걸린 적이 많다.",
         source="미 재무부", tags=("경제지표",)),
    Term("unemployment", "실업률", "일할 의사가 있는데 일자리가 없는 사람의 비율.",
         how_to_read="오르면 소비가 식는다. 다만 너무 낮으면 임금이 올라 금리가 안 내려온다.",
         caution="구직을 포기하면 실업자로 세지 않는다. 고용 건수와 같이 봐야 한다.",
         source="미 노동통계국(BLS)", tags=("경제지표",)),
    Term("payrolls", "비농업 고용", "한 달 동안 늘어난 일자리 수.",
         formula="이번 달 취업자 − 지난달 취업자",
         how_to_read="10만 명 언저리를 경기 판단의 눈금으로 본다. 마이너스면 일자리가 줄었다는 뜻.",
         caution="다음 달에 수정되는 폭이 크다. 한 달치보다 3개월 흐름을 본다.",
         source="미 노동통계국(BLS)", tags=("경제지표",)),

    # --- 기타 -------------------------------------------------------------
    Term("52w", "52주 범위", "최근 1년 사이 가장 높았던 값과 낮았던 값.",
         how_to_read="지금 주가가 그 사이 어디쯤인지 보면 분위기가 읽힌다.",
         caution="싸 보인다고 좋은 게 아니다. 내려온 이유를 봐야 한다.",
         source="일봉 종가", tags=("기타",)),
    Term("my_position", "내 보유", "내가 산 가격 대비 지금 얼마인지.",
         formula="(현재가 − 매수가) × 수량, 원화는 지금 환율로 환산",
         how_to_read="달러 수익률과 원화 수익률은 다르다. 환율이 한 번 더 움직인다.",
         caution="살 때 환율이 아니라 지금 환율로 바꾼 값이다.",
         source="직접 입력 + 시세", tags=("기타",)),
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
    "매출 연간": "revenue",          # 한국(DART)은 사업보고서의 연간 확정치
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
    "52주 범위": "52w",
    "내 보유": "my_position",
    "위험 요인": "risk_factors",
    "존속 의문": "going_concern",
    "내부자 거래": "insider_buy",
    "가이던스": "guidance",
    "가이던스 이행": "track_record",
    "환율": "fx",
    "VIX": "vix",
    "ETF": "etf",
    "소비자물가 CPI": "cpi",
    "근원 CPI": "core_cpi",
    "근원 PCE": "core_pce",
    "기준금리": "policy_rate",
    "10년물 국채금리": "ust10y",
    "장단기 금리차": "yield_curve",
    "실업률": "unemployment",
    "비농업 고용": "payrolls",
}


def lookup(label: str) -> Term | None:
    key = LABEL_TO_KEY.get(label.strip())
    return BY_KEY.get(key) if key else None


def groups() -> dict[str, list[Term]]:
    """태그별로 묶어서 사전 화면에 보여준다."""
    order = ["우선순위", "수익성", "효율", "현금", "밸류에이션", "공시", "ETF", "경제지표", "기타"]
    out: dict[str, list[Term]] = {name: [] for name in order}
    for term in TERMS:
        tag = next((t for t in term.tags if t in out), "기타")
        out[tag].append(term)
    return {k: v for k, v in out.items() if v}
