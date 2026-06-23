"""Create all database tables defined by the SQLAlchemy models.

Run this script when setting up a new local database or after adding new model
tables during prototype development.
"""

from pathlib import Path
import sys

# Allow this script to import the backend app package when run from the repo.
backend_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend_dir))

from app.database import Base, engine
from app import models

if __name__ == "__main__":
    # Importing app.models registers the model classes with Base.metadata.
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully.")
