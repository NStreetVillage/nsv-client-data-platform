# NSV Client Data Platform

Internal Phase 1 prototype for consolidating N Street Village client reporting data across HMIS, HTH, eCW, JotForm, and future data sources.

The platform is not intended to replace those systems. HMIS, HTH, eCW, and JotForm remain systems of record. This app acts as a reporting and analytics layer that imports exported files, matches clients across systems, assigns an internal NSV Client ID, and shows cross-program participation.

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
```

## Current Local Setup

Local development currently uses SQLite for speed and simplicity.

The local environment file is:

```text
backend/.env
```

Recommended local demo settings:

```env
DATABASE_URL=sqlite:///./nsv_data.db
UPLOAD_DIR=uploads
```

PostgreSQL is still the preferred long-term database, but SQLite avoids local PostgreSQL setup issues while testing the upload/import demo.

## Run Locally

From the repository root:

```powershell
cd C:\Users\idbon\Desktop\NstreetVillage\Nstreetvillageproject\nsv-client-data-platform
.\.venv\Scripts\Activate.ps1
```

Create database tables:

```powershell
cd backend
python scripts\create_tables.py
```

Start the API:

```powershell
uvicorn app.main:app --reload
```

Open the upload page:

```text
http://127.0.0.1:8000/upload-page
```

Open Swagger/API docs:

```text
http://127.0.0.1:8000/docs
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
User maps columns
        |
Backend imports rows
        |
App creates or matches clients
        |
App creates program enrollment records
        |
App stores source-specific details
        |
User searches clients and views profile details
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
columns
first preview rows
row_count
```

The frontend uses this response to render the preview table and column mapping controls.

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
```

SQLite is fine for local demo testing. PostgreSQL should be used for production or shared reporting work.

## Recommended Next Steps

1. Add a stronger server-side client search endpoint, such as `GET /clients?search=...`.
2. Build a clearer client profile page for demo use.
3. Create a small redacted demo dataset showing one fake client across BWC, HTH, eCW, and HMIS.
4. Add review/merge tools for duplicate or ambiguous clients.
5. Add Alembic migrations before the schema grows further.
6. Move production/shared usage back to PostgreSQL.
7. Add reporting endpoints for cross-program clients, import summaries, and data quality.
8. Prepare Tableau views after PostgreSQL is stable.
9. Add AI only after clean SQL reporting tables or views exist.

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
