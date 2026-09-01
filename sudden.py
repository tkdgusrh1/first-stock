#!/usr/bin/env python3
"""서든어택 최적화 — 시작 지점.

사용 예:
  python sudden.py            # 화면을 띄운다 (기본)
  python sudden.py status     # 지금 상태만 콘솔에 출력
  python sudden.py apply      # 권장 항목을 바로 적용
  python sudden.py revert     # 마지막 최적화를 되돌린다
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sudden_attack import __version__, backup, engine, ui
from sudden_attack.shell import WINDOWS, is_admin

ROOT = Path(__file__).resolve().parent


def use_utf8_output() -> None:
    """한글이 깨지지 않게. 윈도우 콘솔은 기본이 cp949 라서 그냥 두면 글자가 깨진다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def build(root: Path = ROOT) -> engine.Optimizer:
    return engine.Optimizer(engine.build_context(root), root=root)


def cmd_screen(args) -> int:
    optimizer = build()
    screen = ui.Screen(optimizer, root=ROOT)
    server, url = ui.start(screen, port=args.port, open_browser=not args.no_browser)

    print()
    print("  서든어택 최적화 v" + __version__)
    print("  화면:", url)
    if WINDOWS and not is_admin():
        print("  ! 관리자 권한이 아닙니다 — 일부 항목이 잠깁니다.")
        print("    바탕화면의 시작 파일을 우클릭 → '관리자 권한으로 실행' 하시면 전부 풀립니다.")
    print()
    print("  이 창을 닫거나 Ctrl+C 를 누르면 끝납니다.")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("  끝냅니다.")
    finally:
        server.shutdown()
        server.server_close()
    return 0


def cmd_status(args) -> int:
    optimizer = build()
    ctx = optimizer.ctx
    spec = engine.spec_of(ctx)

    print()
    print(f"  CPU     {spec.cpu or '-'}")
    print(f"  그래픽  {spec.gpu or '-'}")
    print(f"  메모리  {spec.ram_gb or '-'} GB")
    print(f"  윈도우  {spec.windows or '-'}")
    for screen in spec.monitors:
        extra = "" if screen.already_best else f"  (최대 {screen.best_hz}Hz 가능)"
        print(f"  모니터  {screen.width}x{screen.height} {screen.hz}Hz{extra}")
    print(f"  서든어택 {ctx.install.exe if ctx.install else '못 찾음'}")
    print(f"  권한    {'관리자' if ctx.admin else '일반 사용자'}")
    print()

    marks = {"on": "[적용됨]", "off": "[ 안됨 ]", "unknown": "[확인??]", "na": "[해당없음]"}
    for status in optimizer.statuses():
        note = f"  ({status.blocked})" if status.blocked else ""
        print(f"  {marks.get(status.state, '[  ?  ]')} {status.tweak.title}{note}")

    record = backup.latest(ROOT)
    print()
    if record:
        print(f"  되돌릴 기록: {record.label} ({len(record.keys)}개)")
    else:
        print("  되돌릴 기록이 없습니다.")
    return 0


def cmd_apply(args) -> int:
    optimizer = build()
    keys = args.only or optimizer.recommended_keys()
    if not keys:
        print("  바꿀 것이 없습니다. 이미 다 되어 있습니다.")
        return 0

    outcome = optimizer.apply(keys)
    print()
    for step in outcome.steps:
        print(f"  {'O' if step.ok else 'X'}  {step.title} — {step.message}")
    print()
    print("  " + outcome.summary)
    if outcome.record:
        print(f"  되돌리기 기록: {outcome.record.name}")
        print("  되돌리려면: python sudden.py revert")
    return 0 if not outcome.failed else 1


def cmd_revert(args) -> int:
    optimizer = build()
    outcome = optimizer.revert()
    if not outcome.steps:
        print("  되돌릴 기록이 없습니다.")
        return 0
    print()
    for step in outcome.steps:
        print(f"  {'O' if step.ok else 'X'}  {step.title} — {step.message}")
    print()
    print(f"  {outcome.done}개를 원래대로 되돌렸습니다.")
    return 0 if not outcome.failed else 1


def main(argv=None) -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(
        prog="sudden", description="서든어택 최적화 — 윈도우 설정을 게임에 맞게 한 번에"
    )
    parser.add_argument("--verbose", action="store_true", help="자세한 기록 출력")
    sub = parser.add_subparsers(dest="command")

    screen = sub.add_parser("screen", help="화면 띄우기 (기본)")
    screen.add_argument("--port", type=int, default=8770)
    screen.add_argument("--no-browser", action="store_true")
    screen.set_defaults(func=cmd_screen)

    sub.add_parser("status", help="지금 상태 보기").set_defaults(func=cmd_status)

    apply_cmd = sub.add_parser("apply", help="권장 항목 적용")
    apply_cmd.add_argument("--only", nargs="*", help="이 항목만 (예: mouse_accel refresh_rate)")
    apply_cmd.set_defaults(func=cmd_apply)

    sub.add_parser("revert", help="마지막 최적화 되돌리기").set_defaults(func=cmd_revert)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    if not getattr(args, "func", None):
        args.port, args.no_browser = 8770, False
        return cmd_screen(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
