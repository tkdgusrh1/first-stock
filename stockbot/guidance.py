"""가이던스와 실적 수치를 8-K 실적 발표문에서 뽑아낸다.

왜 가능한가:
  회사는 실적을 발표할 때 8-K 항목 2.02 와 함께 보도자료(Exhibit 99.1)를 붙인다.
  그 안에 "we expect revenue of $X to $Y for the third quarter" 같은 문장이
  그대로 들어 있다. 구조화된 데이터는 아니지만 문장은 확실히 있다.

왜 조심해야 하는가:
  표현이 회사마다 제각각이라 100% 잡아내지 못한다. 그래서
    · 잡아낸 문장은 **원문 그대로** 보여준다 (요약·의역하지 않는다)
    · 숫자를 뽑아낼 수 있으면 뽑되, 문장을 항상 함께 남긴다
    · 못 찾으면 '못 찾았다' 고 밝히고 원문 링크를 준다
  회사가 가이던스를 '관리' 한다는 점(낮게 부르기 등)은 사람이 판단할 몫이다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .filing_text import html_to_paragraphs

log = logging.getLogger(__name__)

# "we expect / we anticipate / guidance / outlook" + 숫자 가 있는 문장
GUIDANCE_TRIGGERS = re.compile(
    r"\b(we (?:expect|anticipate|project|forecast|estimate)|"
    r"(?:full[- ]year|fiscal|first|second|third|fourth)\s+(?:quarter|year)\s+(?:guidance|outlook)|"
    r"guidance (?:of|for|range)|outlook for|we are (?:raising|lowering|reaffirming|updating|initiating))\b",
    re.IGNORECASE,
)
# 숫자가 있어야 가이던스로 인정한다 (단순 다짐 문장 제외)
HAS_NUMBER = re.compile(r"\$\s?[\d,.]+|\b\d+(?:\.\d+)?\s?(?:%|million|billion|bn|mm)\b", re.IGNORECASE)

# 범위 표현: "$450 million to $470 million", "$1.20 - $1.30"
RANGE = re.compile(
    r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion|bn)?\s*(?:to|-|–|—|and)\s*\$?\s?([\d,]+(?:\.\d+)?)\s*(million|billion|bn)?",
    re.IGNORECASE,
)

PERIOD_HINT = re.compile(
    r"\b(first|second|third|fourth|full[- ]year|fiscal(?:\s+year)?|Q[1-4]|next quarter)\b[^.]{0,40}?"
    r"\b(quarter|year|20\d\d)\b",
    re.IGNORECASE,
)

METRIC_HINT = [
    (re.compile(r"\brevenue|net sales|sales\b", re.IGNORECASE), "매출"),
    (re.compile(r"\b(eps|earnings per share)\b", re.IGNORECASE), "EPS"),
    (re.compile(r"\b(adjusted )?ebitda\b", re.IGNORECASE), "EBITDA"),
    (re.compile(r"\b(operating (income|margin))\b", re.IGNORECASE), "영업이익"),
    (re.compile(r"\b(gross margin)\b", re.IGNORECASE), "매출총이익률"),
    (re.compile(r"\b(free cash flow|fcf)\b", re.IGNORECASE), "잉여현금흐름"),
]

_MULTIPLIER = {"million": 1e6, "mm": 1e6, "billion": 1e9, "bn": 1e9}


@dataclass
class GuidanceItem:
    sentence: str                     # 원문 그대로
    metric: str | None = None         # 매출 / EPS / …
    period: str | None = None         # 어느 분기·연도에 대한 것인지
    low: float | None = None
    high: float | None = None
    unit: str | None = None           # $ 또는 %

    @property
    def range_text(self) -> str | None:
        if self.low is None:
            return None
        if self.unit == "%":
            return f"{self.low:.1f}% ~ {self.high:.1f}%" if self.high else f"{self.low:.1f}%"
        def fmt(v):
            if v >= 1e9:
                return f"${v / 1e9:,.2f}B"
            if v >= 1e6:
                return f"${v / 1e6:,.1f}M"
            return f"${v:,.2f}"
        return f"{fmt(self.low)} ~ {fmt(self.high)}" if self.high else fmt(self.low)


@dataclass
class GuidanceReport:
    form: str
    filing_date: str
    url: str
    items: list[GuidanceItem] = field(default_factory=list)
    results: list[str] = field(default_factory=list)   # 실적 관련 문장(원문)

    @property
    def found(self) -> bool:
        return bool(self.items)


def _sentences(paragraphs: list[str]) -> list[str]:
    out: list[str] = []
    for para in paragraphs:
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            sentence = " ".join(sentence.split())
            if 40 <= len(sentence) <= 500:
                out.append(sentence)
    return out


def parse_numbers(sentence: str) -> tuple[float | None, float | None, str | None]:
    """문장에서 가이던스 범위를 뽑는다. 못 뽑으면 (None, None, None)."""
    match = RANGE.search(sentence)
    if match:
        low_raw, low_unit, high_raw, high_unit = match.groups()
        unit = (high_unit or low_unit or "").lower()
        scale = _MULTIPLIER.get(unit, 1.0)
        try:
            low = float(low_raw.replace(",", "")) * scale
            high = float(high_raw.replace(",", "")) * scale
        except ValueError:
            return None, None, None
        return low, high, "$"

    percent = re.search(r"\b(\d+(?:\.\d+)?)\s*%\s*(?:to|-|–|and)\s*(\d+(?:\.\d+)?)\s*%", sentence)
    if percent:
        return float(percent.group(1)), float(percent.group(2)), "%"

    single = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion|bn)?", sentence, re.IGNORECASE)
    if single:
        scale = _MULTIPLIER.get((single.group(2) or "").lower(), 1.0)
        try:
            return float(single.group(1).replace(",", "")) * scale, None, "$"
        except ValueError:
            return None, None, None
    return None, None, None


def classify(sentence: str) -> tuple[str | None, str | None]:
    metric = next((label for pattern, label in METRIC_HINT if pattern.search(sentence)), None)
    period_match = PERIOD_HINT.search(sentence)
    return metric, (period_match.group(0).strip() if period_match else None)


def extract_guidance(raw_html: str, form: str, filing_date: str, url: str) -> GuidanceReport:
    """실적 발표문에서 가이던스 문장을 찾아낸다."""
    report = GuidanceReport(form=form, filing_date=filing_date, url=url)
    sentences = _sentences(html_to_paragraphs(raw_html))

    seen: set[str] = set()
    for sentence in sentences:
        if not GUIDANCE_TRIGGERS.search(sentence) or not HAS_NUMBER.search(sentence):
            continue
        marker = sentence[:70].lower()
        if marker in seen:
            continue
        seen.add(marker)

        low, high, unit = parse_numbers(sentence)
        metric, period = classify(sentence)
        report.items.append(
            GuidanceItem(sentence=sentence, metric=metric, period=period,
                         low=low, high=high, unit=unit)
        )
        if len(report.items) >= 8:
            break

    # 실적 자체를 설명한 문장도 함께 담는다 (증감 + 숫자)
    results_pattern = re.compile(
        r"\b(revenue|net sales|net income|earnings|eps|margin)\b.{0,80}?"
        r"\b(increased|decreased|grew|declined|rose|fell|was|were)\b",
        re.IGNORECASE,
    )
    for sentence in sentences:
        if results_pattern.search(sentence) and HAS_NUMBER.search(sentence):
            if sentence not in report.results:
                report.results.append(sentence)
        if len(report.results) >= 6:
            break

    return report


def fetch_guidance(http, edgar, filing) -> GuidanceReport | None:
    """8-K 의 보도자료 첨부(Exhibit 99.x)까지 뒤져서 가이던스를 찾는다.

    가이던스는 8-K 본문이 아니라 첨부된 보도자료에 있는 경우가 대부분이다.
    """
    candidates = []
    if filing.primary_doc:
        candidates.append(filing.doc_url)

    # 첨부 목록에서 ex-99 문서를 찾는다
    try:
        base = f"https://www.sec.gov/Archives/edgar/data/{int(filing.cik)}/{filing.acc_nodash}"
        listing = http.get_text(f"{base}/")
        for name in re.findall(r'href="[^"]*?/([^"/]+\.(?:htm|html|txt))"', listing, re.IGNORECASE):
            if re.search(r"ex[-_]?99", name, re.IGNORECASE):
                candidates.append(f"{base}/{name}")
    except Exception as exc:
        log.debug("첨부 목록 조회 실패 (%s): %s", filing.accession, exc)

    for url in candidates:
        try:
            raw = http.get_text(url, timeout=60)
        except Exception:
            continue
        report = extract_guidance(raw, filing.form, filing.filing_date, url)
        if report.found or report.results:
            return report
    return None
