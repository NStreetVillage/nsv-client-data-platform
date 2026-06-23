# NSV Client Data Platform

Internal Phase 1 prototype for consolidating N Street Village client reporting data and program metrics across HMIS, HTH, eCW, JotForm, planning sheets, and future data sources.

The platform is not intended to replace those systems. HMIS, HTH, eCW, JotForm, and planning workbooks remain systems of record. This app acts as a reporting and analytics layer that imports exported files, matches clients across systems, assigns an internal NSV Client ID, stores program metrics, and supports analysis across both person-level activity and overall program performance.

## Current Phase

Phase 1: data foundation and June 29 demo.

The demo should prove:

1. Upload data from a source file.
2. Import or match client records.
3. Search for a client.
4. Show that one client has interacted with more than one NSV program.

The core business value is:

```text
One client
Many programs
Many source systems
Program metrics
Cross-layer analytics
```

The broader product direction is an all-in-one client processing and analytics application:

```text
Client records + program participation + source details + program metrics
        |
Clean SQL reporting layer
        |
Tableau dashboards, data quality review, and future AI insights
```

## Current Local Setup

Local development currently uses SQLite for speed and simplicity. PostgreSQL is still the preferred long-term database for production/shared reporting, but SQLite is easier for local demo testing.

The local environment file is:

```text
backend/.env
```

Current local demo settings:

```env
DATABASE_URL=sqlite:///./nsv_data_clean.db
UPLOAD_DIR=uploads
```

Do not commit `.env`, local database files, uploaded spreadsheets, or real client data.

## How To Run Locally

Use Windows PowerShell from the repository root.

### 1. Go to the project folder

```powershell
cd C:\Users\idbon\Desktop\NstreetVillage\Nstreetvillageproject\nsv-client-data-platform
```

### 2. Activate the virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
```

Your prompt should show `(.venv)` after activation.

If the virtual environment does not exist yet, create it and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

### 3. Move into the backend folder

```powershell
cd backend
```

### 4. Create or update database tables

Run this every time a new table/model is added, or whenever you reset the local database:

```powershell
python scripts\create_tables.py
```

This creates missing tables only. It does not erase existing data.

### 5. Optional: repair missing DOBs from existing imports

Run this only when existing imported clients have blank DOBs but the raw source rows contain DOB values:

```powershell
python scripts\backfill_missing_dobs.py
```

This only fills missing DOBs. It does not overwrite existing DOBs.

### 6. Start the FastAPI server

Recommended local demo command:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

Keep this PowerShell window open while using the app. To stop the server, press:

```text
Ctrl + C
```

### 7. Open the app in the browser

```text
http://127.0.0.1:8001/upload-page
```

Swagger/API docs:

```text
http://127.0.0.1:8001/docs
```

### If port 8001 is already busy

Use another port, such as `8002`:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload
```

Then open:

```text
http://127.0.0.1:8002/upload-page
```

### Quick restart checklist

When you close PowerShell and need to run the app again:

```powershell
cd C:\Users\idbon\Desktop\NstreetVillage\Nstreetvillageproject\nsv-client-data-platform
.\.venv\Scripts\Activate.ps1
cd backend
python scripts\create_tables.py
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

## Project Structure

```text
nsv-client-data-platform/
|-- backend/        FastAPI backend, import engine, matching logic
|-- frontend/       Single-page prototype UI
|-- database/       Database notes and future schema/migration docs
|-- docs/           Roadmap, demo plan, project documentation
|-- sample-data/    Redacted test data only
|-- deployment/     Server/deployment notes
|-- reports/        Future report logic and definitions
|-- tableau/        Future Tableau documentation/assets
|-- ai/             Future AI feature planning
|-- tests/          Future tests
```

## Application Flow

The current workflow is:

```text
User uploads CSV/XLSX
        |
Backend previews file
        |
App identifies client files vs metrics/planning files
        |
Client file: user maps columns
        |
Backend imports client rows
        |
App creates or matches clients
        |
App creates program enrollment records
        |
App stores source-specific details
        |
User searches clients and views profile details

Metrics/planning file:
        |
Backend stores program metrics
        |
Overview shows metric counts, programs covered, and recent metrics
```

## Backend

The backend is a FastAPI app.

Main app file:

```text
backend/app/main.py
```

Importer logic:

```text
backend/app/importer.py
```

Database models:

```text
backend/app/models.py
```

Database connection:

```text
backend/app/database.py
```

### Main API Routes

```text
GET  /
GET  /upload-page
GET  /clients
GET  /clients/{nsv_client_id}
GET  /clients/{nsv_client_id}/programs
GET  /clients/{nsv_client_id}/details
GET  /programs
GET  /stats
GET  /reviews
POST /upload/preview
POST /upload/import
```

### Import Preview

`POST /upload/preview` accepts a CSV or Excel file, stores it in the local upload folder, reads the first rows, and returns:

```text
upload_id
file_name
file_type
columns
first preview rows
row_count
```

The frontend uses this response to render the preview table and column mapping controls. `file_type` is currently either `client` or `metrics`.

### Import

`POST /upload/import` receives:

```text
upload_id
source_system
program_name
column_mapping
```

The backend then:

1. Finds the previously uploaded file.
2. Reads CSV or Excel data with pandas.
3. Applies user-selected column mappings.
4. Normalizes known source column names.
5. Extracts client identity fields.
6. Matches or creates a client.
7. Creates an enrollment/program record.
8. Stores source details.
9. Writes an import summary.

### Metrics Import

Metrics/planning files such as `H&W Data_FY27(Sheet1).csv` are not client files. They do not create clients, enrollments, or review queue records.

These files are handled separately:

```text
POST /metrics/import
GET  /metrics/summary
```

The app recognizes metrics files by columns such as:

```text
Program
Target
Metric
Method
Jul-27
Aug-27
...
```

The Overview page stores and displays these metrics so leadership can eventually compare:

```text
How many clients were served?
Which programs did they touch?
What targets or metrics were attached to those programs?
How do person-level records compare with overall program goals?
```

## Database Model

The current core tables are:

```text
clients
programs
enrollments
client_sources
source_details
potential_matches
imports
program_metrics
```

### clients

Stores the master client identity:

```text
nsv_client_id
first_name
last_name
date_of_birth
hmis_id
ecw_id
gender
race
ethnicity
veteran_status
```

### programs

Stores NSV programs and their source system:

```text
program_name
source_system
```

Examples:

```text
Capitol Vista / HTH
Bethany Women's Center / JotForm
Behavioral Health Services / eCW
Sharon's Place / HMIS
```

### enrollments

Connects clients to programs:

```text
nsv_client_id
program_id
entry_date
exit_date
status
```

This table is what supports the Phase 1 demo goal of showing one client across multiple programs.

### client_sources

Stores where an imported record came from:

```text
nsv_client_id
source_system
source_client_id
original_file
raw_data_json
match_method
confidence_score
```

### source_details

Stores source-specific fields that do not belong in the main `clients` table.

Examples:

```text
provider_name
funding_source
unit_availability
reason_for_exit
date_placed_in_psh
visit_status
department_name
snap_status
```

This keeps the platform flexible without creating a giant client table for every possible source-system field.

### potential_matches

Stores records needing manual review.

The importer is now less strict than the first version. Missing DOB no longer automatically sends a row to review when there is enough usable identity data.

### imports

Stores import summaries:

```text
rows_processed
rows_created
rows_matched
rows_review
rows_failed
```

### program_metrics

Stores non-client program planning and performance metrics:

```text
program
target
metric
method
notes
sort_order
month_values_json
original_file
imported_at
```

This table is intentionally separate from `clients`. A metrics sheet can support dashboards and leadership reporting without creating fake client records.

## Matching Logic

The current importer matches clients in this order:

1. HMIS ID match.
2. eCW ID match.
3. First name + last name + DOB match.
4. Unique name-only match.
5. Partial identity match.
6. Create a new client.
7. Send to review only when matching is ambiguous.
8. Fail only when there is not enough identity data, usually missing name.

This allows JotForm/BWC rows with missing DOB to import as partial client records instead of going straight to the review queue.

## Supported Source Files

The current importer has been tested against these file types:

```text
HTH Homeless Clients CSV
BWC JotForm sign-in CSV
eCW EBO 4.02 encounter patient CSV
HMIS Entry/Exit DQ Report Excel workbook
```

### HTH

HTH imports support fields such as:

```text
HMIS ID
Client Full Name
Date of Birth
Gender
Date Placed in PSH
Current Lease-up Date
Provider Name
Funding Source
Housed
Exited
Reason for Exit
UNIT AVAILABILITY
```

### JotForm / BWC

JotForm imports support partial clients when DOB is missing but a usable name exists.

Rows without any usable name are rejected instead of creating bad client records.

### eCW

eCW exports are handled as UTF-16/tab-delimited CSV files when needed.

Supported fields include:

```text
Patient Name
Patient First Name
Patient Last Name
Patient Acct No
Patient DOB
Patient Gender
Appointment Date
Visit Status
Department Name
Appointment Provider Name
```

### HMIS

HMIS Excel files may contain multiple sheets. The importer scores sheets based on recognizable identity columns and chooses the most useful sheet automatically.

Supported fields include:

```text
HMIS ID
First Name
Last Name
Date of Birth
Project
Entry Date
Exit Date
Race
Ethnicity
Veteran Status
```

## Frontend

The frontend is currently a single HTML/JavaScript prototype:

```text
frontend/upload.html
```

It includes these tabs:

```text
Overview
Upload Data
Tableau Visualizations
AI Insights
Client Master Records
Review Queue
Data Sources
Admin / Settings
```

The Upload Data tab is the main working interface.

The user selects:

```text
Source System
Program Name
File
Column Mapping
```

Then the frontend sends the file to the backend for preview and import.

The Client Master Records tab can:

```text
Load imported clients
Filter clients in the browser
Open a client profile
Show programs
Show connected sources
Show source-specific details
```

## Current Working Features

```text
FastAPI backend
Upload page
CSV preview
Excel preview
Column mapping
HTH import
JotForm/BWC import with partial clients
eCW import
HMIS import
Client creation
Client matching
Enrollment creation
Source detail storage
Client search
Client profile view
Program/source/detail display
Review queue endpoint
Stats endpoint
```

## Known Limitations

```text
No authentication yet
No production authorization model yet
No Alembic migrations yet
Frontend is still a single HTML prototype
No duplicate merge UI yet
No manual match approval workflow yet
No Tableau connection yet
No AI functionality yet
Metrics storage is basic and does not yet reconcile program labels to the canonical programs table
```

SQLite is fine for local demo testing. PostgreSQL should be used for production or shared reporting work.

## Recommended Next Steps

1. Build review/merge tools for duplicate or ambiguous clients.
2. Create a small redacted demo dataset showing one fake client across BWC, HTH, eCW, and HMIS.
3. Add canonical program mapping so client enrollments and metrics sheets use the same program names.
4. Add reporting endpoints that combine client participation, source details, import summaries, data quality, and program metrics.
5. Add Alembic migrations before the schema grows further.
6. Move production/shared usage back to PostgreSQL.
7. Prepare Tableau views after PostgreSQL is stable.
8. Add AI only after clean SQL reporting tables or views exist.

## Data Safety

Do not commit real client data, credentials, database passwords, API keys, local SQLite databases, or uploaded source files to GitHub.

The repository `.gitignore` is configured to exclude:

```text
.env
uploads/
*.db
*.sqlite
*.csv
*.xlsx
sample-data/raw/
```

Use redacted or fake data for demos, testing, and GitHub commits.
