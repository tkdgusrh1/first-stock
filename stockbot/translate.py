"""규칙으로 못 옮긴 문장을 기계 번역으로 채운다.

원칙 두 가지.
  1) **원문은 절대 지우지 않는다.** 번역은 원문 위에 얹는 것이고,
     화면에는 늘 '기계 번역' 이라고 밝힌다. 기계 번역은 틀릴 수 있다.
  2) 실패해도 화면은 그대로 돌아간다. 번역이 안 되면 원문만 보인다.

번역문은 바뀌지 않으므로 디스크에 저장해두고 두 번 다시 받지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)

GOOGLE_URL = (
    "https://translate.googleapis.com/translate_a/single"
    "?client=gtx&sl=en&tl={target}&dt=t&q={text}"
)

MAX_CHUNK = 1200        # 한 번에 보낼 글자 수 (너무 길면 잘려서 온다)
MAX_TEXT = 4000         # 이보다 긴 문단은 앞부분만 옮긴다
LABEL = "기계 번역"


def _key(text: str, target: str) -> str:
    return hashlib.sha1(f"{target}:{text}".encode("utf-8")).hexdigest()[:20]


def _chunks(text: str) -> list[str]:
    """문장 경계에서 끊는다. 문장 중간에서 자르면 번역이 망가진다."""
    if len(text) <= MAX_CHUNK:
        return [text]
    out, buffer = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(buffer) + len(sentence) + 1 > MAX_CHUNK and buffer:
            out.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}".strip()
    if buffer:
        out.append(buffer)
    return out


def parse_google(payload: str) -> str:
    """구글 응답은 중첩 배열이다. 번역된 조각만 이어 붙인다."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return ""
    pieces = []
    for row in data[0]:
        if isinstance(row, list) and row and isinstance(row[0], str):
            pieces.append(row[0])
    return "".join(pieces).strip()


class Translator:
    """영어 → 한국어. API 키가 필요 없는 공개 엔드포인트를 쓴다."""

    def __init__(self, http, cache_dir: str | Path = ".cache", enabled: bool = True,
                 target: str = "ko") -> None:
        self.http = http
        self.enabled = enabled
        self.target = target
        self.cache_dir = Path(cache_dir) / "translations"
        self._lock = threading.Lock()
        self._memory: dict[str, str] = {}
        self._broken = False        # 한 번 크게 실패하면 그 실행에서는 더 두드리지 않는다

    # --- 저장해둔 것 ------------------------------------------------------
    def _cached(self, key: str) -> str | None:
        if key in self._memory:
            return self._memory[key]
        path = self.cache_dir / f"{key}.txt"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                self._memory[key] = text
                return text
            except OSError:
                return None
        return None

    def _store(self, key: str, text: str) -> None:
        self._memory[key] = text
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.txt").write_text(text, encoding="utf-8")
        except OSError as exc:
            log.debug("번역 캐시 저장 실패: %s", exc)

    # --- 번역 -------------------------------------------------------------
    def translate(self, text: str) -> str:
        """번역문 또는 빈 문자열. 절대 예외를 올리지 않는다."""
        text = (text or "").strip()
        if not text or not self.enabled or self._broken:
            return ""
        if not re.search(r"[A-Za-z]{3}", text):
            return ""                       # 이미 한글이거나 숫자뿐
        text = text[:MAX_TEXT]

        key = _key(text, self.target)
        cached = self._cached(key)
        if cached is not None:
            return cached

        pieces = []
        for chunk in _chunks(text):
            translated = self._fetch(chunk)
            if not translated:
                return ""                   # 일부만 번역된 글은 내보내지 않는다
            pieces.append(translated)

        result = " ".join(pieces).strip()
        if result:
            self._store(key, result)
        return result

    def _fetch(self, chunk: str) -> str:
        url = GOOGLE_URL.format(target=self.target, text=quote(chunk))
        try:
            payload = self.http.get_text(url, timeout=20)
        except Exception as exc:
            log.info("번역 실패(원문은 그대로 보입니다): %s", exc)
            self._broken = True
            return ""
        return parse_google(payload)

    def translate_many(self, texts: list[str], limit: int = 12) -> dict[str, str]:
        """여러 문장을 한 번에. 이미 받아둔 것은 네트워크를 쓰지 않는다."""
        out: dict[str, str] = {}
        if not self.enabled:
            return out
        with self._lock:
            for text in texts[:limit]:
                result = self.translate(text)
                if result:
                    out[text] = result
        return out
