"""근거 — 표본이 큰 연구와, 그 결과를 이 프로그램에 어떻게 반영했는지.

숫자는 논문 요약·보고서 안내문 기준이다(이 프로그램을 만든 환경에서는 원문 페이지가
열리지 않아 검색 결과에 인용된 요약으로 확인했다). 확인하지 못한 숫자는 적지 않았다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Study:
    name: str
    sample: str          # 표본
    finding: str         # 무엇을 찾았나
    applied: str         # 이 프로그램에 반영한 것
    url: str


STUDIES = (
    Study("자본시장연구원 — 코로나19 국면의 개인투자자 (2021)", "국내 개인 약 20만 명(증권사 4곳)",
          "2020년 신규 투자자의 60%가 손실. 회전율·당일 매매·종목 교체가 높을수록, 소액·신규일수록 심함",
          "점검은 주 2회(화·금)만, 사고 나서 최소 5거래일은 손절 말고는 팔지 않음",
          "https://www.kcmi.re.kr/report/report_view?report_no=1243"),
    Study("자본시장연구원 — 개인투자자의 해외투자 특징 및 성과 (2026)", "국내 개인 약 10만 명, 2020~2022",
          "500만원 이하 소액 투자자의 국내주식 회전율 연 45.1회로 가장 높음. 500만원 미만의 35.4%가 해외 레버리지 상품 보유. "
          "비용을 빼면 손실자가 이익자보다 많았음",
          "레버리지·인버스 상품은 백테스트·모의 계좌에서 기본으로 뺌, 연 회전율을 결과에 표시",
          "https://www.kcmi.re.kr/report/report_view?report_no=2254"),
    Study("자본시장연구원 — ETF 시장의 개인투자자 (2024)", "국내 개인 약 13만 6천 명",
          "개인 ETF 거래의 60~70%가 레버리지·인버스. ETF 를 빼면 수익률·샤프가 오히려 좋아졌고, 손해는 주로 인버스에서",
          "레버리지·인버스 상품 제외",
          "https://www.kcmi.re.kr/report/report_view?report_no=1776"),
    Study("Barber·Odean, Trading Is Hazardous to Your Wealth (2000)", "미국 66,465 가구, 1991~1996",
          "가장 많이 거래한 집단 연 11.4%, 시장 17.9%. 평균 회전율 연 75%",
          "모든 결과에 비용을 빼고 '그냥 보유' 와 나란히 표시",
          "https://faculty.haas.berkeley.edu/odean/papers%20current%20versions/individual_investor_performance_final.pdf"),
    Study("Barber·Lee·Liu·Odean, Just How Much Do Individual Investors Lose by Trading? (2009)",
          "대만 주식시장 전체 투자자의 거래 기록",
          "개인 투자자 전체 포트폴리오가 해마다 3.8%p 손해(대만 GDP 의 2.2%)",
          "'그냥 보유' 를 못 이기면 규칙을 쓸 이유가 없다고 결과마다 적음",
          "https://faculty.haas.berkeley.edu/odean/papers%20current%20versions/justhowmuchdoindividualinvestorslose_rfs_2009.pdf"),
    Study("Barber·Lee·Liu·Odean, 대만 단타 투자자 연구", "대만 단타 투자자 45만 명 이상, 1992~2006",
          "비용을 빼고 다음 해에도 꾸준히 번 사람 1% 미만",
          "당일 사고파는 전략은 넣지 않음",
          "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=529063"),
    Study("Chague·De-Losso·Giovannetti, Day Trading for a Living? (2020)", "브라질 단타 시작자 19,646명",
          "300일 이상 버틴 1,551명 중 97% 손실, 최저임금보다 많이 번 사람 1.1%. 오래 해도 나아지지 않음",
          "당일 매매 없음, 실전 전 모의 계좌 단계 필수",
          "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101"),
    Study("Barber·Huang·Odean·Schwarz, Robinhood 사용자 연구 (2022)", "로빈후드 사용자 보유 기록(종목별)",
          "사용자들이 그날 가장 많이 산 종목은 이후 5일 평균 -3% 초과수익(관심 쏠림·떼 매수)",
          "하루에 15% 넘게 오른 날은 그 종목을 새로 사지 않음(추격 매수 금지)",
          "https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13183"),
    Study("ESMA, CFD 개인 계좌 분석 (2018)", "EU 여러 나라 CFD 개인 계좌",
          "개인 계좌의 74~89% 가 손실, 1인당 평균 손실 1,600~29,000유로",
          "빚(신용·미수)·레버리지 없음 — 비중 합계는 늘 계좌 안",
          "https://www.esma.europa.eu/node/84933"),
    Study("Wiecki 외, All That Glitters Is Not Gold (2016)", "알고리즘 888개(개인 퀀트 작성)",
          "백테스트 샤프는 실제 성과를 거의 예측 못 함(R² 0.025 미만). 백테스트를 많이 돌릴수록 격차가 커짐",
          "백테스트 횟수를 화면 맨 위에 셈, 결과는 최대 낙폭·변동성을 앞에 둠",
          "https://www.researchgate.net/publication/307553701"),
    Study("McLean·Pontiff (2016)", "논문에 나온 수익 예측 변수 97개",
          "논문 밖 기간에서 26%, 발표 뒤 58% 수익 감소",
          "결과마다 '같은 비율로 깎으면' 연수익률을 같이 보여줌",
          "https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365"),
    Study("Chui·Titman·Wei, Individualism and Momentum around the World (2010)", "여러 나라 주식시장(국가 간 비교)",
          "개인주의 성향이 강한 나라일수록 모멘텀 수익이 큼 — 동아시아는 약한 편",
          "한국 화면의 모멘텀 전략 결과에 '근거가 약하다' 고 표시",
          "https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2009.01532.x"),
    Study("한국 주식시장의 수익성 프리미엄 연구 (KCI)", "유가증권·코스닥 상장사, 2001~2017",
          "수익성이 높은 회사가 더 높은 수익 — 위험보다 투자자의 늦은 반응으로 설명됨",
          "성장 점수에 ROIC 포함",
          "https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002421442"),
    Study("Asness·Moskowitz·Pedersen, Value and Momentum Everywhere (2013)", "8개 시장·자산군",
          "가치와 모멘텀 프리미엄이 모든 시장에서 나타나고, 둘은 서로 반대로 움직임",
          "2단계(300만원~)부터 성격이 다른 두 번째 전략을 더하라고 권장",
          "https://onlinelibrary.wiley.com/doi/10.1111/jofi.12021"),
    Study("Moskowitz·Ooi·Pedersen, Time Series Momentum (2012)", "선물 58개",
          "12개월 추세를 따르면 58개 전부에서 플러스",
          "신고가 돌파(추세추종) 전략",
          "https://www.sciencedirect.com/science/article/pii/S0304405X11002613"),
    Study("Marshall·Young·Rose (2006) · Darmanin (2026, 미심사)", "다우 35종목 10년 · 대중 신호 5계열",
          "캔들 패턴은 무작위 매매와 차이 없음, 오실레이터·거래량·달력 신호도 '효과 없음'",
          "캔들 패턴 전략은 넣지 않음(효과 없음)",
          "https://arxiv.org/abs/2607.20093"),
    # --- '잃지 않기' — 손절·익절·추세 필터 ---------------------------------------
    Study("이 프로그램이 직접 계산 — S&P 500 152년 (Shiller 월별 자료)", "1871.11~2023.6 월별 지수·배당(예일대 Robert Shiller)",
          "10개월 평균선 위에서만 들고 아래면 현금(드나들 때 0.3% 비용, 현금 이자 0 으로 보수적 가정): "
          "1950년부터 연 11.3% → 10.3%, 최대 낙폭 -49.0% → -19.2%, 최악의 한 해 -39.2% → -12.6%. "
          "152년 전체로는 연 9.2% → 8.9%, 최대 낙폭 -81.8% → -47.0%. "
          "단, 손실 난 해의 비율은 23% → 24% 로 거의 같음 — 손실을 없애지 못하고 크기를 줄인다. "
          "월평균 가격 자료라 실제보다 조금 좋게 나올 수 있음. 2023.7 이후는 배당 자료가 없어 계산에서 뺐다",
          "회전 전략의 '200일선(≈10개월선) 위에서만 보유' 조건, 방어형 ETF 바구니",
          "http://www.econ.yale.edu/~shiller/data.htm"),
    Study("Faber, A Quantitative Approach to Tactical Asset Allocation (2007)", "미국 주식 100년 이상 · 5개 자산군",
          "월말 가격이 10개월 평균선 위면 보유, 아래면 현금 — 수익은 비슷하거나 높고 변동성·낙폭은 낮았다. "
          "평균선 아래에 있을 때 시장 수익이 낮고 변동성이 컸다",
          "방어형 ETF 바구니(주식·채권·금·단기채) + 6개월 순위 회전 + 200일선 조건",
          "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461"),
    Study("Han·Zhou·Zhu, Taming Momentum Crashes: A Simple Stop-Loss Strategy (2016)", "미국 주식 모멘텀, 1926~2013",
          "종목마다 10% 손절을 걸었더니 동일가중 모멘텀 전략의 최악의 한 달 손실이 -49.79% → -11.36%, "
          "샤프 지수는 두 배 넘게",
          "청산 규칙 '손실 상한 7%' · '지키기 세트' — 모멘텀 계열 전략과 함께 쓰기",
          "https://www.researchgate.net/publication/272247075_Taming_Momentum_Crashes_A_Simple_Stop-Loss_Strategy"),
    Study("Kaminski·Lo, When Do Stop-Loss Rules Stop Losses? (2014)", "지수 선물 일별 자료",
          "손절은 가격에 추세(모멘텀)가 있을 때 수익을 더하고, 무작위로 움직이는 가격에서는 수익을 깎는다",
          "손절을 무조건 좋다고 하지 않고, 청산 규칙 비교로 내 종목·전략에서 실제로 덜 잃는지 재게 함",
          "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338"),
    Study("Odean, Are Investors Reluctant to Realize Their Losses? (1998)", "미국 할인 증권사 1만 계좌",
          "개인은 오른 종목을 너무 일찍 팔고 내린 종목을 너무 오래 든다(처분 효과). 그 선택은 이후 성과로 정당화되지 않았다",
          "익절은 '절반만' — 나머지는 본전·추적 손절로 들고 가서 크게 오를 몫을 남김. 내린 종목은 손절선이 대신 판다",
          "https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00072"),
)

CHASE_LIMIT = 0.15        # 하루 상승률이 이보다 크면 그 날은 새로 사지 않는다(추격 매수 금지)
DECAY = 0.58              # McLean·Pontiff: 발표 뒤 평균 수익 감소율

_LEVERAGED = re.compile(r"레버리지|인버스|곱버스|2X|3X|\bULTRA|\bBEAR\b|\bBULL\b|INVERSE|LEVERAGED|SHORT\b",
                        re.IGNORECASE)


def is_leveraged(name: str) -> bool:
    """이름으로 레버리지·인버스 상품을 가린다. 확실하지 않으면 False(멀쩡한 종목을 빼지 않게)."""
    return bool(_LEVERAGED.search(str(name or "")))
