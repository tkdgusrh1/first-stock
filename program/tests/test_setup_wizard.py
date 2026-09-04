"""설정 마법사 — 잘못 넣은 값이 굳어버리지 않게.

실제로 있었던 일.
  이메일 자리에 'tkdgusrh196 inquiring1!!@' 처럼 이메일이 아닌 값이 들어갔다.
  '@' 가 하나 있다는 이유로 통과했고, 프로그램은 뜨는데 SEC 가 403 으로
  전부 막아서 화면이 통째로 비었다. 다시 켜도 config.yml 이 있다는 이유로
  마법사가 안 뜨니 영영 같은 값으로 돌았다.

그래서 여기서 지킬 것.
  · 이메일은 '@' 만으로 통과시키지 않는다
  · 값이 잘못돼 있으면 다음 실행 때 그 항목만 다시 묻는다
  · 고칠 때 나머지 설정과 주석은 건드리지 않는다
"""

import pytest

from stock_analysis import setup_wizard
from stock_analysis.config import load_config
from stock_analysis.http import find_email, valid_email
from stock_analysis.setup_wizard import (
    find_problems,
    set_scalar,
    set_watchlist,
)

GOOD = """# 내 설정
user_agent: "Gildong Hong hong@gmail.com"
telegram_token: "123:abc"
poll_interval_sec: 900       # 15분마다

watchlist:
  - ticker: AAPL
  - ticker: NVDA

dashboard:
  port: 8765
"""


def write(tmp_path, text, name="config.yml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- 이메일인지 아닌지 -------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    ["hong@gmail.com", "a.b+c@sub.example.co.kr", "x1@y-z.com"],
)
def test_real_emails_pass(text):
    assert valid_email(text)


@pytest.mark.parametrize(
    "text",
    [
        "tkdgusrh196 inquiring1!!@",   # 실제로 들어왔던 값
        "hong@gmail",                  # 점이 없다
        "@gmail.com",                  # 앞이 비었다
        "hong at gmail.com",           # @ 가 없다
        "hong @gmail.com",             # 빈칸이 섞였다
        "",
    ],
)
def test_things_that_are_not_emails_fail(text):
    assert not valid_email(text)


def test_email_is_found_inside_a_longer_line():
    assert find_email("Gildong Hong hong@gmail.com") == "hong@gmail.com"
    assert find_email("SANGU tkdgusrh196 inquiring1!!@") == ""


# --- 설정 점검 --------------------------------------------------------------
def test_a_good_config_has_no_problems(tmp_path):
    assert find_problems(write(tmp_path, GOOD)) == []


def test_a_bad_contact_is_caught(tmp_path):
    bad = GOOD.replace("Gildong Hong hong@gmail.com", "SANGU tkdgusrh196 inquiring1!!@")
    assert find_problems(write(tmp_path, bad)) == ["contact"]


def test_an_empty_watchlist_is_caught(tmp_path):
    text = 'user_agent: "Gildong Hong hong@gmail.com"\nwatchlist: []\n'
    assert find_problems(write(tmp_path, text)) == ["watchlist"]


def test_a_broken_file_is_treated_as_all_wrong(tmp_path):
    assert find_problems(write(tmp_path, "이건: [설정이: 아니다")) == ["contact", "watchlist"]


# --- 고칠 때 나머지는 그대로 -------------------------------------------------
def test_fixing_one_line_keeps_the_rest_of_the_file():
    fixed = set_scalar(GOOD, "user_agent", "Gildong Hong new@gmail.com")

    assert 'user_agent: "Gildong Hong new@gmail.com"' in fixed
    assert "# 내 설정" in fixed                      # 주석이 남아 있다
    assert "# 15분마다" in fixed
    assert 'telegram_token: "123:abc"' in fixed      # 다른 값도 그대로
    assert "  port: 8765" in fixed
    assert fixed.count("user_agent:") == 1


def test_a_missing_key_is_appended():
    assert 'user_agent: "A b@c.com"' in set_scalar("forms: [1]\n", "user_agent", "A b@c.com")


def test_replacing_the_watchlist_leaves_neighbours_alone():
    fixed = set_watchlist(GOOD, ["TSLA", "RKLB"])

    assert "  - ticker: TSLA" in fixed and "  - ticker: RKLB" in fixed
    assert "AAPL" not in fixed and "NVDA" not in fixed
    assert "dashboard:" in fixed and "  port: 8765" in fixed
    assert 'user_agent: "Gildong Hong hong@gmail.com"' in fixed


def test_the_fixed_file_still_loads(tmp_path):
    from stock_analysis.config import load_config

    bad = GOOD.replace("Gildong Hong hong@gmail.com", "SANGU tkdgusrh196 inquiring1!!@")
    path = write(tmp_path, bad)
    path.write_text(set_scalar(path.read_text(encoding="utf-8"), "user_agent",
                               "Gildong Hong hong@gmail.com"), encoding="utf-8")

    config = load_config(path)
    assert config.user_agent == "Gildong Hong hong@gmail.com"
    assert [w.ticker for w in config.watchlist] == ["AAPL", "NVDA"]


# --- 처음 설정: 미장 · 국장 · 인증키 ------------------------------------------
def _answers(monkeypatch, *replies):
    """물어보는 순서대로 답을 흘려 넣는다."""
    queue = iter(replies)
    monkeypatch.setattr(setup_wizard, "prompt", lambda q, d="": next(queue, d))


def test_the_setup_asks_for_korean_stocks_too(tmp_path, monkeypatch):
    """미국 티커만 묻고 끝나면, 한국 종목은 넣을 길이 없다."""
    monkeypatch.setenv("FIRST_STOCK_HOME", str(tmp_path / "keys"))
    _answers(monkeypatch, "Hong", "hong@gmail.com", "AAPL", "삼성전자, 카카오", "")

    path = tmp_path / "config.yml"
    assert setup_wizard._create(path)

    text = path.read_text(encoding="utf-8")
    assert "- ticker: AAPL" in text
    assert "- ticker: 삼성전자" in text
    assert "- ticker: 카카오" in text


def test_a_six_digit_code_keeps_its_leading_zero(tmp_path, monkeypatch):
    """따옴표가 없으면 005930 이 5930 으로 읽힌다."""
    monkeypatch.setenv("FIRST_STOCK_HOME", str(tmp_path / "keys"))
    _answers(monkeypatch, "Hong", "hong@gmail.com", "AAPL", "005930", "")

    path = tmp_path / "config.yml"
    setup_wizard._create(path)

    assert '- ticker: "005930"' in path.read_text(encoding="utf-8")
    config = load_config(path, apply_overrides=False)
    assert [w.ticker for w in config.watchlist] == ["AAPL", "005930"]


def test_the_setup_no_longer_asks_about_telegram(tmp_path, monkeypatch):
    """안 쓰는 것을 물어보면 설정이 길어지기만 한다."""
    monkeypatch.setenv("FIRST_STOCK_HOME", str(tmp_path / "keys"))
    asked = []
    queue = iter(["Hong", "hong@gmail.com", "AAPL", "", ""])
    monkeypatch.setattr(setup_wizard, "prompt",
                        lambda q, d="": (asked.append(q), next(queue, d))[1])

    path = tmp_path / "config.yml"
    setup_wizard._create(path)

    assert not any("텔레그램" in q or "봇 토큰" in q for q in asked)
    assert not hasattr(setup_wizard, "ask_telegram")
    # 설정 파일에도 빈 토큰 줄을 남기지 않는다 (주석 안내는 남는다)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert not [ln for ln in lines if ln.startswith("telegram_token")]


def test_the_dart_key_goes_outside_the_program_folder(tmp_path, monkeypatch):
    """config.yml 에 적으면 폴더를 지울 때 같이 사라진다."""
    from stock_analysis import secrets
    from stock_analysis.dart import DartClient

    monkeypatch.setenv("FIRST_STOCK_HOME", str(tmp_path / "keys"))
    monkeypatch.setattr(DartClient, "check_key", lambda self: (True, "상장사 2,600개"))
    _answers(monkeypatch, "Hong", "hong@gmail.com", "AAPL", "삼성전자", "진짜인증키")

    path = tmp_path / "config.yml"
    setup_wizard._create(path)

    assert secrets.get("dart_api_key") == "진짜인증키"
    assert "진짜인증키" not in path.read_text(encoding="utf-8")
    config = load_config(path, apply_overrides=False)
    assert config.dart_api_key == "진짜인증키"        # 그래도 프로그램은 읽는다


def test_a_key_dart_rejects_is_reported_right_away(tmp_path, monkeypatch, capsys):
    """저장만 하고 넘어가면 '넣었는데 왜 안 되지' 가 며칠씩 간다."""
    from stock_analysis.dart import DartClient

    monkeypatch.setenv("FIRST_STOCK_HOME", str(tmp_path / "keys"))
    monkeypatch.setattr(DartClient, "check_key",
                        lambda self: (False, "등록되지 않은 인증키입니다."))
    _answers(monkeypatch, "Hong", "hong@gmail.com", "AAPL", "삼성전자", "틀린키")

    setup_wizard._create(tmp_path / "config.yml")

    out = capsys.readouterr().out
    assert "거절" in out and "등록되지 않은 인증키입니다." in out
    assert "틀린키" not in out                         # 값은 안 찍는다


def test_the_key_step_can_be_skipped(tmp_path, monkeypatch, capsys):
    from stock_analysis import secrets

    monkeypatch.setenv("FIRST_STOCK_HOME", str(tmp_path / "keys"))
    _answers(monkeypatch, "Hong", "hong@gmail.com", "AAPL", "", "")

    setup_wizard._create(tmp_path / "config.yml")

    assert secrets.get("dart_api_key") == ""
    assert "열쇠 보관함" in capsys.readouterr().out     # 나중에 넣을 곳을 알려준다
