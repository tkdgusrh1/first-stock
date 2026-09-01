"""바깥 명령 실행(powercfg, netsh, PowerShell)과 관리자 권한 확인.

레지스트리로 안 되는 것들 — 전원 계획, 백신 예외 — 은 결국 명령을 부르게 된다.
그 호출을 한 군데로 모아두면 검사할 때 가짜 실행기를 끼워 넣기 쉽다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

WINDOWS = sys.platform.startswith("win")

# 창을 띄우지 않고 부른다. 최적화 중에 검은 창이 계속 깜빡이면 고장 난 줄 안다.
_NO_WINDOW = 0x08000000 if WINDOWS else 0


@dataclass
class Result:
    ok: bool
    out: str = ""
    err: str = ""
    code: int = 0


class Shell:
    """진짜 명령 실행기."""

    def run(self, args: list[str], timeout: int = 60) -> Result:
        log.debug("실행: %s", " ".join(args))
        try:
            done = subprocess.run(
                args,
                capture_output=True,
                timeout=timeout,
                creationflags=_NO_WINDOW,
            )
        except FileNotFoundError:
            return Result(ok=False, err=f"{args[0]} 을(를) 찾지 못했습니다.", code=-1)
        except subprocess.TimeoutExpired:
            return Result(ok=False, err=f"{args[0]} 이(가) 응답하지 않습니다.", code=-1)
        except OSError as exc:
            return Result(ok=False, err=str(exc), code=-1)

        # 윈도우 명령들은 한국어 윈도우에서 cp949 로 뱉는다. 깨진 글자 하나 때문에
        # 최적화가 통째로 멈추면 안 되니 못 읽는 글자는 버리고 넘어간다.
        out = _decode(done.stdout)
        err = _decode(done.stderr)
        return Result(ok=done.returncode == 0, out=out, err=err, code=done.returncode)

    def powershell(self, script: str, timeout: int = 90) -> Result:
        return self.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=timeout,
        )


@dataclass
class FakeShell:
    """검사용. 부른 명령을 적어두고, 미리 정해둔 답을 돌려준다."""

    replies: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    default: Result = field(default_factory=lambda: Result(ok=True))

    def run(self, args: list[str], timeout: int = 60) -> Result:
        self.calls.append(list(args))
        joined = " ".join(args).lower()
        for needle, reply in self.replies.items():
            if needle.lower() in joined:
                return reply
        return self.default

    def powershell(self, script: str, timeout: int = 90) -> Result:
        return self.run(["powershell", "-Command", script], timeout=timeout)


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "cp949", "cp1252"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip()


def is_admin() -> bool:
    """관리자 권한으로 돌고 있는가.

    HKLM 을 건드리는 항목(네트워크·시스템 반응성 등)은 관리자가 아니면 실패한다.
    실패한 뒤에 알려주면 늦으니 화면에 먼저 띄우려고 확인한다.
    """
    if not WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:       # pragma: no cover - 윈도우 전용
        return False


def relaunch_as_admin(script: str) -> bool:
    """관리자 권한으로 자기 자신을 다시 띄운다. 띄웠으면 True."""
    if not WINDOWS:
        return False
    try:                    # pragma: no cover - 윈도우 전용
        import ctypes

        from pathlib import Path

        target = Path(script).resolve()
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{target}"', str(target.parent), 1
        )
        return rc > 32
    except Exception as exc:  # pragma: no cover - 윈도우 전용
        log.warning("관리자 권한으로 다시 띄우지 못했습니다: %s", exc)
        return False


def open_shell():
    return Shell() if WINDOWS else FakeShell()
