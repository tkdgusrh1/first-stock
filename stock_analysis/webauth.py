"""대시보드 로그인 — 아이디와 비밀번호.

화면은 원래 내 컴퓨터에서만 열린다(127.0.0.1). 그래도 잠그는 이유가 있다.
  · 한 대의 PC 를 여럿이 쓰면 브라우저만 열어도 보유 종목·매수가가 다 보인다
  · 잠깐 자리를 비웠을 때 화면이 그대로 떠 있다

여기서 지키는 것.
  · **비밀번호를 그대로 저장하지 않는다.** scrypt 로 해시만 남긴다.
    파일을 열어봐도 비밀번호를 되돌릴 수 없다.
  · 로그인하면 무작위 세션 열쇠를 쿠키로 준다. 비밀번호는 다시 오가지 않는다.
  · 연속으로 틀리면 잠깐 잠근다 (자동으로 계속 찔러보는 것 방지).
  · 비밀번호를 잊으면 auth.json 을 지우면 된다 — 내 컴퓨터의 내 파일이니까.
    (그래서 이 잠금은 '남이 내 PC 를 원격으로 뚫는 것' 을 막는 장치가 아니다.
     같은 화면을 옆사람이 무심코 보는 것을 막는 장치다.)
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# scrypt 값. 숫자를 올리면 더 느려지고 더 안전하다.
# 여기서는 로그인 한 번에 0.1초 안쪽이 되도록 잡았다.
SCRYPT_N, SCRYPT_R, SCRYPT_P, DK_LEN = 2 ** 14, 8, 1, 32

MIN_USER = 2
MIN_PASSWORD = 6

MAX_TRIES = 5           # 이만큼 틀리면
LOCK_SECONDS = 60.0     # 이만큼 잠근다

SESSION_HOURS = 24 * 14     # 한 번 로그인하면 2주


@dataclass(frozen=True)
class Record:
    user: str
    salt: str
    digest: str

    def matches(self, user: str, password: str) -> bool:
        if user.strip().lower() != self.user.lower():
            return False
        return secrets.compare_digest(_hash(password, self.salt), self.digest)


def _hash(password: str, salt_hex: str) -> str:
    raw = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt_hex),
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN,
    )
    return raw.hex()


def check_new(user: str, password: str, again: str) -> str:
    """새 아이디·비밀번호가 쓸 만한지. 문제가 없으면 빈 문자열."""
    if len(user.strip()) < MIN_USER:
        return f"아이디는 {MIN_USER}글자 이상이어야 합니다."
    if len(password) < MIN_PASSWORD:
        return f"비밀번호는 {MIN_PASSWORD}글자 이상이어야 합니다."
    if password != again:
        return "비밀번호 확인이 일치하지 않습니다."
    if password.strip().lower() == user.strip().lower():
        return "아이디와 같은 비밀번호는 쓸 수 없습니다."
    return ""


class Auth:
    """로그인 상태를 들고 있는 자리. 파일 하나 + 메모리 세션."""

    def __init__(self, path: str | Path, session_hours: float = SESSION_HOURS) -> None:
        self.path = Path(path)
        self.session_seconds = session_hours * 3600
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}      # 열쇠 → 만료 시각
        self._fails = 0
        self._locked_until = 0.0
        self._record = self._load()

    # --- 계정 -----------------------------------------------------------
    @property
    def configured(self) -> bool:
        return self._record is not None

    @property
    def user(self) -> str:
        return self._record.user if self._record else ""

    def create(self, user: str, password: str, again: str) -> str:
        """첫 비밀번호를 정한다. 문제가 있으면 그 이유를 돌려준다."""
        if self.configured:
            return "이미 계정이 있습니다."
        problem = check_new(user, password, again)
        if problem:
            return problem
        salt = secrets.token_bytes(16).hex()
        self._record = Record(user=user.strip(), salt=salt, digest=_hash(password, salt))
        self._save()
        return ""

    def change_password(self, current: str, password: str, again: str) -> str:
        if not self._record:
            return "아직 계정이 없습니다."
        if not self._record.matches(self._record.user, current):
            return "지금 비밀번호가 맞지 않습니다."
        problem = check_new(self._record.user, password, again)
        if problem:
            return problem
        salt = secrets.token_bytes(16).hex()
        self._record = Record(user=self._record.user, salt=salt, digest=_hash(password, salt))
        self._save()
        with self._lock:
            self._sessions.clear()      # 비밀번호를 바꾸면 열려 있던 곳은 모두 끊는다
        return ""

    # --- 로그인 ---------------------------------------------------------
    def locked_for(self) -> int:
        """앞으로 몇 초 더 잠겨 있는지. 0이면 안 잠겨 있다."""
        return max(0, int(self._locked_until - time.monotonic()))

    def login(self, user: str, password: str) -> str:
        """맞으면 세션 열쇠를, 틀리면 빈 문자열을 돌려준다."""
        if self.locked_for() or not self._record:
            return ""
        if not self._record.matches(user, password):
            self._fails += 1
            if self._fails >= MAX_TRIES:
                self._locked_until = time.monotonic() + LOCK_SECONDS
                self._fails = 0
                log.warning("로그인 %d회 실패로 %d초 동안 잠급니다.", MAX_TRIES, int(LOCK_SECONDS))
            return ""

        self._fails = 0
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = time.time() + self.session_seconds
            self._sweep()
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expires = self._sessions.get(token)
            if expires is None:
                return False
            if expires < time.time():
                self._sessions.pop(token, None)
                return False
            return True

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def _sweep(self) -> None:
        now = time.time()
        for token in [t for t, exp in self._sessions.items() if exp < now]:
            self._sessions.pop(token, None)

    # --- 파일 -----------------------------------------------------------
    def _save(self) -> None:
        if not self._record:
            return
        payload = {
            "user": self._record.user,
            "salt": self._record.salt,
            "hash": self._record.digest,
            "algorithm": f"scrypt n={SCRYPT_N} r={SCRYPT_R} p={SCRYPT_P}",
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            try:
                self.path.chmod(0o600)      # 윈도우에서는 조용히 무시된다
            except OSError:
                pass
        except OSError as exc:
            log.warning("로그인 정보를 저장하지 못했습니다: %s", exc)

    def _load(self) -> Record | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return Record(user=str(payload["user"]), salt=str(payload["salt"]),
                          digest=str(payload["hash"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None
