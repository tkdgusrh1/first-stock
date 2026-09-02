"""텔레그램에서 오는 명령 처리. 폰에서 감시 목록을 계속 고칠 수 있게 한다."""

from __future__ import annotations

import logging
import subprocess
from datetime import date

from .econ_calendar import parse_extra_events, upcoming_events
from .market_calendar import upcoming_market_days
from .messages import format_metrics
from .telegram import esc
from .timeutil import dday, kdate, now

log = logging.getLogger(__name__)

HELP = """🤖 <b>사용할 수 있는 명령</b>

<b>감시 목록</b>
/list — 지금 감시 중인 종목
/add TSLA [이름] — 종목 추가 (바로 감시 시작)
/remove TSLA — 종목 빼기
/peers TSLA RIVN,LCID — 동종업계 비교 대상 지정
/forms TSLA 8-K,4,10-Q — 이 종목만 감시할 폼 지정

<b>메모 기준 입력</b>
/earnings TSLA 2026-10-22 — 실적 발표일 지정 (날짜 없이 치면 추정치 조회)
/consensus TSLA eps=1.01 rev=25000000000 — 컨센서스 입력 (어닝 서프라이즈 계산용)
/milestone TSLA 로보택시 상용화 — 마일스톤 추가 (적자 기업 체크리스트 ⑤)
/buy TSLA 250.5 10 — 내 매수가와 수량 (평가손익을 달러·원화로 보여줍니다)

<b>조회</b>
/metrics [TSLA] — 지표 체크리스트 (비우면 전체)
/calendar [30] — 휴장일·경제지표·실적 일정
/check — 새 공시 지금 확인
/brief — 데일리 브리핑 지금 받기
/status — 봇 상태와 버전

명령은 등록된 대화방에서만 받습니다."""


class CommandRouter:
    def __init__(self, bot) -> None:
        self.bot = bot

    # --- 진입점 ---------------------------------------------------------
    def poll(self, timeout: int = 25) -> int:
        """새 메시지를 받아 처리한다. 처리한 건수를 돌려준다."""
        offset = self.bot.state.command_offset()
        updates = self.bot.notifier.get_updates(offset=offset, timeout=timeout)
        handled = 0
        for update in updates:
            self.bot.state.set_command_offset(update["update_id"] + 1)
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            chat_id = (message.get("chat") or {}).get("id")
            if not text or chat_id is None:
                continue
            if not self.bot.config.is_allowed(chat_id):
                log.warning("허용되지 않은 대화방(%s)에서 온 명령을 무시했습니다.", chat_id)
                continue
            try:
                reply = self.handle(text)
            except Exception as exc:  # 명령 하나가 죽어도 루프는 계속
                log.exception("명령 처리 실패: %s", text)
                reply = f"⚠️ 명령 처리 중 오류: {esc(exc)}"
            if reply:
                self.bot.notifier.reply(chat_id, reply)
            handled += 1
        if updates:
            self.bot.state.save()
        return handled

    def handle(self, text: str) -> str | None:
        parts = text.split()
        command = parts[0].lower().lstrip("/").split("@")[0]  # /add@mybot 형태 대응
        args = parts[1:]

        handler = {
            "help": self.cmd_help,
            "start": self.cmd_help,
            "list": self.cmd_list,
            "add": self.cmd_add,
            "remove": self.cmd_remove,
            "delete": self.cmd_remove,
            "peers": self.cmd_peers,
            "forms": self.cmd_forms,
            "earnings": self.cmd_earnings,
            "consensus": self.cmd_consensus,
            "buy": self.cmd_buy,
            "milestone": self.cmd_milestone,
            "metrics": self.cmd_metrics,
            "calendar": self.cmd_calendar,
            "check": self.cmd_check,
            "brief": self.cmd_brief,
            "status": self.cmd_status,
        }.get(command)

        if handler is None:
            return f"모르는 명령입니다: {esc(text.split()[0])}\n/help 로 사용법을 볼 수 있어요."
        return handler(args)

    # --- 감시 목록 ------------------------------------------------------
    def cmd_help(self, args) -> str:
        return HELP

    def cmd_list(self, args) -> str:
        targets = self.bot.targets()
        if not targets:
            return "감시 중인 종목이 없습니다. /add TSLA 처럼 추가해보세요."
        lines = [f"👀 <b>감시 중인 종목 {len(targets)}개</b>"]
        for target in targets:
            watch = target.watch
            bits = [f"<b>{esc(target.ticker)}</b>"]
            if target.name:
                bits.append(esc(target.name))
            lines.append("• " + " · ".join(bits))
            detail = [f"폼 {'/'.join(watch.forms or self.bot.config.forms)}"]
            if watch.peers:
                detail.append(f"비교 {', '.join(watch.peers)}")
            if watch.earnings_date:
                detail.append(f"실적 {watch.earnings_date.isoformat()}")
            if watch.consensus_eps is not None:
                detail.append(f"컨센 EPS {watch.consensus_eps}")
            if watch.milestones:
                detail.append(f"마일스톤 {len(watch.milestones)}개")
            if watch.source == "telegram":
                detail.append("텔레그램으로 추가됨")
            lines.append("   " + esc(" | ".join(detail)))
        return "\n".join(lines)

    def cmd_add(self, args) -> str:
        if not args:
            return "티커를 알려주세요. 예: /add TSLA 테슬라"

        # 'NVDA:1045810' 또는 'NVDA 1045810' 처럼 CIK 를 직접 줄 수 있다.
        # (SEC 티커 목록이 막힌 환경에서도 종목을 등록할 수 있게 하는 우회로)
        first = args[0]
        explicit_cik = None
        if ":" in first:
            first, _, maybe = first.partition(":")
            if maybe.strip().isdigit():
                explicit_cik = maybe.strip()
        ticker = first.upper()
        rest = list(args[1:])
        if explicit_cik is None and rest and rest[0].isdigit():
            explicit_cik = rest.pop(0)
        name = " ".join(rest) or None

        if explicit_cik:
            cik, resolved = f"{int(explicit_cik):010d}", name or ticker
        else:
            try:
                cik, resolved = self.bot.edgar.resolve(ticker)
            except Exception as exc:
                return (
                    f"❌ {esc(ticker)} 를 SEC에서 찾지 못했습니다: {esc(exc)}\n\n"
                    "SEC 티커 목록이 막힌 상태라면 CIK 를 직접 넣어 등록할 수 있습니다:\n"
                    f"  <code>/add {esc(ticker)}:1045810</code>\n"
                    "CIK 는 브라우저에서 sec.gov 종목 페이지를 열면 확인할 수 있습니다."
                )

        # ETF 는 SEC 펀드 목록에 이름이 없다. 공시 자료에서 상품명을 채워온다.
        if not resolved:
            try:
                resolved = self.bot.edgar.submissions(cik).get("name", "") or ticker
            except Exception:
                resolved = ticker

        if any(t.ticker == ticker for t in self.bot.targets()):
            return f"이미 감시 중입니다: {esc(ticker)}"

        self.bot.overrides.add(ticker, name=name or resolved, cik=cik if explicit_cik else None)
        self.bot.overrides.save()
        self.bot.reload_watchlist()
        return (
            f"✅ <b>{esc(ticker)}</b> ({esc(name or resolved)}) 추가했습니다. CIK {cik}\n"
            "다음 확인부터 새 공시를 알려드립니다. (과거 공시는 보내지 않습니다)"
        )

    def cmd_remove(self, args) -> str:
        if not args:
            return "티커를 알려주세요. 예: /remove TSLA"
        ticker = args[0].upper()
        if not any(t.ticker == ticker for t in self.bot.targets()):
            return f"감시 목록에 없습니다: {esc(ticker)}"
        self.bot.overrides.remove(ticker)
        self.bot.overrides.save()
        self.bot.reload_watchlist()
        return f"🗑 <b>{esc(ticker)}</b> 를 감시 목록에서 뺐습니다."

    def cmd_peers(self, args) -> str:
        if len(args) < 2:
            return "예: /peers NVDA AMD,AVGO"
        ticker = args[0].upper()
        peers = [p.strip().upper() for p in " ".join(args[1:]).replace(" ", ",").split(",") if p.strip()]
        return self._set_field(ticker, "peers", peers, f"비교 대상을 {', '.join(peers)} 로 설정했습니다.")

    def cmd_forms(self, args) -> str:
        if len(args) < 2:
            return "예: /forms NVDA 8-K,4,10-Q"
        ticker = args[0].upper()
        forms = [f.strip().upper() for f in " ".join(args[1:]).replace(" ", ",").split(",") if f.strip()]
        return self._set_field(ticker, "forms", forms, f"감시 폼을 {', '.join(forms)} 로 설정했습니다.")

    # --- 메모 기준 입력 --------------------------------------------------
    def cmd_earnings(self, args) -> str:
        if not args:
            return "예: /earnings TSLA 2026-10-22 (날짜를 빼면 추정치를 알려줍니다)"
        ticker = args[0].upper()
        target = self._find(ticker)
        if target is None:
            return f"감시 목록에 없습니다: {esc(ticker)}"

        if len(args) >= 2:
            try:
                day = date.fromisoformat(args[1])
            except ValueError:
                return "날짜는 YYYY-MM-DD 형식으로 적어주세요. 예: /earnings TSLA 2026-10-22"
            return self._set_field(
                ticker, "earnings_date", day.isoformat(),
                f"실적 발표일을 {kdate(day)} 로 저장했습니다. "
                f"{', '.join(f'D-{d}' for d in self.bot.config.earnings_reminder_days if d)} 와 당일에 알려드릴게요.",
            )

        info = self.bot.earnings_for(target)
        if info is None:
            return (
                f"{esc(ticker)} 의 실적 발표일을 추정하지 못했습니다 "
                "(과거 8-K 2.02 기록이 부족). /earnings TSLA 2026-10-22 처럼 직접 넣어주세요."
            )
        today = now(self.bot.config.timezone).date()
        kind = "추정" if info.estimated else "확정"
        lines = [f"📆 <b>{esc(ticker)} 실적 발표일</b>: {kdate(info.day)} ({dday(today, info.day)}, {kind})"]
        if info.history:
            recent = ", ".join(d.isoformat() for d in info.history[-4:])
            lines.append(f"과거 발표: {recent}")
        lines.append("발표 당일엔 가이던스 → 어닝 서프라이즈 → 마진 순으로 보세요.")
        return "\n".join(lines)

    def cmd_consensus(self, args) -> str:
        if len(args) < 2:
            return "예: /consensus TSLA eps=1.01 rev=25000000000"
        ticker = args[0].upper()
        updates: list[str] = []
        for token in args[1:]:
            if "=" not in token:
                continue
            key, _, value = token.partition("=")
            key = key.strip().lower()
            try:
                number = float(value.replace(",", "").replace("$", ""))
            except ValueError:
                return f"숫자를 읽지 못했습니다: {esc(token)}"
            if key in ("eps",):
                self.bot.overrides.set_field(ticker, "consensus_eps", number)
                updates.append(f"EPS {number}")
            elif key in ("rev", "revenue", "매출"):
                self.bot.overrides.set_field(ticker, "consensus_revenue", number)
                updates.append(f"매출 {number:,.0f}")
            else:
                return f"모르는 항목입니다: {esc(key)} (eps 또는 rev)"
        if not updates:
            return "예: /consensus TSLA eps=1.01 rev=25000000000"
        if self._find(ticker) is None:
            return f"감시 목록에 없습니다: {esc(ticker)}"
        self.bot.overrides.save()
        self.bot.reload_watchlist()
        return f"✅ {esc(ticker)} 컨센서스 저장: {esc(', '.join(updates))}\n다음 실적 발표 때 자동으로 비교합니다."

    def cmd_buy(self, args) -> str:
        """내가 산 가격과 수량. 평가손익을 달러와 원화로 보여주기 위한 값."""
        if len(args) < 3:
            return "예: /buy TSLA 250.5 10  (티커, 매수가, 수량)\n지우려면: /buy TSLA 0 0"
        ticker = args[0].upper()
        target = self._find(ticker)
        if target is None:
            return f"감시 목록에 없습니다: {esc(ticker)}"
        try:
            price = float(args[1].replace(",", ""))
            shares = float(args[2].replace(",", ""))
        except ValueError:
            return "매수가와 수량은 숫자로 넣어주세요. 예: /buy TSLA 250.5 10"

        if price <= 0 or shares <= 0:
            self.bot.overrides.set_field(ticker, "buy_price", None)
            return self._set_field(ticker, "buy_shares", None, f"{esc(ticker)} 보유 정보를 지웠습니다.")

        self.bot.overrides.set_field(ticker, "buy_price", price)
        return self._set_field(
            ticker, "buy_shares", shares,
            f"💼 <b>{esc(ticker)}</b> 매수가 ${price:,.2f} × {shares:,.4g}주 "
            f"(원금 ${price * shares:,.2f}) 을(를) 저장했습니다.",
        )

    def cmd_milestone(self, args) -> str:
        if len(args) < 2:
            return "예: /milestone RKLB Neutron 첫 발사"
        ticker = args[0].upper()
        target = self._find(ticker)
        if target is None:
            return f"감시 목록에 없습니다: {esc(ticker)}"
        milestones = list(target.watch.milestones)
        milestones.append(" ".join(args[1:]))
        return self._set_field(
            ticker, "milestones", milestones,
            "마일스톤을 추가했습니다:\n" + "\n".join(f"• {esc(m)}" for m in milestones),
        )

    # --- 조회 ------------------------------------------------------------
    def cmd_metrics(self, args) -> str | None:
        targets = self.bot.targets()
        if args:
            wanted = {a.upper() for a in args}
            targets = [t for t in targets if t.ticker.upper() in wanted]
            if not targets:
                return f"감시 목록에 없습니다: {esc(', '.join(sorted(wanted)))}"
        for target in targets:
            self.bot.notifier.send(format_metrics(self.bot.metrics_for(target)))
        return None

    def cmd_calendar(self, args) -> str:
        config = self.bot.config
        try:
            days = int(args[0]) if args else 30
        except ValueError:
            days = 30
        days = max(1, min(days, 365))

        today = now(config.timezone).date()
        lines = [f"📅 <b>앞으로 {days}일 일정</b>"]

        holidays = upcoming_market_days(today, days)
        lines.append("\n🏛 <b>휴장·조기폐장</b>")
        lines += [
            f"• {kdate(d.day)} {esc(d.name)} — {d.kind} ({dday(today, d.day)})" for d in holidays
        ] or ["• 없음"]

        events = upcoming_events(
            today,
            days,
            min_importance=int(config.raw.get("econ_min_importance", 2)),
            extra=parse_extra_events(config.raw.get("econ_extra_events")) + self.bot.earnings_events(),
            include_weekly=bool(config.raw.get("econ_include_weekly", False)),
        )
        lines.append("\n📊 <b>경제지표·실적</b>")
        for event in events[:30]:
            when = f" {event.time_et} ET" if event.time_et else ""
            suffix = " <i>(추정)</i>" if event.estimated else ""
            lines.append(f"• {kdate(event.day)}{when} {esc(event.name)}{suffix}")
        if not events:
            lines.append("• 없음")
        return "\n".join(lines)

    def cmd_check(self, args) -> str | None:
        filings = self.bot.check_filings()
        if not filings:
            return "새 공시가 없습니다."
        return None  # 공시 알림이 이미 나갔다

    def cmd_brief(self, args) -> str | None:
        self.bot.daily_brief(force=True)
        return None

    def cmd_status(self, args) -> str:
        config = self.bot.config
        current = now(config.timezone)
        lines = [
            "🤖 <b>봇 상태</b>",
            f"• 현재 시각 {current.strftime('%Y-%m-%d %H:%M')} ({config.timezone})",
            f"• 감시 종목 {len(self.bot.targets())}개 / 폼 {', '.join(config.forms)}",
            f"• 확인 주기 {config.poll_interval_sec}초",
            f"• 데일리 브리핑 {config.daily_brief_time or '꺼짐'}",
            f"• 마지막 확인 {self.bot.state.last_check() or '아직 없음'}",
            f"• 버전 {esc(version())}",
        ]
        warning = self.bot.calendar_warning()
        if warning:
            lines.append(f"• ⚠️ {esc(warning)}")
        return "\n".join(lines)

    # --- 보조 ------------------------------------------------------------
    def _find(self, ticker: str):
        for target in self.bot.targets():
            if target.ticker.upper() == ticker.upper():
                return target
        return None

    def _set_field(self, ticker: str, name: str, value, message: str) -> str:
        if self._find(ticker) is None:
            return f"감시 목록에 없습니다: {esc(ticker)}"
        self.bot.overrides.set_field(ticker, name, value)
        self.bot.overrides.save()
        self.bot.reload_watchlist()
        return f"✅ <b>{esc(ticker)}</b> {message}"


def version() -> str:
    """git 커밋으로 현재 버전을 표시한다 (git 저장소가 아니면 버전 상수)."""
    from . import __version__

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return f"{__version__} ({out.stdout.strip()})"
    except (OSError, subprocess.SubprocessError):
        return __version__
