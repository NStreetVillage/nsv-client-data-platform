"""FastAPI application for the NSV Client Data Platform.

This file defines the HTTP API used by frontend/upload.html. It serves the
single-page upload interface, receives uploaded spreadsheets, exposes client
search/profile endpoints, returns dashboard stats, and manages metrics/review
data.
"""

import os
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Client, Program, ClientSource, SourceDetail, Enrollment, ImportLog, PotentialMatch, ProgramMetric
from .schemas import ClientOut
from .importer import preview_file, import_file
from .metrics import get_metrics_summary, import_metrics_file
from .utils import parse_date_candidates

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

# Main FastAPI app object. Uvicorn imports this when the backend starts.
app = FastAPI(title="NSV Client Data Platform")


class ImportRequest(BaseModel):
    """Request body for importing a file that was already uploaded for preview."""

    upload_id: str
    source_system: str = ""
    program_name: str = ""
    column_mapping: Dict[str, str] = {}


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
    page_size: int = Query(default=50, ge=10, le=100),
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

    # Newer clients appear first so recent imports are easy to inspect.
    clients = (
        query
        .order_by(Client.created_at.desc(), Client.nsv_client_id.asc())
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


@app.post("/upload/preview")
async def upload_preview(file: UploadFile = File(...)):
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
    preview["upload_id"] = upload_id
    return preview


@app.post("/upload/import")
def upload_import(request: ImportRequest, db: Session = Depends(get_db)):
    """Import a previously previewed client file using the selected mapping."""

    matching_files = list(UPLOAD_DIR.glob(f"{request.upload_id}.*"))

    if not matching_files:
        raise HTTPException(status_code=404, detail="Uploaded file not found.")

    file_path = matching_files[0]

    result = import_file(
        db=db,
        file_path=str(file_path),
        source_system=request.source_system,
        program_name=request.program_name,
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
        return import_metrics_file(db=db, file_path=str(matching_files[0]))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.get("/metrics/summary")
def metrics_summary(db: Session = Depends(get_db)):
    """Return a compact summary of imported metrics rows."""

    return get_metrics_summary(db)


@app.get("/reviews")
def get_reviews(db: Session = Depends(get_db)):
    """Return the newest records that need manual matching review."""

    reviews = (
        db.query(PotentialMatch)
        .filter(PotentialMatch.status == "Needs Review")
        .order_by(PotentialMatch.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "review_id": r.review_id,
            "source_system": r.source_system,
            "program_name": r.program_name,
            "possible_nsv_client_id": r.possible_nsv_client_id,
            "suggested_first_name": r.suggested_first_name,
            "suggested_last_name": r.suggested_last_name,
            "suggested_dob": str(r.suggested_dob) if r.suggested_dob else None,
            "confidence_score": r.confidence_score,
            "review_reason": r.review_reason,
            "status": r.status,
        }
        for r in reviews
    ]
