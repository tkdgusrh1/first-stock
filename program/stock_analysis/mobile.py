"""휴대폰 앱(APK) 안에서 켤 때.

안드로이드 쪽(MainActivity)이 start(앱 전용 폴더) 를 부르고, 받은 포트를 화면(WebView)에 띄운다.
PC 판과 같은 코드가 그대로 돈다. 다른 점만 여기 모은다:
  - 설정·기록·열쇠는 앱 전용 저장 공간에 둔다. 다른 앱은 못 읽고, 앱을 지우면 같이 지워진다.
  - 업데이트는 git 이 아니라 새 APK 를 받아 덮어 설치한다(GitHub 릴리스).
  - 끄기 단추·휴대폰 연결(Tailscale)처럼 PC 에서만 뜻이 있는 기능은 숨긴다.
  - 앱을 닫으면 감시도 멈춘다. 텔레그램 알림은 PC 판이 보낸다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

ENV = "FIRST_STOCK_MOBILE"
REPO = "tkdgusrh1/first-stock"
APK_URL = f"https://github.com/{REPO}/releases/latest/download/FirstStock.apk"
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
TAG_PREFIX = "android-v"
PC_ONLY_ACTIONS = frozenset({"update", "quit", "phone_on", "phone_off"})

_lock = threading.Lock()
_port: int | None = None


def active() -> bool:
    return os.environ.get(ENV) == "1"


def start(home: str) -> int:
    """프로그램을 켜고 화면 포트를 돌려준다. 앱을 다시 열어도 한 번만 켠다."""
    global _port
    with _lock:
        if _port:
            return _port
        from . import secrets
        from .app import Bot
        from .config import load_config
        from .dashboard import start_dashboard
        from .setup_wizard import _create as create_config

        root = Path(home)
        root.mkdir(parents=True, exist_ok=True)
        os.environ[ENV] = "1"
        os.environ[secrets.HOME_ENV] = str(root / "keys")
        os.chdir(root)                       # state.json·quant.json·캐시가 이 폴더에 생긴다
        _logging()

        config_path = root / "config.yml"
        if not config_path.exists():
            create_config(config_path)
        bot = Bot(load_config(str(config_path)))
        server = start_dashboard(bot, bot.config.dashboard_port, open_browser=False)
        bot.dashboard_server = server
        threading.Thread(target=bot.run_forever, kwargs={"dashboard": False},
                         daemon=True, name="watch").start()
        _port = server.server_address[1]
        log.info("휴대폰 앱: 화면 포트 %s", _port)
        return _port


def _logging() -> None:
    from .secrets import RedactFilter

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    for handler in logging.getLogger().handlers:      # 안드로이드 기록(logcat)에 열쇠가 남지 않게
        handler.addFilter(RedactFilter())


def parse_release(payload: dict) -> str | None:
    match = re.fullmatch(re.escape(TAG_PREFIX) + r"(\d+(?:\.\d+)*)", str(payload.get("tag_name") or ""))
    return match.group(1) if match else None


def latest_apk(timeout: float = 15.0) -> str | None:
    """GitHub 에 올라간 가장 새 APK 의 버전. 확인 못 하면 None."""
    request = urllib.request.Request(RELEASE_API, headers={"Accept": "application/vnd.github+json",
                                                           "User-Agent": "first-stock-android"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return parse_release(json.loads(resp.read().decode("utf-8")))
    except Exception as exc:
        log.debug("새 APK 확인 실패: %s", exc)
        return None
