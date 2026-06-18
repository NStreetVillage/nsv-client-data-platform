import json
import re
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from .importer import is_metrics_layout, load_file, safe_str
from .models import ProgramMetric


MONTH_COLUMN_PATTERN = re.compile(r"^[A-Za-z]{3}-\d{2}$")


def import_metrics_file(db: Session, file_path: str):
    path = Path(file_path)
    df = load_file(path, allow_metrics=True)

    if not is_metrics_layout(df):
        raise ValueError("This file is not recognized as a program metrics/planning sheet.")

    rows_processed = 0
    rows_imported = 0

    for _, row in df.iterrows():
        rows_processed += 1

        program = safe_str(row.get("Program"))
        target = safe_str(row.get("Target"))
        metric = safe_str(row.get("Metric"))
        method = safe_str(row.get("Method"))

        if not any([program, target, metric, method]):
            continue

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
    total = db.query(ProgramMetric).count()
    latest = (
        db.query(ProgramMetric)
        .order_by(ProgramMetric.imported_at.desc(), ProgramMetric.metric_id.desc())
        .first()
    )

    program_rows = (
        db.query(ProgramMetric.program)
        .filter(ProgramMetric.program.isnot(None))
        .all()
    )
    programs = sorted({row.program for row in program_rows if row.program})

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
