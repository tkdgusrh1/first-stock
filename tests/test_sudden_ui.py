"""화면. 버튼이 실제로 무엇을 하는지, 그리고 화면에 거짓말이 없는지."""

from sudden_fakes import fake_context

from sudden_attack import backup, ui
from sudden_attack.engine import Optimizer


def make(tmp_path, **kwargs):
    optimizer = Optimizer(fake_context(**kwargs), root=tmp_path)
    return ui.Screen(optimizer, root=tmp_path)


def test_the_page_shows_the_computer_and_every_item(tmp_path):
    page = make(tmp_path).render()
    assert "서든어택 최적화" in page
    assert "1920×1080 · 60Hz → 최대 144Hz 가능" in page
    for title in ("마우스 가속 끄기", "게임용 전원 계획 켜기", "배경 녹화(Game DVR) 끄기"):
        assert title in page
    assert "한 번에 최적화" in page


def test_one_click_button_applies_and_the_page_says_so(tmp_path):
    screen = make(tmp_path)
    message = screen.run("apply_all", {})

    assert "적용" in message
    page = screen.render()
    assert "이미 적용돼 있습니다. 더 할 게 없습니다" in page
    assert "원래대로 되돌리기" in page
    assert backup.latest(tmp_path) is not None


def test_only_the_boxes_you_ticked_are_touched(tmp_path):
    screen = make(tmp_path)
    screen.run("apply", {"key": ["mouse_accel"]})

    record = backup.latest(tmp_path)
    assert record.keys == ["mouse_accel"]


def test_ticking_nothing_says_so_instead_of_doing_everything(tmp_path):
    screen = make(tmp_path)
    assert screen.run("apply", {"key": []}) == "고른 항목이 없습니다."
    assert backup.latest(tmp_path) is None


def test_undo_button_puts_it_all_back(tmp_path):
    screen = make(tmp_path)
    screen.run("apply_all", {})
    message = screen.run("revert", {"record": [backup.latest(tmp_path).path.name]})

    assert "되돌렸습니다" in message
    assert screen.optimizer.ctx.shell.active == "381b4222-f694-41f0-9685-ff5bb260df2e"
    assert "아직 바꾼 것이 없어서" in screen.render()


def test_undo_with_nothing_to_undo_says_so(tmp_path):
    assert make(tmp_path).run("revert", {}) == "되돌릴 기록이 없습니다."


def test_a_locked_item_is_shown_as_locked(tmp_path):
    page = make(tmp_path, admin=False).render()
    assert "관리자 권한이 필요합니다" in page
    assert "관리자 권한으로 다시 실행" in page


def test_locked_is_not_reported_as_nothing_to_do(tmp_path):
    """잠겨서 못 한 것을 '다 됐다' 고 말하면 안 된다."""
    screen = make(tmp_path, admin=False)
    screen.run("apply_all", {})
    page = screen.render()
    assert "잠겨 있습니다" in page
    assert "더 할 게 없습니다" not in page


def test_the_game_path_box_appears_only_when_the_game_is_missing(tmp_path):
    assert "서든어택을 못 찾았습니다" in make(tmp_path, install=False).render()
    assert "서든어택을 못 찾았습니다" not in make(tmp_path).render()


def test_typing_a_game_path_that_works(tmp_path):
    folder = tmp_path / "SuddenAttack"
    folder.mkdir()
    (folder / "SuddenAttack.exe").write_text("", encoding="utf-8")

    screen = make(tmp_path, install=False)
    message = screen.run("game_path", {"path": [str(folder)]})
    assert "찾았습니다" in message
    assert screen.optimizer.ctx.install is not None


def test_typing_a_game_path_that_does_not_work(tmp_path):
    screen = make(tmp_path, install=False)
    message = screen.run("game_path", {"path": [str(tmp_path / "없는곳")]})
    assert "못 찾았습니다" in message


def test_the_page_never_promises_what_it_did_not_do(tmp_path):
    """윈도우가 아니면 '적용됨' 이라고 써서는 안 된다."""
    page = make(tmp_path, windows=False).render()
    assert "실제로 바뀌지는 않습니다" in page


def test_the_guide_lists_what_we_deliberately_skip(tmp_path):
    page = make(tmp_path).render()
    assert "일부러 안 건드리는 것" in page
    assert "bcdedit" in page


def test_user_text_cannot_break_the_page(tmp_path):
    screen = make(tmp_path, install=False)
    screen.run("game_path", {"path": ['<script>alert(1)</script>']})
    assert "<script>alert(1)</script>" not in screen.render()


def test_each_item_says_what_it_is_and_what_it_buys(tmp_path):
    page = make(tmp_path).render()
    assert "윈도우가 마우스를 빨리 움직일수록" in page          # 무엇을
    assert "몸이 감각을 외울 수 있게 됩니다" in page             # 뭐가 좋아지는지
    assert "체감 큼" in page and "체감 작음" in page             # 얼마나 느껴지는지


def test_the_basics_section_explains_frames_versus_refresh_rate(tmp_path):
    page = make(tmp_path).render()
    assert "프레임 (FPS)" in page and "주사율 (Hz)" in page
    assert "모니터가 60Hz 면 눈에 보이는 건 초당 60장입니다" in page
    assert "16.7ms" in page


def test_the_basics_section_points_at_this_computer(tmp_path):
    """일반론만 적어두면 내 얘기인지 알 수 없다."""
    page = make(tmp_path).render()
    assert "지금 이 컴퓨터는 <b>60Hz</b> 로 돌고 있고 <b>144Hz</b> 까지 됩니다" in page


def test_a_monitor_already_at_its_best_is_told_so(tmp_path):
    screen = make(tmp_path)
    screen.optimizer.ctx.display.screens[0].hz = 144
    assert "이미 최대입니다" in screen.render()
