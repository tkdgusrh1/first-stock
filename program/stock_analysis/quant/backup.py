"""퀀트 기록 백업 — 프로그램 폴더 바깥(문서\\FirstStock 백업)에 날짜별로 정리해 둔다.

프로그램 폴더를 통째로 지우거나 새로 받아도 모의 계좌·주간 성적표·거래 내역·매매 일지가 남는다.
매주 한 번 저절로, 그리고 화면의 '지금 백업' 단추·모의 계좌 초기화 직전에 만든다. 최근 30개만 둔다.

한 번의 백업(날짜 폴더) 안:
- quant.json            원본 그대로 — 되살릴 때 이것만 프로그램 폴더에 넣으면 된다
- 미국·한국_주간성적표.csv · _거래내역.csv · _계좌가치.csv · _보유종목.csv   엑셀·코랩에서 바로 열리게
- 매매일지.csv, 읽어보기.txt
열쇠(API 키·토큰)와 설정 파일은 넣지 않는다 — 퀀트 기록만.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import time
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

FOLDER = "FirstStock 백업"
ENV = "FIRST_STOCK_BACKUP"          # 다른 자리에 두고 싶을 때(시험에도 쓴다)
EVERY = 7 * 24 * 3600               # 저절로 만드는 간격
KEEP = 30                           # 이만큼 넘으면 오래된 것부터 지운다
DAY_DIR = re.compile(r"\d{4}-\d{2}-\d{2}$")
MARKETS = {"us": "미국", "kr": "한국"}

RETRY = 6 * 3600                    # 저절로 만들다 실패하면 이만큼 뒤에 다시(로그가 쌓이지 않게)

last_error = ""                     # 마지막으로 실패한 이유(화면에 보여준다). 성공하면 비운다
_failed_at = 0.0


def documents() -> Path | None:
    """윈도우의 진짜 '문서' 폴더(원드라이브로 옮겨 둔 경우 포함). 그 밖에는 ~/Documents."""
    if os.name == "nt":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:   # 5 = 내 문서
                return Path(buf.value)
        except Exception:
            pass
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else None


def root() -> Path:
    moved = os.environ.get(ENV, "").strip()
    if moved:
        return Path(moved)
    from .. import mobile, secrets

    if mobile.active():                 # 휴대폰 앱은 앱 안에만 쓸 수 있다
        return secrets.home() / "백업"
    docs = documents()
    return docs / FOLDER if docs else secrets.home() / "백업"


def history(base: Path | None = None) -> list[Path]:
    """날짜 폴더들, 최근 것부터."""
    base = base or root()
    try:
        found = [p for p in base.iterdir() if p.is_dir() and DAY_DIR.fullmatch(p.name)]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.name, reverse=True)


def last_made(base: Path | None = None) -> float | None:
    """가장 최근 백업을 만든 시각(epoch 초). 없으면 None."""
    got = history(base)
    try:
        return got[0].stat().st_mtime if got else None
    except OSError:
        return None


def _csv(rows: list[dict], columns: list[str]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(c) is None else row.get(c) for c in columns])
    return "﻿" + out.getvalue()          # 엑셀이 한글을 바로 읽게


def _files(data: dict) -> dict[str, str]:
    """{파일 이름: 내용} — 원본과 정리한 표들."""
    from .report import to_csv

    files = {"quant.json": json.dumps(data, ensure_ascii=False)}
    for market, name in MARKETS.items():
        reports = (data.get("reports") or {}).get(market) or []
        if reports:
            files[f"{name}_주간성적표.csv"] = "﻿" + to_csv(reports)
        account = (data.get("paper") or {}).get(market) or {}
        engine = account.get("engine") or {}
        if not engine:
            continue
        trades = engine.get("trades") or []
        files[f"{name}_거래내역.csv"] = _csv(
            trades, ["ticker", "entry_day", "entry_price", "exit_day", "exit_price", "shares", "pnl", "pnl_pct",
                     "reason"])
        flows = engine.get("flows") or {}
        curve = [{"day": d, "equity": v, "deposit": flows.get(d)} for d, v in engine.get("curve") or []]
        files[f"{name}_계좌가치.csv"] = _csv(curve, ["day", "equity", "deposit"])
        last = engine.get("last_close") or {}
        held = [dict(p, last_close=last.get(p.get("ticker"))) for p in engine.get("positions") or []]
        files[f"{name}_보유종목.csv"] = _csv(held, ["ticker", "shares", "cost", "last_close", "stop", "entry_day"])
    journal = data.get("journal") or []
    if journal:
        files["매매일지.csv"] = _csv(journal, ["day", "market", "text"])
    return files


README = """FirstStock 퀀트 기록 백업
만든 때: {made}  ·  프로그램 버전: {version}  ·  {reason}

들어 있는 것
- quant.json : 모의 계좌·주간 성적표·백테스트 기록·매매 일지 원본 전부
- 미국/한국_주간성적표.csv : 주마다 한 줄 (week_return = 그 주 수익률, total_return = 시작 뒤 수익률,
  bench_* = 지수, mdd = 최대 낙폭, 0.05 = 5%, version = 그 주를 처리한 프로그램 버전)
- 미국/한국_거래내역.csv : 판 거래마다 한 줄 (pnl = 수수료·세금 뺀 손익, pnl_pct = 수익률)
- 미국/한국_계좌가치.csv : 처리한 거래일마다 계좌 가치 (deposit = 그 날 넣은 돈)
- 미국/한국_보유종목.csv : 백업한 때 들고 있던 것 (cost = 산 값, stop = 손절선)
- 매매일지.csv : 화면의 매매 일지
열쇠(API 키·토큰)와 설정 파일은 넣지 않았습니다.

되살리는 법
1. 프로그램을 끕니다(끄기).
2. 이 폴더의 quant.json 을 프로그램 폴더의 program 폴더 안에 덮어씁니다(state.json 이 있는 곳).
3. 다시 켭니다(시작하기).
"""


def make(store, version: str, today: date, reason: str = "", base: Path | None = None) -> Path:
    """지금 백업을 하나 만든다. 같은 날 다시 만들면 그 날 폴더를 새로 쓴다. 실패하면 OSError."""
    global last_error
    base = base or root()
    with store.lock:
        data = json.loads(json.dumps(store.data))       # 다른 일이 도중에 바꾸지 않게 그 순간을 떠 둔다
    files = _files(data)
    files["읽어보기.txt"] = README.format(made=time.strftime("%Y-%m-%d %H:%M"), version=version,
                                      reason=reason or "직접 만든 백업")
    final = base / today.isoformat()
    tmp = base / f"{today.isoformat()}.tmp"
    try:
        base.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        for name, text in files.items():
            (tmp / name).write_text(text, encoding="utf-8")
        if final.exists():                               # 같은 날 두 번째 — 앞의 것을 치우고 바꿔 끼운다
            shutil.rmtree(final)
        tmp.replace(final)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        last_error = f"{exc.__class__.__name__}: {exc}"
        raise
    for old in history(base)[KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
    last_error = ""
    return final


def has_records(store) -> bool:
    data = store.data
    return bool(data.get("paper") or any(data.get("reports", {}).values()) or data.get("journal"))


def auto(store, version: str, today: date, base: Path | None = None) -> Path | None:
    """마지막 백업이 일주일 넘었고 남길 기록이 있으면 하나 만든다. 실패해도 프로그램은 계속 돈다."""
    if not has_records(store):
        return None
    global _failed_at
    made = last_made(base)
    if (made is not None and time.time() - made < EVERY) or time.time() - _failed_at < RETRY:
        return None
    try:
        return make(store, version, today, "매주 자동 백업", base)
    except OSError as exc:
        _failed_at = time.time()
        log.warning("퀀트 기록을 백업하지 못했습니다: %s", exc)
        return None
