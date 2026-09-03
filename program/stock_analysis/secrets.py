"""열쇠(인증키·토큰)를 **프로그램 폴더 바깥**에 보관한다.

지금까지는 열쇠가 config.yml 안에 있었다. 그런데 그 파일은 program 폴더
안에 있어서, 폴더를 지우고 새로 받으면 열쇠도 같이 사라진다. 그러면
받아둔 열쇠를 매번 다시 찾아 넣어야 한다. 실제로 그 일이 반복됐다.

그래서 열쇠는 사용자 폴더에 따로 둔다.

    윈도우  C:\\Users\\이름\\.first-stock\\keys.json
    맥·리눅스  ~/.first-stock/keys.json

프로그램 폴더를 통째로 지워도 여기는 남는다. 한 번 넣으면 끝이다.

찾는 순서는 **환경변수 → 프로그램 폴더의 config.yml → 여기**다.
config.yml 을 먼저 보는 이유는, 거기 적어둔 사람의 설정을 이 파일이
말없이 덮어쓰면 안 되기 때문이다.

표준 라이브러리만 쓴다. updater 는 PyYAML 이 깔리기 전에도 돌아야 한다.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

FOLDER = ".first-stock"
FILE = "keys.json"
HOME_ENV = "FIRST_STOCK_HOME"      # 보관 자리를 옮기고 싶을 때 (시험에도 쓴다)

# 이 이름들만 다룬다. 아무 값이나 받아 적지 않는다.
KNOWN = {
    "dart_api_key": "DART 인증키",
    "github_token": "GitHub 토큰",
    "telegram_token": "텔레그램 봇 토큰",
    "telegram_chat_id": "텔레그램 대화방 번호",
}


def home() -> Path:
    """열쇠를 두는 폴더. 사용자 폴더 아래라 프로그램을 지워도 남는다."""
    moved = os.environ.get(HOME_ENV, "").strip()
    if moved:
        return Path(moved)
    return Path(os.path.expanduser("~")) / FOLDER


def path() -> Path:
    return home() / FILE


def load() -> dict[str, str]:
    """저장해둔 열쇠들. 파일이 없거나 깨졌으면 빈 것."""
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if k in KNOWN and v}


def get(name: str) -> str:
    return load().get(name, "")


def save(name: str, value: str) -> Path | None:
    """열쇠 하나를 저장한다. 저장한 자리를 돌려준다 (실패하면 None).

    값이 비면 그 항목을 지운다 — 빈 문자열이 열쇠로 남지 않게.
    """
    if name not in KNOWN:
        return None
    found = load()
    value = str(value or "").strip()
    if value:
        found[name] = value
    else:
        found.pop(name, None)

    target = path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
        # 남이 읽지 못하게. 윈도우에는 이 개념이 없어 조용히 넘어간다.
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return target
    except OSError as exc:
        log.warning("열쇠를 저장하지 못했습니다: %s", exc)
        return None


def masked(value: str) -> str:
    """열쇠를 화면에 띄울 때 쓰는 표시. 값 자체는 절대 내보내지 않는다.

    화면을 캡처해 남에게 보여주는 일이 흔하다. 그래서 길이와 앞뒤 두 글자만
    보여준다 — 넣은 값이 맞는지 확인하기엔 충분하고, 훔쳐 쓰기엔 모자란다.
    """
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) <= 6:
        return f"{len(value)}자 (짧음)"
    return f"{len(value)}자, {value[:2]}…{value[-2:]}"


def keep(name: str, value: str) -> bool:
    """config.yml 에만 있던 열쇠를 보관함에도 한 벌 옮겨둔다.

    이미 넣어둔 사람이 다음에 폴더를 지웠을 때 또 잃어버리지 않게 하려는
    것이다. 이미 보관함에 있으면 건드리지 않는다 — 덮어쓸 이유가 없다.
    """
    value = str(value or "").strip()
    if not value or name not in KNOWN or get(name):
        return False
    if save(name, value) is None:
        return False
    log.info("%s 를 %s 에도 보관했습니다. 폴더를 지워도 남습니다.", KNOWN[name], path())
    return True


def find(name: str, from_config: str = "", env: tuple[str, ...] = ()) -> tuple[str, str]:
    """열쇠 하나를 찾는다. (값, 어디서 왔는지)

    환경변수 → config.yml → 사용자 폴더 순으로 본다. 어디서 왔는지를 함께
    돌려주는 이유는, '넣었는데 안 된다' 를 진단할 때 그게 전부이기 때문이다.
    """
    for variable in env:
        value = os.environ.get(variable, "").strip()
        if value:
            return value, f"환경변수 {variable}"
    if str(from_config or "").strip():
        return str(from_config).strip(), "config.yml"
    value = get(name)
    if value:
        return value, str(path())
    return "", ""


__all__ = ["FOLDER", "FILE", "HOME_ENV", "KNOWN",
           "home", "path", "load", "get", "save", "masked", "keep", "find"]
