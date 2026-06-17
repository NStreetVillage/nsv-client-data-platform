# Phase 1 Plan: NSV Client Identity and Data Normalization

## Objective

Create a central database that assigns each client a unique NSV Client ID and connects records from JotForm, HMIS, HTH, and eCW.

## Phase 1 Deliverables

1. NSV Client ID generation
2. Client master table
3. Program table
4. Enrollment table
5. Source record tracking
6. CSV/Excel import process
7. Drag-and-drop upload page
8. File preview
9. Column mapping
10. Initial API for viewing clients and programs

## Matching Logic v1

High confidence match:

- First name
- Last name
- Date of birth

If all three match, reuse the existing NSV Client ID.

If no match exists, create a new NSV Client ID.

## Privacy Note

The NSV Client ID should not be generated directly from name or date of birth. Name and DOB are used only for matching. The generated ID should be internal and non-identifying.
