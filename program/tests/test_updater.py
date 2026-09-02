"""업데이트 도구. 코드만 갈아끼우고 사용자 데이터는 지켜야 한다."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import updater  # noqa: E402


@pytest.mark.parametrize(
    "name",
    [
        "config.yml",              # 설정
        "state.json",              # 이미 알린 공시 기록
        "watchlist.local.yml",     # 화면에서 추가한 종목
        ".env",
        ".venv",
        ".cache",
        "company_tickers.json",    # 직접 받아둔 목록
        "company_tickers.json.txt",
        "company_tickers (1).json",
    ],
)
def test_user_data_is_never_overwritten(name):
    assert updater._keep(name)


@pytest.mark.parametrize("name", ["main.py", "stock_analysis", "bootstrap.py", "requirements.txt", "README.md"])
def test_code_is_replaced(name):
    assert not updater._keep(name)


def test_copy_tree_replaces_code_and_keeps_data(tmp_path):
    source = tmp_path / "new"
    (source / "stock_analysis").mkdir(parents=True)
    (source / "stock_analysis" / "__init__.py").write_text('__version__ = "9.9.9"', encoding="utf-8")
    (source / "main.py").write_text("새 코드", encoding="utf-8")
    (source / "config.yml").write_text("이 값은 옮겨지면 안 된다", encoding="utf-8")

    target = tmp_path / "installed"
    (target / "stock_analysis").mkdir(parents=True)
    (target / "stock_analysis" / "__init__.py").write_text('__version__ = "1.0.0"', encoding="utf-8")
    (target / "main.py").write_text("예전 코드", encoding="utf-8")
    (target / "config.yml").write_text("내 설정", encoding="utf-8")
    (target / "state.json").write_text('{"seen": {}}', encoding="utf-8")
    (target / "company_tickers.json").write_text("{}", encoding="utf-8")

    updater._install(source, target)

    assert (target / "main.py").read_text(encoding="utf-8") == "새 코드"
    assert '9.9.9' in (target / "stock_analysis" / "__init__.py").read_text(encoding="utf-8")
    assert (target / "config.yml").read_text(encoding="utf-8") == "내 설정"
    assert (target / "state.json").exists()
    assert (target / "company_tickers.json").exists()


def test_stale_files_in_a_replaced_package_are_removed(tmp_path):
    """폴더를 통째로 갈아끼워야 예전 모듈이 남지 않는다."""
    source = tmp_path / "new"
    (source / "stock_analysis").mkdir(parents=True)
    (source / "stock_analysis" / "app.py").write_text("새 코드", encoding="utf-8")

    target = tmp_path / "installed"
    (target / "stock_analysis").mkdir(parents=True)
    (target / "stock_analysis" / "app.py").write_text("예전 코드", encoding="utf-8")
    (target / "stock_analysis" / "삭제된모듈.py").write_text("예전 파일", encoding="utf-8")

    updater._install(source, target)

    assert (target / "stock_analysis" / "app.py").read_text(encoding="utf-8") == "새 코드"
    assert not (target / "stock_analysis" / "삭제된모듈.py").exists()


def test_version_is_reported():
    assert updater.current_version()[0].isdigit()


def test_zip_url_points_at_the_working_branch():
    assert updater.REPO == "tkdgusrh1/first-stock"
    assert updater.BRANCH in updater.ZIP_URL
    assert updater.ZIP_URL.startswith("https://")


def test_old_package_folder_is_cleaned_up(tmp_path, monkeypatch):
    """이름이 바뀌기 전 폴더가 남으면 옛 코드가 import 되어 이상하게 돈다."""
    import updater

    (tmp_path / "stockbot").mkdir()
    (tmp_path / "stockbot" / "app.py").write_text("옛 코드", encoding="utf-8")
    (tmp_path / "stock_analysis").mkdir()

    removed = updater._drop_obsolete(tmp_path)

    assert removed == ["stockbot"]
    assert not (tmp_path / "stockbot").exists()
    assert (tmp_path / "stock_analysis").exists()


def test_repo_name_follows_the_git_remote(tmp_path, monkeypatch):
    """저장소 이름을 바꿔도 업데이트가 계속 되어야 한다."""
    import updater

    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/someone/stock-analysis.git\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    assert updater._repo_from_git() == "someone/stock-analysis"


def test_without_git_the_built_in_name_is_used(tmp_path, monkeypatch):
    import updater

    monkeypatch.setattr(updater, "ROOT", tmp_path)
    assert updater._repo_from_git() == ""


# --- 돌고 있는 채로 업데이트해도 되게 ---------------------------------------
def test_a_locked_file_does_not_abort_the_whole_update(tmp_path, monkeypatch):
    """실제로 업데이트가 실패하던 이유.

    윈도우에서는 프로그램이 돌고 있으면 폴더 안 파일이 잠긴다. 예전 코드는
    폴더를 통째로 지웠다가 다시 만들었는데, 삭제가 반쯤 실패하면 그다음
    copytree 가 'File exists' 로 터져서 업데이트 전체가 중단됐다.
    """
    source = tmp_path / "new"
    (source / "stock_analysis").mkdir(parents=True)
    (source / "stock_analysis" / "app.py").write_text("새 코드", encoding="utf-8")
    (source / "stock_analysis" / "metrics.py").write_text("새 지표", encoding="utf-8")
    (source / "main.py").write_text("새 진입점", encoding="utf-8")

    target = tmp_path / "installed"
    (target / "stock_analysis").mkdir(parents=True)
    (target / "stock_analysis" / "app.py").write_text("예전 코드", encoding="utf-8")
    (target / "stock_analysis" / "metrics.py").write_text("예전 지표", encoding="utf-8")
    (target / "main.py").write_text("예전 진입점", encoding="utf-8")

    # app.py 만 잠겨 있는 상황
    real_copy = updater.shutil.copy2

    def locked_copy(src, dst):
        if Path(dst).name == "app.py":
            raise PermissionError("[WinError 32] 다른 프로세스가 사용 중입니다")
        return real_copy(src, dst)

    monkeypatch.setattr(updater.shutil, "copy2", locked_copy)
    copied, failed, _ = updater._install(source, target)

    assert failed == [str(Path("stock_analysis") / "app.py")]
    assert copied == 2                                    # 나머지는 바뀌었다
    assert (target / "main.py").read_text(encoding="utf-8") == "새 진입점"
    assert (target / "stock_analysis" / "metrics.py").read_text(encoding="utf-8") == "새 지표"


def test_a_folder_that_cannot_be_deleted_is_not_fatal(tmp_path, monkeypatch):
    """폴더 삭제가 실패해도 파일 교체는 계속돼야 한다."""
    source = tmp_path / "new"
    (source / "stock_analysis").mkdir(parents=True)
    (source / "stock_analysis" / "app.py").write_text("새 코드", encoding="utf-8")

    target = tmp_path / "installed"
    (target / "stock_analysis").mkdir(parents=True)
    (target / "stock_analysis" / "app.py").write_text("예전 코드", encoding="utf-8")
    (target / "stock_analysis" / "잠긴파일.pyc").write_text("못 지움", encoding="utf-8")

    monkeypatch.setattr(Path, "unlink", _refuse)          # 아무것도 못 지우는 상황
    copied, failed, _ = updater._install(source, target)

    assert failed == [] and copied == 1
    assert (target / "stock_analysis" / "app.py").read_text(encoding="utf-8") == "새 코드"


def _refuse(*args, **kwargs):
    raise PermissionError("[WinError 32] 다른 프로세스가 사용 중입니다")


def test_the_update_reports_what_it_could_not_replace(tmp_path, monkeypatch):
    """실패했는데 성공이라고 말하면, 사용자는 새 버전인 줄 알고 계속 쓴다."""
    monkeypatch.setattr(updater, "_download", lambda url: _tiny_zip(tmp_path))
    monkeypatch.setattr(updater, "_install", lambda src, dst: (0, ["main.py"], []))
    monkeypatch.setattr(updater, "ROOT", tmp_path)

    ok, message = updater.apply_update()
    assert not ok
    assert "main.py" in message and "끄기" in message


def _tiny_zip(tmp_path) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("first-stock-main/main.py", "새 코드")
    return buffer.getvalue()


# --- 비공개 저장소 -----------------------------------------------------------
def test_no_token_means_no_authorization_header(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    for name in updater.TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    assert "Authorization" not in updater._headers()


def test_a_token_from_the_environment_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.setenv("FIRST_STOCK_TOKEN", "abc123")
    assert updater._headers()["Authorization"] == "Bearer abc123"


def test_a_token_in_the_config_file_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    for name in updater.TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config.yml").write_text(
        'user_agent: "A b@c.com"\ngithub_token: "github_pat_xyz"\n', encoding="utf-8"
    )
    assert updater.github_token() == "github_pat_xyz"


def test_a_commented_out_token_is_not_used(tmp_path, monkeypatch):
    """예시 파일의 주석 줄을 토큰으로 착각하면 안 된다."""
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    for name in updater.TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config.yml").write_text('# github_token: "여기에"\n', encoding="utf-8")
    assert updater.github_token() == ""


@pytest.mark.parametrize("code", [401, 403, 404])
def test_a_private_repo_says_what_to_actually_do(monkeypatch, code):
    """'직접 내려받아 주세요' 만으로는 무엇을 해야 할지 알 수 없다."""
    import urllib.error

    def blocked(url):
        raise urllib.error.HTTPError(url, code, "no", {}, None)

    monkeypatch.setattr(updater, "_download", blocked)
    ok, message = updater.apply_update()

    assert not ok
    assert "비공개" in message
    assert "Public" in message and "github_token" in message and "Download ZIP" in message


# --- 막혔을 때 그 자리에서 열쇠 넣기 -----------------------------------------
def test_a_token_is_saved_into_the_config(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    (tmp_path / "config.yml").write_text(
        '# 내 설정\nuser_agent: "A b@c.com"\nwatchlist:\n  - ticker: AAPL\n', encoding="utf-8"
    )

    assert updater.save_token("github_pat_abc")

    saved = (tmp_path / "config.yml").read_text(encoding="utf-8")
    assert 'github_token: "github_pat_abc"' in saved
    assert "# 내 설정" in saved                      # 나머지는 그대로
    assert "  - ticker: AAPL" in saved


def test_saving_a_token_twice_does_not_pile_up(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    (tmp_path / "config.yml").write_text('user_agent: "A b@c.com"\n', encoding="utf-8")

    updater.save_token("first")
    updater.save_token("second")

    saved = (tmp_path / "config.yml").read_text(encoding="utf-8")
    assert saved.count("github_token:") == 1
    assert "second" in saved and "first" not in saved


def test_a_token_that_does_not_work_is_not_saved(tmp_path, monkeypatch, capsys):
    """안 통하는 열쇠를 저장해두면 다음에도 막히면서 원인만 헷갈려진다."""
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.delenv("FIRST_STOCK_TOKEN", raising=False)
    (tmp_path / "config.yml").write_text('user_agent: "A b@c.com"\n', encoding="utf-8")
    monkeypatch.setattr(updater, "check_latest", lambda *a, **k: (None, False))

    assert not updater.try_token_and_save("틀린토큰")
    assert "github_token" not in (tmp_path / "config.yml").read_text(encoding="utf-8")
    assert "이 토큰으로는" in capsys.readouterr().out
    assert "FIRST_STOCK_TOKEN" not in __import__("os").environ


def test_a_working_token_is_saved(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.delenv("FIRST_STOCK_TOKEN", raising=False)
    (tmp_path / "config.yml").write_text('user_agent: "A b@c.com"\n', encoding="utf-8")
    monkeypatch.setattr(updater, "check_latest", lambda *a, **k: ("9.9.9", True))

    assert updater.try_token_and_save("github_pat_good")
    assert 'github_token: "github_pat_good"' in (tmp_path / "config.yml").read_text(encoding="utf-8")
    assert "열쇠가 통합니다" in capsys.readouterr().out


def test_skipping_the_token_leaves_the_config_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    (tmp_path / "config.yml").write_text('user_agent: "A b@c.com"\n', encoding="utf-8")

    assert not updater.try_token_and_save("")           # 그냥 엔터를 누른 경우
    assert "github_token" not in (tmp_path / "config.yml").read_text(encoding="utf-8")


# --- 스스로 갱신 -------------------------------------------------------------
def _fake_repo(tmp_path, version: str) -> None:
    """업데이트가 받아올 새 코드를 흉내낸다."""
    src = tmp_path / "새버전"
    (src / "stock_analysis").mkdir(parents=True, exist_ok=True)
    (src / "stock_analysis" / "__init__.py").write_text(
        f'__version__ = "{version}"', encoding="utf-8"
    )
    return src


def _installed(tmp_path, version: str) -> None:
    (tmp_path / "stock_analysis").mkdir(parents=True, exist_ok=True)
    (tmp_path / "stock_analysis" / "__init__.py").write_text(
        f'__version__ = "{version}"', encoding="utf-8"
    )


def test_a_broken_new_version_is_rolled_back(tmp_path, monkeypatch):
    """자동으로 갱신하다 망가지면, 자다 일어났더니 죽어 있게 된다.

    새 코드가 켜지지도 않으면 이전 버전으로 되돌려야 한다.
    """
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    _installed(tmp_path, "1.0.0")
    src = _fake_repo(tmp_path, "9.9.9")

    monkeypatch.setattr(updater, "apply_update",
                        lambda *a, **k: (updater._install(src, tmp_path)[0], "받음") and (True, "받음"))
    monkeypatch.setattr(updater, "_starts_up", lambda python: False)      # 새 코드가 안 켜진다
    monkeypatch.setattr(updater, "_bootstrap", lambda: None)

    ok, message = updater.auto_update()

    assert not ok
    assert "되돌렸습니다" in message
    assert '"1.0.0"' in (tmp_path / "stock_analysis" / "__init__.py").read_text(encoding="utf-8")


def test_a_good_new_version_stays(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    _installed(tmp_path, "1.0.0")
    src = _fake_repo(tmp_path, "9.9.9")

    monkeypatch.setattr(updater, "apply_update",
                        lambda *a, **k: (updater._install(src, tmp_path), (True, "받음"))[1])
    monkeypatch.setattr(updater, "_starts_up", lambda python: True)
    monkeypatch.setattr(updater, "_bootstrap", lambda: None)

    ok, _ = updater.auto_update()

    assert ok
    assert '"9.9.9"' in (tmp_path / "stock_analysis" / "__init__.py").read_text(encoding="utf-8")


def test_a_failed_download_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    _installed(tmp_path, "1.0.0")
    monkeypatch.setattr(updater, "apply_update", lambda *a, **k: (False, "내려받기 실패"))

    ok, message = updater.auto_update()

    assert not ok and "내려받기 실패" in message
    assert '"1.0.0"' in (tmp_path / "stock_analysis" / "__init__.py").read_text(encoding="utf-8")


def test_the_backup_folder_is_never_overwritten_by_an_update():
    """백업이 새 코드로 덮이면 되돌릴 것이 없어진다."""
    assert updater._keep(updater.BACKUP_DIR)


# --- 폴더가 program/ 안으로 옮겨간 뒤 ---------------------------------------
#
# 받은 ZIP 은 바깥에 '시작하기·업데이트·끄기', 안쪽 program/ 에 코드가 있다.
# 그대로 통째로 부으면 program/program/... 처럼 한 겹 더 들어가서
# **업데이트가 되긴 했는데 아무것도 안 바뀐** 상태가 된다.


def _zip_shaped_source(tmp_path):
    """내려받은 ZIP 을 푼 모양 그대로 만든다."""
    source = tmp_path / "받은것"
    (source / "program" / "stock_analysis").mkdir(parents=True)
    (source / "program" / "main.py").write_text("새 코드\n", encoding="utf-8")
    (source / "program" / "stock_analysis" / "app.py").write_text("새 코드\n", encoding="utf-8")
    (source / "시작하기.bat").write_text("새 실행 파일\n", encoding="utf-8")
    (source / "README.md").write_text("새 설명서\n", encoding="utf-8")
    return source


def test_the_inside_goes_inside_and_the_buttons_stay_outside(tmp_path):
    import updater

    source = _zip_shaped_source(tmp_path)
    here = tmp_path / "설치된곳" / "program"
    here.mkdir(parents=True)
    (here / "main.py").write_text("옛 코드\n", encoding="utf-8")
    (here.parent / "시작하기.bat").write_text("옛 실행 파일\n", encoding="utf-8")

    updater._install(source, here)

    assert (here / "main.py").read_text(encoding="utf-8") == "새 코드\n"
    assert (here / "stock_analysis" / "app.py").exists()
    assert (here.parent / "시작하기.bat").read_text(encoding="utf-8") == "새 실행 파일\n"
    assert (here.parent / "README.md").read_text(encoding="utf-8") == "새 설명서\n"
    # 한 겹 더 파고들지 않았다
    assert not (here / "program").exists()


def test_the_outside_never_gets_code_folders(tmp_path):
    """바깥에는 누르는 파일만 있어야 한다. 폴더가 새로 생기면 정리한 의미가 없다."""
    import updater

    source = _zip_shaped_source(tmp_path)
    here = tmp_path / "설치된곳" / "program"
    here.mkdir(parents=True)

    updater._install(source, here)

    assert not (here.parent / "stock_analysis").exists()
    assert sorted(p.name for p in here.parent.iterdir() if p.is_dir()) == ["program"]


def test_a_users_settings_outside_are_not_overwritten(tmp_path):
    """바깥에 남아 있는 config.yml 은 이사(migrate) 대상이지 덮어쓸 것이 아니다."""
    import updater

    source = _zip_shaped_source(tmp_path)
    (source / "config.yml").write_text("남의 설정\n", encoding="utf-8")
    here = tmp_path / "설치된곳" / "program"
    here.mkdir(parents=True)
    (here.parent / "config.yml").write_text("내 설정\n", encoding="utf-8")

    updater._install(source, here)

    assert (here.parent / "config.yml").read_text(encoding="utf-8") == "내 설정\n"


def test_the_version_check_looks_where_the_file_actually_is():
    """저장소 안 경로가 어긋나면 새 버전이 나와도 영영 못 본다.

    조용히 옛 버전에 머무는 게 증상이라 알아채기가 어렵다. 폴더를 옮겼을 때
    실제로 한 번 겪었다 — 확인 주소만 예전 자리를 가리키고 있었다.
    """
    from pathlib import Path

    import updater

    here = Path(updater.__file__).resolve().parent
    assert (here / "stock_analysis" / "__init__.py").exists()
    assert updater.VERSION_PATH == f"{here.name}/stock_analysis/__init__.py"
    assert updater.VERSION_PATH in updater.VERSION_URL
