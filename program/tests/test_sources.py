"""숫자를 SEC 원문과 대조할 수 있는가.

'이 값을 어떻게 믿나' 에 대한 답은 하나뿐이다 — 원문을 열어 직접 보는 것.
그래서 여기서 지키는 것은 두 가지다.
  1) 합계는 **더한 조각을 그대로 남겨서** 덧셈을 눈으로 검산할 수 있어야 한다
  2) 조각마다 그 값이 실린 SEC 공시로 가는 주소가 있어야 한다
없는 링크를 지어내는 것은 링크가 없는 것보다 나쁘다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analysis.metrics import collect_sources  # noqa: E402
from stock_analysis.xbrl import CompanyFacts  # noqa: E402

CIK = 320193
QUARTERS = [
    (90e9, "2025-01-01", "2025-03-31", "10-Q", "0000320193-25-000052"),
    (94e9, "2025-04-01", "2025-06-30", "10-Q", "0000320193-25-000073"),
    (95e9, "2025-07-01", "2025-09-30", "10-Q", "0000320193-25-000106"),
    (124e9, "2025-10-01", "2025-12-31", "10-K", "0000320193-26-000012"),
]


def facts(quarters=None, cik=CIK, accn=True) -> CompanyFacts:
    """SEC companyfacts 응답과 같은 모양으로 만든다."""
    rows = []
    for value, start, end, form, number in (quarters or QUARTERS):
        row = {"val": value, "start": start, "end": end, "form": form,
               "filed": end, "fy": 2025, "fp": "Q1"}
        if accn:
            row["accn"] = number
        rows.append(row)
    return CompanyFacts({
        "cik": cik, "entityName": "Apple Inc.",
        "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": rows}},
            "StockholdersEquity": {"units": {"USD": [
                {"val": 57e9, "end": "2025-12-31", "form": "10-K",
                 "filed": "2026-02-01", "accn": "0000320193-26-000012"},
            ]}},
        }},
    })


# --- 덧셈이 눈에 보이는가 ---------------------------------------------------
def test_a_sum_shows_the_quarters_that_made_it():
    """'4개 분기 합산' 이라고만 하면 그 말을 믿는 수밖에 없다."""
    source = collect_sources(facts())["revenue"]

    assert source.checkable
    assert len(source.parts) == 4
    assert source.total == sum(q[0] for q in QUARTERS)
    assert sum(part.value for part in source.parts) == source.total   # 덧셈이 맞는다


def test_each_quarter_says_when_and_from_which_filing():
    parts = collect_sources(facts())["revenue"].parts

    assert parts[0].when == "2025-01-01 ~ 2025-03-31"
    assert parts[0].shown == "$90.00B"
    assert parts[-1].form == "10-K"


def test_a_single_value_needs_no_arithmetic_check():
    source = collect_sources(facts())["equity"]

    assert not source.checkable            # 더한 게 아니라 한 시점의 잔액
    assert source.total == 57e9
    assert "시점의 잔액" in source.how


# --- 원문으로 가는 주소 -----------------------------------------------------
def test_every_quarter_links_to_the_filing_it_came_from():
    for part in collect_sources(facts())["revenue"].parts:
        assert part.url.startswith("https://www.sec.gov/Archives/edgar/data/")
        assert part.url.endswith("-index.htm")


def test_the_link_uses_the_company_cik_not_the_filing_agent():
    """접수번호 앞자리는 제출을 대행한 쪽의 번호일 수 있다. 그걸 쓰면 주소가 어긋난다."""
    # 접수번호는 대행사(0001104659) 것인데 회사는 애플(320193) 인 경우
    quarters = [(90e9, "2025-01-01", "2025-03-31", "10-Q", "0001104659-25-000052")]
    part = collect_sources(facts(quarters))["revenue"].parts[0]

    assert "/data/320193/" in part.url
    assert "/data/1104659/" not in part.url


def test_no_accession_number_means_no_link_rather_than_a_made_up_one():
    """없는 링크를 지어내는 것은 링크가 없는 것보다 나쁘다."""
    for part in collect_sources(facts(accn=False))["revenue"].parts:
        assert part.url == ""


def test_a_missing_cik_also_means_no_link():
    for part in collect_sources(facts(cik=""))["revenue"].parts:
        assert part.url == ""


# --- 재무제표가 아닌 값 -----------------------------------------------------
def test_a_price_says_it_is_not_from_a_filing():
    """시세는 SEC 자료가 아니다. 같은 칸에 섞어 놓으면 안 된다."""
    from stock_analysis.metrics import Metrics, Source

    m = Metrics(ticker="AAPL")
    m.sources["price"] = Source(key="price", label="주가",
                                note="Stooq · 종가 (2026-09-03) — 재무제표가 아니라 시세 제공처에서 받은 값입니다.")

    assert m.sources["price"].note
    assert not m.sources["price"].checkable
    assert "재무제표가 아니라" in m.sources["price"].text
