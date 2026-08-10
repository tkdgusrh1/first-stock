"""텔레그램 봇 API 전송."""

from __future__ import annotations

import html
import logging
import time

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: float = 20.0, dry_run: bool = False) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.dry_run = dry_run or not (token and chat_id)
        if self.dry_run and not (token and chat_id):
            log.warning("텔레그램 토큰/chat_id 가 없어 콘솔 출력 모드로 동작합니다.")

    def send(self, text: str, disable_preview: bool = True) -> bool:
        ok = True
        for chunk in split_message(text):
            ok = self._send_one(chunk, disable_preview) and ok
        return ok

    def _send_one(self, text: str, disable_preview: bool) -> bool:
        if self.dry_run:
            print("\n----- [텔레그램 미리보기] -----")
            print(strip_tags(text))
            print("-------------------------------\n")
            return True

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }
        delay = 2.0
        for attempt in range(1, 5):
            try:
                resp = requests.post(API.format(token=self.token, method="sendMessage"),
                                     json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    return True
                if resp.status_code == 429:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", delay)
                    log.warning("텔레그램 레이트리밋, %ss 대기", retry_after)
                    time.sleep(float(retry_after))
                    continue
                log.error("텔레그램 전송 실패 %s: %s", resp.status_code, resp.text[:300])
                if 400 <= resp.status_code < 500:
                    return False  # 잘못된 chat_id/포맷은 재시도해도 소용없다
            except requests.RequestException as exc:
                log.warning("텔레그램 전송 오류(%s/4): %s", attempt, exc)
            time.sleep(delay)
            delay *= 2
        return False

    def check(self) -> bool:
        """토큰/챗 설정이 살아있는지 확인."""
        if self.dry_run:
            return True
        try:
            resp = requests.get(API.format(token=self.token, method="getMe"), timeout=self.timeout)
            return resp.status_code == 200 and resp.json().get("ok", False)
        except requests.RequestException:
            return False


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def strip_tags(text: str) -> str:
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", text))


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """텔레그램 4096자 제한에 맞춰 줄 단위로 자른다."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        line_len = len(line) + 1
        if length + line_len > limit and current:
            chunks.append("\n".join(current))
            current, length = [], 0
        if line_len > limit:  # 한 줄이 너무 길면 강제로 자른다
            for i in range(0, len(line), limit):
                chunks.append(line[i : i + limit])
            continue
        current.append(line)
        length += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks
