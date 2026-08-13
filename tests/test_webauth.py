"""로그인 — 비밀번호를 어떻게 다루는가.

여기서 지켜야 할 것.
  · 비밀번호는 **파일 어디에도 그대로 남지 않는다**
  · 같은 비밀번호라도 저장할 때마다 다른 값이 된다 (소금)
  · 연속으로 틀리면 잠깐 잠긴다
  · 비밀번호를 바꾸면 열려 있던 창은 전부 끊긴다
"""

import json

import pytest

from stock_analysis.webauth import Auth, check_new

PASSWORD = "sesame99"


def fresh(tmp_path, **kwargs) -> Auth:
    return Auth(tmp_path / "auth.json", **kwargs)


def signed_up(tmp_path, **kwargs) -> Auth:
    auth = fresh(tmp_path, **kwargs)
    assert auth.create("tester", PASSWORD, PASSWORD) == ""
    return auth


# --- 계정 만들기 ------------------------------------------------------------
def test_nothing_is_set_up_at_first(tmp_path):
    assert not fresh(tmp_path).configured


def test_creating_an_account(tmp_path):
    auth = signed_up(tmp_path)
    assert auth.configured and auth.user == "tester"


@pytest.mark.parametrize(
    "user,password,again,reason",
    [
        ("t", "sesame99", "sesame99", "아이디"),
        ("tester", "123", "123", "비밀번호는"),
        ("tester", "sesame99", "sesame98", "확인"),
        ("tester", "tester", "tester", "아이디와 같은"),
    ],
)
def test_weak_or_mistyped_input_is_refused(tmp_path, user, password, again, reason):
    assert reason in check_new(user, password, again)
    assert reason in fresh(tmp_path).create(user, password, again)


def test_an_account_is_not_created_twice(tmp_path):
    auth = signed_up(tmp_path)
    assert "이미" in auth.create("other", "another99", "another99")
    assert auth.user == "tester"


# --- 저장 방식 --------------------------------------------------------------
def test_the_password_is_never_stored_as_it_was_typed(tmp_path):
    signed_up(tmp_path)
    saved = (tmp_path / "auth.json").read_text(encoding="utf-8")

    assert PASSWORD not in saved
    assert json.loads(saved)["hash"] != PASSWORD
    assert "scrypt" in json.loads(saved)["algorithm"]


def test_the_same_password_is_stored_differently_every_time(tmp_path):
    """소금이 없으면 해시만 보고도 '둘이 같은 비밀번호' 라는 걸 알 수 있다."""
    one = signed_up(tmp_path / "a")
    two = signed_up(tmp_path / "b")

    first = json.loads((tmp_path / "a" / "auth.json").read_text(encoding="utf-8"))
    second = json.loads((tmp_path / "b" / "auth.json").read_text(encoding="utf-8"))
    assert first["salt"] != second["salt"]
    assert first["hash"] != second["hash"]
    assert one.login("tester", PASSWORD) and two.login("tester", PASSWORD)


def test_the_account_survives_a_restart(tmp_path):
    signed_up(tmp_path)
    again = fresh(tmp_path)
    assert again.configured and again.login("tester", PASSWORD)


def test_a_broken_file_means_starting_over(tmp_path):
    (tmp_path / "auth.json").write_text("{ 망가짐", encoding="utf-8")
    assert not fresh(tmp_path).configured


# --- 로그인 -----------------------------------------------------------------
def test_the_right_password_opens_a_session(tmp_path):
    auth = signed_up(tmp_path)
    token = auth.login("tester", PASSWORD)
    assert token and auth.valid(token)


def test_the_user_id_is_not_case_sensitive(tmp_path):
    assert signed_up(tmp_path).login("TESTER", PASSWORD)


@pytest.mark.parametrize("user,password", [("tester", "틀림"), ("남", PASSWORD), ("", "")])
def test_wrong_details_open_nothing(tmp_path, user, password):
    assert signed_up(tmp_path).login(user, password) == ""


def test_a_made_up_session_key_is_refused(tmp_path):
    auth = signed_up(tmp_path)
    assert not auth.valid("아무거나")
    assert not auth.valid("")
    assert not auth.valid(None)


def test_logging_out_kills_that_key(tmp_path):
    auth = signed_up(tmp_path)
    token = auth.login("tester", PASSWORD)
    auth.logout(token)
    assert not auth.valid(token)


def test_a_session_does_not_last_forever(tmp_path):
    auth = signed_up(tmp_path, session_hours=-1)     # 이미 지난 것으로 만든다
    assert not auth.valid(auth.login("tester", PASSWORD))


def test_two_logins_get_different_keys(tmp_path):
    auth = signed_up(tmp_path)
    assert auth.login("tester", PASSWORD) != auth.login("tester", PASSWORD)


# --- 계속 찔러보는 것 막기 ---------------------------------------------------
def test_repeated_failures_lock_the_door(tmp_path):
    auth = signed_up(tmp_path)
    for _ in range(5):
        auth.login("tester", "틀림")

    assert auth.locked_for() > 0
    assert auth.login("tester", PASSWORD) == ""      # 잠긴 동안에는 맞아도 안 열린다


def test_a_success_clears_the_failure_count(tmp_path):
    auth = signed_up(tmp_path)
    for _ in range(4):
        auth.login("tester", "틀림")
    assert auth.login("tester", PASSWORD)

    for _ in range(4):
        auth.login("tester", "틀림")
    assert auth.locked_for() == 0                    # 아직 잠기지 않았다


# --- 비밀번호 바꾸기 ---------------------------------------------------------
def test_changing_the_password(tmp_path):
    auth = signed_up(tmp_path)
    assert auth.change_password(PASSWORD, "newpass99", "newpass99") == ""
    assert auth.login("tester", "newpass99")
    assert auth.login("tester", PASSWORD) == ""


def test_changing_it_requires_the_current_one(tmp_path):
    auth = signed_up(tmp_path)
    assert "지금 비밀번호" in auth.change_password("틀림", "newpass99", "newpass99")


def test_changing_it_signs_everyone_out(tmp_path):
    """비밀번호를 바꿨는데 열려 있던 창이 그대로면 바꾼 의미가 없다."""
    auth = signed_up(tmp_path)
    token = auth.login("tester", PASSWORD)
    auth.change_password(PASSWORD, "newpass99", "newpass99")
    assert not auth.valid(token)
