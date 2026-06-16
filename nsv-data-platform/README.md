# NSV Data Platform

Internal data platform for consolidating NSV reporting data across HMIS, HTH, eCW, JotForm, and future data sources.

## Current Phase

Phase 1: Data foundation and June 29 demo.

## Demo Deadline

Demo prepared by Monday, June 29.

The demo should show:

1. Upload some data
2. Search for a client
3. Confirm that a client has interacted with more than one NSV program

## Project Structure

```text
nsv-data-platform/
├── backend/        # FastAPI backend, import engine, matching logic
├── frontend/       # Upload page and prototype UI
├── database/       # Schema, migrations, database notes
├── docs/           # Roadmap, demo plan, project documentation
├── sample-data/    # Redacted test data only
├── deployment/     # Server/deployment notes
├── reports/        # Report logic and definitions
├── tableau/        # Future Tableau documentation/assets
├── ai/             # Future AI feature planning
└── tests/          # Future tests
```

## Phase 1 Features

- CSV/Excel upload
- File preview
- Column mapping
- NSV Client ID creation
- Client matching by HMIS ID, eCW ID, or name + DOB
- Review queue for uncertain matches
- Client master records
- Program/source tracking

## Run Locally

From the `backend/` folder:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python scripts/create_tables.py
uvicorn app.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000/upload-page
```

## Important Data Safety Note

Do not commit real client data, credentials, database passwords, or API keys to GitHub.

Use company-owned source control, company-approved storage, and environment variables for secrets.
