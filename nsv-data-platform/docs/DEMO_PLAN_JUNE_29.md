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

### Step 1 - Upload sample data

Upload one or more files from existing sources such as HMIS, HTH, JotForm, or eCW.

### Step 2 - Create / match clients

The system should attempt to match clients using HMIS ID, eCW ID, first name + last name + DOB, or the review queue.

### Step 3 - Search for a client

Use the Client Master Records tab to search for a client by name or NSV ID.

### Step 4 - Show cross-program interaction

Show a client record with program history, such as Bethany Women's Center, Housing, MMVC, or Behavioral Health Services.

## Demo Success

The demo is successful if we can show:

- A file was uploaded
- Client records were created or matched
- A client can be searched
- A client's program interactions can be viewed
- Records needing manual review are separated into a review queue

## Out of Scope for This Demo

- Tableau dashboards
- AI insights
- Full production security implementation
- Production deployment
- Automated SharePoint imports
- Replacing HMIS, HTH, eCW, or JotForm
