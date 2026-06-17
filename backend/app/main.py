import os
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Client, Program, ClientSource, SourceDetail, Enrollment, ImportLog, PotentialMatch
from .schemas import ClientOut
from .importer import preview_file, import_file

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="NSV Client Data Platform")


class ImportRequest(BaseModel):
    upload_id: str
    source_system: str
    program_name: str
    column_mapping: Dict[str, str] = {}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def root():
    return {"message": "NSV Client Data Platform API is running"}

@app.get("/upload-page")
def upload_page():
    project_root = Path(__file__).resolve().parents[2]
    return FileResponse(project_root / "frontend" / "upload.html")


@app.get("/clients", response_model=list[ClientOut])
def get_clients(db: Session = Depends(get_db)):
    return db.query(Client).limit(100).all()


@app.get("/clients/{nsv_client_id}", response_model=ClientOut)
def get_client(nsv_client_id: str, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.nsv_client_id == nsv_client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    return client


@app.get("/clients/{nsv_client_id}/programs")
def get_client_programs(nsv_client_id: str, db: Session = Depends(get_db)):
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
    return {
        "clients": db.query(Client).count(),
        "programs": db.query(Program).count(),
        "imports": db.query(ImportLog).count(),
        "reviews": db.query(PotentialMatch).filter(PotentialMatch.status == "Needs Review").count(),
    }


@app.post("/upload/preview")
async def upload_preview(file: UploadFile = File(...)):
    extension = Path(file.filename).suffix.lower()
    if extension not in [".csv", ".xlsx", ".xls"]:
        raise HTTPException(status_code=400, detail="Only CSV and Excel files are supported.")

    upload_id = str(uuid.uuid4())
    saved_path = UPLOAD_DIR / f"{upload_id}{extension}"

    content = await file.read()
    saved_path.write_bytes(content)

    preview = preview_file(str(saved_path), max_rows=10)
    preview["upload_id"] = upload_id
    return preview


@app.post("/upload/import")
def upload_import(request: ImportRequest, db: Session = Depends(get_db)):
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


@app.get("/reviews")
def get_reviews(db: Session = Depends(get_db)):
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
