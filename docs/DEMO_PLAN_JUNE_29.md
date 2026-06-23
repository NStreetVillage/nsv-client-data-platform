# Demo Plan - Monday, June 29

## Demo Goal

Show that the NSV Data Platform can:

1. Upload sample data
2. Create or match NSV Client IDs
3. Search for a client
4. Show that one client has interacted with more than one NSV program

## Demo Scope

This is not the full production platform yet. This is a Phase 1 proof-of-concept showing the foundation.

## Demo Flow

Use a pre-tested local database or pre-tested import files for the live demo. Avoid discovering a new source-file issue live.

### Step 1 - Upload sample data

Upload one or more files from existing sources such as HMIS, HTH, JotForm, or eCW.

Suggested demo sources:

- HTH housing/client file
- BWC/JotForm sign-in file
- HMIS demographics file
- eCW encounter/patient download

### Step 2 - Create / match clients

The system should attempt to match clients using HMIS ID, eCW ID, first name + last name + DOB, or the review queue.

Talk track:

- The platform does not replace source systems.
- It imports exports from those systems.
- It assigns or reuses one internal NSV Client ID.
- Ambiguous records are separated instead of being forced into a bad match.

### Step 3 - Search for a client

Use the Client Master Records tab to search for a client by name or NSV ID.

Show:

- Paginated client master list
- Search by name, DOB, HMIS ID, eCW ID, or NSV ID
- Missing fields displayed as `N/A`
- Independently scrollable client table

### Step 4 - Show cross-program interaction

Show a client record with program history, such as Bethany Women's Center, Housing, MMVC, or Behavioral Health Services.

Show:

- Slide-in profile panel
- NSV Program card
- Identity card
- Services requested
- Client details
- Source summary
- Repeated source/program rows summarized instead of displayed as a long raw table

### Step 5 - Show program metrics

Upload or show an already-imported H&W/FY planning metrics sheet.

Explain:

- Metrics/planning sheets are not client files.
- They are stored separately in `program_metrics`.
- This keeps the client master clean while preserving program-level targets and measures.
- The long-term goal is to compare person-level activity against program-level metrics.

## Demo Success

The demo is successful if we can show:

- A file was uploaded
- Client records were created or matched
- A client can be searched
- A client's program interactions can be viewed
- Records needing manual review are separated into a review queue
- A metrics/planning sheet can be stored without creating fake clients
- The prototype has a clear path toward Tableau/reporting

## Out of Scope for This Demo

- Tableau dashboards
- AI insights
- Full production security implementation
- Production deployment
- Automated SharePoint imports
- Replacing HMIS, HTH, eCW, or JotForm
- Manual duplicate merge workflow
- Manual review approval workflow
- Production-grade role-based permissions

## Current Phase 1 Status

Working:

- FastAPI backend
- Single-page frontend
- CSV/Excel upload and preview
- Column mapping
- Client imports
- Client matching by source IDs, name, DOB, and partial identity
- Searchable/paginated client master records
- Card-based slide-in client profile
- Review queue endpoint/display
- Metrics/planning sheet storage
- SQLite local demo database

Known limitations:

- Review queue is display-only.
- Duplicate merge tools are not built yet.
- Matching still needs human review for ambiguous/common-name cases.
- Metrics are stored but not yet deeply connected to client/program reporting views.
- Authentication and permissions are not implemented.

## Demo Setup Checklist

Before presenting:

- Start from the project root.
- Activate `.venv`.
- Run `python scripts\create_tables.py` from the `backend` folder.
- Start `uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload`.
- Open `http://127.0.0.1:8001/upload-page`.
- Confirm `/stats` loads counts.
- Search for one known client.
- Open that client's profile.
- Confirm a metrics summary appears on the Overview tab.
- Keep source files ready, but avoid importing a brand-new untested file live.
