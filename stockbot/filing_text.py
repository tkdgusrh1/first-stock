"""분기·연간 보고서(10-Q / 10-K) 본문에서 필요한 부분을 뽑아낸다.

중요한 원칙: **요약문을 지어내지 않는다.**
회사가 쓴 문장을 그대로 발췌하고, 어느 보고서의 어느 항목에서 가져왔는지
출처를 함께 남긴다. 사람이 읽기 좋게 자르고 정리하는 것까지만 한다.

뽑는 곳:
  Item 1  Business             — 이 회사가 무엇을 하는가
  Item 1A Risk Factors         — 회사가 스스로 밝힌 위험
  Item 2/7 MD&A                — 실적이 왜 이렇게 나왔고 앞으로 어떻게 할 것인가
    · Results of Operations    — 실적 분석
    · Liquidity and Capital Resources — 현금 사정
    · Outlook / Guidance       — 전망
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

MAX_DOC_BYTES = 12 * 1024 * 1024        # 본문이 아주 큰 보고서 방어
_MIN_PARAGRAPH = 80                      # 이보다 짧은 조각은 제목·표 잔해로 본다


@dataclass
class Section:
    key: str
    title: str
    paragraphs: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


@dataclass
class FilingText:
    form: str
    filing_date: str
    period: str | None
    url: str
    sections: list[Section] = field(default_factory=list)
    company_words: list[str] = field(default_factory=list)   # 눈에 띄는 문장들

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)


# --------------------------------------------------------------------------
# HTML → 문단
# --------------------------------------------------------------------------
_BLOCK_END = re.compile(
    r"</(p|div|tr|li|h1|h2|h3|h4|h5|h6|table|br)\s*>|<br\s*/?>", re.IGNORECASE
)
_TAGS = re.compile(r"<[^>]+>")
_SCRIPTS = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_SPACES = re.compile(r"[ \t ​]+")
_MULTI_NEWLINE = re.compile(r"\n{2,}")


def html_to_paragraphs(raw: str) -> list[str]:
    """공시 HTML을 문단 목록으로. 표는 잔해가 많아 짧은 조각은 버린다."""
    text = _SCRIPTS.sub(" ", raw)
    text = _BLOCK_END.sub("\n", text)
    text = _TAGS.sub(" ", text)
    text = html.unescape(text)
    text = _SPACES.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n", text)

    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            out.append(line)
    return out


def _join_paragraphs(lines: list[str]) -> list[str]:
    """줄바꿈으로 잘린 문장을 문단으로 다시 붙인다."""
    merged: list[str] = []
    buffer = ""
    for line in lines:
        buffer = f"{buffer} {line}".strip() if buffer else line
        # 문장이 끝나고 충분히 길면 하나의 문단으로 확정
        if buffer.endswith((".", "!", "?", "”", '"')) and len(buffer) >= _MIN_PARAGRAPH:
            merged.append(buffer)
            buffer = ""
    if len(buffer) >= _MIN_PARAGRAPH:
        merged.append(buffer)
    return merged


# --------------------------------------------------------------------------
# 항목(Item) 찾기
# --------------------------------------------------------------------------
ITEM_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("business", "사업 개요 (Item 1. Business)",
     re.compile(r"^item\s*1\s*[.\-–:]?\s*business\b", re.IGNORECASE)),
    ("risk", "위험 요인 (Item 1A. Risk Factors)",
     re.compile(r"^item\s*1a\s*[.\-–:]?\s*risk\s+factors\b", re.IGNORECASE)),
    ("mdna", "경영진 논의 (MD&A)",
     re.compile(r"^item\s*[27]\s*[.\-–:]?\s*management'?s?\s+discussion", re.IGNORECASE)),
]

# MD&A 안에서 다시 나누는 소제목
SUBSECTIONS: list[tuple[str, str, re.Pattern]] = [
    ("results", "실적 분석 (Results of Operations)",
     re.compile(r"^results\s+of\s+operations\b", re.IGNORECASE)),
    ("liquidity", "현금 사정 (Liquidity and Capital Resources)",
     re.compile(r"^liquidity\s+and\s+capital\s+resources\b", re.IGNORECASE)),
    ("outlook", "전망 (Outlook)",
     re.compile(r"^(outlook|guidance|business\s+outlook|financial\s+outlook)\b", re.IGNORECASE)),
    ("overview", "개요 (Overview)",
     re.compile(r"^overview\b", re.IGNORECASE)),
]

# 다음 항목이 시작되면 현재 항목을 끊는다
_NEXT_ITEM = re.compile(r"^item\s*\d+[a-z]?\s*[.\-–:]", re.IGNORECASE)

# 목차(Table of Contents)의 항목 줄은 본문이 아니다
_TOC_LINE = re.compile(r"\.{4,}\s*\d+\s*$|^\s*\d+\s*$")


def extract_sections(paragraphs: list[str]) -> list[Section]:
    """문단 목록에서 Item 별로 잘라낸다."""
    starts: list[tuple[int, str, str]] = []
    for index, line in enumerate(paragraphs):
        if _TOC_LINE.search(line) or len(line) > 400:
            continue
        for key, title, pattern in ITEM_PATTERNS:
            if pattern.match(line):
                starts.append((index, key, title))
                break

    # 같은 항목이 여러 번(목차 + 본문) 잡히면 뒤쪽(본문)을 쓴다
    chosen: dict[str, tuple[int, str]] = {}
    for index, key, title in starts:
        chosen[key] = (index, title)

    sections: list[Section] = []
    for key, (start, title) in sorted(chosen.items(), key=lambda kv: kv[1][0]):
        body: list[str] = []
        for line in paragraphs[start + 1 :]:
            if _NEXT_ITEM.match(line) and len(line) < 400:
                break
            body.append(line)
        merged = _join_paragraphs(body)
        if merged:
            sections.append(Section(key=key, title=title, paragraphs=merged))
    return sections


def split_mdna(section: Section) -> list[Section]:
    """MD&A를 소제목별로 다시 나눈다. 못 나누면 통째로 돌려준다."""
    marks: list[tuple[int, str, str]] = []
    for index, para in enumerate(section.paragraphs):
        head = para[:120]
        for key, title, pattern in SUBSECTIONS:
            if pattern.match(head):
                marks.append((index, key, title))
                break
    if not marks:
        return [section]

    out: list[Section] = []
    for position, (index, key, title) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(section.paragraphs)
        body = list(section.paragraphs[index:end])
        if body:
            body[0] = _strip_heading(body[0], key)
            out.append(Section(key=f"mdna_{key}", title=title, paragraphs=[p for p in body if p]))
    return out or [section]


def _strip_heading(paragraph: str, key: str) -> str:
    """짧은 소제목이 본문 앞에 붙어버린 경우 떼어낸다. ('Outlook We expect…')"""
    for sub_key, _, pattern in SUBSECTIONS:
        if sub_key != key:
            continue
        match = pattern.match(paragraph)
        if match:
            rest = paragraph[match.end():].lstrip(" .:–-")
            # 제목만 있는 줄이면 통째로 버리고, 본문이 이어지면 그 부분만 남긴다
            return rest if len(rest) >= _MIN_PARAGRAPH else paragraph
    return paragraph


# --------------------------------------------------------------------------
# 눈에 띄는 문장 고르기
# --------------------------------------------------------------------------
NOTABLE_PATTERNS = [
    (re.compile(r"\b(increased|decreased|grew|declined|rose|fell)\b.{0,120}?\b\d", re.IGNORECASE), "변화"),
    (re.compile(r"\b(we expect|we anticipate|we plan|we intend|we will continue)\b", re.IGNORECASE), "계획"),
    (re.compile(r"\b(guidance|outlook)\b", re.IGNORECASE), "전망"),
    (re.compile(r"\b(backlog|order book|contract award)\b", re.IGNORECASE), "수주"),
    (re.compile(r"\b(substantial doubt|going concern)\b", re.IGNORECASE), "존속 우려"),
    (re.compile(r"\b(sufficient|fund our operations)\b.{0,80}?\b(months|year)", re.IGNORECASE), "자금"),
]


def notable_sentences(sections: list[Section], limit: int = 12) -> list[str]:
    """숫자·계획·전망이 담긴 문장만 골라낸다. (원문 그대로)"""
    found: list[str] = []
    seen: set[str] = set()
    for section in sections:
        for para in section.paragraphs:
            for sentence in re.split(r"(?<=[.!?])\s+", para):
                sentence = sentence.strip()
                if not (60 <= len(sentence) <= 420):
                    continue
                for pattern, label in NOTABLE_PATTERNS:
                    if pattern.search(sentence):
                        marker = sentence[:60].lower()
                        if marker not in seen:
                            seen.add(marker)
                            found.append(f"[{label}] {sentence}")
                        break
                if len(found) >= limit:
                    return found
    return found


# --------------------------------------------------------------------------
# 조립
# --------------------------------------------------------------------------
def parse_filing(raw_html: str, form: str, filing_date: str, url: str, period: str | None = None) -> FilingText:
    paragraphs = html_to_paragraphs(raw_html[:MAX_DOC_BYTES])
    sections = extract_sections(paragraphs)

    expanded: list[Section] = []
    for section in sections:
        if section.key == "mdna":
            expanded.extend(split_mdna(section))
        else:
            expanded.append(section)

    return FilingText(
        form=form,
        filing_date=filing_date,
        period=period,
        url=url,
        sections=expanded,
        company_words=notable_sentences(expanded),
    )


def fetch_filing_text(http, filing) -> FilingText | None:
    """공시 원문을 받아 본문을 뽑는다. 실패하면 None."""
    try:
        raw = http.get_text(filing.doc_url, timeout=90)
    except Exception as exc:
        log.warning("보고서 원문을 받지 못했습니다 (%s): %s", filing.accession, exc)
        return None

    try:
        parsed = parse_filing(
            raw, filing.form, filing.filing_date, filing.doc_url, filing.report_date
        )
    except Exception as exc:
        log.warning("보고서 본문 해석 실패 (%s): %s", filing.accession, exc)
        return None

    if not parsed.sections:
        log.info("보고서에서 표준 항목을 찾지 못했습니다: %s", filing.doc_url)
    return parsed
