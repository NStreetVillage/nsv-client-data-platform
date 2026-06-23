"""Import and summarize non-client program metrics/planning sheets.

Metrics files are stored separately from client imports because they describe
program planning targets and monthly values rather than individual people.
"""

import json
import re
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from .importer import is_metrics_layout, load_file, safe_str
from .models import ProgramMetric


# Metrics sheets use month columns such as "Jan-26"; this pattern detects them.
MONTH_COLUMN_PATTERN = re.compile(r"^[A-Za-z]{3}-\d{2}$")


def import_metrics_file(db: Session, file_path: str):
    """Read a metrics spreadsheet and store each meaningful row."""

    path = Path(file_path)
    df = load_file(path, allow_metrics=True)

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
            original_file=path.name,
        ))
        rows_imported += 1

    db.commit()

    return {
        "file_name": path.name,
        "rows_processed": rows_processed,
        "rows_imported": rows_imported,
    }


def get_metrics_summary(db: Session):
    """Build the overview summary shown in the metrics panel."""

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

    return {
        "total_metrics": total,
        "program_count": len(programs),
        "latest_file": latest.original_file if latest else None,
        "programs": programs,
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
