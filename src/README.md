# Source Map

This folder explains the intended source-code organization for the NSV Client
Data Platform.

The runnable app still lives in `backend/app` and `frontend/upload.html` so the
current local command keeps working:

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --reload
```

Do not move the live FastAPI package or frontend file into this folder until the
server environment and deployment process are ready. The app has several
important local assumptions today: PyCharm run configuration, upload paths,
SQLite database location, and the `app.main:app` import path.

## Current Live Code

- `backend/app/main.py`: FastAPI route layer and stable uvicorn entrypoint.
- `backend/app/README.md`: quick map for the reorganized backend folders.
- `backend/app/core/database.py`: SQLAlchemy engine/session setup and `.env`
  loading.
- `backend/app/core/utils.py`: shared date, name, ID, and normalization helpers.
- `backend/app/data/models.py`: SQLAlchemy database tables.
- `backend/app/data/schemas.py`: Pydantic response shapes returned by API routes.
- `backend/app/imports/importer.py`: spreadsheet loading, normalization, import
  writes, source detail capture, review-row creation, and import logging.
- `backend/app/imports/matching.py`: client identity matching, aliases, typo
  scoring, and duplicate-candidate selection.
- `backend/app/imports/routing.py`: decides whether uploads are client imports,
  metrics, or enrichment-only reports.
- `backend/app/services/admin_service.py`: high-impact admin workflows such as
  profile deletion and manual profile merging.
- `backend/app/services/client_snapshot.py`: MyChart-style service snapshot and
  client needs summary.
- `backend/app/services/service_rules.py`: reusable rules that infer service
  needs from imported source details.
- `backend/app/services/metrics.py`: metrics/planning/occupancy report import
  and summary logic.
- `frontend/upload.html`: current single-page prototype UI.

## Refactor Direction

When the app moves to an in-house server, the safer long-term structure could
be:

```text
src/
  backend/
    routes/
    services/
    models/
    schemas/
  frontend/
    components/
    pages/
    styles/
  shared/
    docs/
```

For now, refactor by extracting focused backend modules while keeping the live
entry points stable.
