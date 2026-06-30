"""Build person-centered service snapshots from imported source data.

The source systems keep pieces of the same person's story in different places:
HMIS may show housing/program participation, HTH may show case work or service
plans, JotForm may show requested services, and eCW may show healthcare visits.

This module turns those separate imported rows into one profile-friendly view.
It does not create new database tables yet; it summarizes the existing Client,
Enrollment, ClientSource, and SourceDetail records already captured by imports.
"""

from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.data.models import Client, ClientSource, Enrollment, Program, SourceDetail
from app.imports.importer import safe_str
from app.services.metrics import split_service_metric_values, looks_like_program_or_organization
from app.services.service_rules import (
    HEALTH_GAP_NEEDS,
    NEED_SIGNAL_FIELDS,
    health_categories_for_value,
    infer_source_detail_needs,
)


PROGRAM_FIELDS = {
    "nsv_program",
    "program",
    "project",
    "program_provider_id",
}

REQUESTED_SERVICE_FIELDS = {
    "services_requested",
    "other_services_requested",
    "future_service_interest",
}

RECORDED_SERVICE_FIELDS = {
    "provider_specific_service",
    "service_code_description",
    "visit_reason",
    "visit_type",
    "type_of_contact",
}

HEALTH_DOCUMENTED_FIELDS = {
    "appointment_provider_name",
    "resource_provider_name",
    "appointment_facility_name",
    "department_name",
    "practice_name",
    "patient_status",
}

INSURANCE_DOCUMENTED_FIELDS = {
    "appointment_insurance_name",
    "primary_insurance_name",
}

CARE_TEAM_FIELDS = {
    "case_worker_name": "Case worker",
    "staff_completing_form": "Staff completing form",
    "provider_name": "Provider",
    "full_provider_name": "Provider",
    "service_provider": "Service provider",
    "casenote_provider": "Case note provider",
    "appointment_provider_name": "Healthcare provider",
    "resource_provider_name": "Resource provider",
}

HOUSING_FIELDS = {
    "current_housing": "Current housing",
    "current_stay_location": "Current stay location",
    "current_housing_duration": "Time in current situation",
    "reasons_for_unhoused": "Reasons for being unhoused",
    "homelessness_primary_reason": "Primary homelessness reason",
    "prior_living_situation": "Prior living situation",
    "length_of_stay_previous_place": "Length of stay in previous place",
    "homelessness_episode_start": "Homelessness episode started",
    "homelessness_times_last_three_years": "Times homeless in last three years",
    "homelessness_months_last_three_years": "Months homeless in last three years",
    "housed": "Housed",
    "unit_availability": "Unit availability",
    "type_of_voucher": "Voucher type",
    "date_placed_in_psh": "Date placed in PSH",
    "current_lease_up_unit_address": "Lease-up/unit address",
}

ELIGIBILITY_FIELDS = {
    "dc_resident": "DC resident",
    "nsv_resident": "NSV resident",
    "snap_status": "SNAP",
    "has_photo_id": "Photo ID",
    "name_data_quality": "Name data quality",
    "ssn_data_quality": "SSN data quality",
    "dob_data_quality": "DOB data quality",
    "zip_code_data_quality": "ZIP data quality",
    "birth_certificate_status": "Birth certificate",
    "social_security_card_status": "Social Security card",
    "state_id_status": "State-issued ID",
    "health_insurance_status": "Health insurance",
    "case_management_status": "Case management",
    "income_any_source": "Income from any source",
    "snap_food_stamps": "SNAP / Food Stamps",
    "survivor_of_domestic_violence": "Survivor of domestic violence",
    "currently_fleeing_domestic_violence": "Currently fleeing",
    "military_veteran": "Veteran",
    "primary_language": "Primary language",
    "patient_language": "Patient language",
}


def normalize_value(value):
    """Return a clean string or None for blank source values."""

    text = safe_str(value)
    if not text:
        return None
    if text.lower() in {"n/a", "na", "none", "unknown"}:
        return None
    return text


def add_count(counts: dict, label: str | None, source: str | None = None):
    """Add one count to a case-insensitive label bucket."""

    text = normalize_value(label)
    if not text:
        return

    key = text.lower()
    if key not in counts:
        counts[key] = {
            "label": text,
            "count": 0,
            "sources": set(),
        }
    counts[key]["count"] += 1
    if source:
        counts[key]["sources"].add(source)


def add_weighted_count(counts: dict, label: str | None, amount: int, source: str | None = None):
    """Add a pre-counted grouped value to a count bucket."""

    text = normalize_value(label)
    if not text:
        return

    key = text.lower()
    if key not in counts:
        counts[key] = {
            "label": text,
            "count": 0,
            "sources": set(),
        }
    counts[key]["count"] += amount
    if source:
        counts[key]["sources"].add(source)


def count_items_to_list(counts: dict, limit: int = 12):
    """Convert internal count buckets into frontend-friendly dictionaries."""

    items = sorted(counts.values(), key=lambda item: (-item["count"], item["label"]))
    return [
        {
            "label": item["label"],
            "count": item["count"],
            "sources": sorted(item["sources"]),
        }
        for item in items[:limit]
    ]


def latest_values(details: list[SourceDetail], field_names: set[str], labels: dict[str, str], limit: int = 10):
    """Return the latest readable values for a set of source-detail fields."""

    rows = []
    seen = set()
    for detail in details:
        if detail.field_name not in field_names:
            continue

        value = normalize_value(detail.field_value)
        if not value:
            continue

        key = (detail.field_name, value.lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "label": labels.get(detail.field_name, detail.field_name.replace("_", " ").title()),
            "value": value,
            "source": detail.source_system,
            "program": detail.program_name,
            "created_at": str(detail.created_at) if detail.created_at else None,
        })
        if len(rows) >= limit:
            break
    return rows


def compare_requested_and_recorded_services(requested_services: list[dict], recorded_services: list[dict]):
    """Mark requested services as documented or still needing follow-up."""

    recorded_by_key = {item["label"].lower(): item for item in recorded_services}
    comparison = []

    for item in requested_services:
        key = item["label"].lower()
        recorded_item = recorded_by_key.get(key)
        comparison.append({
            "label": item["label"],
            "status": "Documented" if recorded_item else "Needs follow-up",
            "requested_count": item["count"],
            "recorded_count": recorded_item["count"] if recorded_item else 0,
            "requested_sources": item["sources"],
            "recorded_sources": recorded_item["sources"] if recorded_item else [],
        })

    return comparison


def add_health_categories(counts: dict, value: str | None, source: str | None = None):
    """Add health category counts found in one source value."""

    for category in health_categories_for_value(value):
        add_count(counts, category, source)


def build_service_needs(
    client: Client,
    details: list[SourceDetail],
    requested_services: list[dict],
    recorded_services: list[dict],
    health_status: list[dict],
):
    """Infer action-oriented needs from requested services and eligibility gaps."""

    needs = []
    recorded_service_keys = {item["label"].lower() for item in recorded_services}

    for item in requested_services:
        if item["label"].lower() in recorded_service_keys:
            continue
        needs.append({
            "label": item["label"],
            "reason": "Requested or future service interest appears in source data, but no matching service record is attached yet.",
            "sources": item["sources"],
        })

    for item in health_status:
        if item["status"] == "Documented":
            continue
        needs.append({
            "label": f"{item['label']} follow-up",
            "reason": "Health-related need appears in requested services, but no matching health interaction is documented yet.",
            "sources": item["requested_sources"],
        })

    values_by_field = defaultdict(list)
    for detail in details:
        value = normalize_value(detail.field_value)
        if value:
            values_by_field[detail.field_name].append(value)

    if not client.date_of_birth:
        needs.append({
            "label": "Confirm date of birth",
            "reason": "The master profile does not have a DOB yet.",
            "sources": ["Client Master"],
        })

    if any(value.lower() in {"no", "false"} for value in values_by_field.get("has_photo_id", [])):
        needs.append({
            "label": "Photo ID support",
            "reason": "A source record says the client does not have photo ID.",
            "sources": ["JotForm"],
        })

    if any(value.lower() in {"no", "false"} for value in values_by_field.get("snap_status", [])):
        needs.append({
            "label": "Benefits / SNAP follow-up",
            "reason": "A source record says SNAP is not currently active.",
            "sources": ["JotForm"],
        })

    if any(value.lower() in {"yes", "true"} for value in values_by_field.get("benefits_staff_interest", [])):
        needs.append({
            "label": "Benefits staff follow-up",
            "reason": "The client asked to meet with staff about benefits.",
            "sources": ["JotForm"],
        })

    housing_values = " ".join(values_by_field.get("current_housing", [])).lower()
    if any(term in housing_values for term in ["shelter", "unhoused", "homeless", "street"]):
        needs.append({
            "label": "Housing support",
            "reason": "Current housing information suggests housing support may be needed.",
            "sources": ["JotForm", "HMIS", "HTH"],
        })

    for detail in details:
        value = normalize_value(detail.field_value)
        if not value:
            continue
        for suggested_need in infer_source_detail_needs(detail.field_name, value):
            needs.append({
                "label": suggested_need["label"],
                "reason": suggested_need["reason"],
                "sources": [detail.source_system],
            })

    deduped = []
    seen = set()
    for need in needs:
        key = need["label"].lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(need)
    return deduped[:10]


def build_client_service_snapshot(db: Session, nsv_client_id: str):
    """Return a unified service snapshot for one client profile."""

    client = db.query(Client).filter(Client.nsv_client_id == nsv_client_id).first()
    if not client:
        return None

    sources = (
        db.query(ClientSource)
        .filter(ClientSource.nsv_client_id == nsv_client_id)
        .order_by(ClientSource.imported_at.desc())
        .all()
    )
    details = (
        db.query(SourceDetail)
        .filter(SourceDetail.nsv_client_id == nsv_client_id)
        .order_by(SourceDetail.created_at.desc())
        .all()
    )
    enrollments = (
        db.query(Enrollment, Program)
        .join(Program, Enrollment.program_id == Program.program_id)
        .filter(Enrollment.nsv_client_id == nsv_client_id)
        .order_by(Enrollment.created_at.desc())
        .all()
    )

    program_counts = {}
    service_counts = {}
    requested_service_counts = {}
    health_recorded_counts = {}
    health_requested_counts = {}
    care_team_counts = {}
    source_counts = {}

    for source in sources:
        add_count(source_counts, source.source_system, source.original_file)

    for enrollment, program in enrollments:
        add_count(program_counts, program.program_name, program.source_system)

    for detail in details:
        if detail.field_name in PROGRAM_FIELDS:
            add_count(program_counts, detail.field_value, detail.source_system)

        if detail.field_name in REQUESTED_SERVICE_FIELDS:
            for service in split_service_metric_values(detail.field_value):
                add_count(requested_service_counts, service, detail.source_system)
                add_health_categories(health_requested_counts, service, detail.source_system)
            add_health_categories(health_requested_counts, detail.field_value, detail.source_system)

        if detail.field_name in RECORDED_SERVICE_FIELDS:
            for service in split_service_metric_values(detail.field_value):
                add_count(service_counts, service, detail.source_system)
                add_health_categories(health_recorded_counts, service, detail.source_system)
            add_health_categories(health_recorded_counts, detail.field_value, detail.source_system)

        if detail.field_name in HEALTH_DOCUMENTED_FIELDS:
            add_count(health_recorded_counts, "Primary Care / Medical", detail.source_system)
            add_health_categories(health_recorded_counts, detail.field_value, detail.source_system)

        if detail.field_name in INSURANCE_DOCUMENTED_FIELDS:
            add_count(health_recorded_counts, "Insurance", detail.source_system)

        role = CARE_TEAM_FIELDS.get(detail.field_name)
        if role and not looks_like_program_or_organization(detail.field_value):
            add_count(care_team_counts, f"{detail.field_value} ({role})", detail.source_system)

    programs = count_items_to_list(program_counts)
    recorded_services = count_items_to_list(service_counts)
    requested_services = count_items_to_list(requested_service_counts)
    health_recorded = count_items_to_list(health_recorded_counts)
    health_requested = count_items_to_list(health_requested_counts)
    health_status = compare_requested_and_recorded_services(health_requested, health_recorded)
    care_team = count_items_to_list(care_team_counts)
    connected_sources = count_items_to_list(source_counts)
    service_status = compare_requested_and_recorded_services(requested_services, recorded_services)
    needs = build_service_needs(client, details, requested_services, recorded_services, health_status)

    recent_activity = [
        {
            "label": source.source_system,
            "value": source.original_file or "Imported source row",
            "date": str(source.imported_at) if source.imported_at else None,
            "match_method": source.match_method,
        }
        for source in sources[:8]
    ]

    return {
        "client_id": client.nsv_client_id,
        "snapshot_counts": {
            "programs": len(programs),
            "recorded_services": len(recorded_services),
            "requested_services": len(requested_services),
            "service_gaps": len([item for item in service_status if item["status"] == "Needs follow-up"]),
            "health_gaps": len([item for item in health_status if item["status"] == "Needs follow-up"]),
            "needs": len(needs),
            "sources": len(connected_sources),
            "interactions": len(sources),
        },
        "programs": programs,
        "recorded_services": recorded_services,
        "requested_services": requested_services,
        "service_status": service_status,
        "health": {
            "documented": health_recorded,
            "requested": health_requested,
            "status": health_status,
        },
        "needs": needs,
        "care_team": care_team,
        "housing": latest_values(details, set(HOUSING_FIELDS), HOUSING_FIELDS),
        "eligibility": latest_values(details, set(ELIGIBILITY_FIELDS), ELIGIBILITY_FIELDS),
        "connected_sources": connected_sources,
        "recent_activity": recent_activity,
    }


def build_client_needs_summary(db: Session):
    """Aggregate organization-level needs without rebuilding every profile."""

    need_counts = {}
    health_gap_counts = {}
    service_gap_counts = {}
    requested_service_counts = {}
    recorded_service_counts = {}
    requested_health_counts = {}
    recorded_health_counts = {}

    # The metrics page needs broad demand signals, not profile-by-profile detail.
    # Grouping in SQL keeps the dashboard fast even with thousands of clients.
    grouped_details = (
        db.query(
            SourceDetail.field_name,
            SourceDetail.field_value,
            SourceDetail.source_system,
            func.count(SourceDetail.detail_id),
        )
        .filter(SourceDetail.field_value.isnot(None))
        .filter(SourceDetail.field_name.in_(
            set(REQUESTED_SERVICE_FIELDS)
            | set(RECORDED_SERVICE_FIELDS)
            | set(HEALTH_DOCUMENTED_FIELDS)
            | set(INSURANCE_DOCUMENTED_FIELDS)
            | NEED_SIGNAL_FIELDS
            | {"has_photo_id", "snap_status", "benefits_staff_interest", "current_housing"}
        ))
        .group_by(SourceDetail.field_name, SourceDetail.field_value, SourceDetail.source_system)
        .all()
    )

    for field_name, field_value, source_system, row_count in grouped_details:
        value = normalize_value(field_value)
        if not value:
            continue

        if field_name in REQUESTED_SERVICE_FIELDS:
            for service in split_service_metric_values(value):
                add_weighted_count(requested_service_counts, service, row_count, source_system)
                for category in health_categories_for_value(service):
                    add_weighted_count(requested_health_counts, category, row_count, source_system)
            for category in health_categories_for_value(value):
                add_weighted_count(requested_health_counts, category, row_count, source_system)

        if field_name in RECORDED_SERVICE_FIELDS:
            for service in split_service_metric_values(value):
                add_weighted_count(recorded_service_counts, service, row_count, source_system)
                for category in health_categories_for_value(service):
                    add_weighted_count(recorded_health_counts, category, row_count, source_system)
            for category in health_categories_for_value(value):
                add_weighted_count(recorded_health_counts, category, row_count, source_system)

        if field_name in HEALTH_DOCUMENTED_FIELDS:
            add_weighted_count(recorded_health_counts, "Primary Care / Medical", row_count, source_system)
            for category in health_categories_for_value(value):
                add_weighted_count(recorded_health_counts, category, row_count, source_system)

        if field_name in INSURANCE_DOCUMENTED_FIELDS:
            add_weighted_count(recorded_health_counts, "Insurance", row_count, source_system)

        if field_name == "has_photo_id" and value.lower() in {"no", "false"}:
            add_weighted_count(need_counts, "Photo ID support", row_count, source_system)

        if field_name == "snap_status" and value.lower() in {"no", "false"}:
            add_weighted_count(need_counts, "Benefits / SNAP follow-up", row_count, source_system)

        if field_name == "benefits_staff_interest" and value.lower() in {"yes", "true"}:
            add_weighted_count(need_counts, "Benefits staff follow-up", row_count, source_system)

        if field_name == "current_housing":
            lower_value = value.lower()
            if any(term in lower_value for term in ["shelter", "unhoused", "homeless", "street"]):
                add_weighted_count(need_counts, "Housing support", row_count, source_system)

        for suggested_need in infer_source_detail_needs(field_name, value):
            need_label = suggested_need["label"]
            add_weighted_count(need_counts, need_label, row_count, source_system)
            if need_label in HEALTH_GAP_NEEDS:
                add_weighted_count(health_gap_counts, need_label, row_count, source_system)

    for key, item in requested_service_counts.items():
        if key in recorded_service_counts:
            continue
        add_weighted_count(service_gap_counts, item["label"], item["count"], ", ".join(item["sources"]))
        add_weighted_count(need_counts, item["label"], item["count"], ", ".join(item["sources"]))

    for key, item in requested_health_counts.items():
        if key in recorded_health_counts:
            continue
        add_weighted_count(health_gap_counts, item["label"], item["count"], ", ".join(item["sources"]))
        add_weighted_count(need_counts, f"{item['label']} follow-up", item["count"], ", ".join(item["sources"]))

    missing_dob_ids = {
        row.nsv_client_id
        for row in db.query(Client.nsv_client_id).filter(Client.date_of_birth.is_(None)).all()
    }
    missing_dob_count = len(missing_dob_ids)
    if missing_dob_count:
        add_weighted_count(need_counts, "Confirm date of birth", missing_dob_count, "Client Master")

    clients_with_need_fields = {
        row.nsv_client_id
        for row in (
            db.query(SourceDetail.nsv_client_id)
            .filter(SourceDetail.field_value.isnot(None))
            .filter(SourceDetail.field_name.in_(
                set(REQUESTED_SERVICE_FIELDS)
                | NEED_SIGNAL_FIELDS
                | {"has_photo_id", "snap_status", "benefits_staff_interest", "current_housing"}
            ))
            .distinct()
            .all()
        )
    }

    clients_with_need_signals = clients_with_need_fields | missing_dob_ids

    clients_with_source_details = (
        db.query(func.count(func.distinct(SourceDetail.nsv_client_id)))
        .filter(SourceDetail.field_value.isnot(None))
        .scalar()
        or 0
    )

    return {
        "clients_with_needs": len(clients_with_need_signals),
        "clients_with_source_details": clients_with_source_details,
        "top_needs": count_items_to_list(need_counts, limit=12),
        "service_gaps": count_items_to_list(service_gap_counts, limit=12),
        "health_gaps": count_items_to_list(health_gap_counts, limit=12),
    }
