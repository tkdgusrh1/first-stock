"""설정 로딩. config.yml + 환경변수(.env 형태의 OS 환경변수)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


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
    # 내가 산 가격 (둘 다 있어야 손익을 계산한다)
    buy_price: float | None = None
    buy_shares: float | None = None
    earnings_date: date | None = None     # 다음 실적 발표일(알면 직접 지정)
    note: str | None = None
    source: str = "config"          # config | telegram (텔레그램으로 추가된 종목)

    @property
    def label(self) -> str:
        return f"{self.ticker}" + (f" ({self.name})" if self.name else "")

    @property
    def key(self) -> str:
        return (self.ticker or self.cik or "").upper()

    def to_dict(self) -> dict:
        """overrides 파일에 저장할 형태."""
        out: dict = {"ticker": self.ticker}
        for name in ("cik", "name", "note"):
            if getattr(self, name):
                out[name] = getattr(self, name)
        for name in ("forms", "peers", "milestones"):
            if getattr(self, name):
                out[name] = list(getattr(self, name))
        for name in ("consensus_eps", "consensus_revenue", "buy_price", "buy_shares"):
            if getattr(self, name) is not None:
                out[name] = getattr(self, name)
        if self.earnings_date:
            out["earnings_date"] = self.earnings_date.isoformat()
        return out


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
    # 실행 중 업데이트 관련
    path: Path | None = None
    overrides_path: Path = Path("watchlist.local.yml")
    telegram_commands: bool = True
    allowed_chat_ids: list[str] = field(default_factory=list)
    earnings_reminder_days: list[int] = field(default_factory=lambda: [7, 1, 0])
    # 내 컴퓨터에서 보는 대시보드
    dashboard_enabled: bool = True
    dashboard_port: int = 8765
    dashboard_open_browser: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    def is_allowed(self, chat_id: str | int) -> bool:
        """명령을 받아줄 대화방인지. 기본은 알림을 보내는 그 방만 허용한다."""
        allowed = {str(c) for c in self.allowed_chat_ids} or {str(self.telegram_chat_id)}
        return str(chat_id) in allowed


_DEFAULTS = {
    # 8-K 수시공시, 4 내부자거래, 10-Q/10-K 정기보고서,
    # SC 13D/G 대량보유, S-3/424B 증자(희석 위험)
    # SC 13G(단순 투자 목적 5% 신고)는 기본에서 뺐다. 대형주는 기관마다 올려서
    # 수십 건이 되는데 판단에 쓸 내용이 없다. 경영 참여 목적인 13D 만 본다.
    "forms": ["8-K", "4", "10-Q", "10-K", "SC 13D", "S-3", "424B5"],
    "poll_interval_sec": 180,
    "lookback_days": 3,
    "state_path": "state.json",
    "cache_dir": ".cache",
    "timezone": "Asia/Seoul",
    "daily_brief_time": "08:00",
    "econ_lookahead_days": 7,
    "holiday_lookahead_days": 14,
    "metrics_in_brief": True,
    "overrides_path": "watchlist.local.yml",
    "telegram_commands": True,
    "earnings_reminder_days": [7, 1, 0],
}


def parse_watch(item: dict | str, source: str = "config") -> Watch:
    """watchlist 항목 하나를 Watch 로. config 와 overrides 파일이 같이 쓴다."""
    if isinstance(item, str):
        return Watch(ticker=item.upper().strip(), source=source)
    if not item.get("ticker") and not item.get("cik"):
        raise ConfigError(f"watchlist 항목에 ticker 또는 cik 이 필요합니다: {item!r}")
    return Watch(
        ticker=str(item.get("ticker", "")).upper().strip(),
        cik=str(item["cik"]).strip() if item.get("cik") else None,
        name=item.get("name"),
        forms=[str(f).upper() for f in (item.get("forms") or [])],
        peers=[str(p).upper() for p in (item.get("peers") or [])],
        consensus_eps=_as_float(item.get("consensus_eps")),
        consensus_revenue=_as_float(item.get("consensus_revenue")),
        milestones=[str(m) for m in (item.get("milestones") or [])],
        buy_price=_as_float(item.get("buy_price")),
        buy_shares=_as_float(item.get("buy_shares")),
        earnings_date=_as_date(item.get("earnings_date")),
        note=item.get("note"),
        source=source,
    )


def _env(name: str, fallback: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value else fallback


def load_config(path: str | Path = "config.yml", apply_overrides: bool = True) -> Config:
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

    # HTTP 헤더에 한글이 들어가면 SEC 가 403 으로 막는다. 여기서 미리 정리한다.
    from .http import find_email, sanitize_user_agent

    if not user_agent:
        raise ConfigError(
            "SEC는 연락처가 담긴 User-Agent를 요구합니다. "
            'config.yml 의 user_agent 또는 SEC_USER_AGENT 환경변수에 '
            '"이름 이메일@example.com" 형식으로 넣어주세요.'
        )
    # '@' 하나만 보고 넘기면 안 된다. 이메일이 아닌 값이 들어가면 프로그램은
    # 뜨지만 SEC 가 403 으로 전부 막아서, 화면이 통째로 비는 채로 돈다.
    if not find_email(user_agent):
        raise ConfigError(
            "SEC 에 보낼 연락처(이메일)가 올바르지 않습니다.\n"
            f"  지금 값: {user_agent!r}\n"
            '  "Hong Gildong hong@example.com" 처럼 영문 이름과 이메일이 필요합니다.\n'
            "  이 값이 잘못되면 SEC 가 접속을 막아서 아무 정보도 받지 못합니다."
        )

    cleaned_agent = sanitize_user_agent(user_agent)
    if cleaned_agent != user_agent:
        log.warning(
            "user_agent 에 영문이 아닌 글자가 있어 %r 로 바꿔 사용합니다. "
            "SEC 는 영문 이름과 이메일만 받습니다.",
            cleaned_agent,
        )
    user_agent = cleaned_agent

    dashboard = raw.get("dashboard") or {}
    if not isinstance(dashboard, dict):
        raise ConfigError("dashboard 설정은 enabled/port/open_browser 를 담은 항목이어야 합니다.")

    watchlist = [parse_watch(item) for item in raw.get("watchlist") or []]

    # 텔레그램 명령으로 추가/수정한 종목을 얹는다 (원본 config.yml 은 건드리지 않는다)
    overrides_path = Path(merged["overrides_path"])
    if apply_overrides:
        from .overrides import Overrides

        watchlist = Overrides(overrides_path).apply(watchlist)

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
        path=path,
        overrides_path=overrides_path,
        telegram_commands=bool(merged["telegram_commands"]),
        allowed_chat_ids=[str(c) for c in (raw.get("allowed_chat_ids") or [])],
        earnings_reminder_days=sorted(
            {int(d) for d in merged["earnings_reminder_days"]}, reverse=True
        ),
        dashboard_enabled=bool(dashboard.get("enabled", True)),
        dashboard_port=int(dashboard.get("port", 8765)),
        dashboard_open_browser=bool(dashboard.get("open_browser", True)),
        raw=raw,
    )


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            raise ConfigError(f"날짜 형식이 잘못되었습니다 (YYYY-MM-DD): {value!r}") from None
    return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
