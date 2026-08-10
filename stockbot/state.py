"""이미 알린 공시를 기억해서 중복 알림을 막는다."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MAX_SEEN_PER_CIK = 400


class State:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {"seen": {}, "meta": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data.setdefault("seen", {})
                self.data.setdefault("meta", {})
                self.data["seen"] = loaded.get("seen", {}) or {}
                self.data["meta"] = loaded.get("meta", {}) or {}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("상태 파일을 읽지 못해 새로 시작합니다 (%s): %s", self.path, exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent or "."), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # --- 공시 중복 체크 -------------------------------------------------
    def is_seen(self, cik: str, accession: str) -> bool:
        return accession in set(self.data["seen"].get(cik, []))

    def mark_seen(self, cik: str, accession: str) -> None:
        seen = self.data["seen"].setdefault(cik, [])
        if accession in seen:
            return
        seen.append(accession)
        if len(seen) > _MAX_SEEN_PER_CIK:
            del seen[: len(seen) - _MAX_SEEN_PER_CIK]

    def is_bootstrapped(self, cik: str) -> bool:
        """첫 실행이면 과거 공시를 몰아서 보내지 않기 위한 플래그."""
        return bool(self.data["meta"].get(f"bootstrapped:{cik}"))

    def mark_bootstrapped(self, cik: str) -> None:
        self.data["meta"][f"bootstrapped:{cik}"] = True

    # --- 하루 한 번 브리핑 ---------------------------------------------
    def last_brief_date(self) -> str | None:
        return self.data["meta"].get("last_brief_date")

    def set_last_brief_date(self, day: str) -> None:
        self.data["meta"]["last_brief_date"] = day

    # --- 텔레그램 명령 롱폴링 오프셋 -------------------------------------
    def command_offset(self) -> int | None:
        value = self.data["meta"].get("command_offset")
        return int(value) if value is not None else None

    def set_command_offset(self, offset: int) -> None:
        self.data["meta"]["command_offset"] = int(offset)

    # --- 실행 흔적 ------------------------------------------------------
    def last_check(self) -> str | None:
        return self.data["meta"].get("last_check")

    def set_last_check(self, stamp: str) -> None:
        self.data["meta"]["last_check"] = stamp

    # --- 실적 발표 리마인더 (같은 날 중복 발송 방지) ----------------------
    def reminder_sent(self, key: str) -> bool:
        return key in set(self.data["meta"].get("reminders", []))

    def mark_reminder(self, key: str) -> None:
        reminders = self.data["meta"].setdefault("reminders", [])
        if key not in reminders:
            reminders.append(key)
        if len(reminders) > 200:
            del reminders[: len(reminders) - 200]
