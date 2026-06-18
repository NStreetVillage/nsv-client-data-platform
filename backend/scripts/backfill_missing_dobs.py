from pathlib import Path
import json
import sys

backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.database import SessionLocal
from app.models import Client, ClientSource, SourceDetail
from app.utils import parse_date


DOB_FIELDS = [
    "date_of_birth",
    "Date of Birth",
    "Date of Birth.1",
    "DOB",
    "Patient DOB",
    "source_date_of_birth",
    "source_date_of_birth_alternate",
    "source_patient_dob",
]


def parse_from_raw_json(raw_data_json):
    if not raw_data_json:
        return None

    try:
        payload = json.loads(raw_data_json)
    except json.JSONDecodeError:
        return None

    for field in DOB_FIELDS:
        dob = parse_date(payload.get(field))
        if dob:
            return dob

    return None


def parse_from_source_details(details):
    for detail in details:
        if detail.field_name in DOB_FIELDS or "dob" in detail.field_name.lower() or "birth" in detail.field_name.lower():
            dob = parse_date(detail.field_value)
            if dob:
                return dob
    return None


def main():
    db = SessionLocal()
    updated = 0

    try:
        clients = {
            client.nsv_client_id: client
            for client in db.query(Client).filter(Client.date_of_birth.is_(None)).all()
        }
        if not clients:
            print("Backfilled DOB for 0 clients.")
            return

        found_dobs = {}
        for source in db.query(ClientSource).filter(ClientSource.nsv_client_id.in_(clients.keys())).all():
            if source.nsv_client_id in found_dobs:
                continue
            dob = parse_from_raw_json(source.raw_data_json)
            if dob:
                found_dobs[source.nsv_client_id] = dob

        remaining_ids = [client_id for client_id in clients if client_id not in found_dobs]
        if remaining_ids:
            detail_groups = {}
            for detail in db.query(SourceDetail).filter(SourceDetail.nsv_client_id.in_(remaining_ids)).all():
                detail_groups.setdefault(detail.nsv_client_id, []).append(detail)

            for client_id, details in detail_groups.items():
                dob = parse_from_source_details(details)
                if dob:
                    found_dobs[client_id] = dob

        for client_id, dob in found_dobs.items():
            clients[client_id].date_of_birth = dob
            updated += 1

        db.commit()
        print(f"Backfilled DOB for {updated} clients.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
