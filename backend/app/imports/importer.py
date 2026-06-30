"""Spreadsheet preview, normalization, matching, and client import logic.

This is the main data-ingestion file for client CSV/Excel uploads. The frontend
uploads a file, main.py calls preview_file() for a sample, and later calls
import_file() to create/match clients, record source rows, add program
enrollments, and create review records when matching is uncertain.
"""

import json
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.core.utils import clean_name, normalize_for_match, parse_date, generate_nsv_id, split_full_name
from app.data.models import Client, ClientAlias, Program, ClientSource, SourceDetail, Enrollment, ImportLog, PotentialMatch
from app.imports.matching import find_fuzzy_duplicate_candidates, match_client


class UnsupportedClientImportError(ValueError):
    """Raised when a client import endpoint receives a metrics/planning file."""

    pass


# ---------------------------------------------------------------------------
# Column vocabulary
# ---------------------------------------------------------------------------
#
# Source systems use different labels for the same client concepts. These
# aliases let the importer normalize spreadsheets before matching clients.
DEFAULT_COLUMN_ALIASES = {
    "first_name": [
        "first_name", "first name", "firstname", "client first name", "patient first name",
        "client.first name", "first"
    ],
    "last_name": [
        "last_name", "last name", "lastname", "client last name", "patient last name",
        "client.last name", "last"
    ],
    "full_name": [
        "full_name", "full name", "client name", "name", "client full name",
        "patient name", "consumer name"
    ],
    "date_of_birth": [
        "date_of_birth", "dob", "birth date", "date of birth", "client dob",
        "patient dob", "patient date of birth", "date of birth.1", "date of birth(893)"
    ],
    "hmis_id": [
        "hmis_id", "hmis id", "hmisid", "personal id", "personalid", "client id",
        "unique id", "dhs client id# - hmis id", "client uid", "client unique id",
        "most recent hmid"
    ],
    "ecw_id": [
        "ecw_id", "ecw id", "patient id", "patient acct no", "patient account no",
        "patient account number", "acct no"
    ],
    "entry_date": [
        "entry_date", "entry date", "start date", "program entry date", "project start",
        "admission date", "current lease-up date", "date placed in psh",
        "date placed in psh (formula)"
    ],
    "exit_date": [
        "exit_date", "exit date", "end date", "program exit date", "project exit",
        "discharge date"
    ],
    "status": [
        "status", "client status", "program status", "housed", "exited"
    ],
    "gender": ["gender", "sex", "client gender", "patient gender", "gender (retired)"],
    "race": ["race", "client race", "patient race", "primary race"],
    "ethnicity": ["ethnicity", "client ethnicity", "patient ethnicity"],
    "veteran_status": ["veteran status", "veteran", "client veteran status", "are you a military veteran?"],
    "encounter_date": ["appointment date", "encounter date", "visit date", "service date", "submission date", "date of intake"],
    "provider": ["provider", "provider name", "case worker", "caseworker", "appointment provider name", "resource provider name"]
}


OCCUPANCY_REPORT_COLUMNS = {
    "program provider id",
    "households enrolled in hmis",
    "households moved into housing",
}


# ---------------------------------------------------------------------------
# File classification signatures
# ---------------------------------------------------------------------------
#
# These signatures are used before import to decide which branch a file should
# take. Client rosters can create people. Metrics/report files create aggregate
# ProgramMetric rows. Enrichment-only files may update known clients, but should
# not create thousands of review rows when identity is incomplete.
ENRICHMENT_ONLY_SIGNATURES = [
    {"hmis id", "date of contact"},
    {"hmis id", "# of service plan approvals"},
    {"hmis id", "uir category"},
    {"hmis_id", "date of contact"},
    {"hmis_id", "# of service plan approvals"},
    {"hmis_id", "uir category"},
    {"hmis_id", "number of services"},
    {"hmis_id", "service provide provider"},
    {"hmis_id", "service code description"},
    {"hmis_id", "casenote provider"},
    {"hmis_id", "count of case notes"},
    {"hmis_id", "casenote uid"},
    {"client uid", "client unique id", "number of services"},
    {"client uid", "client unique id", "service date"},
    {"client uid", "client unique id", "count of case notes"},
    {"client uid", "client unique id", "casenote uid"},
]


OPERATIONAL_METRICS_SIGNATURES = [
    {"hmis id", "date of contact"},
    {"hmis id", "# of service plan approvals"},
    {"hmis id", "uir category"},
    {"hmis id", "first name", "date of birth"},
    {"hmis id", "project", "entry/service start date"},
    {"hmis id", "program", "funding source"},
    {"hmis id", "client full name", "program"},
    {"hmis_id", "date of contact"},
    {"hmis_id", "# of service plan approvals"},
    {"hmis_id", "uir category"},
    {"hmis_id", "first_name", "date_of_birth"},
    {"hmis_id", "number of services"},
    {"hmis_id", "service provide provider"},
    {"hmis_id", "service code description"},
    {"hmis_id", "casenote provider"},
    {"hmis_id", "count of case notes"},
    {"hmis_id", "casenote uid"},
    {"hmis_id", "project", "entry/service start date"},
    {"hmis_id", "program", "funding source"},
    {"hmis_id", "client full name", "program"},
    {"hmis_id", "full_name", "program"},
    {"client uid", "client unique id", "number of services"},
    {"client uid", "client unique id", "service provide provider"},
    {"client uid", "client unique id", "service code description"},
    {"client uid", "client unique id", "casenote provider"},
    {"client uid", "client unique id", "count of case notes"},
    {"full_name", "source app", "what can we help you with today?"},
    {"full_name", "source app"},
    {"full name", "source app"},
    {"patient name", "appointment provider name"},
    {"full_name", "appointment provider name"},
    {"full_name", "encounter_date", "visit type"},
    {"ecw_id", "encounter_date", "visit type"},
    {"full_name", "encounter_date", "visit reason"},
    {"ecw_id", "encounter_date", "visit reason"},
    {"nsv program", "what data report"},
]


# Extra source columns that should be kept as SourceDetail records for profiles.
# Source details are the "extra" facts that do not belong on the core Client row.
# They let one profile show JotForm, HTH, HMIS, and eCW-specific fields without
# forcing every import format into the same fixed set of database columns.
SOURCE_DETAIL_FIELDS = {
    "full_name": "source_full_name",
    "first_name": "source_first_name",
    "last_name": "source_last_name",
    "date_of_birth": "source_date_of_birth",
    "Full Name": "source_full_name",
    "First Name": "source_first_name",
    "First Name ": "source_first_name",
    "Last Name": "source_last_name",
    "Name Data Quality": "name_data_quality",
    "Client Name": "source_client_name",
    "Patient Name": "source_patient_name",
    "Preferred Name": "source_preferred_name",
    "Name": "source_name",
    "Date of Birth": "source_date_of_birth",
    "Date of Birth.1": "source_date_of_birth_alternate",
    "Date of Birth(893)": "source_date_of_birth",
    "DOB": "source_date_of_birth",
    "DOB Type": "dob_data_quality",
    "Date of Birth Type": "dob_data_quality",
    "Data of Birth Type": "dob_data_quality",
    "Patient DOB": "source_patient_dob",
    "SSN Data Quality": "ssn_data_quality",
    "Client Uid": "source_client_uid",
    "Client Unique Id": "source_client_unique_id",
    "Client First Name": "source_first_name",
    "Client Middle Name": "source_middle_name",
    "Client Last Name": "source_last_name",
    "Middle Name": "source_middle_name",
    "Unique ID": "source_unique_id",
    "Program": "program",
    "Program Provider ID": "program_provider_id",
    "Households Enrolled in HMIS": "households_enrolled_in_hmis",
    "Households moved into Housing": "households_moved_into_housing",
    "Number of Services": "number_of_services",
    "Service Provide Provider": "service_provider",
    "Service Code Description": "service_code_description",
    "Provider Specific Service": "provider_specific_service",
    "Service Date": "service_date",
    "Service User Creating": "service_user_creating",
    "Service User Updating": "service_user_updating",
    "Casenote Provider": "casenote_provider",
    "Last Case Note": "last_case_note",
    "Count of Case Notes": "count_of_case_notes",
    "Casenote Uid": "casenote_uid",
    "Casenote Note Date (MIN)": "casenote_note_date_min",
    "Casenote Note Date (MAX)": "casenote_note_date_max",
    "Casenote Note Date": "casenote_note_date",
    "Casenote User Creating": "casenote_user_creating",
    "Casenote User Updating": "casenote_user_updating",
    "Date of Contact": "date_of_contact",
    "Type of Contact": "type_of_contact",
    "Address": "contact_address",
    "Case Worker - Name": "case_worker_name",
    "Case Worker Name": "case_worker_name",
    "# of Service plan approvals": "service_plan_approvals",
    "Date Created": "date_created",
    "Date Modified": "date_modified",
    "Date of UIR": "date_of_uir",
    "UIR category": "uir_category",
    "Provider Name": "provider_name",
    "Full Provider Name": "full_provider_name",
    "Funding Source": "funding_source",
    "Referral Source": "referral_source",
    "Current Lease Up/Unit Address": "current_lease_up_unit_address",
    "Current Lease-up Date": "current_lease_up_date",
    "Date Placed in PSH": "date_placed_in_psh",
    "Date Placed in PSH (Formula)": "date_placed_in_psh_formula",
    "Housed": "housed",
    "Exited": "exited",
    "Reason for Exit": "reason_for_exit",
    "UNIT AVAILABILITY": "unit_availability",
    "Unit Lease-ups": "unit_lease_ups",
    "Type of Voucher": "type_of_voucher",
    "Date Voucher Issued2": "date_voucher_issued",
    "Date Referred to DHS Housing Matching Team": "date_referred_to_dhs_housing_matching_team",
    "Date of Match to Program": "date_of_match_to_program",
    "Move type": "move_type",
    "DCHA Application Status": "dcha_application_status",
    "# of days in PSH since referral": "days_in_psh_since_referral",
    "Source App": "source_app",
    "Date of Intake": "date_of_intake",
    "What can we help you with today?": "services_requested",
    "Would you like to meet with staff regarding potential benefits?": "benefits_staff_interest",
    "Are there any other services you would like to access at N Street Village?": "other_services_requested",
    "Would you like additional information about any of the following?   *What might you be interested in accessing in the future*": "future_service_interest",
    "Are you receiving SNAP ?": "snap_status",
    "Have you received a letter regarding work requirements ?": "snap_work_requirements_letter",
    "Are you a DC Resident?": "dc_resident",
    "Do you have a photo ID?": "has_photo_id",
    "State/Issuer": "photo_id_state_issuer",
    "Are you an NSV Resident?": "nsv_resident",
    "NSV Program": "nsv_program",
    "Primary Race": "primary_race",
    "Secondary Race": "secondary_race",
    "Pronouns": "pronouns",
    "Additional Pronouns": "additional_pronouns",
    "Primary Language": "primary_language",
    "Current Housing": "current_housing",
    "Where/Address of where you are currently staying": "current_stay_location",
    "How long have you been living in this situation?": "current_housing_duration",
    "Reasons for being unhoused?": "reasons_for_unhoused",
    "Are you linked to a service agency (ex. Volunteers of America, Pathways to Housing, etc.)": "linked_service_agency",
    "Are you a military veteran?": "military_veteran",
    "Staff Completing Form": "staff_completing_form",
    "Encounter ID": "encounter_id",
    "Visit Type": "visit_type",
    "Visit Sub-Type": "visit_sub_type",
    "Visit Status": "visit_status",
    "Visit Reason": "visit_reason",
    "Patient Status": "patient_status",
    "Patient Language": "patient_language",
    "Appointment Facility Name": "appointment_facility_name",
    "Department Name": "department_name",
    "Practice Name": "practice_name",
    "Appointment Provider Name": "appointment_provider_name",
    "Resource Provider Name": "resource_provider_name",
    "Appointment Insurance Name": "appointment_insurance_name",
    "Primary Insurance Name": "primary_insurance_name",
    "Project": "project",
    "Entry Type": "entry_type",
    "Exit Destination": "exit_destination",
    "Household ID": "household_id",
    "Relationship to Head of Household": "relationship_to_head_of_household",
    "Prior Living Situation": "prior_living_situation",
    "Length of Stay in Previous Place": "length_of_stay_previous_place",
    "Approximate date this episode of homelessness started": "homelessness_episode_start",
    "Number of times the client has been on the streets, in ES, or SH in the past three years including today": "homelessness_times_last_three_years",
    "Total number of months homeless on the street, in ES or SH in the past three years": "homelessness_months_last_three_years",
    "Homelessness Primary Reason": "homelessness_primary_reason",
    "Zip Code Data Quality": "zip_code_data_quality",
    "Income from any Source": "income_any_source",
    "SNAP / Food Stamps": "snap_food_stamps",
    "Covered by Health Insurance": "health_insurance_status",
    "Does the client have their birth certificate": "birth_certificate_status",
    "Does the client have their social security card": "social_security_card_status",
    "Does the client have their state-issued ID": "state_id_status",
    "Are you engaged with case management": "case_management_status",
    "Survivor of Domestic Violence": "survivor_of_domestic_violence",
    "If \"Yes,\" When Experience Occurred": "domestic_violence_when",
    " Are you currently fleeing?": "currently_fleeing_domestic_violence",
}

# Fields that belong directly to the client/enrollment import model.
CORE_FIELDS = {
    "first_name",
    "last_name",
    "full_name",
    "date_of_birth",
    "hmis_id",
    "ecw_id",
    "entry_date",
    "exit_date",
    "status",
    "gender",
    "race",
    "ethnicity",
    "veteran_status",
}


def safe_str(value):
    """Convert spreadsheet cells into clean strings or None for empty values."""

    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text == "" or text.lower() in ["nan", "none", "null"]:
        return None
    return text


def is_metrics_layout(df: pd.DataFrame) -> bool:
    """Detect planning sheets with explicit Program/Target/Metric/Method rows."""

    normalized_columns = {str(c).strip().lower() for c in df.columns}
    return {"program", "target", "metric", "method"}.issubset(normalized_columns)


def is_occupancy_report_layout(df: pd.DataFrame) -> bool:
    """Detect HMIS weekly occupancy reports for the metrics branch."""

    normalized_columns = {str(c).strip().lower() for c in df.columns}
    return OCCUPANCY_REPORT_COLUMNS.issubset(normalized_columns)


def is_client_identity_layout(df: pd.DataFrame) -> bool:
    """Detect uploads that clearly contain person-level client identity fields."""

    columns = {str(column).strip().lower() for column in df.columns}

    has_full_name = bool({
        "full_name",
        "full name",
        "client full name",
        "client name",
        "patient name",
        "name",
    } & columns)
    has_split_name = bool({"first_name", "first name", "client first name", "patient first name"} & columns) and bool({
        "last_name",
        "last name",
        "client last name",
        "patient last name",
    } & columns)
    has_birthdate = bool({
        "date_of_birth",
        "date of birth",
        "dob",
        "date of birth.1",
        "date of birth(893)",
        "patient dob",
    } & columns)
    has_source_identity = bool({"source app", "what can we help you with today?"} & columns)

    return (has_full_name or has_split_name) and (has_birthdate or has_source_identity)


def is_supported_metrics_layout(df: pd.DataFrame) -> bool:
    """Return whether a dataframe can be handled by metrics.py."""

    return (
        is_metrics_layout(df)
        or is_occupancy_report_layout(df)
        or (is_operational_metrics_layout(df) and not is_client_identity_layout(df))
    )


def classify_preview_file_type(df: pd.DataFrame) -> str:
    """Return the preview label used by the frontend upload workflow."""

    return "metrics" if is_supported_metrics_layout(df) else "client"


def is_enrichment_only_layout(df: pd.DataFrame) -> bool:
    """Detect reports that should not create new clients by themselves."""

    normalized_columns = {str(c).strip().lower() for c in df.columns}
    return any(signature.issubset(normalized_columns) for signature in ENRICHMENT_ONLY_SIGNATURES)


def is_operational_metrics_layout(df: pd.DataFrame) -> bool:
    """Detect operational reports that can produce aggregate rollup metrics."""

    normalized_columns = {str(c).strip().lower() for c in df.columns}
    return any(signature.issubset(normalized_columns) for signature in OPERATIONAL_METRICS_SIGNATURES)


def load_file(path: Path, allow_metrics: bool = False) -> pd.DataFrame:
    """Read a CSV or Excel file into a dataframe and normalize its layout."""

    if path.suffix.lower() == ".csv":
        # Source files may come from different systems, so try common encodings.
        for encoding in ["utf-8-sig", "utf-16", "latin1"]:
            try:
                df = pd.read_csv(path, encoding=encoding, sep=None, engine="python")
                return normalize_csv_layout(df, allow_metrics=allow_metrics)
            except UnsupportedClientImportError:
                raise
            except Exception:
                continue
        return normalize_csv_layout(pd.read_csv(path, sep=None, engine="python"), allow_metrics=allow_metrics)

    if path.suffix.lower() in [".xlsx", ".xls"]:
        return read_best_excel_sheet(path, allow_metrics=allow_metrics)

    raise ValueError("Only CSV and Excel files are currently supported.")


def normalize_csv_layout(df: pd.DataFrame, allow_metrics: bool = False) -> pd.DataFrame:
    """Fix common CSV layout issues before previewing or importing."""

    if is_metrics_layout(df) or is_occupancy_report_layout(df):
        if allow_metrics:
            return df
        raise UnsupportedClientImportError(
            "This looks like a program metrics/planning file, not a client import file."
        )

    if score_identity_columns(df.columns) > 0:
        return df

    # Some reports have title rows above the real header. Look for a header row.
    for row_index, row in df.head(10).iterrows():
        values = [safe_str(value) for value in row.tolist()]
        normalized_values = {value.strip().lower() for value in values if value}

        header_signatures = [
            {"hmis id"},
            {"program provider id", "households enrolled in hmis"},
            {"client uid", "client first name", "client last name"},
        ]
        if not any(signature.issubset(normalized_values) for signature in header_signatures):
            continue

        new_columns = []
        for index, value in enumerate(values):
            if value:
                new_columns.append(value)
            else:
                new_columns.append(f"Unnamed: {index}")

        cleaned = df.iloc[row_index + 1:].copy()
        cleaned.columns = new_columns
        cleaned = cleaned.dropna(how="all")
        cleaned = cleaned.reset_index(drop=True)

        if is_metrics_layout(cleaned) or is_occupancy_report_layout(cleaned):
            if allow_metrics:
                return cleaned
            raise UnsupportedClientImportError(
                "This looks like a program metrics/planning file, not a client import file."
            )

        return cleaned

    return df


def score_identity_columns(columns) -> int:
    """Score how many known identity columns appear in a sheet."""

    normalized = {str(c).strip().lower() for c in columns}
    score = 0
    for aliases in DEFAULT_COLUMN_ALIASES.values():
        if any(alias in normalized for alias in aliases):
            score += 1
    return score


def read_best_excel_sheet(path: Path, allow_metrics: bool = False) -> pd.DataFrame:
    """Pick the Excel sheet that looks most like a client import sheet."""

    sheets = pd.read_excel(path, sheet_name=None)
    best_sheet = None
    best_score = -1

    for sheet in sheets.values():
        sheet = normalize_csv_layout(sheet, allow_metrics=allow_metrics)

        if is_metrics_layout(sheet) or is_occupancy_report_layout(sheet):
            if allow_metrics:
                return sheet
            raise UnsupportedClientImportError(
                "This looks like a program metrics/planning file, not a client import file."
            )

        score = score_identity_columns(sheet.columns)
        if score > best_score:
            best_sheet = sheet
            best_score = score

    if best_sheet is None:
        raise ValueError("Excel workbook does not contain a readable sheet.")

    return best_sheet


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename recognized source columns to canonical internal field names."""

    lower_map = {str(c).strip().lower(): c for c in df.columns}
    rename_map = {}

    for canonical, aliases in DEFAULT_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_map:
                rename_map[lower_map[alias]] = canonical
                break

    return df.rename(columns=rename_map)


def apply_column_mapping(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """Apply the user's frontend column mapping to the dataframe."""

    clean_mapping = {}
    used_targets = set()

    for source, target in mapping.items():
        # Ignore blank mappings and prevent two source columns from mapping to one target.
        if not target or target in used_targets:
            continue
        clean_mapping[source] = target
        used_targets.add(target)

    return df.rename(columns=clean_mapping)


def normalize_import_columns(df: pd.DataFrame, mapping: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """Apply user mappings first, then apply known automatic aliases."""

    if mapping:
        df = apply_column_mapping(df, mapping)
    return normalize_columns(df)


def get_next_client_number(db: Session) -> int:
    """Return the next number used to generate an NSV client ID."""

    return db.query(Client).count() + 1


def get_or_create_program(db: Session, program_name: str, source_system: str) -> Program:
    """Find an existing program or create it during import."""

    program = db.query(Program).filter(Program.program_name == program_name).first()
    if program:
        return program

    program = Program(program_name=program_name, source_system=source_system)
    db.add(program)
    db.commit()
    db.refresh(program)
    return program


def preview_file(file_path: str, max_rows: int = 10):
    """Return file metadata, columns, and sample rows for the frontend preview."""

    path = Path(file_path)
    df = load_file(path, allow_metrics=True)
    return {
        "file_name": path.name,
        "file_type": classify_preview_file_type(df),
        "columns": [str(c) for c in df.columns],
        "rows": df.head(max_rows).fillna("").to_dict(orient="records"),
        "row_count": len(df),
    }


def extract_identity(row):
    """Extract the identity fields needed for client matching from one row."""

    full_name = safe_str(row.get("full_name")) if "full_name" in row else None
    backup_name = get_first_present_value(row, ["Client Name", "Patient Name", "Preferred Name", "Name"])

    # Some JotForm-style values label a row as returning/new instead of giving a name.
    if full_name:
        normalized_full_name = full_name.strip().lower()
        if normalized_full_name in ["new client", "returning", "returning client"]:
            full_name = backup_name
        elif normalized_full_name.startswith("returning (") and full_name.endswith(")"):
            full_name = full_name[len("returning ("):-1].strip()
        elif normalized_full_name.startswith("new client (") and full_name.endswith(")"):
            full_name = full_name[len("new client ("):-1].strip()

    full_name = full_name or backup_name

    if full_name:
        first_name, last_name = split_full_name(full_name)
    else:
        first_name = clean_name(row.get("first_name", ""))
        last_name = clean_name(row.get("last_name", ""))

    dob = parse_date(row.get("date_of_birth", None))
    if not dob:
        dob = parse_date(get_first_present_value(row, ["Date of Birth.1", "Patient DOB"]))

    hmis_id = safe_str(row.get("hmis_id")) if "hmis_id" in row else None
    ecw_id = safe_str(row.get("ecw_id")) if "ecw_id" in row else None

    return first_name, last_name, dob, hmis_id, ecw_id


def extract_jotform_client_status(row):
    """Return JotForm's New Client/Returning signal when present."""

    # JotForm exports can put "Returning" or "New Client (...)" in different
    # columns depending on the form version, so check the likely places.
    status_candidates = [
        get_first_present_value(row, ["No Label", "Client Type", "Client Status", "Status"]),
        safe_str(row.get("full_name")) if "full_name" in row else None,
        get_first_present_value(row, ["Full Name", "Client Name", "Name"]),
    ]

    for value in status_candidates:
        if not value:
            continue
        normalized = value.strip().lower()
        if normalized.startswith("returning"):
            return "Returning"
        if normalized.startswith("new client"):
            return "New Client"

    return None


def is_jotform_returning_row(row, source_system):
    """Identify JotForm rows that say the person is returning."""

    if "jotform" not in str(source_system or "").lower():
        return False
    return extract_jotform_client_status(row) == "Returning"


def get_first_present_value(row, column_names):
    """Return the first non-empty value found across possible source column names."""

    for column_name in column_names:
        if column_name in row:
            value = safe_str(row.get(column_name))
            if value:
                return value
    return None


def extract_enrollment_dates(row):
    """Extract entry/exit dates for the Enrollment row."""

    entry_date = parse_date(row.get("entry_date", None)) if "entry_date" in row else None
    exit_date = parse_date(row.get("exit_date", None)) if "exit_date" in row else None

    # Housing files may use lease-up or placement dates instead of entry_date.
    if not entry_date:
        entry_date = parse_date(get_first_present_value(row, [
            "Current Lease-up Date",
            "Date Placed in PSH",
            "Date Placed in PSH (Formula)",
        ]))

    return entry_date, exit_date


def extract_status(row):
    """Extract a simple program status from source-specific status columns."""

    status = safe_str(row.get("status")) if "status" in row else None
    housed = get_first_present_value(row, ["Housed", "housed"])
    exited = get_first_present_value(row, ["Exited", "exited"])

    if exited and exited.lower() in ["yes", "y", "true", "1"]:
        return "Exited"
    if housed and housed.lower() in ["yes", "y", "true", "1"]:
        return "Housed"
    return status


def add_source_details(db, row, client, source_system, program_name, path):
    """Store useful non-core source fields for later profile display."""

    normalized_columns = {str(column).strip().lower(): column for column in row.index}
    captured_columns = set()

    for source_column, field_name in SOURCE_DETAIL_FIELDS.items():
        # Match source columns case-insensitively.
        actual_column = normalized_columns.get(source_column.lower())
        if not actual_column:
            continue

        value = safe_str(row.get(actual_column))
        if not value:
            continue

        db.add(SourceDetail(
            nsv_client_id=client.nsv_client_id,
            source_system=source_system,
            program_name=program_name,
            detail_type="HTH Housing" if source_system.upper() == "HTH" else "Source Metadata",
            field_name=field_name,
            field_value=value,
            original_file=path.name,
        ))
        captured_columns.add(actual_column)

    # Keep JotForm's returning/new-client signal visible in the profile even
    # though it is not a standard identity field.
    jotform_status = extract_jotform_client_status(row)
    if jotform_status and "jotform" in str(source_system or "").lower():
        db.add(SourceDetail(
            nsv_client_id=client.nsv_client_id,
            source_system=source_system,
            program_name=program_name,
            detail_type="Source Metadata",
            field_name="jotform_client_status",
            field_value=jotform_status,
            original_file=path.name,
        ))


def add_identity_enrichment_detail(db, client, source_system, program_name, path, reason):
    """Mark a profile that was imported without enough identity detail."""

    existing = (
        db.query(SourceDetail)
        .filter(
            SourceDetail.nsv_client_id == client.nsv_client_id,
            SourceDetail.field_name == "identity_enrichment_status",
            SourceDetail.field_value == reason,
        )
        .first()
    )
    if existing:
        return

    db.add(SourceDetail(
        nsv_client_id=client.nsv_client_id,
        source_system=source_system,
        program_name=program_name,
        detail_type="Identity Enrichment",
        field_name="identity_enrichment_status",
        field_value=reason,
        original_file=path.name,
    ))


def add_client_alias(
    db,
    client,
    first_name,
    last_name,
    dob=None,
    source_system=None,
    original_file=None,
    created_from_review_id=None,
    confidence_score=None,
):
    """Remember a reviewed alternate name spelling for future imports."""

    if not client or not first_name or not last_name:
        return None

    alias_first = clean_name(first_name)
    alias_last = clean_name(last_name)
    if (
        normalize_for_match(alias_first) == normalize_for_match(client.first_name)
        and normalize_for_match(alias_last) == normalize_for_match(client.last_name)
        and (not dob or dob == client.date_of_birth)
    ):
        return None

    for alias in db.query(ClientAlias).filter(ClientAlias.nsv_client_id == client.nsv_client_id).all():
        same_name = (
            normalize_for_match(alias.alias_first_name) == normalize_for_match(alias_first)
            and normalize_for_match(alias.alias_last_name) == normalize_for_match(alias_last)
        )
        same_dob = alias.alias_dob == dob
        if same_name and same_dob:
            return alias

    alias = ClientAlias(
        nsv_client_id=client.nsv_client_id,
        alias_first_name=alias_first,
        alias_last_name=alias_last,
        alias_dob=dob,
        source_system=source_system,
        original_file=original_file,
        created_from_review_id=created_from_review_id,
        confidence_score=confidence_score,
    )
    db.add(alias)
    return alias


def create_client(db: Session, first_name, last_name, dob, hmis_id, ecw_id, row):
    """Create a new master client record from one import row."""

    nsv_id = generate_nsv_id(get_next_client_number(db))
    client = Client(
        nsv_client_id=nsv_id,
        first_name=first_name,
        last_name=last_name,
        date_of_birth=dob,
        hmis_id=hmis_id,
        ecw_id=ecw_id,
        gender=safe_str(row.get("gender")) if "gender" in row else None,
        race=safe_str(row.get("race")) if "race" in row else None,
        ethnicity=safe_str(row.get("ethnicity")) if "ethnicity" in row else None,
        veteran_status=safe_str(row.get("veteran_status")) if "veteran_status" in row else None,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def update_client_from_row(client, hmis_id, ecw_id, dob, row):
    """Fill missing client fields from a matched source row."""

    if hmis_id and not client.hmis_id:
        client.hmis_id = hmis_id
    if ecw_id and not client.ecw_id:
        client.ecw_id = ecw_id
    if dob and not client.date_of_birth:
        client.date_of_birth = dob

    for field in ["gender", "race", "ethnicity", "veteran_status"]:
        value = safe_str(row.get(field)) if field in row else None
        if value and not getattr(client, field):
            setattr(client, field, value)


def add_review_record(db, row, source_system, program_name, path, possible_client, first_name, last_name, dob, confidence, reason):
    """Create a review queue item for uncertain or ambiguous matches."""

    review = PotentialMatch(
        source_system=source_system,
        program_name=program_name,
        original_file=path.name,
        possible_nsv_client_id=possible_client.nsv_client_id if possible_client else None,
        suggested_first_name=first_name,
        suggested_last_name=last_name,
        suggested_dob=dob,
        confidence_score=confidence,
        review_reason=reason,
        status="Needs Review",
        raw_data_json=json.dumps(row.to_dict(), default=str),
    )
    db.add(review)


def add_fuzzy_duplicate_reviews(db, row, source_system, program_name, path, first_name, last_name, dob):
    """Create one review row per likely fuzzy duplicate candidate."""

    fuzzy_candidates = find_fuzzy_duplicate_candidates(db, first_name, last_name, dob)
    if not fuzzy_candidates:
        return 0

    for possible_client, confidence in fuzzy_candidates:
        add_review_record(
            db=db,
            row=row,
            source_system=source_system,
            program_name=program_name,
            path=path,
            possible_client=possible_client,
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            confidence=confidence,
            reason="Possible duplicate by similar name",
        )

    return len(fuzzy_candidates)


def import_file(
    db: Session,
    file_path: str,
    source_system: str,
    program_name: str,
    original_file_name: Optional[str] = None,
    column_mapping: Optional[Dict[str, str]] = None,
):
    """Import a client CSV/Excel file into master clients and related tables."""

    path = Path(file_path)
    display_file_name = original_file_name or path.name

    # Read the file and normalize column names before processing rows.
    df = load_file(path)
    df = normalize_import_columns(df, column_mapping)
    enrichment_only = is_enrichment_only_layout(df)

    # Track import results so the frontend can show a summary.
    rows_processed = 0
    rows_created = 0
    rows_matched = 0
    rows_review = 0
    rows_failed = 0
    failed_rows = []

    program = get_or_create_program(db, program_name, source_system)

    # Process each spreadsheet row independently so one bad row does not stop the import.
    for _, row in df.iterrows():
        rows_processed += 1

        try:
            # Pull out identity fields and decide whether to match, create, review, or fail.
            first_name, last_name, dob, hmis_id, ecw_id = extract_identity(row)
            client, match_method, confidence, action = match_client(db, first_name, last_name, dob, hmis_id, ecw_id)
            jotform_status = extract_jotform_client_status(row)

            if enrichment_only and action == "create":
                rows_failed += 1
                failed_rows.append({
                    "row": rows_processed,
                    "reason": "Operational report row was not matched to an existing client.",
                })
                continue

            if enrichment_only and action == "review" and not client:
                rows_failed += 1
                failed_rows.append({
                    "row": rows_processed,
                    "reason": "Operational report row needs a matching master client.",
                })
                continue

            # Rows without enough identity data cannot be imported.
            if action == "failed":
                rows_failed += 1
                failed_rows.append({"row": rows_processed, "reason": match_method})
                continue

            # Ambiguous rows are saved for manual review instead of auto-matching.
            if action == "review":
                review_path = Path(display_file_name)
                fuzzy_review_count = add_fuzzy_duplicate_reviews(
                    db, row, source_system, program_name, review_path, first_name, last_name, dob
                )
                if fuzzy_review_count:
                    rows_review += fuzzy_review_count
                else:
                    rows_review += 1
                    add_review_record(
                        db=db,
                        row=row,
                        source_system=source_system,
                        program_name=program_name,
                        path=review_path,
                        possible_client=client,
                        first_name=first_name,
                        last_name=last_name,
                        dob=dob,
                        confidence=confidence,
                        reason=match_method,
                    )
                db.commit()
                continue

            # Either create a new client or update missing fields on an existing match.
            if action == "create":
                fuzzy_review_count = add_fuzzy_duplicate_reviews(
                    db, row, source_system, program_name, Path(display_file_name), first_name, last_name, dob
                )
                if fuzzy_review_count:
                    rows_review += fuzzy_review_count
                    db.commit()
                    continue
                client = create_client(db, first_name, last_name, dob, hmis_id, ecw_id, row)
                rows_created += 1
            else:
                rows_matched += 1
                update_client_from_row(client, hmis_id, ecw_id, dob, row)
                db.commit()

            source_client_id = hmis_id or ecw_id

            # Store the original row and matching explanation for traceability.
            db.add(ClientSource(
                nsv_client_id=client.nsv_client_id,
                source_system=source_system,
                source_client_id=source_client_id,
                original_file=display_file_name,
                raw_data_json=json.dumps(row.to_dict(), default=str),
                match_method=f"{match_method} ({jotform_status})" if jotform_status else match_method,
                confidence_score=confidence,
            ))

            # Store additional source-specific details for the client profile.
            add_source_details(db, row, client, source_system, program_name, Path(display_file_name))

            # DOB-less rows are allowed into the database, but tagged so later
            # imports can fill the missing birthday when a match is found.
            if not dob and not client.date_of_birth:
                add_identity_enrichment_detail(
                    db=db,
                    client=client,
                    source_system=source_system,
                    program_name=program_name,
                    path=Path(display_file_name),
                    reason="Needs DOB from another import",
                )

            entry_date, exit_date = extract_enrollment_dates(row)
            status = extract_status(row)

            # Add a program enrollment/activity row for this imported source row.
            db.add(Enrollment(
                nsv_client_id=client.nsv_client_id,
                program_id=program.program_id,
                entry_date=entry_date,
                exit_date=exit_date,
                status=status,
            ))

            db.commit()

        except Exception as error:
            # Roll back this row only, count it as failed, and continue importing.
            db.rollback()
            rows_failed += 1
            failed_rows.append({"row": rows_processed, "reason": str(error)})

    # Save a final summary row for the import history.
    db.add(ImportLog(
        file_name=display_file_name,
        source_system=source_system,
        program_name=program_name,
        rows_processed=rows_processed,
        rows_created=rows_created,
        rows_matched=rows_matched,
        rows_review=rows_review,
        rows_failed=rows_failed,
    ))
    db.commit()

    return {
        "file_name": display_file_name,
        "source_system": source_system,
        "program_name": program_name,
        "rows_processed": rows_processed,
        "rows_created": rows_created,
        "rows_matched": rows_matched,
        "rows_review": rows_review,
        "rows_failed": rows_failed,
        "failed_rows": failed_rows[:50],
    }
