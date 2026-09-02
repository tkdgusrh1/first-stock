from datetime import date

from stock_analysis.xbrl import CompanyFacts


def _fact(start, end, val, form="10-Q", filed=None, fp="Q1"):
    return {
        "start": start,
        "end": end,
        "val": val,
        "form": form,
        "fy": int(end[:4]),
        "fp": fp,
        "filed": filed or end,
    }


def _facts(units):
    return CompanyFacts(
        {
            "entityName": "테스트 주식회사",
            "facts": {"us-gaap": units},
        }
    )


def _quarterly_revenue_units(values):
    """values: [(start, end, val), ...]"""
    return {
        "Revenues": {
            "units": {"USD": [_fact(s, e, v) for s, e, v in values]}
        }
    }


QUARTERS = [
    ("2024-01-01", "2024-03-31", 100.0),
    ("2024-04-01", "2024-06-30", 110.0),
    ("2024-07-01", "2024-09-30", 120.0),
    ("2024-10-01", "2024-12-31", 130.0),
    ("2025-01-01", "2025-03-31", 140.0),
    ("2025-04-01", "2025-06-30", 150.0),
    ("2025-07-01", "2025-09-30", 160.0),
    ("2025-10-01", "2025-12-31", 170.0),
]


def test_quarterly_and_ttm():
    facts = _facts(_quarterly_revenue_units(QUARTERS))
    quarters = facts.quarterly("revenue")
    assert len(quarters) == 8
    assert quarters[-1].end == date(2025, 12, 31)
    assert facts.ttm("revenue") == 140 + 150 + 160 + 170
    assert facts.ttm_prior("revenue") == 100 + 110 + 120 + 130


def test_amended_filing_wins():
    units = _quarterly_revenue_units(QUARTERS[:1])
    units["Revenues"]["units"]["USD"].append(
        _fact("2024-01-01", "2024-03-31", 105.0, form="10-Q/A", filed="2024-08-01")
    )
    facts = _facts(units)
    quarters = facts.quarterly("revenue")
    assert len(quarters) == 1
    assert quarters[0].val == 105.0


def test_q4_is_derived_from_annual():
    """10-K만 있는 4분기는 연간 - 3개 분기로 역산해야 한다."""
    three_quarters = QUARTERS[4:7]
    units = _quarterly_revenue_units(three_quarters)
    units["Revenues"]["units"]["USD"].append(
        _fact("2025-01-01", "2025-12-31", 620.0, form="10-K", filed="2026-02-01", fp="FY")
    )
    facts = _facts(units)
    quarters = facts.quarterly("revenue")
    assert len(quarters) == 4
    q4 = quarters[-1]
    assert q4.end == date(2025, 12, 31)
    assert q4.val == 620.0 - (140 + 150 + 160)
    assert facts.ttm("revenue") == 620.0


def test_concept_fallback_order():
    units = {
        "SalesRevenueNet": {"units": {"USD": [_fact("2025-01-01", "2025-03-31", 55.0)]}},
    }
    facts = _facts(units)
    assert facts.quarterly("revenue")[0].val == 55.0


def test_latest_instant_picks_newest():
    units = {
        "StockholdersEquity": {
            "units": {
                "USD": [
                    {"end": "2025-03-31", "val": 1000, "form": "10-Q", "filed": "2025-04-30"},
                    {"end": "2025-06-30", "val": 1200, "form": "10-Q", "filed": "2025-07-30"},
                ]
            }
        }
    }
    facts = _facts(units)
    latest = facts.latest_instant("equity")
    assert latest.val == 1200
    assert latest.end == date(2025, 6, 30)


def test_ttm_falls_back_to_annual_when_quarters_missing():
    units = {
        "Revenues": {
            "units": {
                "USD": [_fact("2025-01-01", "2025-12-31", 900.0, form="10-K", fp="FY")]
            }
        }
    }
    facts = _facts(units)
    assert facts.ttm("revenue") == 900.0


def test_missing_concept_returns_none():
    facts = _facts({})
    assert facts.ttm("revenue") is None
    assert facts.quarterly("revenue") == []
    assert facts.latest_instant("equity") is None
