"""설정 로딩. config.yml + 환경변수(.env 형태의 OS 환경변수)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    pass


@dataclass
class Watch:
    """감시 대상 종목 하나."""

    ticker: str
    cik: str | None = None          # 없으면 티커로 자동 조회
    name: str | None = None
    forms: list[str] = field(default_factory=list)   # 비면 전역 forms 사용
    peers: list[str] = field(default_factory=list)   # PER/PSR 상대 비교용
    # 메모의 "가이던스 1순위, 어닝 서프라이즈 2순위"를 계산하기 위한 수동 입력값
    consensus_eps: float | None = None    # 이번 분기 EPS 컨센서스
    consensus_revenue: float | None = None  # 이번 분기 매출 컨센서스(달러)
    milestones: list[str] = field(default_factory=list)  # 적자 기업 핵심 마일스톤
    note: str | None = None

    @property
    def label(self) -> str:
        return f"{self.ticker}" + (f" ({self.name})" if self.name else "")


@dataclass
class Config:
    user_agent: str
    telegram_token: str
    telegram_chat_id: str
    watchlist: list[Watch]
    forms: list[str]
    poll_interval_sec: int
    lookback_days: int
    state_path: Path
    cache_dir: Path
    timezone: str
    daily_brief_time: str | None
    econ_lookahead_days: int
    holiday_lookahead_days: int
    metrics_in_brief: bool
    raw: dict[str, Any] = field(default_factory=dict)


_DEFAULTS = {
    "forms": ["8-K", "4"],
    "poll_interval_sec": 900,
    "lookback_days": 3,
    "state_path": "state.json",
    "cache_dir": ".cache",
    "timezone": "Asia/Seoul",
    "daily_brief_time": "08:00",
    "econ_lookahead_days": 7,
    "holiday_lookahead_days": 14,
    "metrics_in_brief": True,
}


def _env(name: str, fallback: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value else fallback


def load_config(path: str | Path = "config.yml") -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"설정 파일이 없습니다: {path}\n"
            "config.example.yml 을 config.yml 로 복사한 뒤 값을 채워주세요."
        )
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    merged = {**_DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}

    # 비밀값은 환경변수 우선(설정 파일에 토큰을 남기지 않도록)
    token = _env("TELEGRAM_BOT_TOKEN", raw.get("telegram_token"))
    chat_id = _env("TELEGRAM_CHAT_ID", str(raw.get("telegram_chat_id") or "") or None)
    user_agent = _env("SEC_USER_AGENT", raw.get("user_agent"))

    if not user_agent:
        raise ConfigError(
            "SEC는 연락처가 담긴 User-Agent를 요구합니다. "
            'config.yml 의 user_agent 또는 SEC_USER_AGENT 환경변수에 '
            '"이름 이메일@example.com" 형식으로 넣어주세요.'
        )
    if "@" not in user_agent:
        raise ConfigError("user_agent 에 연락 가능한 이메일이 포함되어야 합니다. (SEC 요구사항)")

    watchlist: list[Watch] = []
    for item in raw.get("watchlist") or []:
        if isinstance(item, str):
            watchlist.append(Watch(ticker=item.upper().strip()))
            continue
        if not item.get("ticker") and not item.get("cik"):
            raise ConfigError(f"watchlist 항목에 ticker 또는 cik 이 필요합니다: {item!r}")
        watchlist.append(
            Watch(
                ticker=str(item.get("ticker", "")).upper().strip(),
                cik=str(item["cik"]).strip() if item.get("cik") else None,
                name=item.get("name"),
                forms=[str(f).upper() for f in (item.get("forms") or [])],
                peers=[str(p).upper() for p in (item.get("peers") or [])],
                consensus_eps=_as_float(item.get("consensus_eps")),
                consensus_revenue=_as_float(item.get("consensus_revenue")),
                milestones=[str(m) for m in (item.get("milestones") or [])],
                note=item.get("note"),
            )
        )

    if not watchlist:
        raise ConfigError("watchlist 가 비어 있습니다.")

    return Config(
        user_agent=user_agent,
        telegram_token=token or "",
        telegram_chat_id=chat_id or "",
        watchlist=watchlist,
        forms=[str(f).upper() for f in merged["forms"]],
        poll_interval_sec=int(merged["poll_interval_sec"]),
        lookback_days=int(merged["lookback_days"]),
        state_path=Path(merged["state_path"]),
        cache_dir=Path(merged["cache_dir"]),
        timezone=str(merged["timezone"]),
        daily_brief_time=merged["daily_brief_time"],
        econ_lookahead_days=int(merged["econ_lookahead_days"]),
        holiday_lookahead_days=int(merged["holiday_lookahead_days"]),
        metrics_in_brief=bool(merged["metrics_in_brief"]),
        raw=raw,
    )


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
