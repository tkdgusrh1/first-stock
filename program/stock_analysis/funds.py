"""ETF·펀드 처리.

왜 따로 있는가:
  ETF 는 회사가 아니다. 매출도 영업이익률도 ROE 도 없다. 그래서 메모의
  '흑자 기업 5체크 / 적자 기업 5체크' 를 그대로 들이대면 전부 '판단 불가' 가
  된다. ETF 에는 ETF 를 보는 기준이 따로 있어야 한다.

여기서 보는 것:
  · 무엇을 담고 있는가 (지수·암호화폐·단일 종목·섹터·채권)
  · 배수 상품인가 (2배·3배·인버스). 이게 가장 중요하다.
  · 최근에 낸 서류는 무엇인가 (투자설명서 변경·연차보고서)

지어내지 않는 원칙은 그대로다. 이름과 SEC 서류에서 읽어낸 것만 쓴다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ETF·펀드가 내는 서류. 기업의 10-K/10-Q 자리에 해당한다.
FUND_FORMS = ["497", "497K", "485BPOS", "485APOS", "N-CSR", "N-CSRS", "N-CEN", "8-K"]

FUND_FORM_LABEL = {
    "497": "투자설명서 변경 (보수·전략·위험 문구가 바뀜)",
    "497K": "요약 투자설명서",
    "485BPOS": "등록서류 연례 갱신 (확정)",
    "485APOS": "등록서류 변경 신청",
    "N-CSR": "연차보고서 (보유 종목·비용·성과)",
    "N-CSRS": "반기보고서",
    "N-CEN": "연간 운영 보고 (SEC 제출용)",
    "NPORT-P": "분기 보유 명세",
    "24F-2NT": "연간 판매분 신고",
}

# 펀드로 분류되는 SIC 코드
FUND_SIC = {"6726", "6722", "6770"}

# 펀드만 내는 서류 (이게 있으면 회사가 아니라 펀드다)
FUND_ONLY_FORMS = {"N-CSR", "N-CSRS", "N-CEN", "NPORT-P", "497", "497K",
                   "485BPOS", "485APOS", "24F-2NT", "N-1A", "N-8B-2"}

# --- 이름에서 읽어내는 성격 -------------------------------------------------
# ProShares 계열은 배수를 이름으로 쓴다: Ultra=2배, UltraPro=3배, UltraShort=-2배.
LEVERAGE_PATTERNS = [
    (re.compile(r"(\b-?3\s?x\b|\bthree times\b|\btriple\b|\bultrapro\b)", re.I), 3.0),
    (re.compile(r"(\b-?2\s?x\b|\btwo times\b|\bdouble\b|\bultra(?!pro))", re.I), 2.0),
    (re.compile(r"\b1\.5\s?x\b", re.I), 1.5),
]
# 'Short Duration Bond' 처럼 만기가 짧다는 뜻의 short 는 인버스가 아니다.
INVERSE = re.compile(
    r"(\binverse\b|\bbear\b|\bultrashort\b|-\d(?:\.\d)?\s?x\b"
    r"|\bshort\b(?!\s+(?:duration|term|maturity|treasury|bond|dated)))",
    re.I,
)
DAILY = re.compile(r"\bdaily\b", re.I)

KIND_RULES = [
    (re.compile(r"\b(s&p\s?500|nasdaq(?:[- ]?100)?|qqq|russell|dow jones|total (stock )?market|"
                r"msci|ftse|wilshire|crsp|index)\b", re.I), "지수 추종"),
    (re.compile(r"\b(bitcoin|btc|ether(eum)?|eth|solana|xrp|crypto|digital asset|blockchain)\b", re.I),
     "암호화폐"),
    (re.compile(r"\b(treasury|bond|aggregate|municipal|credit|duration|tips)\b", re.I), "채권"),
    (re.compile(r"\b(data cent(er|re)|digital infrastructure|semiconductor|artificial intelligence|"
                r"ai |cloud|cyber|robotics|clean energy|biotech|uranium|lithium)\b", re.I), "테마·섹터"),
    (re.compile(r"\b(gold|silver|oil|natural gas|commodity|copper)\b", re.I), "원자재"),
    (re.compile(r"\b(dividend|value|growth|momentum|quality|low volatility)\b", re.I), "스타일"),
    (re.compile(r"\b(covered call|buffer|income|premium)\b", re.I), "옵션 전략"),
]

# 한 종목에만 배수로 거는 상품을 내는 운용사들
SINGLE_STOCK_ISSUERS = re.compile(
    r"\b(graniteshares|direxion daily [a-z]{2,5} b(ull|ear)|t-rex|defiance daily|"
    r"volatility shares|leverage shares|tradr)\b", re.I
)

# 단일 종목 상품 이름에서 기초 종목 티커를 뽑을 때 무시할 단어들
_NOT_A_TICKER = {
    "ETF", "ETN", "ETP", "USD", "AI", "US", "UK", "EU", "NAV", "SEC", "LLC", "INC",
    "TRUST", "FUND", "SHARES", "DAILY", "LONG", "SHORT", "BULL", "BEAR", "TARGET",
    "COVERED", "CALL", "INCOME", "STRATEGY", "ULTRA", "ULTRAPRO", "PRO", "X", "II",
}
_TICKER_TOKEN = re.compile(r"\b([A-Z]{2,5})\b")


@dataclass
class FundInfo:
    ticker: str
    name: str = ""
    kind: str = ""                 # 지수 추종 / 암호화폐 / 테마·섹터 …
    leverage: float | None = None  # 2.0 = 2배
    inverse: bool = False
    daily_reset: bool = False
    single_stock: bool = False
    underlying: str = ""           # 단일 종목 상품이면 그 종목 티커
    sic: str = ""
    sic_label: str = ""
    series_id: str = ""
    class_id: str = ""
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def risk_label(self) -> str:
        """한 줄로 요약한 성격."""
        parts = []
        if self.leverage and self.inverse:
            parts.append(f"{self.leverage:g}배 인버스")
        elif self.leverage:
            parts.append(f"{self.leverage:g}배 레버리지")
        elif self.inverse:
            parts.append("인버스")
        if self.kind:
            parts.append(self.kind)
        return " · ".join(parts) or "ETF"

    @property
    def high_risk(self) -> bool:
        return bool(self.leverage or self.inverse)


def classify_name(ticker: str, name: str) -> FundInfo:
    """상품명에서 성격을 읽는다. 이름에 없는 것은 넣지 않는다."""
    info = FundInfo(ticker=ticker.upper(), name=name or "")
    text = name or ""

    for pattern, multiple in LEVERAGE_PATTERNS:
        if pattern.search(text):
            info.leverage = multiple
            break
    info.inverse = bool(INVERSE.search(text))
    info.daily_reset = bool(DAILY.search(text)) or bool(info.leverage)
    info.single_stock = bool(SINGLE_STOCK_ISSUERS.search(text))
    if info.single_stock:
        info.underlying = _underlying_ticker(text)

    for pattern, label in KIND_RULES:
        if pattern.search(text):
            info.kind = label
            break
    if not info.kind and info.single_stock:
        info.kind = f"단일 종목({info.underlying})" if info.underlying else "단일 종목"

    _add_warnings(info)
    return info


def _underlying_ticker(name: str) -> str:
    """'2x Long COIN Daily ETF' → COIN.

    단일 종목 상품에서만 쓴다. 대문자 덩어리가 곧 기초 종목이라는 관행에
    기대는 것이라, 확실하지 않으면 빈 문자열을 돌려준다.
    """
    for token in _TICKER_TOKEN.findall(name):
        if token.upper() not in _NOT_A_TICKER:
            return token.upper()
    return ""


def _add_warnings(info: FundInfo) -> None:
    """배수 상품의 위험을 정확한 표현으로 적는다. 겁주려는 게 아니라 구조 설명이다."""
    if info.leverage or info.inverse:
        multiple = f"{info.leverage:g}배" if info.leverage else "역방향"
        info.warnings.append(
            f"하루 수익률의 {multiple}를 맞추도록 매일 되맞추는 상품입니다. "
            "이틀 이상 들고 있으면 기초자산이 제자리로 돌아와도 손실이 남을 수 있습니다."
        )
        info.warnings.append(
            "장기 보유용이 아닙니다. 기초자산이 오르내림을 반복할수록 원금이 깎입니다(변동성 감쇠)."
        )
    if info.single_stock:
        what = f"{info.underlying} 한 종목" if info.underlying else "한 종목"
        info.warnings.append(
            f"{what}에만 겁니다. 여러 종목에 나눠 담는 ETF 와 달리 분산 효과가 없습니다."
        )
    if info.kind == "암호화폐":
        info.warnings.append(
            "기초자산이 24시간 거래되는 암호화폐입니다. 미국 증시가 닫힌 사이에 크게 움직일 수 있습니다."
        )
    if info.kind == "옵션 전략":
        info.notes.append("옵션을 팔아 분배금을 만드는 구조입니다. 기초자산이 크게 오를 때 상승분이 제한됩니다.")


def detect_fund(
    ticker: str,
    submissions: dict | None,
    in_fund_list: bool = False,
    name_hint: str = "",
) -> FundInfo | None:
    """이 종목이 ETF·펀드인지 판정한다.

    근거를 셋 중 하나라도 만족하면 펀드로 본다.
      1) SEC 의 ETF·펀드 티커 목록(company_tickers_mf.json)에 있다
      2) 산업분류(SIC)가 투자회사다
      3) 펀드만 내는 서류(N-CSR·497 등)를 내고 있다
    """
    payload = submissions or {}
    name = payload.get("name") or name_hint or ""
    sic = str(payload.get("sic") or "")
    forms = set()
    recent = ((payload.get("filings") or {}).get("recent") or {})
    for form in (recent.get("form") or [])[:120]:
        forms.add(str(form).upper())

    files_10k = any(f in forms for f in ("10-K", "10-Q"))
    fund_forms = forms & FUND_ONLY_FORMS

    if not (in_fund_list or sic in FUND_SIC or (fund_forms and not files_10k)):
        return None

    info = classify_name(ticker, name)
    info.sic = sic
    info.sic_label = str(payload.get("sicDescription") or "")
    if fund_forms:
        info.notes.append("SEC 에 " + ", ".join(sorted(fund_forms)[:4]) + " 를 제출하는 펀드입니다.")
    return info
