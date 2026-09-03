"""설정을 넣었는데 '없다' 고 할 때 왜 그런지 짚어주는 진단.

'키를 넣었는데 못 읽는다' 가 가장 흔한 막힘이다. 원인은 늘 몇 가지 중
하나인데, 그냥 '없음' 이라고만 하면 사용자는 어디를 봐야 할지 알 수 없다.

여기서 절대 지켜야 하는 것 하나 — **열쇠 값 자체를 화면에 찍지 않는다.**
진단 결과를 캡처해서 남에게 보내는 일이 흔한데, 거기에 열쇠가 찍혀 있으면
그게 유출이다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analysis.doctor import check_settings  # noqa: E402

KEY = "abcdefghij1234567890zzzz"


def write(tmp_path, body) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(body, encoding="utf-8")
    return path


# --- 열쇠를 절대 찍지 않는다 -------------------------------------------------
def test_the_key_itself_is_never_printed(tmp_path, capsys):
    """진단 결과를 캡처해 보내도 안전해야 한다."""
    check_settings(write(tmp_path, f'user_agent: "A b@c.com"\ndart_api_key: "{KEY}"\n'))
    out = capsys.readouterr().out

    assert KEY not in out
    assert "읽었습니다" in out
    assert "24자" in out                 # 들어 있다는 건 확인된다
    assert "ab…zz" in out                # 앞뒤 두 글자만


def test_a_short_value_shows_nothing_of_itself(tmp_path, capsys):
    check_settings(write(tmp_path, 'user_agent: "A b@c.com"\ndart_api_key: "abc"\n'))
    out = capsys.readouterr().out

    assert "abc" not in out.replace("(짧음)", "")
    assert "(짧음)" in out


# --- 왜 못 읽었는지 --------------------------------------------------------
def test_a_commented_line_says_to_remove_the_hash(tmp_path, capsys):
    check_settings(write(tmp_path, 'user_agent: "A b@c.com"\n# dart_api_key: "x"\n'))
    assert "# 이 붙어 있습니다" in capsys.readouterr().out


def test_an_indented_line_says_to_move_it_left(tmp_path, capsys):
    """빈칸 하나 때문에 다른 항목 안으로 들어가 버린다."""
    check_settings(write(tmp_path,
                         'user_agent: "A b@c.com"\ndashboard:\n  dart_api_key: "x"\n'))
    assert "빈칸이 있어" in capsys.readouterr().out


def test_a_missing_line_says_to_add_one(tmp_path, capsys):
    check_settings(write(tmp_path, 'user_agent: "A b@c.com"\n'))
    assert "그런 줄이 없습니다" in capsys.readouterr().out


def test_an_empty_value_says_so(tmp_path, capsys):
    check_settings(write(tmp_path, 'user_agent: "A b@c.com"\ndart_api_key: ""\n'))
    assert "값이 비어 있습니다" in capsys.readouterr().out


# --- 파일 자체가 문제일 때 ---------------------------------------------------
def test_a_missing_file_says_where_it_looked(tmp_path, capsys):
    check_settings(tmp_path / "없는파일.yml")
    out = capsys.readouterr().out

    assert "없음" in out
    assert "없는파일.yml" in out          # 어느 자리를 봤는지 (다른 파일 고쳤을 때)


def test_a_broken_file_says_what_to_check(tmp_path, capsys):
    """따옴표를 안 닫으면 파일 전체를 못 읽는다."""
    check_settings(write(tmp_path, 'user_agent: "안 닫힘\ndart_api_key: "x"\n'))
    out = capsys.readouterr().out

    assert "못 읽음" in out
    assert "따옴표" in out


def test_the_full_path_is_shown(tmp_path, capsys):
    """다른 파일(config.example.yml)을 고치고 있는 경우가 흔하다."""
    path = write(tmp_path, 'user_agent: "A b@c.com"\n')
    check_settings(path)
    assert str(path.resolve()) in capsys.readouterr().out


def test_the_watch_interval_is_shown_in_minutes(tmp_path, capsys):
    check_settings(write(tmp_path, 'user_agent: "A b@c.com"\npoll_interval_sec: 900\n'))
    out = capsys.readouterr().out

    assert "900초" in out and "15분" in out
