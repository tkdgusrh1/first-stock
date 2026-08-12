"""텔레그램 명령으로 바뀐 watchlist 를 따로 보관한다.

config.yml 을 직접 고쳐 쓰면 주석과 서식이 날아가므로,
봇이 만든 변경분만 별도 파일(기본 watchlist.local.yml)에 저장하고
읽을 때 config.yml 위에 얹는다.

구조:
  added:    봇으로 추가한 종목 (config.yml 에 없는 것)
  removed:  config.yml 에 있지만 잠시 끈 종목
  fields:   기존 종목의 일부 값만 덮어쓰기 (컨센서스, 실적 발표일 등)
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .config import Watch, parse_watch

log = logging.getLogger(__name__)

EDITABLE_FIELDS = {
    "consensus_eps": float,
    "consensus_revenue": float,
    "buy_price": float,
    "buy_shares": float,
    "earnings_date": date,
    "name": str,
    "note": str,
    "forms": list,
    "peers": list,
    "milestones": list,
}

_HEADER = (
    "# 이 파일은 봇이 텔레그램 명령을 받아 자동으로 씁니다.\n"
    "# 직접 고쳐도 되지만, 형식이 깨지면 무시되고 새로 만들어집니다.\n"
)


class Overrides:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # settings 는 화면에서 바꾼 설정(번역 열쇠 등). config.yml 을 건드리지 않는다.
        self.data: dict[str, Any] = {"added": [], "removed": [], "fields": {}, "settings": {}}
        self.load()

    # --- 입출력 ---------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            log.warning("%s 를 읽지 못했습니다: %s", self.path, exc)
            return
        if not isinstance(loaded, dict):
            return
        self.data["added"] = list(loaded.get("added") or [])
        self.data["removed"] = [str(t).upper() for t in (loaded.get("removed") or [])]
        self.data["fields"] = dict(loaded.get("fields") or {})
        self.data["settings"] = dict(loaded.get("settings") or {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump(self.data, allow_unicode=True, sort_keys=False, default_flow_style=False)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent or "."), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(_HEADER + body)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # --- 화면에서 바꾼 설정 ------------------------------------------------
    def settings(self, section: str) -> dict:
        """{'translate': {...}} 처럼 구역별로 나눠 담는다."""
        value = self.data.get("settings", {}).get(section)
        return dict(value) if isinstance(value, dict) else {}

    def set_setting(self, section: str, name: str, value) -> None:
        """값이 비면 항목을 지운다 (빈 문자열이 열쇠로 남지 않게)."""
        block = self.data.setdefault("settings", {}).setdefault(section, {})
        if value in (None, ""):
            block.pop(name, None)
        else:
            block[name] = value

    # --- 병합 -----------------------------------------------------------
    def apply(self, base: list[Watch]) -> list[Watch]:
        removed = set(self.data["removed"])
        merged = [w for w in base if w.key not in removed]

        known = {w.key for w in merged}
        for item in self.data["added"]:
            try:
                watch = parse_watch(item, source="telegram")
            except Exception as exc:
                log.warning("overrides 의 종목 항목을 건너뜁니다 (%s): %s", item, exc)
                continue
            if watch.key in known:
                continue
            merged.append(watch)
            known.add(watch.key)

        for key, fields in self.data["fields"].items():
            for watch in merged:
                if watch.key != str(key).upper():
                    continue
                for name, value in (fields or {}).items():
                    if name not in EDITABLE_FIELDS:
                        continue
                    setattr(watch, name, _coerce(name, value))
        return merged

    # --- 변경 -----------------------------------------------------------
    def add(self, ticker: str, **fields) -> None:
        ticker = ticker.upper()
        self.data["removed"] = [t for t in self.data["removed"] if t != ticker]
        for item in self.data["added"]:
            if str(item.get("ticker", "")).upper() == ticker:
                item.update({k: v for k, v in fields.items() if v is not None})
                return
        entry = {"ticker": ticker}
        entry.update({k: v for k, v in fields.items() if v is not None})
        self.data["added"].append(entry)

    def remove(self, ticker: str) -> None:
        ticker = ticker.upper()
        before = len(self.data["added"])
        self.data["added"] = [
            i for i in self.data["added"] if str(i.get("ticker", "")).upper() != ticker
        ]
        # config.yml 에 있던 종목이면 removed 로 꺼둔다
        if len(self.data["added"]) == before and ticker not in self.data["removed"]:
            self.data["removed"].append(ticker)
        self.data["fields"].pop(ticker, None)

    def set_field(self, ticker: str, name: str, value) -> None:
        if name not in EDITABLE_FIELDS:
            raise ValueError(f"수정할 수 없는 항목입니다: {name}")
        ticker = ticker.upper()
        # 봇으로 추가한 종목이면 그 항목에 바로 쓴다
        for item in self.data["added"]:
            if str(item.get("ticker", "")).upper() == ticker:
                item[name] = _serialize(value)
                return
        self.data["fields"].setdefault(ticker, {})[name] = _serialize(value)


def _coerce(name: str, value):
    kind = EDITABLE_FIELDS[name]
    if value is None:
        return None
    if kind is date:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if kind is float:
        return float(value)
    if kind is list:
        return [str(v).upper() if name in ("forms", "peers") else str(v) for v in value]
    return str(value)


def _serialize(value):
    if isinstance(value, date):
        return value.isoformat()
    return value
