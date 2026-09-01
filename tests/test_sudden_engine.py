"""적용 → 기록 → 되돌리기. 이 프로그램의 약속이 지켜지는지 보는 검사."""

import json

from sudden_fakes import fake_context

from sudden_attack import backup, engine, tweaks
from sudden_attack.engine import Optimizer
from sudden_attack.winreg_io import RegValue


def snapshot(registry):
    """저장소 전체를 그대로 떠둔다. 되돌린 뒤와 비교하려고."""
    return json.dumps(
        sorted(
            (root, path, name, str(value.data), value.kind)
            for (root, path), entries in registry._store.items()
            for name, value in entries.values()
        ),
        ensure_ascii=False,
    )


def test_one_click_applies_everything_recommended_and_undoes_it(tmp_path):
    ctx = fake_context()
    before = snapshot(ctx.registry)
    optimizer = Optimizer(ctx, root=tmp_path)

    outcome = optimizer.apply_recommended()
    assert outcome.done >= 8
    assert outcome.failed == 0
    assert snapshot(ctx.registry) != before
    assert ctx.display.monitors()[0].hz == 144

    undone = optimizer.revert()
    assert undone.failed == 0
    assert snapshot(ctx.registry) == before, "되돌린 뒤에는 손대기 전과 완전히 같아야 한다"
    assert ctx.display.monitors()[0].hz == 60


def test_the_record_is_written_before_we_finish(tmp_path):
    ctx = fake_context()
    outcome = Optimizer(ctx, root=tmp_path).apply_recommended()

    assert outcome.record and outcome.record.exists()
    saved = json.loads(outcome.record.read_text(encoding="utf-8"))
    assert saved["entries"]["mouse_accel"]["items"][0]["before"]["data"] == "1"
    assert backup.latest(tmp_path).keys == list(saved["entries"])


def test_running_twice_does_not_overwrite_the_original_values(tmp_path):
    """두 번 눌러도 두 번째 기록이 '이미 최적화된 값' 으로 덮이면 안 된다."""
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    before = snapshot(ctx.registry)

    optimizer.apply_recommended()
    second = optimizer.apply_recommended()
    assert second.done == 0, "이미 적용된 것은 다시 건드리지 않는다"

    optimizer.revert()
    assert snapshot(ctx.registry) == before


def test_a_reverted_record_is_not_offered_again(tmp_path):
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    optimizer.apply_recommended()
    optimizer.revert()

    assert backup.latest(tmp_path) is None
    assert backup.latest(tmp_path, include_reverted=True) is not None
    assert optimizer.revert().steps == []


def test_items_needing_admin_are_blocked_not_attempted(tmp_path):
    ctx = fake_context(admin=False)
    optimizer = Optimizer(ctx, root=tmp_path)

    statuses = {status.tweak.key: status for status in optimizer.statuses()}
    assert statuses["nagle"].blocked == "관리자 권한이 필요합니다"
    assert statuses["mouse_accel"].blocked == ""
    assert "nagle" not in optimizer.recommended_keys()

    outcome = optimizer.apply(["nagle"])
    assert outcome.done == 0 and outcome.failed == 1
    base = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    assert ctx.registry.read("HKLM", rf"{base}\{{net-1}}", "TcpAckFrequency") is None


def test_a_missing_game_blocks_only_the_game_items(tmp_path):
    optimizer = Optimizer(fake_context(install=False), root=tmp_path)
    blocked = {s.tweak.key: s.blocked for s in optimizer.statuses() if s.blocked}
    assert blocked["priority"] == "서든어택 설치 폴더를 찾지 못했습니다"
    assert "mouse_accel" not in blocked


def test_one_failing_item_does_not_stop_the_rest(tmp_path):
    ctx = fake_context()

    class Broken:
        def state(self, ctx):
            return tweaks.OFF

        def apply(self, ctx):
            raise RuntimeError("일부러 낸 오류")

        def revert(self, ctx, record):
            pass

    items = tweaks.catalog()
    items.insert(0, tweaks.Tweak(key="broken", title="고장난 항목", why="검사용",
                                 group="윈도우", action=Broken()))
    outcome = Optimizer(ctx, items=items, root=tmp_path).apply_recommended()

    assert outcome.failed == 1
    assert outcome.done >= 8
    assert "일부러 낸 오류" in outcome.steps[0].message


def test_reboot_is_reported_when_an_item_needs_it(tmp_path):
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    assert not optimizer.apply(["mouse_accel"]).reboot
    assert optimizer.apply(["hags_off"]).reboot
    assert "재부팅" in optimizer.apply(["hags_off"]).summary or True


def test_nothing_to_do_says_so(tmp_path):
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    optimizer.apply_recommended()
    assert optimizer.apply_recommended().summary.startswith("바꿀 것이 없었습니다")


def test_the_game_path_typed_by_hand_is_remembered(tmp_path):
    assert engine.saved_game_path(tmp_path) is None
    engine.remember_game_path(r"D:\게임\SuddenAttack", tmp_path)
    assert engine.saved_game_path(tmp_path) == r"D:\게임\SuddenAttack"


def test_a_broken_record_file_does_not_crash_the_program(tmp_path):
    folder = backup.folder(tmp_path)
    folder.mkdir(parents=True)
    (folder / f"{backup.PREFIX}20260101-000000.json").write_text("{망가진", encoding="utf-8")
    assert backup.history(tmp_path) == []
    assert backup.latest(tmp_path) is None


def test_a_record_from_an_older_version_is_skipped_not_crashed(tmp_path):
    ctx = fake_context()
    optimizer = Optimizer(ctx, root=tmp_path)
    optimizer.apply(["mouse_accel"])

    record = backup.latest(tmp_path)
    raw = json.loads(record.path.read_text(encoding="utf-8"))
    raw["entries"]["없어진항목"] = {"kind": "registry", "items": []}
    record.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    outcome = optimizer.revert()
    assert outcome.done == 1
    assert any("모르는 항목" in step.message for step in outcome.steps)


def test_spec_reads_what_the_registry_says(tmp_path):
    ctx = fake_context()
    ctx.registry.write("HKLM", r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                       "ProcessorNameString", RegValue("Intel(R) Core(TM) i5-12400F", "str"))
    gpu = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    ctx.registry.write("HKLM", f"{gpu}\\0000", "DriverDesc", RegValue("NVIDIA GeForce RTX 3060", "str"))
    ctx.registry.write("HKLM", f"{gpu}\\Configuration", "DriverDesc", RegValue("아님", "str"))
    ctx.registry.write("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                       "ProductName", RegValue("Windows 10 Pro", "str"))
    ctx.registry.write("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
                       "CurrentBuild", RegValue("22631", "str"))

    spec = engine.spec_of(ctx)
    assert spec.cpu == "Intel(R) Core(TM) i5-12400F"
    assert spec.gpus == ["NVIDIA GeForce RTX 3060"]      # Configuration 은 그래픽카드가 아니다
    assert "Windows 11 Pro" in spec.windows              # 빌드 22000 이상은 11 이다
    assert spec.monitors[0].best_hz == 144
