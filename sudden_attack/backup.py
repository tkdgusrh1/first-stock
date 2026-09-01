"""바꾸기 전 값을 파일로 남긴다.

이 프로그램에서 제일 중요한 파일이다. 최적화는 언제든 되돌릴 수 있어야 하고,
되돌리기의 근거는 오직 여기 적힌 '원래 값'이다. 그래서 적용은 기록을 먼저 저장한
뒤에 한다 — 중간에 전원이 나가도 되돌릴 근거는 남아 있어야 한다.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

FOLDER = "backup"
PREFIX = "최적화기록-"


@dataclass
class Record:
    path: Path
    when: datetime
    entries: dict = field(default_factory=dict)
    computer: str = ""
    reverted: str = ""

    @property
    def label(self) -> str:
        return self.when.strftime("%Y년 %m월 %d일 %H:%M")

    @property
    def keys(self) -> list[str]:
        return list(self.entries)


def folder(root: Path | None = None) -> Path:
    base = Path(root) if root else Path(__file__).resolve().parent.parent
    return base / FOLDER


def save(entries: dict, root: Path | None = None, when: datetime | None = None) -> Path:
    """되돌리기 기록을 새 파일로 남기고 그 경로를 준다."""
    when = when or datetime.now()
    target = folder(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{PREFIX}{when:%Y%m%d-%H%M%S}.json"

    payload = {
        "when": when.isoformat(timespec="seconds"),
        "computer": _computer(),
        "entries": entries,
    }
    # 임시 파일에 다 쓰고 나서 이름을 바꾼다. 쓰다 만 기록이 남으면 되돌리기가 막힌다.
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    log.info("되돌리기 기록을 남겼습니다: %s", path.name)
    return path


def history(root: Path | None = None) -> list[Record]:
    """새 기록이 앞에 오도록 정렬해서 전부 준다."""
    target = folder(root)
    if not target.is_dir():
        return []
    records = []
    for path in sorted(target.glob(f"{PREFIX}*.json"), reverse=True):
        record = load(path)
        if record:
            records.append(record)
    return records


def latest(root: Path | None = None, include_reverted: bool = False) -> Record | None:
    for record in history(root):
        if record.reverted and not include_reverted:
            continue
        return record
    return None


def load(path: Path) -> Record | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("기록을 읽지 못했습니다 %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    return Record(
        path=Path(path),
        when=_parse(raw.get("when"), Path(path)),
        entries=raw.get("entries") or {},
        computer=str(raw.get("computer") or ""),
        reverted=str(raw.get("reverted") or ""),
    )


def mark_reverted(record: Record, when: datetime | None = None) -> None:
    """되돌린 기록에 표시를 남긴다. 같은 기록으로 두 번 되돌리지 않기 위해서다."""
    when = when or datetime.now()
    try:
        raw = json.loads(record.path.read_text(encoding="utf-8"))
        raw["reverted"] = when.isoformat(timespec="seconds")
        record.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        record.reverted = raw["reverted"]
    except (OSError, ValueError) as exc:
        log.warning("되돌림 표시를 남기지 못했습니다: %s", exc)


def _parse(value, path: Path) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        try:
            return datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return datetime.now()


def _computer() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""
