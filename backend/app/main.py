import os
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Client, Program, ImportLog, PotentialMatch
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
