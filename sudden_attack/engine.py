"""적용하고, 기록하고, 되돌린다.

항목 하나가 실패해도 나머지는 계속한다. 최적화는 15개를 다 해야 의미가 있는 게
아니라 되는 것부터 하나씩 쌓이는 일이고, 하나 막혔다고 전부 멈추면 사용자는
무엇이 됐고 무엇이 안 됐는지 알 수 없게 된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import backup, game, hardware, tweaks
from .display import open_display
from .shell import WINDOWS, is_admin, open_shell
from .tweaks import NA, OFF, ON, UNKNOWN, Tweak
from .winreg_io import open_registry

log = logging.getLogger(__name__)

STATE_LABEL = {ON: "적용됨", OFF: "안 됨", UNKNOWN: "확인 불가", NA: "해당 없음"}

PATH_FILE = "게임경로.txt"


@dataclass
class Context:
    registry: object
    shell: object
    display: object
    install: object = None
    admin: bool = False
    windows: bool = WINDOWS


@dataclass
class Status:
    tweak: Tweak
    state: str
    blocked: str = ""       # 비어 있지 않으면 지금은 못 한다는 뜻

    @property
    def label(self) -> str:
        return STATE_LABEL.get(self.state, self.state)

    @property
    def can_apply(self) -> bool:
        return not self.blocked and self.state in (OFF, UNKNOWN)


@dataclass
class Step:
    key: str
    title: str
    ok: bool
    message: str


@dataclass
class Outcome:
    steps: list = field(default_factory=list)
    record: Path | None = None
    reboot: bool = False

    @property
    def done(self) -> int:
        return sum(1 for step in self.steps if step.ok)

    @property
    def failed(self) -> int:
        return sum(1 for step in self.steps if not step.ok)

    @property
    def summary(self) -> str:
        if not self.steps:
            return "바꿀 것이 없었습니다. 이미 다 되어 있습니다."
        parts = [f"{self.done}개 적용"]
        if self.failed:
            parts.append(f"{self.failed}개 실패")
        if self.reboot:
            parts.append("일부는 재부팅 후에 적용됩니다")
        return " · ".join(parts)


class Optimizer:
    def __init__(self, ctx: Context, items: list[Tweak] | None = None, root: Path | None = None):
        self.ctx = ctx
        self.items = items if items is not None else tweaks.catalog()
        self.root = root

    # --- 보기 ---------------------------------------------------------
    def statuses(self) -> list[Status]:
        found = []
        for tweak in self.items:
            try:
                state = tweak.action.state(self.ctx)
            except Exception as exc:
                log.debug("%s 상태 확인 실패: %s", tweak.key, exc)
                state = UNKNOWN
            found.append(Status(tweak=tweak, state=state, blocked=self._blocked(tweak, state)))
        return found

    def _blocked(self, tweak: Tweak, state: str) -> str:
        if state == NA:
            if tweak.key in ("fullscreen_opt", "priority", "defender"):
                return "서든어택 설치 폴더를 찾지 못했습니다"
            return "이 컴퓨터에는 해당하지 않습니다"
        if tweak.admin and not self.ctx.admin:
            return "관리자 권한이 필요합니다"
        if not self.ctx.windows:
            return "윈도우에서만 적용됩니다"
        return ""

    def recommended_keys(self) -> list[str]:
        """'한 번에 최적화' 를 눌렀을 때 손댈 항목."""
        return [
            status.tweak.key
            for status in self.statuses()
            if status.tweak.recommended and status.can_apply
        ]

    # --- 적용 ---------------------------------------------------------
    def apply(self, keys: list[str]) -> Outcome:
        wanted = [tweak for tweak in self.items if tweak.key in set(keys)]
        outcome = Outcome()
        entries: dict = {}
        when = datetime.now()

        for tweak in wanted:
            try:
                state = tweak.action.state(self.ctx)
            except Exception as exc:
                log.debug("%s 상태 확인 실패: %s", tweak.key, exc)
                state = UNKNOWN

            blocked = self._blocked(tweak, state)
            if blocked:
                outcome.steps.append(Step(tweak.key, tweak.title, False, blocked))
                continue
            if state == ON:
                continue            # 이미 되어 있다. 손대면 되돌릴 값만 더럽혀진다.

            try:
                entries[tweak.key] = tweak.action.apply(self.ctx)
            except Exception as exc:
                log.warning("%s 적용 실패: %s", tweak.key, exc)
                outcome.steps.append(Step(tweak.key, tweak.title, False, str(exc)))
                continue

            outcome.steps.append(Step(tweak.key, tweak.title, True, "적용했습니다"))
            if tweak.reboot:
                outcome.reboot = True
            # 항목 하나 끝날 때마다 기록을 갱신한다. 중간에 멈춰도 여기까지는 되돌린다.
            outcome.record = backup.save(entries, root=self.root, when=when)

        return outcome

    def apply_recommended(self) -> Outcome:
        return self.apply(self.recommended_keys())

    # --- 되돌리기 -----------------------------------------------------
    def revert(self, record: backup.Record | None = None) -> Outcome:
        record = record or backup.latest(self.root)
        outcome = Outcome()
        if record is None:
            return outcome

        catalog = tweaks.by_key()
        # 넣은 순서의 반대로 되돌린다. 전원 계획처럼 순서가 있는 것 때문에 그렇다.
        for key in reversed(list(record.entries)):
            tweak = catalog.get(key)
            if tweak is None:
                outcome.steps.append(Step(key, key, False, "모르는 항목이라 건너뜁니다"))
                continue
            try:
                tweak.action.revert(self.ctx, record.entries[key])
            except Exception as exc:
                log.warning("%s 되돌리기 실패: %s", key, exc)
                outcome.steps.append(Step(key, tweak.title, False, str(exc)))
                continue
            outcome.steps.append(Step(key, tweak.title, True, "되돌렸습니다"))
            if tweak.reboot:
                outcome.reboot = True

        if not outcome.failed:
            backup.mark_reverted(record)
        outcome.record = record.path
        return outcome


# ---------------------------------------------------------------------------
def build_context(root: Path | None = None) -> Context:
    """이 컴퓨터에 맞는 도구들을 열고, 서든어택을 찾아둔다."""
    registry = open_registry()
    ctx = Context(
        registry=registry,
        shell=open_shell(),
        display=open_display(),
        admin=is_admin(),
        windows=WINDOWS,
    )
    ctx.install = game.find(registry=registry, saved=saved_game_path(root))
    return ctx


def spec_of(ctx: Context) -> hardware.Spec:
    return hardware.read(ctx.registry, ctx.display)


def saved_game_path(root: Path | None = None) -> str | None:
    path = backup.folder(root) / PATH_FILE
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def remember_game_path(value: str, root: Path | None = None) -> None:
    target = backup.folder(root)
    target.mkdir(parents=True, exist_ok=True)
    (target / PATH_FILE).write_text(value.strip(), encoding="utf-8")
