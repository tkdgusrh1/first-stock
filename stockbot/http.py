"""SEC 요청 규칙(User-Agent, 초당 요청 수)을 지키는 HTTP 세션."""

from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger(__name__)

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
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
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
