"""SQLAlchemy table definitions for the NSV Client Data Platform.

Each class in this file maps to one database table. These models describe how
client identities, source records, program enrollments, import logs, review
items, and planning metrics are stored.
"""

from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base


class Client(Base):
    """Master client record keyed by the generated NSV client ID."""

    __tablename__ = "clients"

    # Core identity fields used for matching records across source systems.
    nsv_client_id = Column(String(30), primary_key=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    date_of_birth = Column(Date, nullable=True)

    # Optional external IDs from source systems.
    hmis_id = Column(String(100), nullable=True)
    ecw_id = Column(String(100), nullable=True)

    # Optional demographic fields copied from imports when available.
    gender = Column(String(100), nullable=True)
    race = Column(String(255), nullable=True)
    ethnicity = Column(String(255), nullable=True)
    veteran_status = Column(String(100), nullable=True)

    # Timestamps are managed by the database.
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships let the app navigate from one client to related records.
    sources = relationship("ClientSource", back_populates="client")
    enrollments = relationship("Enrollment", back_populates="client")
    source_details = relationship("SourceDetail", back_populates="client")


class Program(Base):
    """NSV program list used by enrollment records."""

    __tablename__ = "programs"

    program_id = Column(Integer, primary_key=True, autoincrement=True)
    program_name = Column(String(255), nullable=False, unique=True)
    source_system = Column(String(100), nullable=False)

    enrollments = relationship("Enrollment", back_populates="program")


class ClientSource(Base):
    """One imported source row attached to a client."""

    __tablename__ = "client_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nsv_client_id = Column(String(30), ForeignKey("clients.nsv_client_id"), nullable=True)
    source_system = Column(String(100), nullable=False)
    source_client_id = Column(String(100), nullable=True)
    original_file = Column(String(255), nullable=True)

    # raw_data_json preserves the original row for audit/debugging.
    raw_data_json = Column(Text, nullable=True)

    # match_method and confidence_score explain how this row was connected.
    match_method = Column(String(100), nullable=True)
    confidence_score = Column(Float, nullable=True)
    imported_at = Column(DateTime(timezone=True), server_default=func.now())

    client = relationship("Client", back_populates="sources")


class SourceDetail(Base):
    """Normalized extra fields captured from source rows."""

    __tablename__ = "source_details"

    detail_id = Column(Integer, primary_key=True, autoincrement=True)
    nsv_client_id = Column(String(30), ForeignKey("clients.nsv_client_id"), nullable=False)
    source_system = Column(String(100), nullable=False)
    program_name = Column(String(255), nullable=True)
    detail_type = Column(String(100), nullable=True)
    field_name = Column(String(255), nullable=False)
    field_value = Column(Text, nullable=True)
    original_file = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    client = relationship("Client", back_populates="source_details")


class Enrollment(Base):
    """A client's participation record in a program."""

    __tablename__ = "enrollments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nsv_client_id = Column(String(30), ForeignKey("clients.nsv_client_id"), nullable=False)
    program_id = Column(Integer, ForeignKey("programs.program_id"), nullable=False)

    entry_date = Column(Date, nullable=True)
    exit_date = Column(Date, nullable=True)
    status = Column(String(50), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    client = relationship("Client", back_populates="enrollments")
    program = relationship("Program", back_populates="enrollments")


class PotentialMatch(Base):
    """Rows that need human review before being matched to a client."""

    __tablename__ = "potential_matches"

    review_id = Column(Integer, primary_key=True, autoincrement=True)
    source_system = Column(String(100), nullable=False)
    program_name = Column(String(255), nullable=True)
    original_file = Column(String(255), nullable=True)

    possible_nsv_client_id = Column(String(30), nullable=True)
    suggested_first_name = Column(String(100), nullable=True)
    suggested_last_name = Column(String(100), nullable=True)
    suggested_dob = Column(Date, nullable=True)

    confidence_score = Column(Float, nullable=False)
    review_reason = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="Needs Review")
    raw_data_json = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ImportLog(Base):
    """One summary row for each completed client import."""

    __tablename__ = "imports"

    import_id = Column(Integer, primary_key=True, autoincrement=True)
    file_name = Column(String(255), nullable=False)
    source_system = Column(String(100), nullable=False)
    program_name = Column(String(255), nullable=True)
    rows_processed = Column(Integer, default=0)
    rows_created = Column(Integer, default=0)
    rows_matched = Column(Integer, default=0)
    rows_review = Column(Integer, default=0)
    rows_failed = Column(Integer, default=0)
    imported_at = Column(DateTime(timezone=True), server_default=func.now())


class ProgramMetric(Base):
    """Non-client program metrics/planning sheet rows."""

    __tablename__ = "program_metrics"

    metric_id = Column(Integer, primary_key=True, autoincrement=True)
    program = Column(String(255), nullable=True)
    target = Column(Text, nullable=True)
    metric = Column(Text, nullable=True)
    method = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    sort_order = Column(String(50), nullable=True)
    month_values_json = Column(Text, nullable=True)
    original_file = Column(String(255), nullable=True)
    imported_at = Column(DateTime(timezone=True), server_default=func.now())
