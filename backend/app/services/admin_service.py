"""Admin cleanup and merge workflows.

Routes in main.py call these helpers when a user deletes profiles, resets local
demo data, or manually merges duplicate master profiles. Keeping these
workflows here makes high-impact admin behavior easier to review and modify.
"""

from typing import List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.data.models import Client, ClientAlias, ClientSource, Enrollment, PotentialMatch, SourceDetail
from app.imports.importer import add_client_alias


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
