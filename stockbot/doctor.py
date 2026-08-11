"""연결 진단.

SEC 접속이 막히는 원인은 환경마다 다르다(봇 차단, 백신, 회사망, VPN…).
추측 대신 실제로 여러 조합을 두드려 보고 무엇이 되는지 표로 보여준다.
"""

from __future__ import annotations

import socket
import ssl
import time
from urllib.parse import urlparse

import requests

from .http import build_profiles, sanitize_user_agent

TARGETS = [
    ("SEC 티커 목록", "https://www.sec.gov/files/company_tickers.json"),
    ("SEC 티커 목록(대체)", "https://www.sec.gov/files/company_tickers_exchange.json"),
    ("SEC 공시 데이터", "https://data.sec.gov/submissions/CIK0000320193.json"),
    ("SEC 재무 데이터", "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"),
    ("EDGAR 검색", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=8-K&count=1&output=atom"),
    ("주가(Stooq)", "https://stooq.com/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv"),
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


def run_doctor(user_agent: str) -> int:
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
