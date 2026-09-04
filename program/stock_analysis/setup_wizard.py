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

import yaml

from .http import find_email, valid_email

TEMPLATE = """# 이 파일은 처음 실행 때 자동으로 만들어졌습니다. 언제든 직접 고쳐도 됩니다.
# 저장하면 봇이 재시작 없이 다시 읽습니다.

user_agent: "{user_agent}"

forms: ["8-K", "4"]          # 8-K=수시공시, 4=내부자 거래
poll_interval_sec: 300       # 5분마다 확인 (더 짧게 하면 SEC 가 막을 수 있습니다)
lookback_days: 3
timezone: "Asia/Seoul"

daily_brief_time: "08:00"    # 매일 아침 브리핑 (끄려면 null)
econ_min_importance: 2
econ_include_weekly: false
metrics_in_brief: true
earnings_reminder_days: [7, 1, 0]

overrides_path: "watchlist.local.yml"

# ★ 인증키는 이 파일에 없습니다. 일부러 그렇습니다.
#   이 파일은 program 폴더 안이라, 폴더를 지우거나 새로 받으면 같이 사라집니다.
#   그래서 인증키는 폴더 **바깥**에 따로 둡니다:
#
#       {keys_path}
#
#   넣고 고치는 곳은 화면의 '한국' 단추 → '열쇠 보관함' 입니다.
#   어느 자리에서 읽었는지는 `python main.py doctor` 가 알려줍니다.
#
#   굳이 여기 적고 싶다면 아래 줄의 # 을 지우세요. 여기 적은 값이 우선입니다.
# dart_api_key: ""
# telegram_token: ""
# telegram_chat_id: ""
# github_token: ""

auto_update: true            # 새 버전이 나오면 알아서 갱신 (끄려면 false)

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
    "watchlist": "감시할 종목",
}



def set_scalar(text: str, key: str, value: str) -> str:
    """`key: 값` 한 줄만 갈아끼운다. 주석과 나머지 설정은 건드리지 않는다."""
    line = f'{key}: "{value}"'
    pattern = re.compile(rf"^{re.escape(key)}\s*:.*$", re.M)
    if pattern.search(text):
        return pattern.sub(lambda _: line, text, count=1)
    return text.rstrip("\n") + "\n" + line + "\n"


def set_watchlist(text: str, tickers: list[str]) -> str:
    """watchlist 블록만 새 목록으로 바꾼다."""
    block = "watchlist:\n" + "\n".join(_watch_line(t) for t in tickers) + "\n"
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


def ask_watchlist(current: list[str] | None = None) -> list[str]:
    print("감시할 미국 종목 (티커, 쉼표로 구분)")
    default = ", ".join(current or [])
    tickers: list[str] = []
    while not tickers:
        raw = prompt("  예: AAPL, NVDA, TSLA", default)
        tickers = [t.strip().upper() for t in re.split(r"[,\s]+", raw) if t.strip()]
        if not tickers:
            print("  → 하나 이상 입력해주세요. (나중에 화면에서 추가·삭제할 수 있어요)")
    return tickers


def ask_korean() -> list[str]:
    """한국 종목. 여섯 자리를 외우게 하지 않는다 — 회사 이름으로도 받는다.

    다만 이름 → 코드 변환에는 DART 인증키가 필요하다. 아직 없을 수 있으니
    여기서는 적어만 두고, 실제로 푸는 일은 프로그램이 뜬 뒤에 한다.
    """
    print("감시할 한국 종목 (없으면 그냥 엔터)")
    print("  회사 이름 그대로 적으셔도 됩니다. 종목 코드도 받습니다.")
    raw = prompt("  예: 삼성전자, 카카오, 005930")
    found = [w.strip() for w in re.split(r"[,\n]+", raw) if w.strip()]
    if found:
        print(f"  → {len(found)}개 담았습니다. 회사 이름은 프로그램이 뜬 뒤 코드로 바꿉니다.")
    return found


def ask_dart_key(needed: bool = True) -> str:
    """DART 인증키를 **프로그램 폴더 바깥**에 저장한다.

    config.yml 에 적으면 폴더를 지울 때 같이 사라진다. 그 일이 반복돼서
    보관 자리를 옮겼다. 그리고 넣는 자리에서 바로 통하는지 확인한다 —
    저장만 하고 넘어가면 '넣었는데 왜 안 되지' 가 며칠씩 간다.
    """
    from . import secrets

    print("DART 인증키" + ("" if needed else " (한국 종목을 안 보면 건너뛰어도 됩니다)"))
    if needed:
        print("  한국 종목의 공시·재무제표를 받는 데 필요합니다. 없으면 주가만 보입니다.")
    print("  무료·1분: https://opendart.fss.or.kr → 인증키 신청 → 메일 인증")

    already = secrets.get("dart_api_key")
    if already:
        print(f"  이미 넣어둔 키가 있습니다 ({secrets.masked(already)}).")
        if not prompt("  바꾸시겠어요? (바꾸려면 y, 그대로 두려면 엔터)"):
            return already

    key = prompt("  인증키 붙여넣기 (없으면 그냥 엔터)")
    if not key:
        print("  → 건너뜁니다. 나중에 화면의 '한국' → '열쇠 보관함' 에서 넣으시면 됩니다.")
        return ""

    ok, why = _check_dart_key(key)
    if not ok:
        print(f"  → DART 가 거절했습니다: {why}")
        print("     그래도 저장은 해둡니다. 화면의 '열쇠 보관함' 에서 고쳐 넣으실 수 있어요.")
    else:
        print(f"  → {why}")
    where = secrets.save("dart_api_key", key)
    if where:
        print(f"     저장 자리: {where}")
        print("     이 자리는 프로그램 폴더 바깥이라, 폴더를 지우고 새로 받아도 남습니다.")
    return key


def _check_dart_key(key: str) -> tuple[bool, str]:
    from .dart import DartClient
    from .http import HttpClient

    try:
        return DartClient(HttpClient(user_agent="first-stock-setup"), key).check_key()
    except Exception as exc:                 # 확인에 실패했다고 설정까지 막지 않는다
        return False, f"확인하지 못했습니다 ({exc})"


def _watch_line(ticker: str) -> str:
    """설정 파일의 한 줄. 숫자로만 된 코드는 따옴표가 없으면 앞의 0 이 날아간다."""
    if ticker.isdigit():
        return f'  - ticker: "{ticker}"'
    return f"  - ticker: {ticker}"


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
    if "watchlist" in keys:
        from . import markets

        current = [str(w.get("ticker", "")) for w in raw.get("watchlist") or []
                   if isinstance(w, dict)]
        us = [t for t in current if t and markets.market_of(t) != markets.KR]
        found = ask_watchlist(us)
        print()
        found += ask_korean()
        text = set_watchlist(text, found)
        print()

    config_path.write_text(text, encoding="utf-8")
    print(f"✅ 고쳤습니다: {config_path}")
    print()
    return True


def _create(config_path: Path) -> bool:
    from . import secrets

    print()
    print("=" * 58)
    print("  처음 실행이네요. 몇 가지만 물어볼게요. (2분이면 끝납니다)")
    print("=" * 58)
    print()

    print("[1/4] ", end="")
    user_agent = ask_contact()
    print()

    print("[2/4] ", end="")
    tickers = ask_watchlist()
    print()

    print("[3/4] ", end="")
    korean = ask_korean()
    print()

    print("[4/4] ", end="")
    ask_dart_key(needed=bool(korean))

    config_path.write_text(
        TEMPLATE.format(
            user_agent=user_agent,
            keys_path=secrets.path(),
            watchlist="\n".join(_watch_line(t) for t in tickers + korean),
        ),
        encoding="utf-8",
    )

    print()
    print(f"✅ 설정을 저장했습니다: {config_path}")
    print(f"   감시 종목 {len(tickers) + len(korean)}개"
          + (f" (미국 {len(tickers)} · 한국 {len(korean)})" if korean else ""))
    print("   알림 없이 대시보드 화면으로만 봅니다. 종목은 화면에서 언제든 넣고 뺄 수 있어요.")
    print()
    return True
