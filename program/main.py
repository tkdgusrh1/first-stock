#!/usr/bin/env python3
"""SEC EDGAR 공시 감시 + 텔레그램 알림 봇.

사용 예:
  python main.py run                # 주기 감시 (공시 + 데일리 브리핑)
  python main.py check              # 새 공시 1회 확인 (cron/GitHub Actions용)
  python main.py brief --force      # 데일리 브리핑 즉시 전송
  python main.py metrics TSLA       # 종목 지표 리포트
  python main.py earnings           # 실적 발표일 확인/추정
  python main.py verify NVDA        # 화면의 숫자를 SEC 원문과 대조
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

from stock_analysis.app import Bot
from stock_analysis.config import ConfigError, load_config
from stock_analysis.doctor import run_doctor
from stock_analysis.econ_calendar import parse_extra_events, upcoming_events
from stock_analysis.market_calendar import upcoming_market_days
from stock_analysis.messages import format_earnings_reminder
from stock_analysis.setup_wizard import find_problems, repair_wizard, run_wizard
from stock_analysis.timeutil import dday, kdate, now


def use_utf8_output() -> None:
    """화면·로그에 한글과 이모지를 안전하게 쓴다.

    창 없이 돌 때 표준 출력은 로그 파일이다. 윈도우에서 파일로 내보내면
    파이썬이 시스템 기본 인코딩(한국이면 cp949)을 쓰는데, 거기에는 '🚨'
    같은 글자가 없다. 그 한 글자에 UnicodeEncodeError 가 나면서 속보 확인이
    통째로 실패했다. 출력 경로를 UTF-8 로 고정하고, 그래도 안 되는 글자는
    깨진 채로 넘어가게 한다 — 글자 하나 때문에 기능이 멈추면 안 된다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass        # pythonw 처럼 출력이 아예 없는 경우


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
    sub.add_parser("ensure-config", help="설정이 쓸 만한지 보고, 아니면 그 자리에서 물어보기")

    p_check = sub.add_parser("check", help="새 공시를 1회 확인")
    p_check.add_argument("--force", action="store_true", help="첫 실행이어도 과거 공시를 모두 알림")

    p_brief = sub.add_parser("brief", help="데일리 브리핑 전송")
    p_brief.add_argument("--force", action="store_true", help="오늘 이미 보냈어도 다시 전송")

    p_metrics = sub.add_parser("metrics", help="종목 지표 리포트")
    p_metrics.add_argument("tickers", nargs="*", help="비우면 watchlist 전체")

    p_cal = sub.add_parser("calendar", help="휴장일·경제지표 일정 출력")
    p_cal.add_argument("--days", type=int, default=30, help="며칠 앞까지 볼지 (기본 30)")

    p_report = sub.add_parser("report", help="실제 공시 원문에서 무엇을 뽑아내는지 확인")
    p_report.add_argument("tickers", nargs="*", help="비우면 watchlist 전체")
    p_report.add_argument("--full", action="store_true", help="발췌를 잘라내지 않고 전부 출력")

    p_verify = sub.add_parser(
        "verify", help="화면의 숫자를 SEC 원문과 대조할 수 있게 출처를 전부 출력")
    p_verify.add_argument("tickers", nargs="*", help="비우면 watchlist 전체")

    p_earn = sub.add_parser("earnings", help="실적 발표일 확인 (없으면 과거 간격으로 추정)")
    p_earn.add_argument("tickers", nargs="*", help="비우면 watchlist 전체")
    p_earn.add_argument("--notify", action="store_true", help="콘솔 대신 텔레그램으로 전송")

    sub.add_parser("update", help="git pull + 의존성 설치로 봇을 최신 버전으로 갱신")
    sub.add_parser("doctor", help="SEC 접속·번역이 막힐 때 원인 진단")
    sub.add_parser("test", help="텔레그램 연결 및 설정 확인")

    args = parser.parse_args(argv)
    use_utf8_output()
    setup_logging(args.verbose)

    if args.command == "update":
        return cmd_update()

    config_path = Path(args.config)
    if args.command == "setup":
        return cmd_setup(config_path)

    if not ensure_config(config_path):
        return 2
    if args.command == "ensure-config":
        return 0

    config = _load_or_repair(config_path)
    if config is None:
        return 2

    if args.command == "calendar":
        return cmd_calendar(config, args.days)
    if args.command == "doctor":
        settings = config.raw.get("translate")
        return run_doctor(config.user_agent,
                          settings if isinstance(settings, dict) else {},
                          config.dart_api_key,
                          config.path or args.config)

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
    if args.command == "report":
        return cmd_report(bot, args.tickers, args.full)
    if args.command == "verify":
        return cmd_verify(bot, args.tickers)
    if args.command == "earnings":
        return cmd_earnings(bot, args.tickers, args.notify)
    if args.command == "test":
        return cmd_test(bot)
    return 1


def ensure_config(config_path: Path) -> bool:
    """설정이 쓸 만한 상태가 되게 만든다. 필요하면 물어본다.

    설정이 없으면 마법사를 띄우고(더블클릭 실행 대응), 있는데 값이 잘못됐으면
    그 항목만 다시 묻는다. 그냥 넘어가면 다시 켜도 계속 같은 잘못된 값으로
    돌아서 원인을 찾을 수가 없다. 입력을 받을 수 없는 환경이면 마법사가
    알아서 빠져나오고 안내를 남긴다.
    """
    if not config_path.exists():
        print(f"설정 파일이 없습니다: {config_path}")
        return run_wizard(config_path)
    if find_problems(config_path):
        return repair_wizard(config_path)
    return True


def _load_or_repair(config_path: Path):
    """설정을 읽는다. 값이 잘못됐으면 그 자리에서 다시 물어보고 한 번 더 시도한다."""
    try:
        return load_config(str(config_path))
    except ConfigError as exc:
        print(f"\n설정 오류: {exc}\n", file=sys.stderr)
        if not repair_wizard(config_path):
            return None
    try:
        return load_config(str(config_path))
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        print(f"  {config_path} 를 직접 열어 고친 뒤 다시 실행해주세요.", file=sys.stderr)
        return None


def cmd_setup(config_path: Path) -> int:
    """설정 만들기 / 고치기."""
    if not config_path.exists():
        return 0 if run_wizard(config_path) else 1

    print(f"{config_path} 이 이미 있습니다.")
    print("  1) 값을 다시 입력해서 고치기 (나머지 설정은 그대로)")
    print("  2) 처음부터 새로 만들기 (지금 설정은 config.yml.bak 으로 백업)")
    print("  3) 그만두기")
    try:
        answer = input("번호 [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        print()
        return 1

    if answer == "2":
        backup = config_path.with_suffix(config_path.suffix + ".bak")
        backup.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"지금 설정을 {backup} 에 백업했습니다.")
        config_path.unlink()
        return 0 if run_wizard(config_path) else 1
    if answer == "1":
        return 0 if repair_wizard(config_path, ["contact", "telegram", "watchlist"]) else 1
    print("취소했습니다.")
    return 0


def cmd_report(bot: Bot, tickers: list[str], full: bool) -> int:
    """실제 SEC 원문에서 무엇이 뽑히는지 눈으로 확인하는 명령.

    회사마다 보고서 HTML 구조가 달라 추출이 빗나갈 수 있다.
    화면에 띄우기 전에 이걸로 먼저 검증할 수 있게 만들었다.
    """
    targets = bot.targets()
    if tickers:
        wanted = {t.upper() for t in tickers}
        targets = [t for t in targets if t.ticker.upper() in wanted]
        if not targets:
            print(f"watchlist 에서 찾지 못했습니다: {', '.join(sorted(wanted))}", file=sys.stderr)
            return 1

    limit = 100000 if full else 400
    problems = 0

    for target in targets:
        print()
        print("=" * 70)
        print(f"  {target.ticker}  ({target.name or ''})  CIK {target.cik}")
        print("=" * 70)

        report = bot.report_for(target, refresh=True)
        if report is None:
            print("  [보고서] 원문을 받지 못했습니다.")
            problems += 1
        elif not report.sections:
            print(f"  [보고서] {report.form} {report.filing_date} — 표준 항목을 찾지 못했습니다.")
            print(f"           {report.url}")
            problems += 1
        else:
            print(f"  [보고서] {report.form} · 제출 {report.filing_date} · {report.url}")
            for section in report.sections:
                print()
                print(f"  ── {section.title}  (문단 {len(section.paragraphs)}개)")
                for para in section.paragraphs[: (20 if full else 2)]:
                    print(f"     {para[:limit]}")
            if report.company_words:
                print()
                print(f"  ── 회사가 직접 밝힌 문장 {len(report.company_words)}개")
                for sentence in report.company_words[: (20 if full else 5)]:
                    print(f"     · {sentence[:limit]}")

        guidance = bot.guidance_for(target, refresh=True)
        print()
        if guidance is None:
            print("  [가이던스] 실적 발표(8-K 2.02) 를 찾지 못했습니다.")
        elif not guidance.items:
            print(f"  [가이던스] {guidance.form} {guidance.filing_date} — 전망 문장을 찾지 못했습니다.")
            print(f"             {guidance.url}")
            problems += 1
        else:
            print(f"  [가이던스] {guidance.form} · {guidance.filing_date} · {guidance.url}")
            for item in guidance.items:
                head = " / ".join(x for x in (item.metric, item.period, item.range_text) if x)
                print(f"     · {head or '(분류 실패)'}")
                print(f"       {item.sentence[:limit]}")

        industry = bot.industry_for(target, refresh=True)
        print()
        if industry:
            print(f"  [업종] SIC {industry.sic} · {industry.description}")
            print(f"         동종업계: {', '.join(industry.peers) or '찾지 못함'}")
        else:
            print("  [업종] 분류를 가져오지 못했습니다.")

        estimate = bot.estimate_for(target, refresh=True)
        if estimate and estimate.found:
            print(f"  [컨센서스] EPS {estimate.eps} · 매출 {estimate.revenue} ({estimate.source})")
        else:
            print("  [컨센서스] 자동 수집 실패 — 화면의 '직접 입력' 안내를 참고하세요.")

    print()
    print("=" * 70)
    if problems:
        print(f"  {problems}건은 추출이 빗나갔습니다. 위 원문 링크를 열어 실제 구조를 확인해주세요.")
    else:
        print("  모든 항목을 정상적으로 뽑아냈습니다.")
    print("=" * 70)
    return 0


def cmd_verify(bot: Bot, tickers: list[str]) -> int:
    """화면의 숫자가 어디서 왔는지 전부 펼쳐서 보여준다.

    '이 값을 어떻게 믿나' 에 대한 답은 하나뿐이다 — 원문을 열어 직접 보는 것.
    여기서는 항목 이름, 더한 분기와 그 값, 그리고 그 값이 실린 SEC 공시
    주소까지 찍는다. **한 종목만 직접 맞춰봐도 같은 코드가 만든 나머지를
    믿을 근거가 된다.**
    """
    from stock_analysis.metrics import _money
    from stock_analysis.trust import doubts

    targets = bot.targets()
    if tickers:
        wanted = {t.upper() for t in tickers}
        targets = [t for t in targets if t.ticker.upper() in wanted]
    if not targets:
        print("확인할 종목이 없습니다.")
        return 1

    for target in targets:
        print()
        print("=" * 70)
        print(f"  {target.ticker} — {target.name}")
        print("=" * 70)
        try:
            metrics = bot.metrics_for(target, with_peers=False)
        except Exception as exc:
            print(f"  지표를 계산하지 못했습니다: {exc}")
            continue

        if not metrics.sources:
            print("  출처를 남길 재무 데이터가 없습니다.")
            continue

        for source in metrics.sources.values():
            print()
            if source.note:
                print(f"● {source.label}")
                print(f"    {source.note}")
                continue

            total = f"  =  {_money(source.total)}" if source.total is not None else ""
            print(f"● {source.label}{total}")
            print(f"    SEC 항목  {source.concept}")
            print(f"    구한 방법  {source.how}")
            for part in source.parts:
                print(f"      · {part.when}  {part.shown:>14}  {part.form}")
                if part.url:
                    print(f"        {part.url}")

        shaky = doubts(metrics)
        if shaky:
            print()
            print("● 판단에 쓰지 않은 값 (참고)")
            for doubt in shaky.values():
                print(f"    · {doubt.label} {doubt.shown}")
                print(f"      {doubt.reason}")

        print()
        print("  위 주소를 열어 숫자가 같은지 직접 맞춰보세요.")
        print("  하나만 맞춰봐도 같은 코드가 만든 나머지를 믿을 근거가 됩니다.")
    print()
    return 0


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
    from stock_analysis import __version__

    print()
    print("=" * 58)
    print(f"  관심 종목 감시를 시작합니다  (버전 {__version__})")
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
