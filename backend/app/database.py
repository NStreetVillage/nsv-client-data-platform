"""Database connection setup for the NSV Client Data Platform.

This module is imported by the API, import scripts, and SQLAlchemy models.
It reads the database URL from environment variables, creates the SQLAlchemy
engine, and exposes the shared session factory and model base class.
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Load values from backend/.env so local development can configure DATABASE_URL.
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing. Create a .env file first.")

# PostgreSQL can hang for a while when unreachable; this keeps failures fast.
connect_args = {}
if DATABASE_URL.startswith("postgresql"):
    connect_args["connect_timeout"] = 5

# The engine owns the database connection pool.
engine = create_engine(DATABASE_URL, connect_args=connect_args)

# SessionLocal creates a new database session for each request or script run.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# All ORM model classes inherit from Base so create_all can discover them.
Base = declarative_base()
