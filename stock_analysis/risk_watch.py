"""위험 요인이 이번에 바뀌었는가.

10-K·10-Q 의 'Item 1A. Risk Factors' 는 회사가 스스로 밝히는 위험 목록이다.
대부분은 해마다 거의 같은 문장이 반복된다. 그래서 **새로 추가된 문단**이
중요하다. 회사가 어떤 위험을 처음으로 적어 넣는 순간이기 때문이다.

여기서 하는 일은 두 가지뿐이다.
  1) 이번 보고서와 직전 보고서의 위험 요인을 문단 단위로 맞춰보고,
     직전에 없던 문단만 골라낸다
  2) 그중에서도 특히 무거운 표현(존속 의문·내부통제 미비·상장폐지 통보 등)이
     있으면 이름표를 붙인다

문장은 손대지 않는다. 회사가 쓴 그대로 보여주고 출처 링크를 붙인다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# 문단이 '같은 문단' 인지 판단하는 기준. 해마다 숫자나 연도만 바뀌는 경우가
# 많아서 완전 일치로 보면 전부 '새 위험' 이 되어버린다.
SIMILAR_ENOUGH = 0.6
MIN_PARAGRAPH = 120        # 이보다 짧은 조각은 제목·표 잔해일 가능성이 크다
MAX_SHOWN = 6

# 특히 무거운 표현. 있으면 이름표를 붙인다.
RED_FLAGS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"substantial doubt[\s\S]{0,60}going concern", re.I),
     "존속 의문",
     "회사가 '계속기업으로서 존속할 수 있을지 중대한 의문' 이 있다고 밝혔습니다. "
     "감사인이 이 문구를 붙이면 자금 조달이 급해졌다는 뜻입니다."),
    (re.compile(r"material weakness(?:es)? in (?:our )?internal control", re.I),
     "내부통제 미비",
     "재무보고 내부통제에 중대한 결함이 있다고 밝혔습니다. 숫자 자체의 신뢰도 문제입니다."),
    (re.compile(r"(restate|restatement) (?:of )?(?:our )?(?:previously issued )?(?:consolidated )?financial statements", re.I),
     "재무제표 재작성",
     "과거 재무제표를 다시 쓴다는 뜻입니다. 이전에 본 숫자가 틀렸다는 의미입니다."),
    (re.compile(r"(notice|notification) (?:of )?(?:non[- ]?compliance|delisting)|delisted from", re.I),
     "상장폐지 통보",
     "거래소 상장 규정을 못 맞춰 통보를 받았습니다."),
    (re.compile(r"(default|breach)[\s\S]{0,50}(covenant|credit agreement|indenture)", re.I),
     "채무 약정 위반",
     "빌린 돈에 붙은 조건을 어겼거나 어길 수 있다는 뜻입니다. 조기 상환을 요구받을 수 있습니다."),
    (re.compile(r"(may|will) (?:need|require|be required) to (?:raise|seek) additional (?:capital|financing|funds)", re.I),
     "추가 자금 필요",
     "돈을 더 조달해야 한다고 밝혔습니다. 증자로 이어지면 주식 수가 늘어납니다(희석)."),
    (re.compile(r"(loss|termination) of[\s\S]{0,40}(largest|significant|key) customer", re.I),
     "핵심 고객 이탈",
     "매출을 크게 의존하는 고객을 잃을 수 있다는 뜻입니다."),
    (re.compile(r"(sec|department of justice|doj) (investigation|subpoena|inquiry)", re.I),
     "당국 조사",
     "규제기관 조사·소환을 받고 있다고 밝혔습니다."),
]

# 10-Q 는 위험 요인을 통째로 싣지 않고 '바뀐 게 없다' 고만 쓰는 경우가 흔하다.
NO_CHANGE = re.compile(
    r"no material changes[\s\S]{0,80}risk factors|"
    r"there have been no material changes[\s\S]{0,60}(previously|annual report)",
    re.I,
)


@dataclass
class Flag:
    label: str
    meaning: str
    sentence: str


@dataclass
class RiskChange:
    ticker: str
    current_form: str = ""
    current_date: str = ""
    current_url: str = ""
    previous_form: str = ""
    previous_date: str = ""
    previous_url: str = ""
    added: list[str] = field(default_factory=list)      # 직전에 없던 문단 (원문)
    added_total: int = 0
    removed_total: int = 0
    compared: bool = False          # 직전 보고서와 실제로 맞춰봤는가
    no_material_changes: bool = False
    flags: list[Flag] = field(default_factory=list)

    @property
    def level(self) -> str:
        """화면 색깔. 지어낸 판단이 아니라 무엇이 발견됐는지에 따른다."""
        if self.flags:
            return "poor"
        if self.added:
            return "fair"
        if self.compared or self.no_material_changes:
            return "good"
        return "unknown"

    @property
    def summary(self) -> str:
        if self.flags:
            names = " · ".join(f.label for f in self.flags)
            # '새로' 는 직전 보고서와 실제로 맞춰봤을 때만 쓸 수 있는 말이다.
            if self.compared and self.added:
                return f"무겁게 볼 표현이 새로 들어왔습니다: {names}"
            return f"무겁게 볼 표현이 있습니다(직전 보고서와 비교하지는 못했습니다): {names}"
        if self.added:
            return f"직전 보고서에 없던 위험 문단이 {self.added_total}개 있습니다."
        if self.no_material_changes:
            return "회사가 '위험 요인에 중요한 변화 없음' 이라고 밝혔습니다."
        if self.compared:
            return "직전 보고서와 견줘 새로 추가된 위험 문단이 없습니다."
        return "비교할 직전 보고서를 찾지 못했습니다."


def _normalize(text: str) -> set[str]:
    """문단을 비교용 단어 집합으로. 숫자와 연도는 해마다 바뀌므로 뺀다."""
    words = re.sub(r"[^a-z ]+", " ", text.lower()).split()
    return {w for w in words if len(w) > 3}


def _similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def diff_paragraphs(current: list[str], previous: list[str]) -> tuple[list[str], int]:
    """(직전에 없던 문단들, 사라진 문단 수)

    표현이 조금 다듬어진 정도는 같은 문단으로 본다. 그래야 '진짜 새로운 위험' 만 남는다.
    """
    current = [p for p in current if len(p) >= MIN_PARAGRAPH]
    previous = [p for p in previous if len(p) >= MIN_PARAGRAPH]

    old_sets = [_normalize(p) for p in previous]
    matched_old: set[int] = set()

    added: list[str] = []
    for paragraph in current:
        tokens = _normalize(paragraph)
        size = len(tokens)
        best, best_at = 0.0, -1
        for index, old in enumerate(old_sets):
            # 크기가 너무 다르면 Jaccard 가 기준을 넘을 수 없다.
            # 10-K 는 위험 문단이 200개씩 되어 전부 맞대보면 4만 번 비교가 된다.
            # 먼저 크기로 걸러내면 대부분은 계산하지 않아도 된다.
            if not old or size * SIMILAR_ENOUGH > len(old) or len(old) * SIMILAR_ENOUGH > size:
                continue
            score = _similarity(tokens, old)
            if score > best:
                best, best_at = score, index
                if best >= 0.95:
                    break        # 거의 같은 문단을 찾았으면 더 볼 필요 없다
        if best >= SIMILAR_ENOUGH:
            matched_old.add(best_at)
        else:
            added.append(paragraph)

    removed = len(previous) - len(matched_old)
    return added, max(0, removed)


def find_flags(paragraphs: list[str]) -> list[Flag]:
    """무겁게 볼 표현을 찾는다. 근거 문장을 함께 남긴다."""
    flags: list[Flag] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        for pattern, label, meaning in RED_FLAGS:
            if label in seen:
                continue
            match = pattern.search(paragraph)
            if match:
                seen.add(label)
                flags.append(Flag(label=label, meaning=meaning, sentence=_sentence_at(paragraph, match)))
    return flags


def _sentence_at(paragraph: str, match: re.Match) -> str:
    """찾은 표현이 들어 있는 문장 하나만 잘라온다. 문단 전체는 너무 길다."""
    start = paragraph.rfind(".", 0, match.start()) + 1
    end = paragraph.find(".", match.end())
    end = end + 1 if end != -1 else len(paragraph)
    return paragraph[start:end].strip() or paragraph[:400]


def build_risk_change(ticker: str, current, previous=None) -> RiskChange:
    """이번 보고서와 직전 보고서(filing_text.FilingText) → 위험 요인 변화."""
    change = RiskChange(ticker=ticker.upper())
    if current is None:
        return change

    change.current_form = current.form
    change.current_date = current.filing_date
    change.current_url = current.url

    section = current.section("risk")
    paragraphs = section.paragraphs if section else []

    # 10-Q 는 '바뀐 게 없다' 한 줄로 끝내는 일이 흔하다. 그것도 정보다.
    haystack = " ".join(paragraphs[:6]) if paragraphs else ""
    if NO_CHANGE.search(haystack):
        change.no_material_changes = True

    if previous is not None:
        change.previous_form = previous.form
        change.previous_date = previous.filing_date
        change.previous_url = previous.url
        old = previous.section("risk")
        if old and old.paragraphs and paragraphs:
            added, removed = diff_paragraphs(paragraphs, old.paragraphs)
            change.compared = True
            change.added_total = len(added)
            change.removed_total = removed
            change.added = added[:MAX_SHOWN]

    # 새 문단이 있으면 거기서, 없으면 이번 위험 요인 전체에서 무거운 표현을 찾는다.
    change.flags = find_flags(change.added or paragraphs)
    return change
