"""바깥 명령 실행 — 한글 출력과 실패 처리."""

from sudden_attack.shell import Result, Shell, _decode


def test_korean_windows_output_is_read_not_dropped():
    """윈도우 명령은 한국어 윈도우에서 cp949 로 나온다. UTF-8 로만 읽으면 깨진다."""
    assert _decode("전원 구성표".encode("cp949")) == "전원 구성표"
    assert _decode("Power Scheme".encode("utf-8")) == "Power Scheme"
    assert _decode(b"") == ""
    assert _decode(None) == ""


def test_undecodable_bytes_do_not_raise():
    """글자 하나 때문에 최적화가 통째로 멈추면 안 된다."""
    assert isinstance(_decode(b"\xff\xfe\x00\x01"), str)


def test_a_missing_command_is_a_failure_not_a_crash():
    result = Shell().run(["이런-명령은-없다"])
    assert isinstance(result, Result)
    assert not result.ok
    assert "찾지 못했습니다" in result.err


def test_a_command_that_runs(tmp_path):
    result = Shell().run(["echo", "안녕"])
    assert result.ok and result.out == "안녕"
