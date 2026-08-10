"""SEC 요청 규칙(User-Agent, 초당 요청 수)을 지키는 HTTP 세션."""

from __future__ import annotations

import logging
import threading
import time
import unicodedata

import requests

log = logging.getLogger(__name__)


class ForbiddenError(RuntimeError):
    """SEC 가 요청을 거부했다. 보통 User-Agent 문제라 따로 구분한다."""


def sanitize_user_agent(value: str) -> str:
    """User-Agent 를 ASCII 로 정리한다.

    HTTP 헤더에 한글을 넣으면 바이트가 깨져 SEC 가 403 으로 막는다.
    설정 마법사에서 이름을 한글로 적는 경우가 흔해서 여기서 정리한다.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    cleaned = " ".join(text.encode("ascii", "ignore").decode("ascii").split())

    email = next((word for word in cleaned.split() if "@" in word and "." in word), "")
    name = " ".join(word for word in cleaned.split() if word != email).strip()

    if not email:
        # 이메일까지 날아갔다면 원문에서 다시 찾아본다 (한글 이름 + 영문 이메일 조합)
        import re

        found = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", str(value or ""))
        email = found.group(0) if found else ""

    if not email:
        return cleaned or "first-stock bot"
    if not name:
        name = "first-stock bot"      # 한글 이름이 통째로 빠진 경우
    return f"{name} {email}"

# SEC는 초당 10회 이하를 요구한다. 여유를 두고 8회/초로 제한한다.
_SEC_MIN_INTERVAL = 0.125


class RateLimiter:
    """호출 간 최소 간격을 보장하는 스레드 안전 리미터."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last = time.monotonic()


class HttpClient:
    """재시도 + 레이트리밋이 붙은 얇은 requests 래퍼."""

    def __init__(
        self,
        user_agent: str,
        min_interval: float = _SEC_MIN_INTERVAL,
        timeout: float = 20.0,
        max_retries: int = 4,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = RateLimiter(min_interval)
        self.user_agent = sanitize_user_agent(user_agent)
        self.session = requests.Session()
        # SEC 는 연락처가 담긴 User-Agent 와 gzip 수용을 요구한다.
        # Accept 계열이 비어 있으면 WAF 가 403 으로 막는 경우가 있어 같이 채운다.
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/html, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Accept-Encoding": "gzip, deflate",
            }
        )

    def get(self, url: str, **kwargs) -> requests.Response:
        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:  # 네트워크 오류
                last_exc = exc
                log.warning("GET 실패(%s/%s) %s: %s", attempt, self.max_retries, url, exc)
            else:
                if resp.status_code == 403 and "sec.gov" in url:
                    raise ForbiddenError(
                        "SEC가 접속을 거부했습니다 (403).\n"
                        f"  현재 User-Agent: {self.user_agent!r}\n"
                        "  SEC는 '영문 이름 + 이메일' 형식의 연락처를 요구합니다.\n"
                        "  config.yml 의 user_agent 를 영문으로 고쳐주세요. 예: \"Gildong Hong hong@gmail.com\""
                    )
                # 429/5xx는 재시도, 그 외는 그대로 반환
                if resp.status_code < 500 and resp.status_code != 429:
                    return resp
                last_exc = requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                log.warning("GET %s -> %s (재시도 %s/%s)", url, resp.status_code, attempt, self.max_retries)
            if attempt < self.max_retries:
                time.sleep(delay)
                delay *= 2
        raise RuntimeError(f"요청 실패: {url}") from last_exc

    def get_json(self, url: str, **kwargs):
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def get_text(self, url: str, **kwargs) -> str:
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.text
