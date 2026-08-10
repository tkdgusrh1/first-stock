"""테스트용 companyfacts 생성기."""

from __future__ import annotations

from stockbot.xbrl import CompanyFacts

QUARTER_ENDS = [
    ("2024-01-01", "2024-03-31"),
    ("2024-04-01", "2024-06-30"),
    ("2024-07-01", "2024-09-30"),
    ("2024-10-01", "2024-12-31"),
    ("2025-01-01", "2025-03-31"),
    ("2025-04-01", "2025-06-30"),
    ("2025-07-01", "2025-09-30"),
    ("2025-10-01", "2025-12-31"),
]


def duration_unit(values: list[float], unit: str = "USD") -> dict:
    entries = []
    for (start, end), val in zip(QUARTER_ENDS, values):
        entries.append(
            {
                "start": start,
                "end": end,
                "val": val,
                "form": "10-Q",
                "fy": int(end[:4]),
                "fp": "Q1",
                "filed": end,
            }
        )
    return {"units": {unit: entries}}


def instant_unit(value: float, end: str = "2025-12-31", unit: str = "USD") -> dict:
    return {"units": {unit: [{"end": end, "val": value, "form": "10-K", "filed": end}]}}


def build_facts(
    *,
    revenue: list[float],
    net_income: list[float],
    operating_income: list[float] | None = None,
    ocf: list[float] | None = None,
    capex: list[float] | None = None,
    eps: list[float] | None = None,
    equity: float | None = None,
    cash: float | None = None,
    debt: float | None = None,
    shares: float | None = None,
    name: str = "테스트 주식회사",
) -> CompanyFacts:
    units: dict = {
        "Revenues": duration_unit(revenue),
        "NetIncomeLoss": duration_unit(net_income),
    }
    if operating_income:
        units["OperatingIncomeLoss"] = duration_unit(operating_income)
    if ocf:
        units["NetCashProvidedByUsedInOperatingActivities"] = duration_unit(ocf)
    if capex:
        units["PaymentsToAcquirePropertyPlantAndEquipment"] = duration_unit(capex)
    if eps:
        units["EarningsPerShareDiluted"] = duration_unit(eps, unit="USD/shares")
    if equity is not None:
        units["StockholdersEquity"] = instant_unit(equity)
    if cash is not None:
        units["CashAndCashEquivalentsAtCarryingValue"] = instant_unit(cash)
    if debt is not None:
        units["LongTermDebtNoncurrent"] = instant_unit(debt)
    if shares is not None:
        units["CommonStockSharesOutstanding"] = instant_unit(shares, unit="shares")
    return CompanyFacts({"entityName": name, "facts": {"us-gaap": units}})
