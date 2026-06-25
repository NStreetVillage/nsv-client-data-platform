"""Decide whether an uploaded spreadsheet should import as clients or metrics.

The upload flow has two major destinations:

1. Client import
   Creates or updates master client records, client source rows, enrollments,
   source details, and review queue rows when identity matching is uncertain.

2. Metrics/report import
   Creates aggregate ProgramMetric rows for planning sheets, occupancy reports,
   operational reports, and enrichment-only files that should not create new
   client records by themselves.

Keeping this decision in one small module makes the app's branching path easier
to understand from the code: main.py handles HTTP requests, importer.py handles
client ingestion, metrics.py handles reporting, and this file decides which
destination an uploaded dataframe belongs to.
"""

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from .importer import is_enrichment_only_layout, is_supported_metrics_layout


class ImportRoute(str, Enum):
    """The two high-level import destinations supported by the prototype."""

    CLIENT = "client"
    METRICS = "metrics"


@dataclass(frozen=True)
class ImportRouteDecision:
    """A readable explanation of how the app classified an uploaded file."""

    route: ImportRoute
    reason: str
    is_supported_metrics_layout: bool
    is_enrichment_only_layout: bool


def classify_upload_dataframe(df: pd.DataFrame) -> ImportRouteDecision:
    """Choose the import destination for a normalized uploaded dataframe."""

    metrics_layout = is_supported_metrics_layout(df)
    enrichment_only = is_enrichment_only_layout(df)

    if metrics_layout:
        return ImportRouteDecision(
            route=ImportRoute.METRICS,
            reason="recognized metrics/report layout",
            is_supported_metrics_layout=True,
            is_enrichment_only_layout=enrichment_only,
        )

    if enrichment_only:
        return ImportRouteDecision(
            route=ImportRoute.METRICS,
            reason="enrichment-only operational report",
            is_supported_metrics_layout=False,
            is_enrichment_only_layout=True,
        )

    return ImportRouteDecision(
        route=ImportRoute.CLIENT,
        reason="client identity import",
        is_supported_metrics_layout=False,
        is_enrichment_only_layout=False,
    )
