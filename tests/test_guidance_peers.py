"""가이던스 추출 · 동종업계 자동 탐색 · 컨센서스 수집."""

import json

import pytest

from stockbot.estimates import EstimateClient, links_for
from stockbot.guidance import classify, extract_guidance, parse_numbers
from stockbot.peers import find_peers, industry_of

PRESS_RELEASE = """<html><body>
<p>Rocket Lab Reports Second Quarter 2026 Financial Results</p>
<p>Revenue increased 32% to $105.6 million for the second quarter of 2026,
   compared to $80.0 million in the same period of 2025.</p>
<p>GAAP net loss was $41.0 million for the quarter.</p>
<p>Third Quarter 2026 Guidance</p>
<p>For the third quarter of 2026, we expect revenue of $120 million to $130 million.</p>
<p>We expect GAAP gross margin of 30% to 32% for the third quarter.</p>
<p>We are raising our full-year 2026 revenue outlook to approximately $480 million.</p>
<p>We remain committed to serving our customers with excellence.</p>
</body></html>"""


# --- 가이던스 ---------------------------------------------------------------
def report():
    return extract_guidance(PRESS_RELEASE, "8-K", "2026-08-05", "https://sec.gov/x.htm")


def test_guidance_sentences_are_found():
    assert report().found
    assert len(report().items) >= 2


def test_guidance_keeps_the_original_sentence():
    """요약하지 않는다. 회사가 쓴 문장 그대로여야 한다."""
    sentences = [i.sentence for i in report().items]
    assert any("we expect revenue of $120 million to $130 million" in s for s in sentences)


def test_guidance_range_is_parsed():
    revenue = next(i for i in report().items if i.metric == "매출" and i.low)
    assert revenue.low == 120e6
    assert revenue.high == 130e6
    assert "120" in revenue.range_text and "130" in revenue.range_text


def test_percent_guidance_is_parsed():
    low, high, unit = parse_numbers("We expect GAAP gross margin of 30% to 32% for the third quarter.")
    assert (low, high, unit) == (30.0, 32.0, "%")


def test_single_value_guidance():
    low, high, unit = parse_numbers("full-year revenue of approximately $480 million")
    assert low == 480e6 and high is None and unit == "$"


def test_sentences_without_numbers_are_ignored():
    """'최선을 다하겠다' 같은 문장은 가이던스가 아니다."""
    sentences = [i.sentence for i in report().items]
    assert not any("committed to serving our customers" in s for s in sentences)


def test_metric_and_period_are_classified():
    metric, period = classify("For the third quarter of 2026, we expect revenue of $120 million.")
    assert metric == "매출"
    assert "third quarter" in (period or "").lower()


def test_results_sentences_are_collected():
    results = report().results
    assert any("Revenue increased 32%" in s for s in results)


def test_empty_document():
    empty = extract_guidance("<p>아무 내용 없음</p>", "8-K", "2026-01-01", "u")
    assert not empty.found
    assert empty.results == []


# --- 동종업계 ---------------------------------------------------------------
SIC_PAGE = """<html><body><table>
<tr><td><a href="/cgi-bin/browse-edgar?action=getcompany&CIK=0001819994">RKLB</a></td></tr>
<tr><td><a href="/cgi-bin/browse-edgar?action=getcompany&CIK=0001318605">TSLA</a></td></tr>
<tr><td><a href="/cgi-bin/browse-edgar?action=getcompany&CIK=0001045810">NVDA</a></td></tr>
<tr><td><a href="/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193">AAPL</a></td></tr>
</table></body></html>"""


class FakeIndustryHttp:
    def __init__(self, sic="3760", description="Guided Missiles & Space Vehicles"):
        self.sic, self.description = sic, description

    def get_json(self, url, **kwargs):
        return {"sic": self.sic, "sicDescription": self.description}

    def get_text(self, url, **kwargs):
        return SIC_PAGE


class FakeEdgar:
    def ticker_map(self):
        return {
            "RKLB": ("0001819994", "Rocket Lab"),
            "TSLA": ("0001318605", "Tesla"),
            "NVDA": ("0001045810", "NVIDIA"),
            "AAPL": ("0000320193", "Apple"),
        }


def test_industry_lookup():
    sic, description = industry_of(FakeIndustryHttp(), "0001819994")
    assert sic == "3760"
    assert "Space" in description


def test_peers_exclude_self_and_map_to_tickers():
    industry = find_peers(FakeIndustryHttp(), FakeEdgar(), "0001819994", "RKLB", limit=4)
    assert industry.sic == "3760"
    assert "RKLB" not in industry.peers
    assert set(industry.peers) <= {"TSLA", "NVDA", "AAPL"}
    assert len(industry.peers) == 3


def test_peers_respect_the_limit():
    industry = find_peers(FakeIndustryHttp(), FakeEdgar(), "0001819994", "RKLB", limit=2)
    assert len(industry.peers) == 2


def test_missing_sic_returns_none():
    class NoSic(FakeIndustryHttp):
        def get_json(self, url, **kwargs):
            return {}

    assert find_peers(NoSic(), FakeEdgar(), "0001819994", "RKLB") is None


def test_broken_ticker_map_still_reports_industry():
    class BrokenEdgar:
        def ticker_map(self):
            raise RuntimeError("403")

    industry = find_peers(FakeIndustryHttp(), BrokenEdgar(), "0001819994", "RKLB")
    assert industry.sic == "3760"
    assert industry.peers == []


# --- 컨센서스 ---------------------------------------------------------------
class FakeYahoo:
    def __init__(self, payload=None, fail=False):
        self.payload = payload
        self.fail = fail

    def get(self, url, **kwargs):
        if self.fail:
            raise RuntimeError("blocked")
        return object()

    def get_text(self, url, **kwargs):
        if self.fail:
            raise RuntimeError("blocked")
        if "getcrumb" in url:
            return "abc123"
        return json.dumps(self.payload)


SUMMARY = {
    "quoteSummary": {
        "result": [
            {
                "earningsTrend": {
                    "trend": [
                        {
                            "period": "0q",
                            "earningsEstimate": {"avg": {"raw": 1.01}, "numberOfAnalysts": {"raw": 12}},
                            "revenueEstimate": {"avg": {"raw": 45000000000}},
                        }
                    ]
                },
                "earningsHistory": {
                    "history": [
                        {
                            "quarter": {"fmt": "2026-06-30"},
                            "epsActual": {"raw": 1.10},
                            "epsEstimate": {"raw": 1.00},
                            "surprisePercent": {"raw": 0.10},
                        }
                    ]
                },
            }
        ]
    }
}


def test_consensus_is_parsed_when_available():
    estimate = EstimateClient(FakeYahoo(SUMMARY)).fetch("NVDA")
    assert estimate.eps == 1.01
    assert estimate.revenue == 45000000000
    assert estimate.analysts == 12
    assert estimate.source == "Yahoo Finance"
    assert estimate.history[0]["actual"] == 1.10


def test_blocked_consensus_returns_none_not_a_guess():
    """자동 수집이 막히면 추측값을 만들지 않고 None 을 준다."""
    client = EstimateClient(FakeYahoo(fail=True))
    assert client.fetch("NVDA") is None
    # 한 번 막히면 다시 시도하지 않는다
    assert client._blocked


@pytest.mark.parametrize("name", ["Yahoo Finance", "Nasdaq", "Zacks", "StockAnalysis"])
def test_manual_lookup_links_are_provided(name):
    links = {n: (url, hint) for n, url, hint in links_for("NVDA")}
    assert name in links
    url, hint = links[name]
    assert "NVDA" in url and url.startswith("https://")
    assert hint
