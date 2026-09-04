"""열쇠 보관함 — 프로그램 폴더를 지워도 인증키가 남아야 한다.

폴더를 지웠다 새로 깔 때마다 config.yml 안의 인증키가 같이 사라져서, 매번
발급받은 키를 다시 찾아 넣는 일이 실제로 반복됐다. 그래서 저장 자리를 사용자
폴더로 옮겼다. 여기 시험들은 그 약속을 지키는지 본다.
"""

import json

import pytest

from stock_analysis import secrets
from stock_analysis.config import load_config


def test_the_store_lives_outside_the_program_folder(tmp_path, monkeypatch):
    """program 폴더 안이면 지울 때 같이 없어진다. 그러면 옮긴 의미가 없다."""
    monkeypatch.delenv(secrets.HOME_ENV, raising=False)
    monkeypatch.setattr(secrets.os.path, "expanduser", lambda _: str(tmp_path / "사용자"))

    assert secrets.path() == tmp_path / "사용자" / ".first-stock" / "keys.json"


def test_a_saved_key_comes_back(tmp_path):
    assert secrets.save("dart_api_key", "abcd1234") is not None
    assert secrets.get("dart_api_key") == "abcd1234"


def test_a_key_survives_the_program_folder_being_deleted(tmp_path):
    """이 시험이 이 파일 전체의 이유다."""
    secrets.save("dart_api_key", "살아남아야한다")

    program = tmp_path / "program"          # 폴더를 통째로 지운 셈 치고
    program.mkdir()
    (program / "config.yml").write_text("x: 1\n", encoding="utf-8")
    for item in program.iterdir():
        item.unlink()
    program.rmdir()

    assert secrets.get("dart_api_key") == "살아남아야한다"


def test_an_empty_value_removes_the_key():
    """빈 문자열이 열쇠로 남으면 '넣었는데 안 된다' 가 된다."""
    secrets.save("dart_api_key", "abcd1234")
    secrets.save("dart_api_key", "  ")

    assert secrets.get("dart_api_key") == ""
    assert "dart_api_key" not in secrets.load()


def test_an_unknown_name_is_not_stored():
    """아무 이름이나 받아 적지 않는다."""
    assert secrets.save("아무거나", "값") is None
    assert secrets.load() == {}


def test_a_broken_store_does_not_crash_the_program():
    secrets.path().parent.mkdir(parents=True, exist_ok=True)
    secrets.path().write_text("{ 망가진 파일", encoding="utf-8")

    assert secrets.load() == {}
    assert secrets.get("dart_api_key") == ""


def test_the_stored_file_is_not_world_readable():
    import os
    import sys

    saved = secrets.save("dart_api_key", "abcd1234")
    if sys.platform.startswith("win"):
        pytest.skip("윈도우에는 이 권한 개념이 없다")
    assert os.stat(saved).st_mode & 0o077 == 0


# --- 어느 자리를 읽었나 -------------------------------------------------------
def test_the_environment_wins(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "환경변수값")
    secrets.save("dart_api_key", "보관함값")

    value, where = secrets.find("dart_api_key", "설정파일값", ("DART_API_KEY",))
    assert value == "환경변수값"
    assert "DART_API_KEY" in where


def test_the_config_file_beats_the_store(monkeypatch):
    """직접 적어둔 값을 보관함이 말없이 덮어쓰면 안 된다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    secrets.save("dart_api_key", "보관함값")

    value, where = secrets.find("dart_api_key", "설정파일값", ("DART_API_KEY",))
    assert value == "설정파일값"
    assert where == "config.yml"


def test_the_store_is_used_when_nothing_else_has_it(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    secrets.save("dart_api_key", "보관함값")

    value, where = secrets.find("dart_api_key", "", ("DART_API_KEY",))
    assert value == "보관함값"
    assert where == str(secrets.path())


def test_nothing_anywhere_says_so(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    assert secrets.find("dart_api_key", "", ("DART_API_KEY",)) == ("", "")


# --- 화면·기록에 값이 새지 않아야 한다 ---------------------------------------
def test_the_shown_form_hides_the_key():
    """화면을 캡처해 물어보는 일이 흔하다. 값 자체가 보이면 안 된다."""
    shown = secrets.masked("abcdef1234567890")

    assert "abcdef1234567890" not in shown
    assert "16자" in shown and "ab" in shown and "90" in shown


def test_a_short_key_shows_nothing_but_its_length():
    assert secrets.masked("abc") == "3자 (짧음)"
    assert secrets.masked("") == ""


# --- 설정 로딩에 붙어 있나 ---------------------------------------------------
def _config_file(tmp_path, extra: str = "") -> str:
    path = tmp_path / "config.yml"
    path.write_text(
        'user_agent: "Tester tester@example.com"\n'
        "watchlist:\n  - ticker: AAPL\n" + extra,
        encoding="utf-8",
    )
    return str(path)


def test_the_program_reads_the_dart_key_from_the_store(tmp_path, monkeypatch):
    """config.yml 에 없어도, 보관함에 넣어뒀으면 한국 종목이 돌아야 한다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    secrets.save("dart_api_key", "보관함키")

    config = load_config(_config_file(tmp_path), apply_overrides=False)

    assert config.dart_api_key == "보관함키"
    assert config.key_sources["dart_api_key"] == str(secrets.path())


def test_a_key_written_in_the_config_still_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    secrets.save("dart_api_key", "보관함키")

    config = load_config(_config_file(tmp_path, 'dart_api_key: "설정키"\n'),
                         apply_overrides=False)

    assert config.dart_api_key == "설정키"
    assert config.key_sources["dart_api_key"] == "config.yml"


def test_the_telegram_token_also_comes_from_the_store(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    secrets.save("telegram_token", "봇토큰")
    secrets.save("telegram_chat_id", "12345")

    config = load_config(_config_file(tmp_path), apply_overrides=False)

    assert config.telegram_token == "봇토큰"
    assert config.telegram_chat_id == "12345"


# --- 화면에서 넣기 -----------------------------------------------------------
def test_saving_a_key_from_the_screen_takes_effect_at_once(bot, monkeypatch):
    """저장만 하고 다시 켜야 먹으면, 사람은 안 먹는 줄 안다."""
    from stock_analysis.dart import DartClient

    monkeypatch.setattr(DartClient, "check_key",
                        lambda self: (True, "상장사 2,600개를 받았습니다."))
    assert not bot.dart.ready

    message = bot.save_key("dart_api_key", "새인증키")

    assert bot.dart.ready
    assert bot.config.dart_api_key == "새인증키"
    assert secrets.get("dart_api_key") == "새인증키"
    assert "새인증키" not in message          # 값은 화면에 안 띄운다
    assert "저장했습니다" in message
    assert "2,600개" in message               # 실제로 통했다는 확인까지


def test_a_key_dart_rejects_says_why(bot, monkeypatch):
    """'저장했습니다' 만 뜨고 안 되면 무엇을 고쳐야 할지 알 수가 없다."""
    from stock_analysis.dart import DartClient

    monkeypatch.setattr(DartClient, "check_key",
                        lambda self: (False, "등록되지 않은 인증키입니다."))

    message = bot.save_key("dart_api_key", "틀린키")

    assert "거절" in message
    assert "등록되지 않은 인증키입니다." in message
    assert "틀린키" not in message             # 그래도 값은 안 띄운다
    assert secrets.get("dart_api_key") == "틀린키"   # 고쳐 넣을 수 있게 남긴다


def test_clearing_a_key_from_the_screen(bot, monkeypatch):
    from stock_analysis.dart import DartClient

    monkeypatch.setattr(DartClient, "check_key", lambda self: (True, ""))
    bot.save_key("dart_api_key", "새인증키")
    message = bot.save_key("dart_api_key", "")

    assert not bot.dart.ready
    assert secrets.get("dart_api_key") == ""
    assert "지웠습니다" in message


def test_an_unknown_key_name_is_refused(bot):
    assert "모르는" in bot.save_key("아무거나", "값")


def test_the_saved_file_holds_what_we_expect():
    secrets.save("dart_api_key", "값1")
    secrets.save("github_token", "값2")

    stored = json.loads(secrets.path().read_text(encoding="utf-8"))
    assert stored == {"dart_api_key": "값1", "github_token": "값2"}


# --- 이미 config.yml 에 넣어둔 사람 -------------------------------------------
def test_a_key_in_the_config_is_copied_into_the_store(tmp_path, monkeypatch):
    """이미 넣어둔 사람이 다음에 폴더를 지웠을 때 또 잃어버리면 안 된다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)

    load_config(_config_file(tmp_path, 'dart_api_key: "설정에만있던키"\n'),
                apply_overrides=False)

    assert secrets.get("dart_api_key") == "설정에만있던키"


def test_copying_does_not_overwrite_what_is_already_kept(tmp_path, monkeypatch):
    """보관함 값을 config.yml 이 말없이 덮어쓰면, 최근에 넣은 쪽이 사라진다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    secrets.save("dart_api_key", "보관함키")

    load_config(_config_file(tmp_path, 'dart_api_key: "설정키"\n'), apply_overrides=False)

    assert secrets.get("dart_api_key") == "보관함키"
