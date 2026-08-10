"""처음 실행할 때 물어보면서 config.yml 을 만들어 준다.

터미널 명령을 몰라도 시작 스크립트를 더블클릭하면 이 순서로 진행된다.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

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


def run_wizard(config_path: Path) -> bool:
    """대화형으로 설정을 만든다. 성공하면 True."""
    try:
        return _run(config_path)
    except WizardAborted:
        print()
        print("입력을 받을 수 없어 설정을 만들지 못했습니다.")
        print(f"  config.example.yml 을 {config_path} 로 복사한 뒤 직접 채워주세요.")
        print("  (터미널에서 실행 중이라면 `python main.py setup` 으로 다시 시도할 수 있습니다)")
        return False


def _run(config_path: Path) -> bool:
    print()
    print("=" * 58)
    print("  처음 실행이네요. 몇 가지만 물어볼게요. (3분이면 끝납니다)")
    print("=" * 58)
    print()

    # 1. SEC User-Agent
    print("[1/3] SEC 공시 서버가 연락처를 요구합니다.")
    print("      이름과 이메일이 SEC 서버에만 전달됩니다. 아무 데도 공개되지 않아요.")
    print("      ※ 이름은 반드시 영문으로! 한글을 넣으면 SEC가 접속을 막습니다.")
    name = prompt("      이름 (영문)", "Investor")
    if not name.isascii():
        print("      → 한글은 SEC가 받지 않아서 'Investor' 로 대체합니다.")
        name = "Investor"
    email = ""
    while "@" not in email:
        email = prompt("      이메일")
        if "@" not in email:
            print("      → 이메일 형식이 아닙니다. 다시 입력해주세요.")
    user_agent = f"{name} {email}"
    print()

    # 2. 텔레그램
    print("[2/3] 텔레그램 알림 설정 (건너뛰면 대시보드 화면으로만 봅니다)")
    print("      만드는 법: 텔레그램에서 @BotFather 검색 → /newbot → 나온 토큰을 붙여넣기")
    token = prompt("      봇 토큰 (없으면 그냥 엔터)")
    chat_id = ""
    if token:
        if not _check_token(token):
            print("      → 토큰이 올바르지 않은 것 같습니다. 알림 없이 진행할게요.")
            token = ""
        else:
            print()
            print("      이제 방금 만든 봇을 텔레그램에서 찾아 아무 메시지나 보내주세요.")
            prompt("      보냈으면 엔터")
            chat_id = _detect_chat_id(token)
            if chat_id:
                print(f"      → 대화방을 찾았습니다 (chat_id: {chat_id})")
            else:
                print("      → 자동으로 못 찾았습니다.")
                chat_id = prompt("      chat_id 를 직접 입력 (모르면 엔터)")
    print()

    # 3. 감시할 종목
    print("[3/3] 감시할 종목 (미국 주식 티커, 쉼표로 구분)")
    tickers: list[str] = []
    while not tickers:
        raw = prompt("      예: AAPL, NVDA, TSLA")
        tickers = [t.strip().upper() for t in re.split(r"[,\s]+", raw) if t.strip()]
        if not tickers:
            print("      → 하나 이상 입력해주세요. (나중에 화면에서 추가·삭제할 수 있어요)")

    watchlist = "\n".join(f"  - ticker: {t}" for t in tickers)
    config_path.write_text(
        TEMPLATE.format(
            user_agent=user_agent,
            token=token,
            chat_id=chat_id,
            watchlist=watchlist,
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
        print("      → 텔레그램 서버에 연결하지 못했습니다. 인터넷 연결을 확인해주세요.")
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
