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
        print("     티커→CIK 조회만 안 되는 상태입니다. config.yml 에 cik 를 직접 적으면")
        print("     나머지 기능(공시·지표)은 전부 정상 동작합니다. 예:")
        print("       watchlist:")
        print("         - ticker: NVDA")
        print("           cik: 1045810")
        code = 1
    else:
        print("  ❌ SEC 접속이 모두 막혔습니다.")
        print("     - 브라우저에서 https://www.sec.gov/files/company_tickers.json 을 열어보세요.")
        print("       브라우저도 안 열리면 네트워크 문제입니다 (백신·방화벽·회사망·공유기).")
        print("     - 백신의 '웹 보호/HTTPS 검사' 를 잠시 꺼보세요.")
        print("     - VPN 을 쓰고 있다면 끄고, 안 쓰고 있다면 켜서 다시 시도해보세요.")
        code = 2

    print("=" * 66)
    print()
    return code
