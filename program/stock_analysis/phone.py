"""휴대폰에서 보기 — Tailscale(무료 사설망)로 내 PC 화면을 내 휴대폰에만 연다.

프로그램은 계속 127.0.0.1 에만 열린다. Tailscale 이 PC 안에서 https://<PC이름>.<망>.ts.net 을
받아 127.0.0.1:포트 로 넘겨준다(`tailscale serve`). 인터넷에는 열리지 않고(funnel 아님),
같은 Tailscale 계정으로 로그인한 기기만 닿는다. 그래도 한 번 더 — 넘겨받은 요청에 Tailscale 이
붙여주는 로그인 이름(Tailscale-User-Login)이 이 PC 주인과 같을 때만 받는다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

TIMEOUT = 20
_WINDOWS_PATHS = (r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe")
_ENABLE_LINK = re.compile(r"https://login\.tailscale\.com/\S+")


@dataclass
class Link:
    host: str = ""          # 예: my-pc.tail1234.ts.net
    owner: str = ""         # 이 PC 를 로그인한 Tailscale 계정(이메일 꼴)
    problem: str = ""       # 안 됐을 때 사람에게 보여줄 말
    enable_url: str = ""    # 처음 한 번 Tailscale 에서 HTTPS 를 허락해야 할 때 그 주소

    @property
    def url(self) -> str:
        return f"https://{self.host}/" if self.host and not self.problem else ""


def find() -> str:
    found = shutil.which("tailscale")
    if found:
        return found
    for path in _WINDOWS_PATHS:
        if os.path.exists(path):
            return path
    return ""


def _run(exe: str, *args: str) -> tuple[int, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT, creationflags=flags)
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    except subprocess.TimeoutExpired as exc:
        # 'HTTPS 를 허락하세요' 안내를 띄우고 기다리는 경우가 있다 — 그때까지 나온 글(양쪽 다)을 돌려준다
        parts = [p.decode("utf-8", "replace") if isinstance(p, bytes) else (p or "") for p in (exc.stdout, exc.stderr)]
        return -1, "".join(parts)
    except OSError as exc:
        return -1, str(exc)


def parse_status(text: str) -> Link:
    try:
        data = json.loads(text)
    except ValueError:
        return Link(problem="Tailscale 상태를 읽지 못했습니다.")
    if data.get("BackendState") != "Running":
        return Link(problem="Tailscale 에 로그인돼 있지 않습니다. 작업표시줄의 Tailscale 아이콘을 눌러 로그인하세요.")
    me = data.get("Self") or {}
    host = str(me.get("DNSName") or "").rstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
        return Link(problem="Tailscale 에서 이 PC 의 이름을 받지 못했습니다. 관리 화면에서 MagicDNS 를 켜 주세요.")
    user = (data.get("User") or {}).get(str(me.get("UserID"))) or {}
    owner = str(user.get("LoginName") or "").strip().lower()
    if me.get("Tags") or not owner or owner == "tagged-devices":
        # 태그로 등록된 PC 는 '주인 계정' 이 없다 — 휴대폰 요청을 누구와 맞춰볼지 몰라 모두 거절하게 된다
        return Link(problem="이 PC 가 Tailscale 에 태그로 등록돼 있어 주인 계정을 알 수 없습니다. "
                            "내 계정으로 로그인한 PC 에서만 켤 수 있습니다.")
    return Link(host=host, owner=owner)


def share(port: int) -> Link:
    """https://<PC>.ts.net → 127.0.0.1:port 를 잇는다. 이미 이어져 있으면 그대로 덮어쓴다."""
    exe = find()
    if not exe:
        return Link(problem="이 PC 에 Tailscale 이 없습니다. tailscale.com/download 에서 받아 설치·로그인하세요.")
    code, out = _run(exe, "status", "--json")
    if code != 0:
        return Link(problem="Tailscale 이 꺼져 있습니다. 시작 메뉴에서 Tailscale 을 켜고 로그인하세요.")
    link = parse_status(out)
    if link.problem:
        return link
    code, out = _run(exe, "serve", "--bg", f"http://127.0.0.1:{port}")
    if code != 0:
        enable = _ENABLE_LINK.search(out)
        if enable:
            link.enable_url = enable.group(0)
            link.problem = "Tailscale 에서 HTTPS 를 한 번 허락해야 합니다. 아래 주소를 열어 'Enable' 을 누른 뒤 다시 켜 주세요."
        else:
            link.problem = "Tailscale 연결을 만들지 못했습니다: " + " ".join(out.split())[:200]
        return link
    log.info("휴대폰 주소: https://%s/", link.host)
    return link


def unshare() -> None:
    exe = find()
    if exe:
        _run(exe, "serve", "--https=443", "off")
