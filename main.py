#!/usr/bin/env python3
"""SEC EDGAR 공시 감시 + 텔레그램 알림 봇.

사용 예:
  python main.py run                # 주기 감시 (공시 + 데일리 브리핑)
  python main.py check              # 새 공시 1회 확인 (cron/GitHub Actions용)
  python main.py brief --force      # 데일리 브리핑 즉시 전송
  python main.py metrics TSLA       # 종목 지표 리포트
  python main.py earnings           # 실적 발표일 확인/추정
  python main.py calendar           # 휴장일·경제지표 일정만 콘솔 출력
  python main.py update             # git pull 로 봇 최신화
  python main.py test               # 텔레그램 연결 확인

run 으로 띄워두면 텔레그램에서 /add, /remove, /consensus, /earnings 같은 명령으로
감시 목록을 계속 고칠 수 있습니다. (/help 참고)
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from stockbot.app import Bot
from stockbot.config import ConfigError, load_config
from stockbot.doctor import run_doctor
from stockbot.econ_calendar import parse_extra_events, upcoming_events
from stockbot.market_calendar import upcoming_market_days
from stockbot.messages import format_earnings_reminder
from stockbot.setup_wizard import run_wizard
from stockbot.timeutil import dday, kdate, now


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEC EDGAR 공시 감시 텔레그램 봇")
    parser.add_argument("-c", "--config", default="config.yml", help="설정 파일 경로 (기본: config.yml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="디버그 로그")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램으로 보내지 않고 콘솔에 출력")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="주기적으로 감시 + 대시보드 (기본 동작)")
    p_run.add_argument("--no-dashboard", action="store_true", help="브라우저 대시보드 없이 실행")
    p_run.add_argument("--dashboard", action="store_true", help="설정과 무관하게 대시보드 켜기")

    sub.add_parser("setup", help="설정 파일(config.yml)을 대화형으로 만들기")

    p_check = sub.add_parser("check", help="새 공시를 1회 확인")
    p_check.add_argument("--force", action="store_true", help="첫 실행이어도 과거 공시를 모두 알림")

    p_brief = sub.add_parser("brief", help="데일리 브리핑 전송")
    p_brief.add_argument("--force", action="store_true", help="오늘 이미 보냈어도 다시 전송")

    p_metrics = sub.add_parser("metrics", help="종목 지표 리포트")
    p_metrics.add_argument("tickers", nargs="*", help="비우면 watchlist 전체")

    p_cal = sub.add_parser("calendar", help="휴장일·경제지표 일정 출력")
    p_cal.add_argument("--days", type=int, default=30, help="며칠 앞까지 볼지 (기본 30)")

    p_earn = sub.add_parser("earnings", help="실적 발표일 확인 (없으면 과거 간격으로 추정)")
    p_earn.add_argument("tickers", nargs="*", help="비우면 watchlist 전체")
    p_earn.add_argument("--notify", action="store_true", help="콘솔 대신 텔레그램으로 전송")

    sub.add_parser("update", help="git pull + 의존성 설치로 봇을 최신 버전으로 갱신")
    sub.add_parser("doctor", help="SEC 접속이 막힐 때 원인 진단")
    sub.add_parser("test", help="텔레그램 연결 및 설정 확인")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.command == "update":
        return cmd_update()

    config_path = Path(args.config)
    if args.command == "setup":
        if config_path.exists():
            answer = input(f"{config_path} 이 이미 있습니다. 새로 만들까요? (y/N) ").strip().lower()
            if answer != "y":
                print("취소했습니다.")
                return 0
        return 0 if run_wizard(config_path) else 1

    # 설정이 없으면 바로 마법사를 띄운다 (더블클릭 실행 대응).
    # 입력을 받을 수 없는 환경이면 마법사가 알아서 빠져나오고 안내를 남긴다.
    if not config_path.exists():
        print(f"설정 파일이 없습니다: {config_path}")
        if not run_wizard(config_path):
            return 2

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    if args.command == "calendar":
        return cmd_calendar(config, args.days)
    if args.command == "doctor":
        return run_doctor(config.user_agent)

    bot = Bot(config, dry_run=args.dry_run)

    if args.command == "run":
        dashboard = None
        if args.no_dashboard:
            dashboard = False
        elif args.dashboard:
            dashboard = True
        print_startup(bot, dashboard)
        bot.run_forever(dashboard=dashboard)
        return 0
    if args.command == "check":
        filings = bot.check_filings(force=getattr(args, "force", False))
        print(f"새 공시 {len(filings)}건")
        return 0
    if args.command == "brief":
        text = bot.daily_brief(force=args.force)
        if text is None:
            print("오늘 브리핑은 이미 전송했습니다. (--force 로 재전송)")
        return 0
    if args.command == "metrics":
        results = bot.send_metrics(args.tickers)
        if args.dry_run and not results:
            return 1
        return 0
    if args.command == "earnings":
        return cmd_earnings(bot, args.tickers, args.notify)
    if args.command == "test":
        return cmd_test(bot)
    return 1


def cmd_earnings(bot: Bot, tickers: list[str], notify: bool) -> int:
    targets = bot.targets()
    if tickers:
        wanted = {t.upper() for t in tickers}
        targets = [t for t in targets if t.ticker.upper() in wanted]
        if not targets:
            print(f"watchlist 에서 찾지 못했습니다: {', '.join(sorted(wanted))}", file=sys.stderr)
            return 1

    today = now(bot.config.timezone).date()
    for target in targets:
        info = bot.earnings_for(target)
        if info is None:
            print(f"{target.ticker}: 발표일을 알 수 없습니다 (config 의 earnings_date 에 직접 지정하세요)")
            continue
        kind = "추정" if info.estimated else "확정"
        print(f"{target.ticker}: {kdate(info.day)} ({dday(today, info.day)}, {kind})")
        if info.history:
            print(f"   과거 발표: {', '.join(d.isoformat() for d in info.history[-4:])}")
        if notify:
            bot.notifier.send(
                format_earnings_reminder(target.ticker, target.name, info, today, bot.metrics_hint(target))
            )
    return 0


def cmd_update() -> int:
    """git pull 로 최신 코드를 받고 의존성을 맞춘다."""
    if not Path(".git").exists():
        print("git 저장소가 아닙니다. 수동으로 최신 코드를 받아주세요.", file=sys.stderr)
        return 1
    steps = [
        (["git", "pull", "--ff-only"], "코드 갱신"),
        ([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], "의존성 설치"),
    ]
    for command, label in steps:
        print(f"· {label}: {' '.join(command)}")
        result = subprocess.run(command)
        if result.returncode != 0:
            print(f"❌ {label} 실패", file=sys.stderr)
            return result.returncode
    print("✅ 업데이트 완료. 상시 실행 중이라면 봇을 재시작하세요 (systemd: sudo systemctl restart first-stock)")
    return 0


def print_startup(bot: Bot, dashboard: bool | None) -> None:
    """더블클릭으로 켠 사람이 콘솔만 봐도 상황을 알 수 있게."""
    config = bot.config
    show_dashboard = config.dashboard_enabled if dashboard is None else dashboard
    print()
    print("=" * 58)
    print("  관심 종목 감시를 시작합니다")
    print("=" * 58)
    # 여기서 bot.targets() 를 부르면 SEC 조회 때문에 화면이 한참 비어 있게 된다.
    # 설정에 적힌 값만 먼저 보여주고, 조회는 감시 루프에서 한다.
    print(f"  감시 종목   {', '.join(w.ticker or w.cik or '?' for w in config.watchlist) or '없음'}")
    print(f"  확인 주기   {config.poll_interval_sec // 60}분마다")
    print(f"  텔레그램    {'꺼짐 (화면으로만 봅니다)' if bot.notifier.dry_run else '켜짐'}")
    if show_dashboard:
        print(f"  화면        http://127.0.0.1:{config.dashboard_port}/ (잠시 뒤 브라우저가 열립니다)")
    print()
    print("  ※ 이 창을 닫으면 감시가 멈춥니다. 끄려면 Ctrl+C")
    print("=" * 58)
    print()

    # SEC 접속을 미리 확인한다. 여기서 막히면 화면이 텅 비어 보이므로
    # 원인을 지금 알려주는 편이 훨씬 낫다.
    try:
        bot.edgar.ticker_map()
    except Exception as exc:
        print("⚠️  SEC 접속 확인에 실패했습니다. 종목 정보가 채워지지 않을 수 있습니다.")
        print()
        for line in str(exc).splitlines():
            print(f"    {line}")
        print()
        print("  고친 뒤 config.yml 을 저장하면 재시작 없이 다시 읽습니다.")
        print("=" * 58)
        print()


def cmd_test(bot: Bot) -> int:
    ok = True
    print("· 설정 로드: OK")
    targets = bot.targets()
    for target in targets:
        print(f"· {target.ticker}: CIK {target.cik} ({target.name})")
    if not targets:
        print("· watchlist 해석 실패", file=sys.stderr)
        ok = False

    if bot.notifier.dry_run:
        print("· 텔레그램: 미설정(콘솔 출력 모드)")
    elif bot.notifier.check():
        print("· 텔레그램 봇 인증: OK")
        bot.notifier.send("✅ <b>연결 테스트</b>\nSEC EDGAR 감시 봇이 정상적으로 연결되었습니다.")
        print("· 테스트 메시지 전송 완료")
    else:
        print("· 텔레그램 인증 실패 — 토큰을 확인하세요", file=sys.stderr)
        ok = False
    return 0 if ok else 1


def cmd_calendar(config, days: int) -> int:
    today = now(config.timezone).date()
    print(f"[미국 증시 휴장·조기폐장] {today} 기준 {days}일")
    for entry in upcoming_market_days(today, days):
        print(f"  {kdate(entry.day)}  {entry.name} — {entry.kind}")

    print(f"\n[주요 경제지표] {today} 기준 {days}일")
    events = upcoming_events(
        today,
        days,
        min_importance=int(config.raw.get("econ_min_importance", 2)),
        extra=parse_extra_events(config.raw.get("econ_extra_events")),
        include_weekly=bool(config.raw.get("econ_include_weekly", False)),
    )
    for event in events:
        mark = {3: "***", 2: "**", 1: "*"}.get(event.importance, "")
        est = " (추정일)" if event.estimated else ""
        when = f" {event.time_et} ET" if event.time_et else ""
        print(f"  {kdate(event.day)}{when}  {mark} {event.name}{est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
