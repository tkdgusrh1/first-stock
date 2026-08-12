"""영어 공시 문장을 한글로 옮긴다 — 규칙으로.

왜 규칙인가:
  공시 문장은 생각보다 정형적이다. "Revenue increased 78% year over year to
  $213.0 million" 같은 문장은 회사가 달라도 뼈대가 같다. 이런 문장은 뜻을
  지어낼 여지 없이 그대로 한글로 옮길 수 있다. 숫자는 손대지 않고 그대로 둔다.

  규칙에 걸리지 않는 문장은 **억지로 옮기지 않는다.** 그런 문장은
  translate.py 의 기계 번역으로 넘기고, 기계 번역이라는 사실을 화면에 밝힌다.

위험 요인(Item 1A)은 문장이 길고 늘 같은 꼴이라("We may ... If we fail to ...,
our business could be materially and adversely affected") 통째로 옮기는 대신
**무엇에 관한 위험인지** 를 한글 주제로 분류한다. 그게 읽는 사람에게 더 쓸모 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 숫자 표기 정리 — $213.0 million → $213.0M
# --------------------------------------------------------------------------
_SCALE = {"trillion": "T", "billion": "B", "million": "M", "thousand": "K"}
_MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)(?:\s*(trillion|billion|million|thousand))?", re.I)


def money(text: str) -> str:
    """문장 속 금액 표기를 짧게 고친다. 값 자체는 바꾸지 않는다."""

    def swap(match: re.Match) -> str:
        amount = match.group(1)
        scale = _SCALE.get((match.group(2) or "").lower(), "")
        return f"${amount}{scale}"

    return _MONEY.sub(swap, text)


# --------------------------------------------------------------------------
# 1) 문장 단위 한글 옮김 — 정형 표현만
# --------------------------------------------------------------------------
_UP = r"(?:increased|grew|rose|climbed|was up|were up|improved)"
_DOWN = r"(?:decreased|declined|fell|dropped|was down|were down)"
_Q = r"(?:first|second|third|fourth)"
_QUARTER_KO = {"first": "1분기", "second": "2분기", "third": "3분기", "fourth": "4분기"}

# 문장을 옮길 때는 조사를 붙이지 않는다. "$213.0M 가 됐습니다" 처럼
# 영어·숫자 뒤에 조사가 붙으면 읽기 나빠져서, 표 라벨처럼 짧게 끊는다.
#
# (정규식, 한글 틀). 틀의 {n} 은 정규식의 n 번째 묶음이 들어갈 자리.
SENTENCE_RULES: list[tuple[re.Pattern, str]] = [
    # 매출 증감
    (re.compile(rf"\brevenue[s]?\b[^.]{{0,60}}?\b{_UP}\b[^.]{{0,40}}?([\d.]+)\s?%[^.]{{0,60}}?to\s+(\$[\d.,]+\s*\w*)", re.I),
     "매출 {2} — 전년 대비 {1}% 증가"),
    (re.compile(rf"\brevenue[s]?\b[^.]{{0,60}}?\b{_DOWN}\b[^.]{{0,40}}?([\d.]+)\s?%[^.]{{0,60}}?to\s+(\$[\d.,]+\s*\w*)", re.I),
     "매출 {2} — 전년 대비 {1}% 감소"),
    (re.compile(rf"\brevenue[s]?\b[^.]{{0,60}}?\b{_UP}\b[^.]{{0,40}}?to\s+(\$[\d.,]+\s*\w*)", re.I),
     "매출 {1} — 증가"),
    (re.compile(rf"\brevenue[s]?\b[^.]{{0,60}}?\b{_DOWN}\b[^.]{{0,40}}?to\s+(\$[\d.,]+\s*\w*)", re.I),
     "매출 {1} — 감소"),
    (re.compile(rf"\brevenue[s]?\b[^.]{{0,20}}?for the ({_Q})\s+quarter[^.]{{0,60}}?\bw(?:as|ere)\s+(\$[\d.,]+\s*\w*)", re.I),
     "{1} 매출 {2}"),
    (re.compile(r"\brevenue[s]?\b[^.]{0,40}?\bof\s+(\$[\d.,]+\s*\w*)[^.]{0,40}?compared to[^.]{0,30}?(\$[\d.,]+\s*\w*)", re.I),
     "매출 {1} — 비교 대상 {2}"),

    # 손익
    (re.compile(r"\bnet loss\b[^.]{0,40}?(?:was|of)\s+(\$[\d.,]+\s*\w*)[^.]{0,60}?compared to[^.]{0,40}?(\$[\d.,]+\s*\w*)", re.I),
     "순손실 {1} — 비교 대상 {2}"),
    (re.compile(r"\bnet income\b[^.]{0,40}?(?:was|of)\s+(\$[\d.,]+\s*\w*)[^.]{0,60}?compared to[^.]{0,40}?(\$[\d.,]+\s*\w*)", re.I),
     "순이익 {1} — 비교 대상 {2}"),
    (re.compile(r"\bnet loss\b[^.]{0,40}?(?:was|of)\s+(\$[\d.,]+\s*\w*)", re.I),
     "순손실 {1}"),
    (re.compile(r"\bnet income\b[^.]{0,40}?(?:was|of)\s+(\$[\d.,]+\s*\w*)", re.I),
     "순이익 {1}"),

    # 마진
    (re.compile(rf"\bgross margin\b[^.]{{0,40}}?\b{_UP}\b[^.]{{0,20}}?to\s+([\d.]+)\s?%[^.]{{0,30}}?from\s+([\d.]+)\s?%", re.I),
     "매출총이익률 {2}% → {1}% (개선)"),
    (re.compile(rf"\bgross margin\b[^.]{{0,40}}?\b{_DOWN}\b[^.]{{0,20}}?to\s+([\d.]+)\s?%[^.]{{0,30}}?from\s+([\d.]+)\s?%", re.I),
     "매출총이익률 {2}% → {1}% (악화)"),
    (re.compile(r"\bgross margin\b[^.]{0,40}?\bw(?:as|ere)\s+([\d.]+)\s?%", re.I),
     "매출총이익률 {1}%"),

    # 현금·자금
    (re.compile(r"\bended the (?:quarter|period|year)\s+with\s+(\$[\d.,]+\s*\w*)", re.I),
     "기말 현금 {1}"),
    (re.compile(r"\bcash(?:, cash equivalents)?[^.]{0,60}?\bof\s+(\$[\d.,]+\s*\w*)", re.I),
     "보유 현금 {1}"),
    (re.compile(r"\b(?:sufficient|adequate)\b[^.]{0,80}?fund (?:our )?operations[^.]{0,60}?(?:at least\s+)?([\w]+)\s+(months|years)", re.I),
     "현재 자금으로 최소 {1}{2} 운영 가능하다고 밝힘"),

    # 수주
    (re.compile(rf"\bbacklog\b[^.]{{0,40}}?\b{_UP}\b[^.]{{0,40}}?to\s+(\$[\d.,]+\s*\w*)", re.I),
     "수주잔고 {1} — 증가"),
    (re.compile(r"\bbacklog\b[^.]{0,40}?\bof\s+(\$[\d.,]+\s*\w*)", re.I),
     "수주잔고 {1}"),

    # 전망 (가이던스)
    (re.compile(r"\bexpect[^.]{0,60}?revenue[^.]{0,60}?(\$[\d.,]+\s*\w*)\s*(?:to|-|–)\s*(\$[\d.,]+\s*\w*)", re.I),
     "다음 기간 매출 전망 {1} ~ {2}"),
    (re.compile(r"\bexpect[^.]{0,40}?(?:adjusted )?ebitda[^.]{0,60}?(\$[\d.,]+\s*\w*)\s*(?:to|-|–)\s*(\$[\d.,]+\s*\w*)", re.I),
     "조정 EBITDA 전망 {1} ~ {2}"),
    (re.compile(r"\braise[sd]?\b[^.]{0,30}?(?:full[- ]year\s+)?(?:revenue\s+)?(?:guidance|outlook)", re.I),
     "연간 전망 상향"),
    (re.compile(r"\b(?:lowered|lowers|lower|cuts|cut|reduced|reduces|reduce)\b[^.]{0,30}?(?:full[- ]year\s+)?(?:revenue\s+)?(?:guidance|outlook)", re.I),
     "연간 전망 하향"),

    # 사업 활동
    (re.compile(r"\b(?:completed|conducted|performed)\s+(\d+)\s+(?:successful\s+)?launches", re.I),
     "이 기간 발사 {1}회"),
    (re.compile(r"\bawarded\b[^.]{0,60}?contract[^.]{0,40}?(?:valued at|worth)\s+(\$[\d.,]+\s*\w*)", re.I),
     "{1} 규모 계약 수주"),
    (re.compile(r"\b(?:signed|entered into)\b[^.]{0,60}?agreement[^.]{0,40}?(\$[\d.,]+\s*\w*)", re.I),
     "{1} 규모 계약 체결"),
]

# 묶음으로 잡힌 영어 단어를 한글로. 숫자는 손대지 않는다.
_WORD_KO = {
    "months": "개월", "years": "년",
    "twelve": "12", "eighteen": "18", "twenty-four": "24", "six": "6",
    **_QUARTER_KO,
}


# --------------------------------------------------------------------------
# 2) 위험 요인 주제 분류
# --------------------------------------------------------------------------
TOPIC_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\bgoing concern\b|\bsubstantial doubt\b", re.I),
     "존속 우려", "회사가 계속 버틸 수 있을지에 대한 의문입니다."),
    (re.compile(r"\b(raise|seek|obtain)\b[^.]{0,40}\b(additional capital|additional financing|more funding)\b", re.I),
     "자금 조달", "돈을 더 마련해야 한다는 내용입니다. 증자로 이어지면 주식 수가 늘어납니다."),
    (re.compile(r"\b(supplier|suppliers|supply chain|component shortage|sole source)\b", re.I),
     "공급망", "부품·원자재를 특정 업체에 의존한다는 내용입니다."),
    (re.compile(r"\b(largest customer|significant customers|customer concentration|small number of customers)\b", re.I),
     "고객 집중", "매출이 소수 고객에 몰려 있다는 내용입니다."),
    (re.compile(r"\b(competition|competitors|competitive)\b", re.I),
     "경쟁", "경쟁이 심해질 수 있다는 내용입니다."),
    (re.compile(r"\b(regulation|regulatory|licens\w+|FAA|FDA|approval process|compliance)\b", re.I),
     "규제·인허가", "정부 승인이나 규제에 걸릴 수 있다는 내용입니다."),
    (re.compile(r"\b(litigation|lawsuit|legal proceedings|claims against us)\b", re.I),
     "소송", "소송에 휘말릴 수 있다는 내용입니다."),
    (re.compile(r"\b(cyber\w*|data breach|information security|ransomware)\b", re.I),
     "사이버 보안", "해킹·정보 유출 위험입니다."),
    (re.compile(r"\b(intellectual property|patents?|trade secrets?|infringe\w*)\b", re.I),
     "지식재산", "특허·기술 보호에 관한 내용입니다."),
    (re.compile(r"\b(key personnel|attract and retain|qualified employees|labor)\b", re.I),
     "인력", "핵심 인력을 잃거나 못 뽑을 수 있다는 내용입니다."),
    (re.compile(r"\b(launch failure|product defect|recall|quality issues|malfunction)\b", re.I),
     "제품 결함·사고", "제품이나 서비스가 실패할 수 있다는 내용입니다."),
    (re.compile(r"\b(foreign currency|exchange rate|currency fluctuation)\b", re.I),
     "환율", "환율이 실적에 영향을 준다는 내용입니다."),
    (re.compile(r"\b(interest rate|inflation|economic conditions|recession)\b", re.I),
     "경기·금리", "경기나 금리 변화에 영향을 받는다는 내용입니다."),
    (re.compile(r"\b(tariff|trade war|export control|sanction|geopolitical)\b", re.I),
     "무역·지정학", "관세·수출 규제·국제 정세에 관한 내용입니다."),
    (re.compile(r"\b(indebtedness|credit agreement|covenant|debt service)\b", re.I),
     "차입금", "빌린 돈과 그 조건에 관한 내용입니다."),
    (re.compile(r"\b(dilut\w+|additional shares|equity offering)\b", re.I),
     "희석", "주식이 늘어 내 지분 비중이 줄 수 있다는 내용입니다."),
    (re.compile(r"\b(internal control|material weakness|restat\w+)\b", re.I),
     "회계·내부통제", "재무 숫자의 신뢰도에 관한 내용입니다."),
    (re.compile(r"\b(climate|natural disaster|severe weather|pandemic|epidemic)\b", re.I),
     "재해·기후", "자연재해나 감염병 위험입니다."),
    (re.compile(r"\b(stock price|market price of our|volatil\w+)\b", re.I),
     "주가 변동", "주가가 크게 흔들릴 수 있다는 내용입니다."),
    (re.compile(r"\b(acquisition|acquisitions|integrate\w*)\b", re.I),
     "인수합병", "회사를 사거나 합치는 데 따르는 위험입니다."),
    (re.compile(r"\b(tax|taxation|deferred tax)\b", re.I),
     "세금", "세금 제도 변화에 관한 내용입니다."),
]

# 필러 문장(공시마다 똑같이 붙는 상투구). 요약할 값이 없다.
_BOILERPLATE = re.compile(
    r"^(?:in addition|furthermore|moreover|as a result|accordingly|see|refer to)\b", re.I
)
_LABEL = re.compile(r"^\[[^\]]+\]\s*")


@dataclass
class KoreanNote:
    """한 문장·문단에 대한 한글 설명."""

    line: str = ""              # 규칙으로 옮긴 한 줄 (없을 수 있음)
    topic: str = ""             # 무엇에 관한 것인가 (위험 요인용)
    meaning: str = ""           # 그 주제가 무슨 뜻인지
    machine: str = ""           # 기계 번역 (규칙으로 못 옮긴 경우)
    figures: list[str] = field(default_factory=list)   # 문장에서 뽑은 수치

    @property
    def has_line(self) -> bool:
        return bool(self.line)

    @property
    def empty(self) -> bool:
        return not (self.line or self.topic or self.machine)


def strip_label(text: str) -> str:
    """filing_text 가 붙인 '[변화]' 같은 앞머리를 뗀다."""
    return _LABEL.sub("", text or "").strip()


def figures_in(text: str) -> list[str]:
    """문장에 나온 금액과 퍼센트. 숫자는 절대 바꾸지 않는다."""
    found: list[str] = []
    for match in _MONEY.finditer(text):
        value = money(match.group(0))
        if value not in found:
            found.append(value)
    for match in re.finditer(r"\b\d+(?:\.\d+)?\s?%", text):
        value = match.group(0).replace(" ", "")
        if value not in found:
            found.append(value)
    return found[:6]


def translate_sentence(text: str) -> str:
    """정형 표현이면 한글 한 줄로. 아니면 빈 문자열."""
    sentence = strip_label(text)
    if not sentence or _BOILERPLATE.match(sentence):
        return ""

    for pattern, template in SENTENCE_RULES:
        match = pattern.search(sentence)
        if not match:
            continue
        line = template
        for index, group in enumerate(match.groups(), start=1):
            value = money(group.strip()) if group else ""
            value = _WORD_KO.get(value.lower(), value)
            line = line.replace(f"{{{index}}}", value)
        return line
    return ""


def topic_of(text: str) -> tuple[str, str]:
    """위험 문단이 무엇에 관한 것인지. (주제, 뜻) — 모르면 ('', '')."""
    sentence = strip_label(text)
    for pattern, topic, meaning in TOPIC_RULES:
        if pattern.search(sentence):
            return topic, meaning
    return "", ""


def note_for(text: str, kind: str = "sentence") -> KoreanNote:
    """문장 하나에 대한 한글 설명을 만든다.

    kind="risk" 면 주제 분류를 함께 붙인다(위험 문단은 길어서 통째로 옮기지 않는다).
    """
    note = KoreanNote(figures=figures_in(text))
    note.line = translate_sentence(text)
    if kind == "risk":
        note.topic, note.meaning = topic_of(text)
    return note


def range_ko(text: str | None) -> str:
    """'$230 million to $240 million' → '$230M ~ $240M'."""
    if not text:
        return ""
    cleaned = re.sub(r"\s*(?:to|through|–|-)\s*", " ~ ", text.strip(), flags=re.I)
    return money(cleaned)


def annotate(texts: list[str], kind: str = "sentence", translator=None,
             limit: int = 12) -> dict[str, KoreanNote]:
    """여러 문장에 한글을 붙인다. {원문: 설명}

    규칙이 먼저다. 규칙으로 옮긴 문장은 기계 번역을 부르지 않는다.
    위험 문단은 길어서 주제만으로는 부족하므로 기계 번역도 함께 시도한다.
    번역기가 없거나 막혀 있으면 규칙 결과만 돌아온다.
    """
    notes = {text: note_for(text, kind) for text in texts if text}

    if translator is None:
        return notes

    need = [
        text for text, note in notes.items()
        if not note.line or kind == "risk"
    ]
    for text, translated in (translator.translate_many(need, limit=limit) or {}).items():
        notes[text].machine = translated
    return notes


def guidance_line(metric: str | None, period: str | None, range_text: str | None) -> str:
    """가이던스 항목을 한글 한 줄로. 이미 뽑아둔 값만 쓴다."""
    what = {"매출": "매출", "EPS": "주당순이익(EPS)", "EBITDA": "EBITDA"}.get(metric or "", metric or "")
    when = period_ko(period)
    if not what and not range_text:
        return ""
    head = " ".join(b for b in (when, what) if b) or "다음 기간 실적"
    if range_text:
        return f"{head} 전망 {range_ko(range_text)}"
    return f"{head} 전망을 밝힘"


def period_ko(period: str | None) -> str:
    if not period:
        return ""
    text = period.lower()
    for english, korean in _QUARTER_KO.items():
        if english in text:
            return korean
    if "full year" in text or "fiscal year" in text or "annual" in text:
        return "연간"
    return period
