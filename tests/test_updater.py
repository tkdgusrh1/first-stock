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


@pytest.mark.parametrize("name", ["main.py", "stockbot", "bootstrap.py", "requirements.txt", "README.md"])
def test_code_is_replaced(name):
    assert not updater._keep(name)


def test_copy_tree_replaces_code_and_keeps_data(tmp_path):
    source = tmp_path / "new"
    (source / "stockbot").mkdir(parents=True)
    (source / "stockbot" / "__init__.py").write_text('__version__ = "9.9.9"', encoding="utf-8")
    (source / "main.py").write_text("새 코드", encoding="utf-8")
    (source / "config.yml").write_text("이 값은 옮겨지면 안 된다", encoding="utf-8")

    target = tmp_path / "installed"
    (target / "stockbot").mkdir(parents=True)
    (target / "stockbot" / "__init__.py").write_text('__version__ = "1.0.0"', encoding="utf-8")
    (target / "main.py").write_text("예전 코드", encoding="utf-8")
    (target / "config.yml").write_text("내 설정", encoding="utf-8")
    (target / "state.json").write_text('{"seen": {}}', encoding="utf-8")
    (target / "company_tickers.json").write_text("{}", encoding="utf-8")

    updater._copy_tree(source, target)

    assert (target / "main.py").read_text(encoding="utf-8") == "새 코드"
    assert '9.9.9' in (target / "stockbot" / "__init__.py").read_text(encoding="utf-8")
    assert (target / "config.yml").read_text(encoding="utf-8") == "내 설정"
    assert (target / "state.json").exists()
    assert (target / "company_tickers.json").exists()


def test_stale_files_in_a_replaced_package_are_removed(tmp_path):
    """폴더를 통째로 갈아끼워야 예전 모듈이 남지 않는다."""
    source = tmp_path / "new"
    (source / "stockbot").mkdir(parents=True)
    (source / "stockbot" / "app.py").write_text("새 코드", encoding="utf-8")

    target = tmp_path / "installed"
    (target / "stockbot").mkdir(parents=True)
    (target / "stockbot" / "app.py").write_text("예전 코드", encoding="utf-8")
    (target / "stockbot" / "삭제된모듈.py").write_text("예전 파일", encoding="utf-8")

    updater._copy_tree(source, target)

    assert (target / "stockbot" / "app.py").read_text(encoding="utf-8") == "새 코드"
    assert not (target / "stockbot" / "삭제된모듈.py").exists()


def test_version_is_reported():
    assert updater.current_version()[0].isdigit()


def test_zip_url_points_at_the_working_branch():
    assert updater.REPO == "tkdgusrh1/first-stock"
    assert updater.BRANCH in updater.ZIP_URL
    assert updater.ZIP_URL.startswith("https://")
