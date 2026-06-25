"""Import and summarize reporting data for the NSV Client Data Platform.

This file owns the metrics/reporting side of the application. It does not
create master client records. Instead, it stores aggregate ProgramMetric rows
from planning sheets, occupancy reports, operational reports, and client-linked
SourceDetail fields that can be summarized for dashboards.
"""

import json
import re
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from .importer import (
    is_metrics_layout,
    is_occupancy_report_layout,
    is_operational_metrics_layout,
    load_file,
    normalize_import_columns,
    safe_str,
)
from .models import ProgramMetric, SourceDetail


# ---------------------------------------------------------------------------
# Metrics vocabulary
# ---------------------------------------------------------------------------
#
# These constants define which fields become dashboard insights. The importer
# captures detailed source fields on client profiles; this file later turns
# those details into counts for case workers, providers, services, and UIRs.

# Metrics sheets use month columns such as "Jan-26"; this pattern detects them.
MONTH_COLUMN_PATTERN = re.compile(r"^[A-Za-z]{3}-\d{2}$")

CASE_WORKER_FIELDS = {
    "case_worker_name",
    "staff_completing_form",
}

PROVIDER_FIELDS = {
    "provider_name",
    "full_provider_name",
    "service_provider",
    "casenote_provider",
    "appointment_provider_name",
    "resource_provider_name",
}

SERVICE_FIELDS = {
    "services_requested",
    "other_services_requested",
    "provider_specific_service",
    "service_code_description",
    "visit_reason",
    "visit_type",
}

UIR_FIELDS = {"uir_category"}

KNOWN_SERVICE_LABELS = [
    "Employment Support",
    "Behavioral Health",
    "Meals",
    "Housing Support",
    "Legal Services",
    "Transportation",
    "Benefits",
    "Shower",
    "Showers",
    "Laundry",
    "Classes",
    "Class",
    "Healthcare",
    "Medical",
    "Case Management",
    "Emergency Shelter",
]

SERVICE_LABEL_ALIASES = {
    "Meal": "Meals",
    "Showers": "Shower",
    "Class": "Classes",
}

NON_PROVIDER_PATTERNS = [
    "n. street village",
    "n street village",
    "nsv -",
    "capitol vista",
    "pat handy",
    "phyllis wheatley",
    "miriam's house",
    "erna's house",
    "sharon's place",
    "bethany women's center",
]


def import_metrics_file(db: Session, file_path: str, original_file_name: str | None = None):
    """Dispatch one uploaded report to the correct metrics importer."""

    path = Path(file_path)
    display_file_name = original_file_name or path.name
    df = load_file(path, allow_metrics=True)

    if is_occupancy_report_layout(df):
        return import_occupancy_report(db, df, display_file_name)

    normalized_df = normalize_import_columns(df)
    if is_operational_metrics_layout(normalized_df):
        return import_operational_metrics_report(db, normalized_df, display_file_name)

    if not is_metrics_layout(df):
        raise ValueError("This file is not recognized as a program metrics/planning sheet.")

    rows_processed = 0
    rows_imported = 0

    for _, row in df.iterrows():
        rows_processed += 1

        # These four columns are the minimum shape of a planning/metrics row.
        program = safe_str(row.get("Program"))
        target = safe_str(row.get("Target"))
        metric = safe_str(row.get("Metric"))
        method = safe_str(row.get("Method"))

        # Skip empty spacer rows from spreadsheets.
        if not any([program, target, metric, method]):
            continue

        # Preserve month columns as JSON so the schema can handle changing months.
        month_values = {}
        for column in row.index:
            column_name = str(column).strip()
            if MONTH_COLUMN_PATTERN.match(column_name):
                value = safe_str(row.get(column))
                if value:
                    month_values[column_name] = value

        db.add(ProgramMetric(
            program=program,
            target=target,
            metric=metric,
            method=method,
            notes=safe_str(row.get("Notes")),
            sort_order=safe_str(row.get("Sort")),
            month_values_json=json.dumps(month_values),
            original_file=display_file_name,
        ))
        rows_imported += 1

    db.commit()

    return {
        "file_name": display_file_name,
        "rows_processed": rows_processed,
        "rows_imported": rows_imported,
    }


def import_occupancy_report(db: Session, df: pd.DataFrame, file_name: str):
    """Store HMIS weekly occupancy rows as reporting metrics."""

    rows_processed = 0
    rows_imported = 0
    metric_columns = [
        "Households Enrolled in HMIS",
        "Households moved into Housing",
    ]

    for _, row in df.iterrows():
        rows_processed += 1
        program = safe_str(row.get("Program Provider ID"))
        if not program:
            continue

        for metric_column in metric_columns:
            value = safe_str(row.get(metric_column))
            if not value:
                continue

            db.add(ProgramMetric(
                program=program,
                target="Weekly Occupancy / HMIS Numbers",
                metric=metric_column,
                method="HMIS weekly occupancy report",
                notes=value,
                sort_order=None,
                month_values_json=json.dumps({"value": value}),
                original_file=file_name,
            ))
            rows_imported += 1

    db.commit()

    return {
        "file_name": file_name,
        "rows_processed": rows_processed,
        "rows_imported": rows_imported,
    }


def get_column(df: pd.DataFrame, candidates: list[str]):
    """Find the first matching dataframe column from a list of possible names."""

    normalized_columns = {str(column).strip().lower(): column for column in df.columns}
    for candidate in candidates:
        column = normalized_columns.get(candidate.lower())
        if column is not None:
            return column
    return None


def normalize_metric_value(value):
    """Clean a spreadsheet value so it is readable as a metric label."""

    text = safe_str(value)
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip()


def split_service_metric_values(value):
    """Split service text from forms into useful service categories."""

    text = normalize_metric_value(value)
    if not text:
        return []

    pieces = re.split(r"[,;/\n]+", text)
    values = []
    for piece in pieces:
        cleaned = normalize_metric_value(piece)
        if not cleaned:
            continue

        lower_piece = cleaned.lower()
        matched_labels = [
            SERVICE_LABEL_ALIASES.get(label, label)
            for label in KNOWN_SERVICE_LABELS
            if label.lower() in lower_piece
        ]
        values.extend(matched_labels or [cleaned])

    # De-dupe within one row while preserving order.
    seen = set()
    deduped = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def looks_like_program_or_organization(value):
    """Return true when a value is an NSV program/org, not a person/provider."""

    text = normalize_metric_value(value)
    if not text:
        return True

    lower_text = text.lower()
    if any(pattern in lower_text for pattern in NON_PROVIDER_PATTERNS):
        return True

    # HMIS provider/program exports often look like "NSV - Program - Type(1234)".
    if " - " in text and re.search(r"\(\d+\)$", text):
        return True

    return False


def count_values(df: pd.DataFrame, column_name, splitter=None, value_filter=None):
    """Count non-empty values in one column."""

    counts = {}
    if column_name is None:
        return counts

    for value in df[column_name]:
        values = splitter(value) if splitter else [normalize_metric_value(value)]
        for item in values:
            if not item:
                continue
            if value_filter and not value_filter(item):
                continue
            key = item.lower()
            if key not in counts:
                counts[key] = {"label": item, "count": 0}
            counts[key]["count"] += 1

    return counts


def add_rollup_metrics(db: Session, file_name: str, target: str, metric_name: str, counts: dict, limit: int = 50):
    """Persist one aggregate metric row for each counted value."""

    rows_imported = 0
    for item in top_counts(counts, limit=limit):
        db.add(ProgramMetric(
            program=item["label"],
            target=target,
            metric=metric_name,
            method="Operational report rollup",
            notes=str(item["count"]),
            sort_order=None,
            month_values_json=json.dumps({"count": item["count"]}),
            original_file=file_name,
        ))
        rows_imported += 1
    return rows_imported


def import_operational_metrics_report(db: Session, df: pd.DataFrame, file_name: str):
    """Create aggregate metrics from rosters, activity files, and reports."""

    rows_processed = len(df)
    rows_imported = 0

    hmis_column = get_column(df, ["hmis_id", "HMIS ID", "Client Uid", "Client Unique Id", "Most recent HMID"])
    if hmis_column is not None:
        unique_ids = {
            normalize_metric_value(value)
            for value in df[hmis_column]
            if normalize_metric_value(value)
        }
        if unique_ids:
            db.add(ProgramMetric(
                program="All Programs",
                target="Operational Report Summary",
                metric="Unique HMIS/client IDs in file",
                method="Operational report rollup",
                notes=str(len(unique_ids)),
                sort_order=None,
                month_values_json=json.dumps({"count": len(unique_ids)}),
                original_file=file_name,
            ))
            rows_imported += 1

    rollup_specs = [
        ("Client Roster", "Rows by Program", ["program", "Project", "Entry Exit Provider Id", "Program Provider ID", "NSV Program"]),
        ("Client Roster", "Rows by Funding Source", ["Funding Source"]),
        ("Client Roster", "Rows by Referral Source", ["Referral Source"]),
        ("Client Roster", "Rows by Provider", ["Provider Name", "Full Provider Name", "provider"]),
        ("Client Roster", "Rows by Voucher Type", ["Type of Voucher"]),
        ("Client Roster", "Rows by Exit Reason", ["Reason for Exit", "Exit Destination"]),
        ("Client Roster", "Rows by Housing Status", ["Housed", "Exited", "Moved into Housing binary"]),
        ("Client Demographics", "Rows by Gender", ["gender", "Gender(894)", "Gender (Retired)", "Sex"]),
        ("Client Demographics", "Rows by Race", ["race", "Primary Race", "Race and Ethnicity(4587)"]),
        ("Client Demographics", "Rows by Ethnicity", ["ethnicity"]),
        ("Client Demographics", "Rows by Veteran Status", ["veteran_status", "Veteran Status"]),
        ("Client Demographics", "Rows by Sexual Orientation", ["Sexual Orientation"]),
        ("Contacts", "Contacts by Type", ["Type of Contact"]),
        ("Contacts", "Contacts by Case Worker", ["Case Worker - Name", "Case Worker Name", "case_worker_name"]),
        ("Service Plans", "Service Plans by Case Worker", ["Case Worker Name", "case_worker_name"]),
        ("UIR", "UIRs by Category", ["UIR category", "uir_category"]),
        ("HMIS Services", "Services by Provider", ["Service Provide Provider", "service_provider"]),
        ("HMIS Services", "Services by Code", ["Service Code Description", "service_code_description"]),
        ("HMIS Services", "Services by Specific Service", ["Provider Specific Service", "provider_specific_service"]),
        ("HMIS Case Notes", "Case Notes by Provider", ["Casenote Provider", "casenote_provider"]),
        ("Healthcare", "Encounters by Provider", ["Appointment Provider Name", "appointment_provider_name", "Resource Provider Name", "resource_provider_name"]),
        ("Healthcare", "Encounters by Facility", ["Appointment Facility Name", "appointment_facility_name", "Department Name", "department_name"]),
        ("Healthcare", "Encounters by Visit Type", ["Visit Type", "visit_type", "Visit Reason", "visit_reason"]),
        ("Data Sources", "Programs by Source Report", ["What Data Report"]),
    ]

    for target, metric_name, candidates in rollup_specs:
        column = get_column(df, candidates)
        value_filter = None
        if "Provider" in metric_name:
            value_filter = lambda value: not looks_like_program_or_organization(value)
        rows_imported += add_rollup_metrics(
            db=db,
            file_name=file_name,
            target=target,
            metric_name=metric_name,
            counts=count_values(df, column, value_filter=value_filter),
        )

    service_columns = [
        get_column(df, ["What can we help you with today?", "services_requested"]),
        get_column(df, ["Are there any other services you would like to access at N Street Village?", "other_services_requested"]),
        get_column(df, ["Provider Specific Service", "provider_specific_service"]),
        get_column(df, ["Service Code Description", "service_code_description"]),
    ]
    service_counts = {}
    for column in service_columns:
        for key, item in count_values(df, column, splitter=split_service_metric_values).items():
            if key not in service_counts:
                service_counts[key] = item
            else:
                service_counts[key]["count"] += item["count"]
    rows_imported += add_rollup_metrics(db, file_name, "Services and Supports", "Rows by Service", service_counts)

    db.commit()

    return {
        "file_name": file_name,
        "rows_processed": rows_processed,
        "rows_imported": rows_imported,
    }


def increment_count(counts: dict, value: str | None):
    """Increment a case-insensitive display count for one insight value."""

    text = safe_str(value)
    if not text:
        return

    key = text.lower()
    if key not in counts:
        counts[key] = {"label": text, "count": 0}
    counts[key]["count"] += 1


def top_counts(counts: dict, limit: int = 8):
    """Return the most common insight values in a frontend-friendly shape."""

    return sorted(counts.values(), key=lambda item: (-item["count"], item["label"]))[:limit]


def summarize_source_detail_insights(db: Session):
    """Aggregate operational details already attached to client profiles."""

    case_workers = {}
    providers = {}
    services = {}
    uir_categories = {}

    detail_rows = (
        db.query(SourceDetail.field_name, SourceDetail.field_value)
        .filter(SourceDetail.field_value.isnot(None))
        .all()
    )

    for field_name, field_value in detail_rows:
        if field_name in CASE_WORKER_FIELDS:
            increment_count(case_workers, field_value)
        if field_name in PROVIDER_FIELDS:
            if not looks_like_program_or_organization(field_value):
                increment_count(providers, field_value)
        if field_name in SERVICE_FIELDS:
            for service_value in split_service_metric_values(field_value):
                increment_count(services, service_value)
        if field_name in UIR_FIELDS:
            increment_count(uir_categories, field_value)

    return {
        "case_workers": top_counts(case_workers),
        "providers": top_counts(providers),
        "services": top_counts(services),
        "uir_categories": top_counts(uir_categories),
    }


def summarize_occupancy_metrics(db: Session):
    """Summarize imported occupancy reports into high-level reporting numbers."""

    occupancy_rows = (
        db.query(ProgramMetric)
        .filter(ProgramMetric.target == "Weekly Occupancy / HMIS Numbers")
        .all()
    )
    totals = {}

    for metric in occupancy_rows:
        try:
            payload = json.loads(metric.month_values_json or "{}")
        except json.JSONDecodeError:
            payload = {}

        raw_value = safe_str(payload.get("value") or metric.notes)
        if not raw_value:
            continue

        try:
            value = float(raw_value)
        except ValueError:
            continue

        if metric.metric not in totals:
            totals[metric.metric] = 0
        totals[metric.metric] += value

    return [
        {"label": label, "value": int(value) if value.is_integer() else value}
        for label, value in sorted(totals.items())
    ]


def get_metrics_summary(db: Session):
    """Build the full metrics payload used by the Overview dashboard."""

    total = db.query(ProgramMetric).count()

    # The latest row tells us which file was most recently imported.
    latest = (
        db.query(ProgramMetric)
        .order_by(ProgramMetric.imported_at.desc(), ProgramMetric.metric_id.desc())
        .first()
    )

    # Collect unique program names for a compact "programs covered" summary.
    program_rows = (
        db.query(ProgramMetric.program)
        .filter(ProgramMetric.program.isnot(None))
        .all()
    )
    programs = sorted({row.program for row in program_rows if row.program})

    # Show a small recent sample on the Overview tab.
    recent_metrics = (
        db.query(ProgramMetric)
        .order_by(ProgramMetric.imported_at.desc(), ProgramMetric.metric_id.desc())
        .limit(10)
        .all()
    )

    operational_insights = summarize_source_detail_insights(db)
    has_operational_data = any(operational_insights[key] for key in operational_insights)

    return {
        "total_metrics": total,
        "program_count": len(programs),
        "latest_file": latest.original_file if latest else None,
        "programs": programs,
        "has_operational_data": has_operational_data,
        "operational_insights": operational_insights,
        "occupancy_summary": summarize_occupancy_metrics(db),
        "recent_metrics": [
            {
                "program": metric.program,
                "target": metric.target,
                "metric": metric.metric,
                "method": metric.method,
                "notes": metric.notes,
                "sort_order": metric.sort_order,
                "original_file": metric.original_file,
                "imported_at": str(metric.imported_at) if metric.imported_at else None,
            }
            for metric in recent_metrics
        ],
    }
