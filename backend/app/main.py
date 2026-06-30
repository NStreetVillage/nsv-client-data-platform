"""FastAPI application for the NSV Client Data Platform.

This file defines the HTTP API used by frontend/upload.html. It serves the
single-page upload interface, receives uploaded spreadsheets, exposes client
search/profile endpoints, returns dashboard stats, and manages metrics/review
data.
"""

import os
import uuid
import json
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
import pandas as pd
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .client_snapshot import build_client_needs_summary, build_client_service_snapshot
from .database import SessionLocal
from .models import Client, ClientAlias, Program, ClientSource, SourceDetail, Enrollment, ImportLog, PotentialMatch, ProgramMetric
from .schemas import ClientOut
from .importer import (
    add_identity_enrichment_detail,
    add_client_alias,
    add_source_details,
    create_client,
    extract_enrollment_dates,
    extract_identity,
    extract_status,
    get_or_create_program,
    import_file,
    load_file,
    normalize_import_columns,
    preview_file,
    update_client_from_row,
)
from .import_routing import ImportRoute, classify_upload_dataframe
from .metrics import get_metrics_summary, import_metrics_file
from .utils import normalize_for_match, parse_date_candidates

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

# Main FastAPI app object. Uvicorn imports this when the backend starts.
app = FastAPI(title="NSV Client Data Platform")


class ImportRequest(BaseModel):
    """Request body for importing a file that was already uploaded for preview."""

    upload_id: str
    source_system: str = ""
    program_name: str = ""
    original_file_name: Optional[str] = None
    column_mapping: Dict[str, str] = {}


class DeleteClientsRequest(BaseModel):
    """Request body for deleting selected client profiles from Admin settings."""

    nsv_client_ids: List[str]


class ClearDatabaseRequest(BaseModel):
    """Request body for clearing all demo/import data from Admin settings."""

    confirmation: str = ""


class MergeClientsRequest(BaseModel):
    """Request body for merging one duplicate client profile into another."""

    keep_nsv_client_id: str
    merge_nsv_client_id: str


class ReviewActionRequest(BaseModel):
    """Request body for resolving one review queue row."""

    action: str
    target_nsv_client_id: Optional[str] = None


class BulkReviewActionRequest(BaseModel):
    """Request body for resolving selected review rows at once."""

    review_ids: List[int]


def get_db():
    """Create one database session per request and close it afterward."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def root():
    """Simple health check endpoint for confirming the API is running."""

    return {"message": "NSV Client Data Platform API is running"}


@app.get("/upload-page")
def upload_page():
    """Serve the prototype frontend page."""

    project_root = Path(__file__).resolve().parents[2]
    return FileResponse(project_root / "frontend" / "upload.html")


@app.get("/clients")
def get_clients(
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=500),
    sort_by: str = Query(default="created_at"),
    sort_dir: str = Query(default="desc"),
    db: Session = Depends(get_db),
):
    """Search and paginate master client records for the Clients tab."""

    query = db.query(Client)

    if search:
        # Search across core client fields, DOB interpretations, and source details.
        search_text = search.strip()
        search_like = f"%{search_text}%"
        filters = [
            Client.nsv_client_id.ilike(search_like),
            Client.first_name.ilike(search_like),
            Client.last_name.ilike(search_like),
            Client.hmis_id.ilike(search_like),
            Client.ecw_id.ilike(search_like),
        ]

        for parsed_date in parse_date_candidates(search_text):
            filters.append(Client.date_of_birth == parsed_date)

        detail_matches = (
            select(SourceDetail.nsv_client_id)
            .filter(SourceDetail.field_value.ilike(search_like))
        )
        filters.append(Client.nsv_client_id.in_(detail_matches))

        name_parts = [part for part in search_text.split() if part]
        if len(name_parts) >= 2:
            # Support searching a full name like "Jane Doe".
            first_part = name_parts[0]
            last_part = " ".join(name_parts[1:])
            filters.append(
                (Client.first_name.ilike(f"%{first_part}%"))
                & (Client.last_name.ilike(f"%{last_part}%"))
            )

        query = query.filter(or_(*filters))

    total = query.count()
    offset = (page - 1) * page_size

    sort_direction = sort_dir.lower()
    is_ascending = sort_direction == "asc"

    first_name_sort = func.lower(func.ltrim(Client.first_name, " .`'\"-"))
    last_name_sort = func.lower(func.ltrim(Client.last_name, " .`'\"-"))

    if sort_by == "first_name":
        sort_order = [
            first_name_sort.asc() if is_ascending else first_name_sort.desc(),
            last_name_sort.asc() if is_ascending else last_name_sort.desc(),
        ]
    elif sort_by == "last_name":
        sort_order = [
            last_name_sort.asc() if is_ascending else last_name_sort.desc(),
            first_name_sort.asc() if is_ascending else first_name_sort.desc(),
        ]
    elif sort_by == "dob":
        sort_order = [
            Client.date_of_birth.is_(None).asc(),
            Client.date_of_birth.asc() if is_ascending else Client.date_of_birth.desc(),
        ]
    elif sort_by == "nsv_id":
        sort_order = [Client.nsv_client_id.asc() if is_ascending else Client.nsv_client_id.desc()]
    else:
        sort_order = [Client.created_at.asc() if is_ascending else Client.created_at.desc()]

    clients = (
        query
        .order_by(*sort_order, Client.nsv_client_id.asc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "items": [ClientOut.model_validate(client) for client in clients],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


@app.get("/clients/{nsv_client_id}", response_model=ClientOut)
def get_client(nsv_client_id: str, db: Session = Depends(get_db)):
    """Return one client master record by NSV client ID."""

    client = db.query(Client).filter(Client.nsv_client_id == nsv_client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    return client


@app.get("/clients/{nsv_client_id}/programs")
def get_client_programs(nsv_client_id: str, db: Session = Depends(get_db)):
    """Return program enrollment/activity rows for a client profile."""

    client = db.query(Client).filter(Client.nsv_client_id == nsv_client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

    enrollments = (
        db.query(Enrollment, Program)
        .join(Program, Enrollment.program_id == Program.program_id)
        .filter(Enrollment.nsv_client_id == nsv_client_id)
        .order_by(Enrollment.created_at.desc())
        .all()
    )

    return [
        {
            "program_id": program.program_id,
            "program_name": program.program_name,
            "source_system": program.source_system,
            "entry_date": str(enrollment.entry_date) if enrollment.entry_date else None,
            "exit_date": str(enrollment.exit_date) if enrollment.exit_date else None,
            "status": enrollment.status,
        }
        for enrollment, program in enrollments
    ]


@app.get("/clients/{nsv_client_id}/details")
def get_client_details(nsv_client_id: str, db: Session = Depends(get_db)):
    """Return source summary and detailed source fields for a client profile."""

    client = db.query(Client).filter(Client.nsv_client_id == nsv_client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

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

    return {
        "sources": [
            {
                "source_system": source.source_system,
                "source_client_id": source.source_client_id,
                "original_file": source.original_file,
                "match_method": source.match_method,
                "confidence_score": source.confidence_score,
                "imported_at": str(source.imported_at) if source.imported_at else None,
            }
            for source in sources
        ],
        "details": [
            {
                "source_system": detail.source_system,
                "program_name": detail.program_name,
                "detail_type": detail.detail_type,
                "field_name": detail.field_name,
                "field_value": detail.field_value,
                "original_file": detail.original_file,
                "created_at": str(detail.created_at) if detail.created_at else None,
            }
            for detail in details
        ],
    }


@app.get("/clients/{nsv_client_id}/service-snapshot")
def get_client_service_snapshot(nsv_client_id: str, db: Session = Depends(get_db)):
    """Return a MyChart-style service snapshot for one client profile."""

    snapshot = build_client_service_snapshot(db, nsv_client_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Client not found.")
    return snapshot


@app.get("/programs")
def get_programs(db: Session = Depends(get_db)):
    """Return all known programs."""

    programs = db.query(Program).all()
    return [
        {
            "program_id": p.program_id,
            "program_name": p.program_name,
            "source_system": p.source_system,
        }
        for p in programs
    ]


@app.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """Return dashboard counts displayed on the Overview tab."""

    return {
        "clients": db.query(Client).count(),
        "programs": db.query(Program).count(),
        "imports": db.query(ImportLog).count(),
        "reviews": db.query(PotentialMatch).filter(PotentialMatch.status == "Needs Review").count(),
        "metrics": db.query(ProgramMetric).count(),
    }


def delete_client_records(db: Session, nsv_client_ids: List[str]):
    """Delete clients and dependent records that point at those NSV client IDs."""

    if not nsv_client_ids:
        return {
            "deleted_clients": 0,
            "deleted_sources": 0,
            "deleted_details": 0,
            "deleted_enrollments": 0,
            "deleted_aliases": 0,
            "deleted_reviews": 0,
        }

    # Delete child/source rows first so the client master rows do not leave
    # orphaned profile details, enrollments, or review queue references behind.
    deleted_sources = db.query(ClientSource).filter(ClientSource.nsv_client_id.in_(nsv_client_ids)).delete(synchronize_session=False)
    deleted_details = db.query(SourceDetail).filter(SourceDetail.nsv_client_id.in_(nsv_client_ids)).delete(synchronize_session=False)
    deleted_enrollments = db.query(Enrollment).filter(Enrollment.nsv_client_id.in_(nsv_client_ids)).delete(synchronize_session=False)
    deleted_aliases = db.query(ClientAlias).filter(ClientAlias.nsv_client_id.in_(nsv_client_ids)).delete(synchronize_session=False)
    deleted_reviews = db.query(PotentialMatch).filter(PotentialMatch.possible_nsv_client_id.in_(nsv_client_ids)).delete(synchronize_session=False)
    deleted_clients = db.query(Client).filter(Client.nsv_client_id.in_(nsv_client_ids)).delete(synchronize_session=False)

    return {
        "deleted_clients": deleted_clients,
        "deleted_sources": deleted_sources,
        "deleted_details": deleted_details,
        "deleted_enrollments": deleted_enrollments,
        "deleted_aliases": deleted_aliases,
        "deleted_reviews": deleted_reviews,
    }


def merge_client_records(db: Session, keep_id: str, merge_id: str):
    """Merge one duplicate client profile into the profile the user wants to keep."""

    keep_id = keep_id.strip()
    merge_id = merge_id.strip()
    if not keep_id or not merge_id:
        raise HTTPException(status_code=400, detail="Both NSV IDs are required.")
    if keep_id == merge_id:
        raise HTTPException(status_code=400, detail="Choose two different client profiles to merge.")

    keep_client = db.query(Client).filter(Client.nsv_client_id == keep_id).first()
    merge_client = db.query(Client).filter(Client.nsv_client_id == merge_id).first()
    if not keep_client:
        raise HTTPException(status_code=404, detail=f"Keep profile {keep_id} was not found.")
    if not merge_client:
        raise HTTPException(status_code=404, detail=f"Duplicate profile {merge_id} was not found.")

    # Preserve the duplicate profile's name as an alias so future imports can
    # still match that spelling after the duplicate master row is removed.
    add_client_alias(
        db=db,
        client=keep_client,
        first_name=merge_client.first_name,
        last_name=merge_client.last_name,
        dob=merge_client.date_of_birth,
        source_system="Manual Merge",
        original_file="Admin merge",
        confidence_score=1.0,
    )

    # Fill missing kept-profile fields from the duplicate profile, but do not
    # overwrite values that are already present on the kept profile.
    for field in ["date_of_birth", "hmis_id", "ecw_id", "gender", "race", "ethnicity", "veteran_status"]:
        if not getattr(keep_client, field) and getattr(merge_client, field):
            setattr(keep_client, field, getattr(merge_client, field))

    moved_sources = db.query(ClientSource).filter(ClientSource.nsv_client_id == merge_id).update(
        {ClientSource.nsv_client_id: keep_id},
        synchronize_session=False,
    )
    moved_details = db.query(SourceDetail).filter(SourceDetail.nsv_client_id == merge_id).update(
        {SourceDetail.nsv_client_id: keep_id},
        synchronize_session=False,
    )
    moved_enrollments = db.query(Enrollment).filter(Enrollment.nsv_client_id == merge_id).update(
        {Enrollment.nsv_client_id: keep_id},
        synchronize_session=False,
    )
    moved_aliases = db.query(ClientAlias).filter(ClientAlias.nsv_client_id == merge_id).update(
        {ClientAlias.nsv_client_id: keep_id},
        synchronize_session=False,
    )
    updated_reviews = db.query(PotentialMatch).filter(PotentialMatch.possible_nsv_client_id == merge_id).update(
        {PotentialMatch.possible_nsv_client_id: keep_id},
        synchronize_session=False,
    )

    db.delete(merge_client)
    db.commit()

    return {
        "status": "merged",
        "kept_nsv_client_id": keep_id,
        "merged_nsv_client_id": merge_id,
        "moved_sources": moved_sources,
        "moved_details": moved_details,
        "moved_enrollments": moved_enrollments,
        "moved_aliases": moved_aliases,
        "updated_reviews": updated_reviews,
    }


@app.delete("/admin/clients")
def admin_delete_clients(request: DeleteClientsRequest, db: Session = Depends(get_db)):
    """Delete selected client profiles and their dependent imported records."""

    # The frontend sends NSV IDs from checked rows. De-duping here protects the
    # endpoint if the browser sends the same ID twice.
    nsv_client_ids = sorted({client_id.strip() for client_id in request.nsv_client_ids if client_id.strip()})
    if not nsv_client_ids:
        raise HTTPException(status_code=400, detail="Select at least one client to delete.")

    result = delete_client_records(db, nsv_client_ids)
    db.commit()

    return {
        "requested_clients": len(nsv_client_ids),
        **result,
    }


@app.post("/admin/clients/merge")
def admin_merge_clients(request: MergeClientsRequest, db: Session = Depends(get_db)):
    """Merge one duplicate profile into another kept profile."""

    return merge_client_records(
        db=db,
        keep_id=request.keep_nsv_client_id,
        merge_id=request.merge_nsv_client_id,
    )


@app.delete("/admin/database")
def admin_clear_database(request: ClearDatabaseRequest, db: Session = Depends(get_db)):
    """Clear all local demo/import data tables for a clean testing slate."""

    # The slider lives in the browser, but the backend still requires a literal
    # confirmation string so an accidental request cannot wipe the database.
    if request.confirmation != "DELETE ALL":
        raise HTTPException(status_code=400, detail='Confirmation must be exactly "DELETE ALL".')

    # Clear dependent/import tables before clients and programs because they are
    # the records that point back to master client/program rows.
    deleted = {
        "source_details": db.query(SourceDetail).delete(synchronize_session=False),
        "client_sources": db.query(ClientSource).delete(synchronize_session=False),
        "enrollments": db.query(Enrollment).delete(synchronize_session=False),
        "client_aliases": db.query(ClientAlias).delete(synchronize_session=False),
        "potential_matches": db.query(PotentialMatch).delete(synchronize_session=False),
        "imports": db.query(ImportLog).delete(synchronize_session=False),
        "program_metrics": db.query(ProgramMetric).delete(synchronize_session=False),
        "clients": db.query(Client).delete(synchronize_session=False),
        "programs": db.query(Program).delete(synchronize_session=False),
    }
    db.commit()

    return {"status": "cleared", "deleted": deleted}


def attach_review_row_to_client(db: Session, review: PotentialMatch, client: Client, match_method: str):
    """Move a reviewed source row into the normal imported-record tables."""

    raw_row = json.loads(review.raw_data_json or "{}")
    row = pd.Series(raw_row)
    first_name, last_name, dob, hmis_id, ecw_id = extract_identity(row)

    update_client_from_row(client, hmis_id, ecw_id, dob, row)
    add_client_alias(
        db=db,
        client=client,
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        source_system=review.source_system,
        original_file=review.original_file,
        created_from_review_id=review.review_id,
        confidence_score=review.confidence_score,
    )
    source_client_id = hmis_id or ecw_id

    db.add(ClientSource(
        nsv_client_id=client.nsv_client_id,
        source_system=review.source_system,
        source_client_id=source_client_id,
        original_file=review.original_file,
        raw_data_json=review.raw_data_json,
        match_method=match_method,
        confidence_score=review.confidence_score,
    ))

    add_source_details(db, row, client, review.source_system, review.program_name, Path(review.original_file or "review_row"))
    if not dob and not client.date_of_birth:
        add_identity_enrichment_detail(
            db=db,
            client=client,
            source_system=review.source_system,
            program_name=review.program_name,
            path=Path(review.original_file or "review_row"),
            reason="Needs DOB from another import",
        )

    program = get_or_create_program(db, review.program_name or "Unknown Program", review.source_system)
    entry_date, exit_date = extract_enrollment_dates(row)
    status = extract_status(row)
    db.add(Enrollment(
        nsv_client_id=client.nsv_client_id,
        program_id=program.program_id,
        entry_date=entry_date,
        exit_date=exit_date,
        status=status,
    ))

    review.possible_nsv_client_id = client.nsv_client_id
    review.status = "Resolved"
    review.review_reason = f"Resolved - {match_method}"


@app.post("/upload/preview")
async def upload_preview(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Save an uploaded CSV/Excel file and return preview rows/columns."""

    extension = Path(file.filename).suffix.lower()
    if extension not in [".csv", ".xlsx", ".xls"]:
        raise HTTPException(status_code=400, detail="Only CSV and Excel files are supported.")

    # Store the raw upload under a UUID so the later import request can find it.
    upload_id = str(uuid.uuid4())
    saved_path = UPLOAD_DIR / f"{upload_id}{extension}"

    content = await file.read()
    saved_path.write_bytes(content)

    preview = preview_file(str(saved_path), max_rows=10)
    route_decision = classify_upload_dataframe(load_file(saved_path, allow_metrics=True))
    preview["file_type"] = route_decision.route.value
    preview["import_route_reason"] = route_decision.reason
    previous_imports = (
        db.query(ImportLog)
        .filter(ImportLog.file_name == file.filename)
        .order_by(ImportLog.imported_at.desc(), ImportLog.import_id.desc())
        .limit(5)
        .all()
    )
    preview["upload_id"] = upload_id
    preview["original_file_name"] = file.filename
    preview["duplicate_file"] = len(previous_imports) > 0
    preview["previous_imports"] = [
        {
            "import_id": item.import_id,
            "source_system": item.source_system,
            "program_name": item.program_name,
            "rows_processed": item.rows_processed,
            "rows_created": item.rows_created,
            "rows_matched": item.rows_matched,
            "rows_review": item.rows_review,
            "rows_failed": item.rows_failed,
            "imported_at": str(item.imported_at) if item.imported_at else None,
        }
        for item in previous_imports
    ]
    return preview


@app.post("/upload/import")
def upload_import(request: ImportRequest, db: Session = Depends(get_db)):
    """Import a previously previewed client file using the selected mapping."""

    matching_files = list(UPLOAD_DIR.glob(f"{request.upload_id}.*"))

    if not matching_files:
        raise HTTPException(status_code=404, detail="Uploaded file not found.")

    file_path = matching_files[0]

    # Classify the upload once, then send it to the correct ingestion path.
    # This keeps client identity imports separate from metrics/report imports.
    preview_df = normalize_import_columns(load_file(file_path, allow_metrics=True), request.column_mapping)
    route_decision = classify_upload_dataframe(preview_df)

    if route_decision.route == ImportRoute.METRICS:
        metrics_result = import_metrics_file(
            db=db,
            file_path=str(file_path),
            original_file_name=request.original_file_name,
        )
        return {
            **metrics_result,
            "import_mode": "metrics",
            "import_route_reason": route_decision.reason,
            "source_system": request.source_system,
            "program_name": request.program_name,
            "rows_created": 0,
            "rows_matched": 0,
            "rows_review": 0,
            "rows_failed": 0,
            "failed_rows": [],
        }

    result = import_file(
        db=db,
        file_path=str(file_path),
        source_system=request.source_system,
        program_name=request.program_name,
        original_file_name=request.original_file_name,
        column_mapping=request.column_mapping,
    )

    return result


@app.post("/metrics/import")
def metrics_import(request: ImportRequest, db: Session = Depends(get_db)):
    """Import a previously previewed metrics/planning sheet."""

    matching_files = list(UPLOAD_DIR.glob(f"{request.upload_id}.*"))

    if not matching_files:
        raise HTTPException(status_code=404, detail="Uploaded file not found.")

    try:
        return import_metrics_file(
            db=db,
            file_path=str(matching_files[0]),
            original_file_name=request.original_file_name,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.get("/metrics/summary")
def metrics_summary(db: Session = Depends(get_db)):
    """Return a compact summary of imported metrics rows."""

    summary = get_metrics_summary(db)
    summary["client_needs_summary"] = build_client_needs_summary(db)
    return summary


def serialize_review(review: PotentialMatch, possible_client: Optional[Client] = None):
    """Convert one review row into the shape used by the Review Queue UI."""

    recommended_action = "accept_match" if review.possible_nsv_client_id else "create_partial_profile"
    imported_name = f"{review.suggested_first_name or ''} {review.suggested_last_name or ''}".strip() or "Unknown"
    if possible_client:
        possible_name = f"{possible_client.first_name or ''} {possible_client.last_name or ''}".strip()
        group_name = f"Possible existing profile: {possible_name or possible_client.nsv_client_id}"
        group_key = f"profile-{possible_client.nsv_client_id}"
    else:
        group_name = f"Possible new profile: {imported_name}"
        group_key = normalize_for_match(imported_name) or f"review-{review.review_id}"
    return {
        "review_id": review.review_id,
        "source_system": review.source_system,
        "program_name": review.program_name,
        "duplicate_group_key": group_key,
        "duplicate_group_label": group_name,
        "possible_nsv_client_id": review.possible_nsv_client_id,
        "possible_client": ClientOut.model_validate(possible_client).model_dump() if possible_client else None,
        "suggested_first_name": review.suggested_first_name,
        "suggested_last_name": review.suggested_last_name,
        "suggested_dob": str(review.suggested_dob) if review.suggested_dob else None,
        "confidence_score": review.confidence_score,
        "review_reason": review.review_reason,
        "recommended_action": recommended_action,
        "status": review.status,
    }


@app.get("/reviews")
def get_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Return paginated records that need manual matching review."""

    query = db.query(PotentialMatch).filter(PotentialMatch.status == "Needs Review")
    total = query.count()
    reviews = (
        query
        .order_by(
            PotentialMatch.suggested_last_name.asc(),
            PotentialMatch.suggested_first_name.asc(),
            PotentialMatch.created_at.desc(),
            PotentialMatch.review_id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    possible_ids = [review.possible_nsv_client_id for review in reviews if review.possible_nsv_client_id]
    possible_clients = {}
    if possible_ids:
        possible_clients = {
            client.nsv_client_id: client
            for client in db.query(Client).filter(Client.nsv_client_id.in_(possible_ids)).all()
        }

    return {
        "items": [
            serialize_review(review, possible_clients.get(review.possible_nsv_client_id))
            for review in reviews
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 0,
    }


@app.post("/reviews/{review_id}/action")
def apply_review_action(review_id: int, request: ReviewActionRequest, db: Session = Depends(get_db)):
    """Resolve one review row by accepting, creating, or dismissing it."""

    review = db.query(PotentialMatch).filter(PotentialMatch.review_id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review row not found.")
    if review.status != "Needs Review":
        raise HTTPException(status_code=400, detail="Review row has already been resolved.")

    action = request.action
    if action == "dismiss":
        review.status = "Dismissed"
        review.review_reason = "Dismissed by reviewer"
        db.commit()
        return {"status": "dismissed", "review_id": review_id}

    if action == "accept_match":
        target_id = request.target_nsv_client_id or review.possible_nsv_client_id
        if not target_id:
            raise HTTPException(status_code=400, detail="No target client was provided for this match.")
        client = db.query(Client).filter(Client.nsv_client_id == target_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Target client was not found.")
        attach_review_row_to_client(db, review, client, "Manual review match")
        db.commit()
        return {"status": "resolved", "review_id": review_id, "nsv_client_id": client.nsv_client_id}

    if action == "create_partial_profile":
        row = pd.Series(json.loads(review.raw_data_json or "{}"))
        first_name, last_name, dob, hmis_id, ecw_id = extract_identity(row)
        if not first_name or not last_name:
            raise HTTPException(status_code=400, detail="Cannot create a profile without first and last name.")
        client = create_client(db, first_name, last_name, dob, hmis_id, ecw_id, row)
        attach_review_row_to_client(db, review, client, "Manual review created partial profile")
        db.commit()
        return {"status": "created", "review_id": review_id, "nsv_client_id": client.nsv_client_id}

    raise HTTPException(status_code=400, detail="Unsupported review action.")


@app.post("/reviews/apply-recommended")
def apply_recommended_reviews(request: BulkReviewActionRequest, db: Session = Depends(get_db)):
    """Apply recommended actions for selected review queue rows."""

    resolved = []
    failed = []
    review_ids = sorted(set(request.review_ids))

    for review_id in review_ids:
        review = db.query(PotentialMatch).filter(PotentialMatch.review_id == review_id).first()
        if not review or review.status != "Needs Review":
            failed.append({"review_id": review_id, "reason": "Review row was not found or already resolved."})
            continue

        try:
            if review.possible_nsv_client_id:
                client = db.query(Client).filter(Client.nsv_client_id == review.possible_nsv_client_id).first()
                if not client:
                    raise ValueError("Suggested client was not found.")
                attach_review_row_to_client(db, review, client, "Bulk recommended match")
                resolved.append({"review_id": review_id, "action": "matched", "nsv_client_id": client.nsv_client_id})
            else:
                row = pd.Series(json.loads(review.raw_data_json or "{}"))
                first_name, last_name, dob, hmis_id, ecw_id = extract_identity(row)
                if not first_name or not last_name:
                    raise ValueError("Missing first or last name.")
                client = create_client(db, first_name, last_name, dob, hmis_id, ecw_id, row)
                attach_review_row_to_client(db, review, client, "Bulk recommended partial profile")
                resolved.append({"review_id": review_id, "action": "created", "nsv_client_id": client.nsv_client_id})
        except Exception as error:
            db.rollback()
            failed.append({"review_id": review_id, "reason": str(error)})

    db.commit()
    return {"resolved": resolved, "failed": failed}
