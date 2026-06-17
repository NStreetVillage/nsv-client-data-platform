import json
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import or_

from .models import Client, Program, ClientSource, SourceDetail, Enrollment, ImportLog, PotentialMatch
from .utils import clean_name, normalize_for_match, parse_date, generate_nsv_id, split_full_name


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
        "patient dob", "patient date of birth", "date of birth.1"
    ],
    "hmis_id": [
        "hmis_id", "hmis id", "hmisid", "personal id", "personalid", "client id",
        "unique id", "dhs client id# - hmis id"
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


SOURCE_DETAIL_FIELDS = {
    "Program": "program",
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
    "What can we help you with today?": "services_requested",
    "Are there any other services you would like to access at N Street Village?": "other_services_requested",
    "Are you receiving SNAP ?": "snap_status",
    "Are you a DC Resident?": "dc_resident",
    "Are you an NSV Resident?": "nsv_resident",
    "NSV Program": "nsv_program",
    "Primary Language": "primary_language",
    "Current Housing": "current_housing",
    "Staff Completing Form": "staff_completing_form",
    "Encounter ID": "encounter_id",
    "Visit Type": "visit_type",
    "Visit Status": "visit_status",
    "Visit Reason": "visit_reason",
    "Department Name": "department_name",
    "Practice Name": "practice_name",
    "Appointment Facility Name": "appointment_facility_name",
    "Appointment Provider Name": "appointment_provider_name",
    "Project": "project",
    "Entry Type": "entry_type",
    "Exit Destination": "exit_destination",
    "Household ID": "household_id",
    "Relationship to Head of Household": "relationship_to_head_of_household",
}

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
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text == "" or text.lower() in ["nan", "none", "null"]:
        return None
    return text


def load_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        for encoding in ["utf-8-sig", "utf-16", "latin1"]:
            try:
                return pd.read_csv(path, encoding=encoding, sep=None, engine="python")
            except Exception:
                continue
        return pd.read_csv(path, sep=None, engine="python")

    if path.suffix.lower() in [".xlsx", ".xls"]:
        return read_best_excel_sheet(path)

    raise ValueError("Only CSV and Excel files are currently supported.")


def score_identity_columns(columns) -> int:
    normalized = {str(c).strip().lower() for c in columns}
    score = 0
    for aliases in DEFAULT_COLUMN_ALIASES.values():
        if any(alias in normalized for alias in aliases):
            score += 1
    return score


def read_best_excel_sheet(path: Path) -> pd.DataFrame:
    sheets = pd.read_excel(path, sheet_name=None)
    best_sheet = None
    best_score = -1

    for sheet in sheets.values():
        score = score_identity_columns(sheet.columns)
        if score > best_score:
            best_sheet = sheet
            best_score = score

    if best_sheet is None:
        raise ValueError("Excel workbook does not contain a readable sheet.")

    return best_sheet


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    rename_map = {}

    for canonical, aliases in DEFAULT_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_map:
                rename_map[lower_map[alias]] = canonical
                break

    return df.rename(columns=rename_map)


def apply_column_mapping(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    clean_mapping = {}
    used_targets = set()

    for source, target in mapping.items():
        if not target or target in used_targets:
            continue
        clean_mapping[source] = target
        used_targets.add(target)

    return df.rename(columns=clean_mapping)


def normalize_import_columns(df: pd.DataFrame, mapping: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    if mapping:
        df = apply_column_mapping(df, mapping)
    return normalize_columns(df)


def get_next_client_number(db: Session) -> int:
    return db.query(Client).count() + 1


def get_or_create_program(db: Session, program_name: str, source_system: str) -> Program:
    program = db.query(Program).filter(Program.program_name == program_name).first()
    if program:
        return program

    program = Program(program_name=program_name, source_system=source_system)
    db.add(program)
    db.commit()
    db.refresh(program)
    return program


def preview_file(file_path: str, max_rows: int = 10):
    path = Path(file_path)
    df = load_file(path)
    return {
        "file_name": path.name,
        "columns": [str(c) for c in df.columns],
        "rows": df.head(max_rows).fillna("").to_dict(orient="records"),
        "row_count": len(df),
    }


def find_by_hmis_id(db: Session, hmis_id: Optional[str]):
    if not hmis_id:
        return None
    return db.query(Client).filter(Client.hmis_id == hmis_id).first()


def find_by_ecw_id(db: Session, ecw_id: Optional[str]):
    if not ecw_id:
        return None
    return db.query(Client).filter(Client.ecw_id == ecw_id).first()


def find_by_name_dob(db: Session, first_name: str, last_name: str, dob):
    if not first_name or not last_name or not dob:
        return None

    candidates = db.query(Client).filter(Client.date_of_birth == dob).all()
    nf = normalize_for_match(first_name)
    nl = normalize_for_match(last_name)

    for client in candidates:
        if normalize_for_match(client.first_name) == nf and normalize_for_match(client.last_name) == nl:
            return client

    return None


def find_name_only_candidates(db: Session, first_name: str, last_name: str):
    if not first_name or not last_name:
        return []

    candidates = (
        db.query(Client)
        .filter(Client.first_name.ilike(first_name), Client.last_name.ilike(last_name))
        .limit(5)
        .all()
    )
    matches = []
    for client in candidates:
        if (
            normalize_for_match(client.first_name) == normalize_for_match(first_name)
            and normalize_for_match(client.last_name) == normalize_for_match(last_name)
        ):
            matches.append(client)

    return matches


def find_by_partial_identity(db: Session, first_name: str, last_name: str, dob):
    if not first_name and not last_name and not dob:
        return []

    filters = []
    if dob:
        filters.append(Client.date_of_birth == dob)
    if first_name:
        filters.append(Client.first_name.ilike(first_name))
    if last_name:
        filters.append(Client.last_name.ilike(last_name))

    candidates = db.query(Client).filter(or_(*filters)).limit(10).all()
    matches = []
    nf = normalize_for_match(first_name)
    nl = normalize_for_match(last_name)

    for client in candidates:
        first_matches = nf and normalize_for_match(client.first_name) == nf
        last_matches = nl and normalize_for_match(client.last_name) == nl
        dob_matches = dob and client.date_of_birth == dob

        if dob_matches and (first_matches or last_matches):
            matches.append(client)

    return matches


def extract_identity(row):
    full_name = (
        safe_str(row.get("full_name")) if "full_name" in row else None
    ) or get_first_present_value(row, ["Client Name", "Patient Name", "Preferred Name", "Name"])

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


def get_first_present_value(row, column_names):
    for column_name in column_names:
        if column_name in row:
            value = safe_str(row.get(column_name))
            if value:
                return value
    return None


def extract_enrollment_dates(row):
    entry_date = parse_date(row.get("entry_date", None)) if "entry_date" in row else None
    exit_date = parse_date(row.get("exit_date", None)) if "exit_date" in row else None

    if not entry_date:
        entry_date = parse_date(get_first_present_value(row, [
            "Current Lease-up Date",
            "Date Placed in PSH",
            "Date Placed in PSH (Formula)",
        ]))

    return entry_date, exit_date


def extract_status(row):
    status = safe_str(row.get("status")) if "status" in row else None
    housed = get_first_present_value(row, ["Housed", "housed"])
    exited = get_first_present_value(row, ["Exited", "exited"])

    if exited and exited.lower() in ["yes", "y", "true", "1"]:
        return "Exited"
    if housed and housed.lower() in ["yes", "y", "true", "1"]:
        return "Housed"
    return status


def add_source_details(db, row, client, source_system, program_name, path):
    normalized_columns = {str(column).strip().lower(): column for column in row.index}
    captured_columns = set()

    for source_column, field_name in SOURCE_DETAIL_FIELDS.items():
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


def match_client(db: Session, first_name, last_name, dob, hmis_id, ecw_id):
    """
    Matching hierarchy:
    1. HMIS ID match
    2. eCW ID match
    3. Name + DOB match
    4. Unique name-only match
    5. Partial DOB + name match
    6. Create partial client when enough identity data exists
    """

    client = find_by_hmis_id(db, hmis_id)
    if client:
        return client, "HMIS ID", 1.00, "matched"

    client = find_by_ecw_id(db, ecw_id)
    if client:
        return client, "eCW ID", 1.00, "matched"

    client = find_by_name_dob(db, first_name, last_name, dob)
    if client:
        return client, "Name + DOB", 0.95, "matched"

    name_candidates = find_name_only_candidates(db, first_name, last_name)
    if len(name_candidates) == 1:
        return name_candidates[0], "Name only", 0.75, "matched"

    partial_candidates = find_by_partial_identity(db, first_name, last_name, dob)
    if len(partial_candidates) == 1:
        return partial_candidates[0], "Partial identity", 0.70, "matched"

    if not first_name or not last_name:
        return None, "Missing name", 0.00, "failed"

    if len(name_candidates) > 1:
        return name_candidates[0], "Multiple name matches", 0.50, "review"

    if not dob:
        return None, "New partial client", 0.60, "create"

    return None, "New client", 0.90, "create"


def create_client(db: Session, first_name, last_name, dob, hmis_id, ecw_id, row):
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


def update_client_from_row(client, hmis_id, ecw_id, row):
    if hmis_id and not client.hmis_id:
        client.hmis_id = hmis_id
    if ecw_id and not client.ecw_id:
        client.ecw_id = ecw_id

    for field in ["gender", "race", "ethnicity", "veteran_status"]:
        value = safe_str(row.get(field)) if field in row else None
        if value and not getattr(client, field):
            setattr(client, field, value)


def add_review_record(db, row, source_system, program_name, path, possible_client, first_name, last_name, dob, confidence, reason):
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


def import_file(
    db: Session,
    file_path: str,
    source_system: str,
    program_name: str,
    column_mapping: Optional[Dict[str, str]] = None,
):
    path = Path(file_path)
    df = load_file(path)
    df = normalize_import_columns(df, column_mapping)

    rows_processed = 0
    rows_created = 0
    rows_matched = 0
    rows_review = 0
    rows_failed = 0
    failed_rows = []

    program = get_or_create_program(db, program_name, source_system)

    for _, row in df.iterrows():
        rows_processed += 1

        try:
            first_name, last_name, dob, hmis_id, ecw_id = extract_identity(row)
            client, match_method, confidence, action = match_client(db, first_name, last_name, dob, hmis_id, ecw_id)

            if action == "failed":
                rows_failed += 1
                failed_rows.append({"row": rows_processed, "reason": match_method})
                continue

            if action == "review":
                rows_review += 1
                add_review_record(
                    db=db,
                    row=row,
                    source_system=source_system,
                    program_name=program_name,
                    path=path,
                    possible_client=client,
                    first_name=first_name,
                    last_name=last_name,
                    dob=dob,
                    confidence=confidence,
                    reason=match_method,
                )
                db.commit()
                continue

            if action == "create":
                client = create_client(db, first_name, last_name, dob, hmis_id, ecw_id, row)
                rows_created += 1
            else:
                rows_matched += 1
                update_client_from_row(client, hmis_id, ecw_id, row)
                db.commit()

            source_client_id = hmis_id or ecw_id

            db.add(ClientSource(
                nsv_client_id=client.nsv_client_id,
                source_system=source_system,
                source_client_id=source_client_id,
                original_file=path.name,
                raw_data_json=json.dumps(row.to_dict(), default=str),
                match_method=match_method,
                confidence_score=confidence,
            ))

            add_source_details(db, row, client, source_system, program_name, path)

            entry_date, exit_date = extract_enrollment_dates(row)
            status = extract_status(row)

            db.add(Enrollment(
                nsv_client_id=client.nsv_client_id,
                program_id=program.program_id,
                entry_date=entry_date,
                exit_date=exit_date,
                status=status,
            ))

            db.commit()

        except Exception as error:
            db.rollback()
            rows_failed += 1
            failed_rows.append({"row": rows_processed, "reason": str(error)})

    db.add(ImportLog(
        file_name=path.name,
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
        "file_name": path.name,
        "source_system": source_system,
        "program_name": program_name,
        "rows_processed": rows_processed,
        "rows_created": rows_created,
        "rows_matched": rows_matched,
        "rows_review": rows_review,
        "rows_failed": rows_failed,
        "failed_rows": failed_rows[:50],
    }
