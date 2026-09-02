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
