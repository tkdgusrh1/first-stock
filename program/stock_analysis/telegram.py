"""텔레그램 봇 API 전송."""

from __future__ import annotations

import html
import logging
import sys
import time

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096


def show(text: str) -> None:
    """콘솔·로그로 내보낸다. 글자 하나 때문에 알림이 죽지 않게.

    창 없이 돌 때 표준 출력은 로그 파일이고, 윈도우에서 그 인코딩은 cp949 다.
    거기에 '🚨' 를 쓰면 UnicodeEncodeError 가 나면서 속보 확인 전체가 실패했다.
    알림 문구를 못 찍는 것보다 알림이 안 가는 쪽이 훨씬 나쁘다.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = text.encode(encoding, "replace").decode(encoding, "replace")
        try:
            print(safe)
        except (UnicodeEncodeError, OSError, ValueError):
            log.debug("콘솔에 쓰지 못했습니다.")
    except (OSError, ValueError):
        log.debug("콘솔에 쓰지 못했습니다.")


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
            show("\n----- [텔레그램 미리보기] -----")
            show(strip_tags(text))
            show("-------------------------------\n")
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

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict]:
        """텔레그램 롱폴링으로 새 메시지를 받아온다. 명령 처리용."""
        if self.dry_run:
            return []
        params: dict = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(
                API.format(token=self.token, method="getUpdates"),
                params=params,
                timeout=timeout + 10,
            )
        except requests.RequestException as exc:
            log.warning("getUpdates 실패: %s", exc)
            return []
        if resp.status_code != 200:
            log.warning("getUpdates 응답 %s: %s", resp.status_code, resp.text[:200])
            return []
        return resp.json().get("result", []) or []

    def reply(self, chat_id: str | int, text: str) -> bool:
        """특정 대화방으로 답장 (명령을 보낸 사람에게)."""
        if self.dry_run:
            show(f"\n----- [답장 → {chat_id}] -----\n{strip_tags(text)}\n----------------\n")
            return True
        original, self.chat_id = self.chat_id, str(chat_id)
        try:
            return self.send(text)
        finally:
            self.chat_id = original

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
