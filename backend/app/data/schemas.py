"""Pydantic response schemas used by the FastAPI endpoints.

Schemas define the JSON shape returned to the browser. They sit between the
SQLAlchemy database models and the frontend so API responses stay predictable.
"""

from datetime import date
from pydantic import BaseModel


class ClientOut(BaseModel):
    """Public JSON representation of a client master record."""

    nsv_client_id: str
    first_name: str
    last_name: str
    date_of_birth: date | None = None
    hmis_id: str | None = None
    ecw_id: str | None = None
    gender: str | None = None
    race: str | None = None
    ethnicity: str | None = None
    veteran_status: str | None = None

    class Config:
        # Allows Pydantic to read values directly from SQLAlchemy model objects.
        from_attributes = True
