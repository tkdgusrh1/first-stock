#!/usr/bin/env python3
"""SEC EDGAR 공시 감시 + 텔레그램 알림 봇.

사용 예:
  python main.py run                # 주기 감시 (공시 + 데일리 브리핑)
  python main.py check              # 새 공시 1회 확인 (cron/GitHub Actions용)
  python main.py brief --force      # 데일리 브리핑 즉시 전송
  python main.py metrics TSLA       # 종목 지표 리포트
  python main.py calendar           # 휴장일·경제지표 일정만 콘솔 출력
  python main.py test               # 텔레그램 연결 확인
"""

from __future__ import annotations

import argparse
import logging
import sys

from stockbot.app import Bot
from stockbot.config import ConfigError, load_config
from stockbot.econ_calendar import parse_extra_events, upcoming_events
from stockbot.market_calendar import upcoming_market_days
from stockbot.timeutil import kdate, now


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

    sub.add_parser("run", help="주기적으로 감시 (기본 동작)")

    p_check = sub.add_parser("check", help="새 공시를 1회 확인")
    p_check.add_argument("--force", action="store_true", help="첫 실행이어도 과거 공시를 모두 알림")

    p_brief = sub.add_parser("brief", help="데일리 브리핑 전송")
    p_brief.add_argument("--force", action="store_true", help="오늘 이미 보냈어도 다시 전송")

    p_metrics = sub.add_parser("metrics", help="종목 지표 리포트")
    p_metrics.add_argument("tickers", nargs="*", help="비우면 watchlist 전체")

    p_cal = sub.add_parser("calendar", help="휴장일·경제지표 일정 출력")
    p_cal.add_argument("--days", type=int, default=30, help="며칠 앞까지 볼지 (기본 30)")

    sub.add_parser("test", help="텔레그램 연결 및 설정 확인")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    if args.command == "calendar":
        return cmd_calendar(config, args.days)

    bot = Bot(config, dry_run=args.dry_run)

    if args.command == "run":
        bot.run_forever()
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
    if args.command == "test":
        return cmd_test(bot)
    return 1


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
