from datetime import date
from pydantic import BaseModel


class ClientOut(BaseModel):
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
        from_attributes = True
