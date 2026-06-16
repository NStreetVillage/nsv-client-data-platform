import re
from datetime import datetime, date
from typing import Optional


def clean_name(value: str) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    value = re.sub(r"\s+", " ", value)
    return value.title()


def normalize_for_match(value: str) -> str:
    if value is None:
        return ""
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9 ]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def parse_date(value) -> Optional[date]:
    if value is None:
        return None

    raw = str(value).strip()
    if raw == "" or raw.lower() in ["nan", "none", "null", "unknown"]:
        return None

    # Let pandas-style timestamps pass through as strings.
    raw = raw.split(" ")[0]

    for fmt in [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%Y/%m/%d",
    ]:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    return None


def generate_nsv_id(next_number: int) -> str:
    return f"NSV-{next_number:06d}"


def split_full_name(full_name: str):
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
