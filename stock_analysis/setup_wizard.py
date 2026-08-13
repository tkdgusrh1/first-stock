"""처음 실행할 때 물어보면서 config.yml 을 만들어 준다.

터미널 명령을 몰라도 시작 스크립트를 더블클릭하면 이 순서로 진행된다.

여기서 지키는 것이 하나 있다. **잘못 넣은 값은 다음 실행 때 다시 물어본다.**
설정 파일이 있다는 이유만으로 그냥 넘어가면, 오타 하나 때문에 프로그램이
영영 안 도는 상태가 된다 (SEC 연락처가 대표적이다 — 이메일이 아니면 SEC 가
접속을 막아서 화면이 통째로 빈 채로 돈다).

고칠 때는 **문제가 된 항목만** 다시 묻고, 나머지 설정과 주석은 그대로 둔다.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests
import yaml

from .http import find_email, valid_email

TEMPLATE = """# 이 파일은 처음 실행 때 자동으로 만들어졌습니다. 언제든 직접 고쳐도 됩니다.
# 저장하면 봇이 재시작 없이 다시 읽습니다.

user_agent: "{user_agent}"
telegram_token: "{token}"
telegram_chat_id: "{chat_id}"

forms: ["8-K", "4"]          # 8-K=수시공시, 4=내부자 거래
poll_interval_sec: 900       # 15분마다 확인
lookback_days: 3
timezone: "Asia/Seoul"

daily_brief_time: "08:00"    # 매일 아침 브리핑 (끄려면 null)
econ_min_importance: 2
econ_include_weekly: false
metrics_in_brief: true
earnings_reminder_days: [7, 1, 0]

telegram_commands: true
overrides_path: "watchlist.local.yml"

dashboard:
  enabled: true
  port: 8765
  open_browser: true
  login: true          # 화면을 아이디·비밀번호로 잠급니다 (끄려면 false)

watchlist:
{watchlist}
"""


class WizardAborted(Exception):
    """키보드 입력을 받을 수 없는 상태 (파이프 실행, Ctrl+C 등)."""


def prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        # 입력을 못 받는데 계속 물어보면 무한 루프가 된다. 여기서 끊는다.
        raise WizardAborted() from None
    return answer or default


# --------------------------------------------------------------------------
# 설정 파일 점검
# --------------------------------------------------------------------------
def find_problems(config_path: Path) -> list[str]:
    """지금 설정으로 프로그램이 제대로 돌 수 있는지 본다.

    돌려주는 값은 다시 물어봐야 할 항목 이름이다. 비어 있으면 문제 없음.
    """
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ["contact", "watchlist"]
    if not isinstance(raw, dict):
        return ["contact", "watchlist"]

    bad: list[str] = []
    if not find_email(raw.get("user_agent") or ""):
        bad.append("contact")
    if not (raw.get("watchlist") or []):
        bad.append("watchlist")
    return bad


PROBLEM_LABEL = {
    "contact": "SEC 연락처(이름·이메일)",
    "telegram": "텔레그램 알림",
    "watchlist": "감시할 종목",
}

# 프로그램이 못 도는 값들. 텔레그램은 없어도 화면으로 볼 수 있어 여기 없다.
REQUIRED = ["contact", "watchlist"]


def set_scalar(text: str, key: str, value: str) -> str:
    """`key: 값` 한 줄만 갈아끼운다. 주석과 나머지 설정은 건드리지 않는다."""
    line = f'{key}: "{value}"'
    pattern = re.compile(rf"^{re.escape(key)}\s*:.*$", re.M)
    if pattern.search(text):
        return pattern.sub(lambda _: line, text, count=1)
    return text.rstrip("\n") + "\n" + line + "\n"


def set_watchlist(text: str, tickers: list[str]) -> str:
    """watchlist 블록만 새 목록으로 바꾼다."""
    block = "watchlist:\n" + "\n".join(f"  - ticker: {t}" for t in tickers) + "\n"
    pattern = re.compile(r"^watchlist\s*:.*(?:\n[ \t]+\S.*)*", re.M)
    if pattern.search(text):
        return pattern.sub(lambda _: block.rstrip("\n"), text, count=1)
    return text.rstrip("\n") + "\n\n" + block


# --------------------------------------------------------------------------
# 물어보기
# --------------------------------------------------------------------------
def ask_contact(current: str = "") -> str:
    print("SEC 공시 서버가 연락처를 요구합니다.")
    print("  이름과 이메일이 SEC 서버에만 전달됩니다. 아무 데도 공개되지 않아요.")
    print("  ※ 이름은 영문으로, 이메일은 실제로 쓰는 주소 형식이어야 합니다.")
    print("     이메일이 형식에 안 맞으면 SEC 가 접속을 막아서 아무것도 못 받습니다.")

    parts = str(current or "").split()
    now_email = find_email(current)
    now_name = " ".join(w for w in parts if now_email not in w).strip()

    default_name = now_name if now_name and now_name.isascii() else "Investor"
    name = prompt("  이름 (영문)", default_name)
    if not name.isascii():
        print("  → 한글은 SEC 가 받지 않아서 'Investor' 로 대체합니다.")
        name = "Investor"

    email = ""
    while not valid_email(email):
        email = prompt("  이메일 (예: hong@gmail.com)", now_email)
        if not valid_email(email):
            print("  → 이메일 형식이 아닙니다. 'ID@도메인.com' 처럼 빈칸 없이 입력해주세요.")
    return f"{name} {email}"


def ask_telegram(token: str = "", chat_id: str = "") -> tuple[str, str]:
    print("텔레그램 알림 설정 (건너뛰면 대시보드 화면으로만 봅니다)")
    print("  만드는 법: 텔레그램에서 @BotFather 검색 → /newbot → 나온 토큰을 붙여넣기")
    token = prompt("  봇 토큰 (없으면 그냥 엔터)", token)
    if not token:
        return "", ""
    if not _check_token(token):
        print("  → 토큰이 올바르지 않은 것 같습니다. 알림 없이 진행할게요.")
        return "", ""
    if chat_id:
        return token, chat_id

    print()
    print("  이제 방금 만든 봇을 텔레그램에서 찾아 아무 메시지나 보내주세요.")
    prompt("  보냈으면 엔터")
    found = _detect_chat_id(token)
    if found:
        print(f"  → 대화방을 찾았습니다 (chat_id: {found})")
        return token, found
    print("  → 자동으로 못 찾았습니다.")
    return token, prompt("  chat_id 를 직접 입력 (모르면 엔터)")


def ask_watchlist(current: list[str] | None = None) -> list[str]:
    print("감시할 종목 (미국 주식 티커, 쉼표로 구분)")
    default = ", ".join(current or [])
    tickers: list[str] = []
    while not tickers:
        raw = prompt("  예: AAPL, NVDA, TSLA", default)
        tickers = [t.strip().upper() for t in re.split(r"[,\s]+", raw) if t.strip()]
        if not tickers:
            print("  → 하나 이상 입력해주세요. (나중에 화면에서 추가·삭제할 수 있어요)")
    return tickers


# --------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------
def run_wizard(config_path: Path) -> bool:
    """설정이 없으면 새로 만들고, 있으면 잘못된 항목만 고친다. 성공하면 True."""
    try:
        if config_path.exists():
            bad = find_problems(config_path)
            return _repair(config_path, bad) if bad else True
        return _create(config_path)
    except WizardAborted:
        print()
        print("입력을 받을 수 없어 설정을 만들지 못했습니다.")
        print(f"  config.example.yml 을 {config_path} 로 복사한 뒤 직접 채워주세요.")
        print("  (터미널에서 실행 중이라면 `python main.py setup` 으로 다시 시도할 수 있습니다)")
        return False


def repair_wizard(config_path: Path, keys: list[str] | None = None) -> bool:
    """지금 잘못된 항목을 다시 물어본다. 설정 오류로 못 뜰 때 부른다."""
    try:
        return _repair(config_path, keys or find_problems(config_path) or ["contact"])
    except WizardAborted:
        print()
        print(f"입력을 받을 수 없습니다. {config_path} 를 직접 열어 고쳐주세요.")
        return False


def _repair(config_path: Path, keys: list[str]) -> bool:
    text = config_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raw = {}

    print()
    print("=" * 58)
    print("  아래 항목을 다시 여쭤봅니다.")
    for key in keys:
        print(f"    · {PROBLEM_LABEL.get(key, key)}")
    print("  나머지 설정은 그대로 둡니다.")
    print("=" * 58)
    print()

    if "contact" in keys:
        current = str(raw.get("user_agent") or "")
        if current:
            print(f"지금 값: {current!r} ← 이메일로 읽히지 않습니다.")
        text = set_scalar(text, "user_agent", ask_contact(current))
        print()
    if "telegram" in keys:
        token, chat_id = ask_telegram(str(raw.get("telegram_token") or ""),
                                      str(raw.get("telegram_chat_id") or ""))
        text = set_scalar(text, "telegram_token", token)
        text = set_scalar(text, "telegram_chat_id", chat_id)
        print()
    if "watchlist" in keys:
        current = [str(w.get("ticker", "")) for w in raw.get("watchlist") or []
                   if isinstance(w, dict)]
        text = set_watchlist(text, ask_watchlist([t for t in current if t]))
        print()

    config_path.write_text(text, encoding="utf-8")
    print(f"✅ 고쳤습니다: {config_path}")
    print()
    return True


def _create(config_path: Path) -> bool:
    print()
    print("=" * 58)
    print("  처음 실행이네요. 몇 가지만 물어볼게요. (3분이면 끝납니다)")
    print("=" * 58)
    print()

    print("[1/3] ", end="")
    user_agent = ask_contact()
    print()

    print("[2/3] ", end="")
    token, chat_id = ask_telegram()
    print()

    print("[3/3] ", end="")
    tickers = ask_watchlist()

    config_path.write_text(
        TEMPLATE.format(
            user_agent=user_agent,
            token=token,
            chat_id=chat_id,
            watchlist="\n".join(f"  - ticker: {t}" for t in tickers),
        ),
        encoding="utf-8",
    )

    print()
    print(f"✅ 설정을 저장했습니다: {config_path}")
    if not token:
        print("   텔레그램 없이 대시보드로만 봅니다. 나중에 알림을 켜려면 config.yml 의")
        print("   telegram_token / telegram_chat_id 를 채워주세요.")
    print()
    return True


def _check_token(token: str) -> bool:
    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
        return resp.status_code == 200 and resp.json().get("ok", False)
    except requests.RequestException:
        print("  → 텔레그램 서버에 연결하지 못했습니다. 인터넷 연결을 확인해주세요.")
        return False


def _detect_chat_id(token: str) -> str:
    """봇에게 보낸 메시지에서 chat_id 를 찾아낸다."""
    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
        if resp.status_code != 200:
            return ""
        for update in reversed(resp.json().get("result", []) or []):
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("id") is not None:
                return str(chat["id"])
    except (requests.RequestException, ValueError):
        pass
    return ""
