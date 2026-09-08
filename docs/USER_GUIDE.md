# Recruiter User Guide

## Dashboard

The Dashboard (`/`) shows:
- Total candidate counts by status (Draft, Ready, Generated, Needs Attention)
- Today's volume
- Status distribution charts
- Duplicate mobile warnings
- Batch activity

Use the global search bar (or press `/` or `Ctrl+K`) to search candidates by name, mobile, facility, or role. Search never exposes Aadhaar numbers or addresses.

## Smart Upload Workflow

Navigate to **Smart Upload** (`/smart-upload`).

### Upload

Upload one or more files for a single candidate:
- **Aadhaar document** (image or PDF) — extracts name, DOB, Aadhaar number, address
- **Recruiter screenshot** (image) — extracts mobile, role, hub/facility, salary

### OCR Extraction

The system runs OCR locally (documents never leave your machine). It then:
1. Classifies each file as Aadhaar or screenshot
2. Extracts fields from each
3. Runs business rules to resolve cost code, role, facility, and location
4. Scores confidence for each field

### Review

The review form shows every extracted field with:
- **Value** — what was extracted
- **Source** — Aadhaar, Screenshot, or Manual
- **Confidence** — High, Review, or Missing

Fields marked "Review" or "Missing" need your attention. The "Evidence/Why" section explains how each value was determined.

### Approve or Save Draft

- **Approve** — validates all required fields, saves the candidate with status "Ready"
- **Save Draft** — saves partial progress; you can resume later from the Drafts section

## Manual Entry Workflow

Navigate to **Manual Entry** (`/manual-entry`).

Fill in the form fields:
- Candidate Name (required)
- Mobile (required, normalized to 10 digits)
- Cost Code (select from 4421, 4441, 8751, 8752)
- Role/Designation (autocomplete filtered by cost code)
- Facility/Hub (autocomplete filtered by cost code)
- Salary (accepts formats like "18k", "18000", "₹18,000")

You can also upload an Aadhaar image here — it runs the same OCR pipeline and pre-fills the form.

**Duplicate check**: entering a mobile number that already exists in the system shows a warning with the existing candidate's details.

**Save Draft** or **Confirm Candidate** (same as Smart Upload approval).

## Candidate Detail Page

Click any candidate name to open `/candidates/{id}`. Shows:
- All candidate fields (Aadhaar is masked except on this detail page)
- Review summary (Ready / Needs Review / Portal Failed)
- Evidence snapshot (how OCR values were determined)
- Edit history (field changes with timestamps)
- Timeline of events (creation, approval, edits, generation, portal uploads)
- Documents attached to this candidate
- Generated Excel files and portal outcomes
- Quick actions: Edit, Approve, Mark Review, Add to Batch

Use Previous/Next arrows to navigate between candidates.

## Pipeline View

Navigate to **Pipeline** (`/pipeline`). A Kanban-style board showing candidates in columns:

| Draft | Needs Review | Ready | Generated | Portal Pending | Portal Success | Portal Failed |
|-------|-------------|-------|-----------|----------------|----------------|---------------|

Each card shows: name, mobile, role, facility, cost code, batch ID.

**Filters** (top of page): search, today only, entity, operation, cost code, role, facility, batch, status.

**Saved Views**: save filter combinations for quick access later.

## Batch Management

### Batch Review

Navigate to **Batch Review** (`/batch-review?batch_id=N`). Shows all candidates in a batch with status counts.

From here you can:
- Review individual candidates
- Remove candidates from the batch
- Edit candidates inline
- Generate the Self Onboarding Excel (only "Ready" candidates are included)

### Batch Detail

Navigate to **Batch Detail** (`/batches/{id}`). Shows:
- Batch summary (total, ready, needs review, generated, portal status)
- Candidate list with status
- Generated files and their portal upload status
- Batch event timeline

### Generating Excel

From a batch page, click **Generate Onboarding Excel**. A preview dialog shows:
- How many candidates are ready
- Which candidates are excluded and why
- Output folder and filename

The generated file is saved to `data\generated\{date}\uploads\`.

## Data Quality Center

Navigate to **Data Quality** (`/data-quality`). Shows issue categories as cards:
- **Critical**: Needs Review, Duplicate Mobile, Missing Name
- **Review**: Unknown Facility, Missing Address, Invalid Salary, Role/Facility Conflicts
- **Informational**: Low Confidence

Click any card to see the affected candidates.

## Follow-ups

Navigate to **Follow-ups** (`/follow-ups`). Task tracking for candidate-related actions.

- Create follow-ups linked to a candidate with a reason, owner, due date, and notes
- View overdue, due today, upcoming, and completed items
- Mark as complete or cancel

## Issue Center

Navigate to **Issues** (`/issues`). Track and resolve issues:
- Create issues with type, severity, title, and detail
- Link to a candidate or batch
- Resolve with notes

## Notifications

Navigate to **Notifications** (`/notifications`). Shows system notifications:
- Mark individual notifications as read
- Mark all as read
- Filter by read/unread

## Reports

Navigate to **Reports** (`/reports`). Shows candidate counts and summary statistics.

## Settings

Navigate to **Settings** (`/settings`). Shows:
- Master data status (Designation Master, Facility Master)
- Admin master status (facilities, roles, overrides)
- Output configuration (generated Excel folder)
- Generated file history
- Portal status and upload history
- Health summary
- Daily master mirror

## Validation

Navigate to **Validation** (`/validation`). A testing tool for verifying the resolver logic:
- Run individual test cases or all cases in a set
- View expected vs actual results
- Export results to CSV

This is a read-only diagnostic tool — it never creates candidates or modifies data.
