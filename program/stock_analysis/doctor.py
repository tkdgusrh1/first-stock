"""연결 진단.

SEC 접속이 막히는 원인은 환경마다 다르다(봇 차단, 백신, 회사망, VPN…).
추측 대신 실제로 여러 조합을 두드려 보고 무엇이 되는지 표로 보여준다.
"""

from __future__ import annotations

import socket
import ssl
import time
from datetime import date

import requests

from .http import build_profiles, sanitize_user_agent

TARGETS = [
    ("SEC 티커 목록", "https://www.sec.gov/files/company_tickers.json"),
    ("SEC 티커 목록(대체)", "https://www.sec.gov/files/company_tickers_exchange.json"),
    ("SEC ETF 목록", "https://www.sec.gov/files/company_tickers_mf.json"),
    ("SEC 공시 데이터", "https://data.sec.gov/submissions/CIK0000320193.json"),
    ("SEC 재무 데이터", "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"),
    ("EDGAR 검색", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=8-K&count=1&output=atom"),
    ("주가(Stooq)", "https://stooq.com/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv"),
    ("경제 지표(FRED)", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2026-01-01"),
]

OK, BLOCKED, FAILED = "성공", "차단(403)", "실패"


def _probe(url: str, headers: dict, timeout: float = 15.0) -> tuple[str, str]:
    """(상태, 설명)"""
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.SSLError as exc:
        return FAILED, f"SSL 오류 — 백신·방화벽이 통신을 가로채는 중일 수 있습니다 ({_short(exc)})"
    except requests.exceptions.ProxyError as exc:
        return FAILED, f"프록시 오류 ({_short(exc)})"
    except requests.exceptions.ConnectTimeout:
        return FAILED, "연결 시간 초과 — 방화벽에 막혔을 수 있습니다"
    except requests.exceptions.ConnectionError as exc:
        return FAILED, f"연결 불가 ({_short(exc)})"
    except requests.RequestException as exc:
        return FAILED, _short(exc)

    if resp.status_code == 403:
        return BLOCKED, "서버가 거부함"
    if resp.status_code == 200:
        return OK, f"{len(resp.content):,} 바이트"
    return FAILED, f"HTTP {resp.status_code}"


def _short(exc: Exception, limit: int = 90) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _dns_check(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError as exc:
        return f"조회 실패 ({exc})"


def _print_manual_file_help() -> None:
    from pathlib import Path

    print("     방법 1) 목록을 직접 저장하기 (가장 확실)")
    print("       1. 브라우저에서 https://www.sec.gov/files/company_tickers.json 열기")
    print("       2. Ctrl+S 를 눌러 저장. 파일 형식은 '모든 파일',")
    print("          파일 이름은 company_tickers.json")
    print(f"       3. 저장 위치: {Path('.').resolve()}")
    print("       4. 봇을 다시 실행하면 그 파일을 먼저 사용합니다")


def _check_candidates(user_agent: str) -> None:
    """추천 후보 목록을 SEC 에서 받을 수 있는지 실제로 물어본다.

    이 호출은 '눈여겨볼 종목' 이 통째로 매달려 있는 곳이다. 여기가 막히면
    추천이 조용히 감시 목록 안에서만 돌게 되므로, 되는지 안 되는지를
    직접 확인할 수 있어야 한다.
    """
    from .universe import FRAMES_URL, REVENUE_CONCEPTS

    print("● 추천 후보 목록 (SEC 매출 순위)")
    year = str(date.today().year - 1)
    url = FRAMES_URL.format(concept=REVENUE_CONCEPTS[0], period=year)
    print(f"  {url}")
    try:
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=60)
    except Exception as exc:
        print(f"    - 실패       {_short(exc)}")
        print("     추천은 감시 목록 안에서만 돌게 됩니다.")
        print()
        return

    if resp.status_code != 200:
        print(f"    - HTTP {resp.status_code}")
        print(f"     {year}년 자료가 아직 없을 수 있습니다. 프로그램은 이전 연도로 다시 시도합니다.")
        print()
        return

    try:
        rows = (resp.json() or {}).get("data") or []
    except ValueError:
        print("    - 실패       JSON 이 아닙니다")
        print()
        return

    print(f"    - 성공       {year}년 매출을 신고한 기업 {len(rows):,}개")
    if rows:
        top = max(rows, key=lambda r: r.get("val") or 0)
        print(f"      가장 큰 곳: {top.get('entityName', '?')} (CIK {top.get('cik')})")
    print()


def _check_translation(settings: dict | None = None) -> None:
    """번역기가 실제로 도는지 확인한다. 어느 것이 되는지 눈으로 보여준다."""
    from .http import HttpClient
    from .translate import PROVIDERS, Translator

    print("● 번역")
    sample = "Revenue increased 78% year over year to $213.0 million."
    print(f"  시험 문장: {sample}")

    translator = Translator(HttpClient(user_agent="first-stock doctor"),
                            cache_dir=".cache-doctor", settings=settings or {})
    ready = translator.available()

    for provider in PROVIDERS:
        if provider.key not in ready:
            reason = "열쇠 없음" if provider.needs_key else "꺼짐"
            print(f"    - {provider.label:<14} 건너뜀     {reason} · {provider.note}")
            continue
        try:
            text = translator._run(provider.key, sample)
        except Exception as exc:      # 진단 도구는 어떤 오류에도 계속 진행한다
            text = ""
            print(f"    - {provider.label:<14} 실패       {_short(exc)}")
            continue
        if text:
            print(f"    - {provider.label:<14} 성공       {text}")
        else:
            print(f"    - {provider.label:<14} 실패       응답이 비어 있습니다")

    if not ready:
        print("  ⚠️  쓸 수 있는 번역기가 없습니다. config 의 translate 를 확인하세요.")
    print("     규칙으로 옮기는 한글 요약은 번역기 없이도 그대로 나옵니다.")
    print()


def run_doctor(user_agent: str, translate_settings: dict | None = None) -> int:
    agent = sanitize_user_agent(user_agent)
    profiles = build_profiles(agent)

    print()
    print("=" * 66)
    print("  연결 진단")
    print("=" * 66)
    print(f"  User-Agent : {agent}")
    print(f"  파이썬     : {requests.__name__} {requests.__version__} / OpenSSL {ssl.OPENSSL_VERSION}")
    for host in ("www.sec.gov", "data.sec.gov"):
        print(f"  DNS {host:<14}: {_dns_check(host)}")
    print()

    results: dict[tuple[str, str], str] = {}
    working: set[str] = set()

    for label, url in TARGETS:
        print(f"● {label}")
        print(f"  {url}")
        for name, headers in profiles.items():
            status, detail = _probe(url, headers)
            results[(label, name)] = status
            if status == OK:
                working.add(name)
            print(f"    - 헤더조합 {name:<8} {status:<9} {detail}")
            if status == OK:
                break          # 되는 조합을 찾았으면 다음 주소로
            time.sleep(0.3)
        print()

    _check_candidates(agent)
    _check_translation(translate_settings)

    print("=" * 66)
    sec_ok = any(results.get((label, name)) == OK for label, url in TARGETS
                 for name in profiles if "sec.gov" in url)
    data_ok = any(results.get((label, name)) == OK for label, url in TARGETS
                  for name in profiles if "data.sec.gov" in url)
    www_ok = any(results.get((label, name)) == OK for label, url in TARGETS
                 for name in profiles if "www.sec.gov" in url)

    if sec_ok and www_ok:
        best = sorted(working)[0] if working else "sec"
        print(f"  ✅ SEC 접속 정상입니다. (헤더조합 '{best}')")
        print("     봇을 다시 실행하면 종목 정보가 채워집니다.")
        code = 0
    elif data_ok and not www_ok:
        print("  ⚠️  data.sec.gov 는 되는데 www.sec.gov 만 막혔습니다.")
        print("     티커→CIK 조회만 안 되는 상태이고, 공시·지표는 전부 정상입니다.")
        _print_manual_file_help()
        print("     방법 2) CIK 를 직접 넣기 — 화면 입력창에 'NVDA:1045810' 처럼 입력")
        code = 1
    else:
        print("  ❌ 파이썬에서 SEC 접속이 막혔습니다.")
        print()
        print("     먼저 브라우저에서 아래 주소를 열어보세요.")
        print("       https://www.sec.gov/files/company_tickers.json")
        print()
        print("     [브라우저에서는 열린다]  ← SEC 가 파이썬 요청만 막는 경우입니다")
        _print_manual_file_help()
        print()
        print("     [브라우저에서도 안 열린다]  ← 네트워크 문제입니다")
        print("       - 백신(V3·알약 등)의 '웹 보호 / HTTPS 검사' 를 잠시 꺼보세요")
        print("       - VPN 을 쓰고 있다면 끄고, 안 쓰고 있다면 켜서 다시 시도")
        print("       - 회사·학교 망이라면 그쪽에서 차단하는 것입니다")
        code = 2

    print("=" * 66)
    print()
    return code
