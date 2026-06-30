# Backend App Map

`app.main:app` is still the backend entrypoint for PyCharm and uvicorn.

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --reload
```

## Folders

- `core/`: shared infrastructure, including database setup and utility helpers.
- `data/`: database models and API response schemas.
- `imports/`: spreadsheet classification, client import, matching, aliases, and review-row creation.
- `services/`: higher-level app behavior such as admin cleanup, metrics, service rules, and client profile snapshots.

## Main Flow

`main.py` receives the request, then calls the focused modules:

```text
main.py
  -> imports/routing.py
  -> imports/importer.py
  -> imports/matching.py
  -> services/metrics.py
  -> services/client_snapshot.py
  -> services/admin_service.py
```

This keeps the launch file stable while making the code easier to browse.
