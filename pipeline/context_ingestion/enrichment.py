"""
Step 5: Enrichment — extract signals, drivers, metrics, and infer topic.

Regex and keyword-based extraction that classifies each chunk with:
- signals: directional market indicators (e.g. "prices_up", "inventory_down")
- drivers: causal factors (e.g. "mortgage_rates", "fed_policy")
- metrics_mentioned: specific data points referenced in the text
- topic: best-fit topic from the source config
"""

from __future__ import annotations

import re
from typing import List


# ---------------------------------------------------------------------------
# Signal patterns (regex → label)
# ---------------------------------------------------------------------------

_SIGNAL_PATTERNS = [
    (r"price[s]?\s+(?:rose|increased|climbed|surged|jumped|up)", "prices_up"),
    (r"price[s]?\s+(?:fell|dropped|declined|decreased|down)", "prices_down"),
    (r"inventory\s+(?:rose|increased|climbed|grew|up|surged)", "inventory_up"),
    (r"inventory\s+(?:fell|dropped|declined|shrank|down|low)", "inventory_down"),
    (r"demand\s+(?:rose|increased|strong|surged|high)", "demand_up"),
    (r"demand\s+(?:fell|weakened|dropped|cooled|slowed)", "demand_down"),
    (r"(?:rate[s]?\s+(?:rose|increased|climbed|hiked|up))", "rates_up"),
    (r"(?:rate[s]?\s+(?:fell|dropped|cut|decreased|down))", "rates_down"),
    (r"(?:sales\s+(?:rose|increased|climbed|surged|up))", "sales_up"),
    (r"(?:sales\s+(?:fell|dropped|declined|decreased|down))", "sales_down"),
    (r"(?:supply\s+(?:tight|low|limited|constrained|shortage))", "supply_tight"),
    (r"(?:construction|building|starts)\s+(?:rose|increased|up)", "construction_up"),
    (r"(?:construction|building|starts)\s+(?:fell|declined|down)", "construction_down"),
]


# ---------------------------------------------------------------------------
# Driver keywords (driver_name → keyword list)
# ---------------------------------------------------------------------------

_DRIVER_KEYWORDS = {
    "mortgage_rates": ["mortgage rate", "interest rate", "30-year", "30 year", "fixed rate"],
    "inflation": ["inflation", "cpi", "consumer price", "cost of living"],
    "employment": ["employment", "unemployment", "jobs", "labor market", "payroll"],
    "fed_policy": ["federal reserve", "fed ", "fomc", "monetary policy", "rate hike", "rate cut"],
    "housing_supply": ["housing supply", "new construction", "housing starts", "building permits"],
    "migration": ["migration", "relocat", "moving to", "moving from", "inbound", "outbound"],
    "affordability": ["affordability", "affordable", "unaffordable", "price-to-income"],
    "economy": ["gdp", "recession", "economic growth", "economy"],
}


# ---------------------------------------------------------------------------
# Metric keywords
# ---------------------------------------------------------------------------

_METRIC_KEYWORDS = [
    "median sale price", "median home price", "median price",
    "days on market", "dom",
    "inventory", "active listings",
    "price drop", "price reduction", "price cut",
    "homes sold", "sales volume",
    "new listings",
    "months of supply",
    "sale-to-list", "list price",
    "mortgage rate", "interest rate",
    "housing starts", "building permits",
    "cpi", "inflation rate",
    "unemployment rate",
]


# ---------------------------------------------------------------------------
# Topic inference keywords
# ---------------------------------------------------------------------------

_TOPIC_SIGNALS = {
    "pricing": ["price", "median", "cost", "afford", "value"],
    "inventory": ["inventory", "listing", "supply", "active listing"],
    "demand": ["demand", "buyer", "bidding", "offer", "competition", "sold"],
    "forecast": ["forecast", "predict", "expect", "outlook", "project"],
    "mortgage_rates": ["mortgage", "rate", "interest", "fixed"],
    "interest_rates": ["interest rate", "fed funds", "federal reserve"],
    "inflation": ["inflation", "cpi", "consumer price"],
    "economy": ["gdp", "recession", "economic", "growth"],
    "employment": ["employment", "unemployment", "jobs", "labor"],
    "housing_supply": ["construction", "housing starts", "building", "permits"],
    "construction": ["construction", "build", "starts", "permits"],
    "migration": ["migration", "moving", "relocat", "inbound"],
    "sales": ["sold", "sales", "transaction", "closed"],
    "local_market": ["local", "city", "neighborhood", "metro"],
    "policy": ["policy", "regulation", "zoning", "law", "legislation"],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_signals(text: str) -> List[str]:
    """Extract directional market signals from text."""
    text_lower = text.lower()
    signals = []
    for pattern, signal in _SIGNAL_PATTERNS:
        if re.search(pattern, text_lower):
            signals.append(signal)
    return list(set(signals))


def extract_drivers(text: str) -> List[str]:
    """Extract causal driver categories mentioned in text."""
    text_lower = text.lower()
    drivers = []
    for driver, keywords in _DRIVER_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            drivers.append(driver)
    return drivers


def extract_metrics_mentioned(text: str) -> List[str]:
    """Extract specific metric keywords found in text."""
    text_lower = text.lower()
    return [m for m in _METRIC_KEYWORDS if m in text_lower]


def infer_topic(text: str, config_topics: List[str]) -> str:
    """Pick the most relevant topic from the source config based on text content."""
    text_lower = text.lower()
    best_topic = config_topics[0] if config_topics else "general"
    best_score = 0

    for topic in config_topics:
        keywords = _TOPIC_SIGNALS.get(topic, [topic])
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_topic = topic

    return best_topic
