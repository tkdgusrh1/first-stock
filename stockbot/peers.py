"""동종업계 종목을 자동으로 찾는다.

SEC 는 회사마다 산업분류 코드(SIC)를 붙여 둔다. 그 코드로 같은 업종의
다른 상장사를 EDGAR 에서 조회하고, 티커가 있는 회사만 골라낸다.

임의로 '비슷해 보이는 회사' 를 지어내지 않는다. SEC 가 같은 업종으로
분류한 회사만 쓰고, 어떤 분류인지 화면에 함께 표시한다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
BROWSE_SIC_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&SIC={sic}"
    "&type=10-K&dateb=&owner=include&count={count}&action=getcompany"
)

DEFAULT_PEER_COUNT = 4
_MAX_SCAN = 100


@dataclass
class Industry:
    sic: str
    description: str
    peers: list[str]                 # 티커 목록


def industry_of(http, cik: str) -> tuple[str | None, str]:
    """이 회사의 SIC 코드와 업종 이름."""
    try:
        data = http.get_json(SUBMISSIONS_URL.format(cik=cik))
    except Exception as exc:
        log.warning("업종 조회 실패 (CIK %s): %s", cik, exc)
        return None, ""
    sic = str(data.get("sic") or "").strip()
    return (sic or None), str(data.get("sicDescription") or "").strip()


def _ciks_in_industry(http, sic: str, count: int = _MAX_SCAN) -> list[str]:
    """같은 SIC 코드를 가진 회사들의 CIK."""
    try:
        html = http.get_text(BROWSE_SIC_URL.format(sic=sic, count=count), timeout=60)
    except Exception as exc:
        log.warning("업종 목록 조회 실패 (SIC %s): %s", sic, exc)
        return []

    found: list[str] = []
    for match in re.finditer(r"CIK=(\d{10})", html):
        cik = match.group(1)
        if cik not in found:
            found.append(cik)
    return found


def find_peers(
    http,
    edgar,
    cik: str,
    ticker: str,
    limit: int = DEFAULT_PEER_COUNT,
) -> Industry | None:
    """같은 업종에서 티커가 있는 회사를 limit 개까지 찾는다."""
    sic, description = industry_of(http, cik)
    if not sic:
        return None

    try:
        by_cik = {mapped: tkr for tkr, (mapped, _) in edgar.ticker_map().items()}
    except Exception as exc:
        log.warning("티커 목록이 없어 동종업계를 찾지 못했습니다: %s", exc)
        return Industry(sic=sic, description=description, peers=[])

    peers: list[str] = []
    for other in _ciks_in_industry(http, sic):
        if other == cik:
            continue
        peer_ticker = by_cik.get(other)
        # 우선주·워런트 등 접미사가 붙은 티커는 건너뛴다
        if not peer_ticker or not peer_ticker.isalpha() or peer_ticker == ticker.upper():
            continue
        if peer_ticker not in peers:
            peers.append(peer_ticker)
        if len(peers) >= limit:
            break

    return Industry(sic=sic, description=description, peers=peers)
