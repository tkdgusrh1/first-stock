"""퀀트 연습장의 기록 — 상태 파일(state.json) 옆의 quant.json.

공시 상태와 섞지 않는다. 연습장을 통째로 지워도(초기화) 공시 기록은 그대로다.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

MAX_JOURNAL = 300
MAX_TRIALS = 500          # 시장마다 기억할 백테스트 샤프(하루 단위) — '운이 아닐 확률' 의 시도 횟수
MAX_REPORTS = 520         # 시장마다 주간 성적표(10년치)


class QuantStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data: dict = {"backtests": {}, "compare": {}, "exits": {}, "paper": {}, "settings": {},
                           "journal": [], "runs": 0, "trials": {}, "reports": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                for key, default in self.data.items():
                    value = loaded.get(key, default)
                    self.data[key] = value if isinstance(value, type(default)) else default
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("퀀트 기록을 읽지 못해 새로 시작합니다 (%s): %s", self.path, exc)

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent or "."), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self.data, fh, ensure_ascii=False)
                os.replace(tmp, self.path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    # --- 설정(시장마다) ------------------------------------------------------
    def settings(self, market: str) -> dict:
        return dict(self.data["settings"].get(market) or {})

    def set_settings(self, market: str, values: dict) -> None:
        self.data["settings"][market] = values

    # --- 백테스트 ------------------------------------------------------------
    def backtest(self, market: str) -> dict | None:
        return self.data["backtests"].get(market)

    def set_backtest(self, market: str, result: dict) -> None:
        self.data["backtests"][market] = result
        self.data["runs"] = int(self.data.get("runs") or 0) + 1

    def compare(self, market: str) -> list:
        return list(self.data["compare"].get(market) or [])

    def set_compare(self, market: str, rows: list) -> None:
        self.data["compare"][market] = rows
        self.data["runs"] = int(self.data.get("runs") or 0) + len(rows)

    def exits(self, market: str) -> dict | None:
        """청산 규칙 비교 결과 {strategy, rows, ...}."""
        return self.data["exits"].get(market)

    def set_exits(self, market: str, result: dict) -> None:
        self.data["exits"][market] = result
        self.data["runs"] = int(self.data.get("runs") or 0) + len(result.get("rows") or [])

    @property
    def runs(self) -> int:
        return int(self.data.get("runs") or 0)

    def trials(self, market: str) -> list[float]:
        return [float(v) for v in (self.data["trials"].get(market) or []) if isinstance(v, (int, float))]

    def add_trial(self, market: str, sr: float | None) -> None:
        if sr is None:
            return
        got = self.data["trials"].setdefault(market, [])
        got.append(round(float(sr), 8))
        del got[:-MAX_TRIALS]

    # --- 주간 성적표 ---------------------------------------------------------
    def reports(self, market: str) -> list:
        return list(self.data["reports"].get(market) or [])

    def add_report(self, market: str, report: dict) -> None:
        """같은 주가 이미 있으면 바꿔 끼운다(다시 계산한 경우)."""
        got = [r for r in (self.data["reports"].get(market) or []) if r.get("week") != report.get("week")]
        got.append(report)
        got.sort(key=lambda r: r.get("week") or "")
        self.data["reports"][market] = got[-MAX_REPORTS:]

    def clear_reports(self, market: str) -> None:
        self.data["reports"].pop(market, None)

    # --- 모의 계좌 -----------------------------------------------------------
    def paper(self, market: str) -> dict | None:
        return self.data["paper"].get(market)

    def set_paper(self, market: str, value: dict | None) -> None:
        if value is None:
            self.data["paper"].pop(market, None)
        else:
            self.data["paper"][market] = value

    # --- 매매 일지 -----------------------------------------------------------
    def journal(self) -> list:
        return list(self.data["journal"])

    def add_journal(self, day: str, market: str, text: str) -> None:
        self.data["journal"].insert(0, {"day": day, "market": market, "text": text})
        del self.data["journal"][MAX_JOURNAL:]
