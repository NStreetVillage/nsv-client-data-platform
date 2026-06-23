"""Shared data-cleaning helpers for imports, matching, and search.

The functions here keep common formatting rules in one place: cleaning names,
normalizing names for comparison, parsing dates from messy source files, and
generating NSV client IDs.
"""

import re
from datetime import datetime, date
from typing import Optional


def clean_name(value: str) -> str:
    """Trim extra whitespace and convert a name to title case."""

    if value is None:
        return ""
    value = str(value).strip()
    value = re.sub(r"\s+", " ", value)
    return value.title()


def normalize_for_match(value: str) -> str:
    """Make names comparable by lowercasing and removing punctuation."""

    if value is None:
        return ""
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9 ]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def parse_date(value) -> Optional[date]:
    """Parse dates from the different formats found in source exports."""

    if value is None:
        return None

    raw = str(value).strip()
    if raw == "" or raw.lower() in ["nan", "none", "null", "unknown"]:
        return None

    # Let pandas-style timestamps pass through as strings without breaking
    # month-name dates such as "Feb 11, 1982".
    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", raw):
        raw = raw.split(" ")[0]

    for fmt in [
        "%Y-%m-%d",
        "%Y-%d-%m",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%Y/%m/%d",
        "%Y/%d/%m",
        "%b %d, %Y",
        "%B %d, %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d-%B-%Y",
        "%d-%B-%y",
    ]:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    return None


def parse_date_candidates(value) -> list[date]:
    """
    Return all plausible full dates for a search string.

    This intentionally allows ambiguous searches like "1982-11-2" to match
    either November 2 or February 11, because source systems mix US dates,
    ISO dates, and human-entered month/day order.
    """
    parsed = parse_date(value)
    candidates = []
    if parsed:
        candidates.append(parsed)

    if value is None:
        return candidates

    raw = str(value).strip()
    match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", raw)
    if match:
        year, first, second = [int(part) for part in match.groups()]
        for month, day in [(first, second), (second, first)]:
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if candidate not in candidates:
                candidates.append(candidate)

    return candidates


def generate_nsv_id(next_number: int) -> str:
    """Format a database sequence number as an NSV client ID."""

    return f"NSV-{next_number:06d}"


def split_full_name(full_name: str):
    """Split a single full-name field into first and last name pieces."""

    cleaned = clean_name(full_name)
    if not cleaned:
        return "", ""

    # Handle "Last, First" format.
    if "," in cleaned:
        last, first = cleaned.split(",", 1)
        return clean_name(first), clean_name(last)

    parts = cleaned.split(" ")
    if len(parts) == 1:
        return parts[0], ""

    return parts[0], " ".join(parts[1:])
