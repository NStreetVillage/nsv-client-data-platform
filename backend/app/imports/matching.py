"""Client identity matching rules for imported source rows.

This module answers one focused question: "Does this incoming row belong to an
existing NSV client profile, should it create a profile, or should a human
review it?" Keeping that logic here makes matching rules easier to tune without
digging through file parsing and database-write code in importer.py.
"""

from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.utils import normalize_for_match
from app.data.models import Client, ClientAlias


def find_by_hmis_id(db: Session, hmis_id: Optional[str]):
    """Find a client by HMIS ID when the source row provides one."""

    if not hmis_id:
        return None
    return db.query(Client).filter(Client.hmis_id == hmis_id).first()


def find_by_ecw_id(db: Session, ecw_id: Optional[str]):
    """Find a client by eCW ID when the source row provides one."""

    if not ecw_id:
        return None
    return db.query(Client).filter(Client.ecw_id == ecw_id).first()


def find_by_name_dob(db: Session, first_name: str, last_name: str, dob):
    """Find a client when first name, last name, and DOB all match."""

    if not first_name or not last_name or not dob:
        return None

    candidates = db.query(Client).filter(Client.date_of_birth == dob).all()
    nf = normalize_for_match(first_name)
    nl = normalize_for_match(last_name)

    for client in candidates:
        if normalize_for_match(client.first_name) == nf and normalize_for_match(client.last_name) == nl:
            return client

    return None


def find_by_saved_alias(db: Session, first_name: str, last_name: str, dob=None):
    """Find clients through reviewed aliases from previous manual matches."""

    if not first_name or not last_name:
        return []

    nf = normalize_for_match(first_name)
    nl = normalize_for_match(last_name)
    aliases = (
        db.query(ClientAlias)
        .filter(
            ClientAlias.alias_first_name.ilike(first_name),
            ClientAlias.alias_last_name.ilike(last_name),
        )
        .limit(10)
        .all()
    )

    matches = []
    for alias in aliases:
        if normalize_for_match(alias.alias_first_name) != nf:
            continue
        if normalize_for_match(alias.alias_last_name) != nl:
            continue
        if dob and alias.alias_dob and alias.alias_dob != dob:
            continue
        if alias.client:
            matches.append(alias.client)

    return matches


def find_name_only_candidates(db: Session, first_name: str, last_name: str):
    """Find exact normalized name matches when DOB is unavailable."""

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


def find_name_variant_candidates(db: Session, first_name: str, last_name: str, dob=None):
    """Find a unique first-name + last-initial/prefix match when DOB is missing."""

    if not first_name or not last_name:
        return []

    nf = normalize_for_match(first_name)
    nl = normalize_for_match(last_name)
    if not nf or not nl:
        return []

    candidates = db.query(Client).filter(Client.first_name.ilike(first_name)).limit(25).all()
    matches = []

    for client in candidates:
        client_first = normalize_for_match(client.first_name)
        client_last = normalize_for_match(client.last_name)
        if client_first != nf or not client_last:
            continue

        if dob and client.date_of_birth and client.date_of_birth != dob:
            continue

        incoming_is_initial = len(nl) == 1 and client_last.startswith(nl)
        existing_is_initial = len(client_last) == 1 and nl.startswith(client_last)
        short_prefix_match = min(len(nl), len(client_last)) >= 3 and (
            nl.startswith(client_last) or client_last.startswith(nl)
        )

        if incoming_is_initial or existing_is_initial or short_prefix_match:
            matches.append(client)

    return matches


def find_by_partial_identity(db: Session, first_name: str, last_name: str, dob):
    """Find weaker candidates when only part of the identity matches."""

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


def name_similarity(left: str, right: str) -> float:
    """Score two normalized name strings for typo/variant matching."""

    left_clean = normalize_for_match(left)
    right_clean = normalize_for_match(right)
    if not left_clean or not right_clean:
        return 0.0
    if left_clean == right_clean:
        return 1.0
    return SequenceMatcher(None, left_clean, right_clean).ratio()


def fuzzy_candidate_score(first_name: str, last_name: str, dob, client: Client) -> float:
    """Return a conservative score for likely duplicate client profiles."""

    incoming_first = normalize_for_match(first_name)
    incoming_last = normalize_for_match(last_name)
    client_first = normalize_for_match(client.first_name)
    client_last = normalize_for_match(client.last_name)
    if not incoming_first or not incoming_last or not client_first or not client_last:
        return 0.0

    first_score = name_similarity(incoming_first, client_first)
    last_score = name_similarity(incoming_last, client_last)
    full_score = name_similarity(f"{incoming_first} {incoming_last}", f"{client_first} {client_last}")
    score = (first_score * 0.35) + (last_score * 0.45) + (full_score * 0.20)

    if dob and client.date_of_birth:
        score += 0.12 if dob == client.date_of_birth else -0.20
    elif dob or client.date_of_birth:
        score -= 0.03

    # First-initial + similar last name is common in abbreviated/dirty exports.
    if incoming_first[:1] == client_first[:1] and last_score >= 0.84:
        score = max(score, 0.78)

    return max(0.0, min(score, 1.0))


def is_strong_fuzzy_duplicate(first_name: str, last_name: str, dob, client: Client, score: float) -> bool:
    """Decide whether a fuzzy score is strong enough to send to review."""

    incoming_first = normalize_for_match(first_name)
    incoming_last = normalize_for_match(last_name)
    client_first = normalize_for_match(client.first_name)
    client_last = normalize_for_match(client.last_name)
    first_score = name_similarity(incoming_first, client_first)
    last_score = name_similarity(incoming_last, client_last)
    full_score = name_similarity(f"{incoming_first} {incoming_last}", f"{client_first} {client_last}")

    if dob and client.date_of_birth and dob == client.date_of_birth:
        return score >= 0.76 and (first_score >= 0.62 or last_score >= 0.88)

    # Without DOB, a shared or nearly shared last name is not enough. This keeps
    # unrelated people with the same surname out of the duplicate queue.
    if first_score < 0.78:
        return False
    if last_score < 0.84:
        return False
    return score >= 0.84 and full_score >= 0.84


def find_fuzzy_duplicate_candidates(db: Session, first_name: str, last_name: str, dob=None, limit: int = 5):
    """Find existing profiles with similar names that need human review."""

    nf = normalize_for_match(first_name)
    nl = normalize_for_match(last_name)
    if not nf or not nl:
        return []

    filters = []
    if dob:
        filters.append(Client.date_of_birth == dob)
    filters.append(Client.first_name.ilike(f"{first_name[:1]}%"))
    filters.append(Client.last_name.ilike(f"{last_name[:1]}%"))

    candidates = db.query(Client).filter(or_(*filters)).limit(250).all()
    scored = []
    for client in candidates:
        score = fuzzy_candidate_score(first_name, last_name, dob, client)
        if is_strong_fuzzy_duplicate(first_name, last_name, dob, client, score):
            scored.append((client, round(score, 2)))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def match_client(db: Session, first_name, last_name, dob, hmis_id, ecw_id):
    """
    Choose the safest import action for one row's identity.

    Return shape:
    - matched client or possible client
    - match method/reason
    - confidence score
    - action: matched, review, create, or failed
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

    alias_candidates = find_by_saved_alias(db, first_name, last_name, dob)
    if len(alias_candidates) == 1:
        return alias_candidates[0], "Reviewed alias", 0.92, "matched"
    if len(alias_candidates) > 1:
        return alias_candidates[0], "Multiple reviewed alias matches", 0.55, "review"

    name_candidates = find_name_only_candidates(db, first_name, last_name)
    if len(name_candidates) == 1:
        return name_candidates[0], "Name only", 0.75, "matched"

    name_variant_candidates = find_name_variant_candidates(db, first_name, last_name, dob)
    if len(name_variant_candidates) == 1:
        return name_variant_candidates[0], "Name variant", 0.68, "matched"

    partial_candidates = find_by_partial_identity(db, first_name, last_name, dob)
    if len(partial_candidates) == 1:
        return partial_candidates[0], "Partial identity", 0.70, "matched"

    if (hmis_id or ecw_id) and (not first_name or not last_name):
        return None, "ID-only row needs identity from another import", 0.40, "review"

    if not first_name or not last_name:
        return None, "Missing name", 0.00, "failed"

    if len(name_candidates) > 1:
        return name_candidates[0], "Multiple name matches", 0.50, "review"

    if len(name_variant_candidates) > 1:
        return name_variant_candidates[0], "Multiple name variant matches", 0.50, "review"

    if not dob:
        return None, "New partial client", 0.60, "create"

    return None, "New client", 0.90, "create"
