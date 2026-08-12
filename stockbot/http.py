"""SEC 요청 규칙(User-Agent, 초당 요청 수)을 지키는 HTTP 세션."""

from __future__ import annotations

import logging
import threading
import time
import unicodedata

import requests

log = logging.getLogger(__name__)


class ForbiddenError(RuntimeError):
    """SEC 가 요청을 거부했다(403). 헤더 조합을 바꿔가며 재시도한 뒤에도 실패한 경우."""


def build_profiles(user_agent: str) -> dict[str, dict[str, str]]:
    """SEC 가 받아주는 헤더 조합은 환경에 따라 다르다. 후보를 순서대로 준비한다.

    - sec:     SEC 문서가 안내하는 조합 (연락처 UA + gzip)
    - minimal: 헤더를 최소한만. 부가 헤더가 WAF 를 건드리는 경우가 있다
    - browser: 봇 차단에 막힐 때. 브라우저 형식이지만 연락처를 함께 남긴다
    """
    contact = user_agent.split()[-1] if "@" in user_agent else user_agent
    return {
        "sec": {
            "User-Agent": user_agent,
            "Accept": "application/json, text/html, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        },
        "minimal": {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        },
        "browser": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 (contact: {contact})"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        },
    }


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
        self.profiles = build_profiles(self.user_agent)
        self.profile_name = "sec"
        self.session = requests.Session()
        self._apply_profile(self.profile_name)

    def _apply_profile(self, name: str) -> None:
        self.session.headers.clear()
        self.session.headers.update(self.profiles[name])
        self.profile_name = name

    def _retry_other_profiles(self, url: str, **kwargs):
        """403 이면 다른 헤더 조합으로 같은 주소를 다시 두드려본다.

        통하는 조합을 찾으면 그 뒤로는 계속 그걸 쓴다. SEC 의 봇 차단은
        네트워크 환경마다 반응이 달라서, 어느 게 통할지는 해봐야 안다.
        """
        for name in self.profiles:
            if name == self.profile_name:
                continue
            self.limiter.wait()
            try:
                resp = self.session.get(url, timeout=self.timeout, headers=self.profiles[name], **kwargs)
            except requests.RequestException:
                continue
            if resp.status_code != 403:
                log.info("헤더 조합 '%s' 로 전환합니다 (403 우회 성공).", name)
                self._apply_profile(name)
                return resp
        return None

    def get(self, url: str, timeout: float | None = None, **kwargs) -> requests.Response:
        delay = 2.0
        last_exc: Exception | None = None
        timeout = timeout or self.timeout
        for attempt in range(1, self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, timeout=timeout, **kwargs)
            except requests.RequestException as exc:  # 네트워크 오류
                last_exc = exc
                log.warning("GET 실패(%s/%s) %s: %s", attempt, self.max_retries, url, exc)
            else:
                if resp.status_code == 403 and "sec.gov" in url:
                    # SEC 봇 차단은 헤더 조합에 따라 반응이 다르다. 다른 조합을 시도해본다.
                    alternate = self._retry_other_profiles(url, **kwargs)
                    if alternate is not None:
                        return alternate
                    raise ForbiddenError(
                        "SEC가 접속을 거부했습니다 (403). 헤더 조합을 "
                        f"{len(self.profiles)}가지로 바꿔봤지만 모두 막혔습니다.\n"
                        f"  현재 User-Agent: {self.user_agent!r}\n"
                        "  아래를 차례로 확인해보세요.\n"
                        "   1) 브라우저에서 https://www.sec.gov/files/company_tickers.json 이 열리는지\n"
                        "      → 브라우저도 안 열리면 네트워크(공유기·백신·VPN·회사망) 문제입니다\n"
                        "   2) `python main.py doctor` 를 실행해 어디까지 되는지 확인\n"
                        "   3) VPN 을 켜고 있다면 끄고, 없다면 켜고 다시 시도"
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

    # --- POST (번역 API 용) ------------------------------------------------
    def _post(self, url: str, *, data=None, json_body=None, headers=None,
              timeout: float | None = None) -> str:
        """번역기들은 POST 를 쓴다. SEC 용 헤더·재시도 규칙과 섞이지 않게 따로 둔다.

        열쇠가 담긴 헤더를 세션에 남기지 않도록 요청마다만 붙인다.
        """
        merged = {"User-Agent": self.user_agent}
        merged.update(headers or {})
        self.limiter.wait()
        resp = self.session.post(
            url, data=data, json=json_body, headers=merged,
            timeout=timeout or self.timeout,
        )
        if resp.status_code >= 400:
            # 본문에 원인이 적혀 있는 경우가 많다(열쇠 오류·한도 초과 등)
            detail = " ".join(resp.text.split())[:160]
            raise RuntimeError(f"HTTP {resp.status_code} {detail}")
        return resp.text

    def post_form(self, url: str, form: dict, headers: dict | None = None,
                  timeout: float | None = None) -> str:
        return self._post(url, data=form, headers=headers, timeout=timeout)

    def post_json(self, url: str, body, headers: dict | None = None,
                  timeout: float | None = None) -> str:
        return self._post(url, json_body=body, headers=headers, timeout=timeout)
