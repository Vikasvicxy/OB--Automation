# Technical Architecture

## Tech Stack

- **Language**: Python 3.10+
- **Web Framework**: FastAPI 0.115
- **Templating**: Jinja2 3.1
- **Database**: SQLite 3 (via Python `sqlite3` module)
- **OCR**: RapidOCR (ONNX Runtime) — default; PaddleOCR (optional)
- **PDF Rendering**: PyMuPDF (fitz)
- **Image Processing**: Pillow
- **Excel**: openpyxl
- **Fuzzy Matching**: difflib (stdlib) + rapidfuzz
- **Browser Automation**: Playwright (Chromium)
- **Frontend**: Vanilla JavaScript + CSS (no framework)

## Project Structure

```
TeamHR-Automation/
├── app/
│   ├── main.py                  # FastAPI entry point — all routes
│   ├── config.py                # Centralized config, env loading, feature flags
│   ├── database.py              # SQLite persistence layer (schema + CRUD)
│   ├── rules.py                 # Business rules, resolver, normalization
│   ├── master_data.py           # Excel master loading, effective view merge
│   ├── admin_master.py          # Admin-managed facilities/roles CRUD
│   ├── ocr.py                   # Document intelligence — extraction orchestration
│   ├── ocr_backends.py          # OCR backend abstraction (RapidOCR/PaddleOCR)
│   ├── ocr_models.py            # OCRLine / FieldEvidence data models
│   ├── evidence.py              # Evidence scoring and candidate ranking
│   ├── generation.py            # Self Onboarding Excel generation + daily master
│   ├── result_parser.py         # eSampark result/error workbook parser
│   ├── validation.py            # Manual validation engine (read-only test cases)
│   ├── backup_service.py        # Backup, verify, safe restore
│   ├── health.py                # System health reporting
│   ├── communication.py         # Provider-independent comms (WhatsApp/Email/SMS/Voice)
│   ├── logging_config.py        # Structured logging with PII sanitization
│   ├── debug_view.py            # Development-only OCR debug overlay
│   ├── uat_catalog.py           # UAT test case definitions
│   ├── document_parsers/
│   │   └── aadhaar.py           # Layout-aware Aadhaar parser (bounding boxes)
│   ├── portal/
│   │   ├── esampark.py          # Playwright automation for eSampark portal
│   │   ├── service.py           # High-level portal orchestration
│   │   ├── selectors.py         # Centralized DOM selectors for portal
│   │   ├── verified_selectors.py # Selectors verified against live portal
│   │   └── live_upload.py       # Controlled single-candidate live upload
│   ├── templates/               # 25 Jinja2 HTML templates
│   │   ├── index.html           # Dashboard
│   │   ├── manual_entry.html    # Manual entry form
│   │   ├── smart_upload.html    # Smart upload (OCR) page
│   │   ├── candidates.html      # Candidate list/search
│   │   ├── candidate_detail.html # Single candidate detail view
│   │   ├── batch_review.html    # Batch review page
│   │   ├── batch_detail.html    # Batch detail page
│   │   ├── pipeline.html        # Kanban/pipeline view
│   │   ├── data_quality.html    # Data quality command center
│   │   ├── settings.html        # Settings and master data status
│   │   ├── admin_master.html    # Admin master data management
│   │   ├── backups.html         # Backup management
│   │   ├── health.html          # Health monitoring
│   │   ├── portal_status.html   # Portal upload status
│   │   ├── live_upload.html     # Live upload safety screen
│   │   ├── communications.html  # Communication management
│   │   ├── follow_ups.html      # Follow-up task tracking
│   │   ├── issue_center.html    # Issue tracking
│   │   ├── notifications_center.html # Notification center
│   │   ├── reports.html         # Reports page
│   │   ├── validation.html      # Manual validation tool
│   │   ├── uat.html             # UAT test runner
│   │   ├── debug_ocr.html       # OCR debug view
│   │   ├── 404.html             # Not found page
│   │   └── partials/            # Reusable template fragments
│   └── static/
│       ├── style.css            # Global styles
│       ├── ui.css               # UI component styles
│       ├── ui.js                # UI component logic
│       ├── app.js               # Application scripts
│       ├── manual_entry.js      # Manual entry form logic
│       ├── manual_entry.css     # Manual entry styles
│       └── smart_upload.css     # Smart upload styles
├── scripts/
│   ├── setup_windows.ps1        # Automated Windows setup
│   ├── Start-TeamHR.ps1        # Start server (PowerShell)
│   ├── Start-TeamHR.bat        # Start server (batch file)
│   ├── Stop-TeamHR.ps1         # Stop server
│   ├── verify_real_portal.py    # Portal selector verification
│   ├── verify_upload_history.py # Upload history verification
│   ├── verify_upload_history_row_structure.py # Row structure verification
│   └── benchmark_real_ocr.py    # OCR benchmarking
├── tests/                       # 17 test files
│   ├── test_phase3.py           # Excel generation tests
│   ├── test_phase4.py           # Portal integration tests
│   ├── test_phase5.py           # Admin master tests
│   ├── test_phase6.py           # Review workspace tests
│   ├── test_phase7.py           # UAT tests
│   ├── test_validation.py       # Validation engine tests
│   ├── test_evidence_scoring.py # Evidence scoring tests
│   ├── test_admin_master.py     # Admin CRUD tests
│   ├── test_integration.py      # Integration tests
│   ├── test_portal.py           # Portal automation tests
│   ├── test_template_smoke.py   # Template rendering smoke tests
│   ├── test_ui_foundation.py    # UI foundation tests
│   ├── test_fixes.py            # Regression tests
│   ├── test_live_upload.py      # Live upload safety tests
│   ├── test_layout_parser.py    # Layout parser tests
│   └── benchmark_ocr.py         # OCR performance benchmarks
├── data/
│   ├── database/                # SQLite database (gitignored)
│   ├── generated/               # Generated Excel files (gitignored)
│   ├── backups/                 # Backup ZIP files (gitignored)
│   ├── masters/                 # Excel master files (user-provided)
│   ├── logs/                    # Application logs (gitignored)
│   ├── portal/                  # Portal browser state (gitignored)
│   ├── ocr_benchmark/           # OCR benchmark images (gitignored)
│   └── config.json              # Runtime config (output dir, etc.)
├── docs/                        # Documentation
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variable template
├── .gitignore                   # Git ignore rules
└── README.md                    # Project overview
```

## Database Schema Overview

The SQLite database (`data/database/teamhr.db`) contains these tables:

| Table | Purpose |
|-------|---------|
| `candidates` | Core candidate records (name, mobile, cost code, role, facility, salary, status) |
| `batches` | Onboarding batches with candidate counts |
| `generated_files` | Generated Self Onboarding Excel files |
| `generation_audit` | Audit trail for Excel generation |
| `daily_master` | Daily operational master export (masked Aadhaar) |
| `documents` | Source document metadata (Aadhaar, screenshots) |
| `master_load_history` | History of Excel master reloads |
| `portal_uploads` | eSampark portal upload records |
| `portal_audit` | Portal automation audit trail |
| `live_uploads` | Live upload records |
| `candidate_events` | Per-candidate event timeline |
| `candidate_edit_history` | Safe edit history (old/new values, no sensitive data) |
| `batch_events` | Per-batch event timeline |
| `master_facilities` | Admin-managed facility records |
| `master_roles` | Admin-managed role records |
| `master_role_cost_codes` | Role-to-cost-code associations |
| `master_role_aliases` | Role alias mappings |
| `master_change_history` | Admin master change audit |
| `candidate_drafts` | Recovery drafts for Smart Upload / Manual Entry |
| `system_events` | System-level events (backup, restore, etc.) |
| `saved_views` | Saved filter/view configurations |
| `manual_validation_runs` | Validation test run results |
| `uat_runs` | UAT run records |
| `uat_results` | UAT test results |
| `follow_ups` | Follow-up task tracking |
| `issues` | Issue tracking |
| `notifications` | System notifications |
| `communication_outbox` | Communication message outbox |

Indexes exist on: batch_id, mobile, name, status, facility_name, candidate_id, event_type, and other frequently queried columns.

## Data Flow

### OCR Pipeline (Smart Upload)

```
Upload files → OCR engine (RapidOCR) → OCRLine objects (text + bounding boxes)
    → Document classifier (Aadhaar vs Screenshot)
    → Field extractors (name, DOB, Aadhaar, address, mobile, salary, role, hub)
    → Evidence scorer (multi-candidate ranking with confidence margin)
    → Business rule resolver (cost code, role, facility, location)
    → Review payload (fields + confidence + source)
    → User reviews and approves → SQLite candidate record
```

### Excel Generation

```
Batch of Ready candidates → Validate (name, mobile, cost code, role, facility, state)
    → Load Self_Onboarding_Template.xlsx (openpyxl)
    → Map fields to template columns
    → Write candidate rows into template copy
    → Save to data/generated/{date}/uploads/
    → Record in generated_files + generation_audit tables
    → Export daily master (masked Aadhaar)
```

### Portal Upload

```
Generated file → Validate → Playwright browser session
    → Login to eSampark (LDAP, cookies preserved)
    → Navigate to Onboarding → Self Onboarding → FTC
    → Upload workbook
    → Poll Upload History for status
    → Download result/error workbooks
    → Parse results → Update candidate portal_status
```

### Live Upload (Single Candidate)

```
Both safety flags enabled → Manual review checklist (4 items)
    → Two-step confirmation → Final guard re-check
    → Playwright upload of single-candidate file
    → Result parsing → Status update
```

## Security Model

### PII Handling

- **Aadhaar numbers**: stored in SQLite only; displayed masked (`XXXX XXXX XXXX`) everywhere except the Candidate Detail page; never logged; never in URLs or localStorage; never in backups (only the database snapshot, which contains them)
- **Full addresses**: stored in SQLite only; never displayed outside the detail page; never in search results, pipeline cards, or data quality drill-downs
- **Mobile numbers**: stored in SQLite; displayed in lists and cards; used for duplicate detection

### Edit History

Safe fields (name, mobile, cost code, role, facility, salary, status) have old/new values recorded in `candidate_edit_history`. Sensitive fields (Aadhaar, address) only record "Aadhaar updated" / "Address updated" — never the actual values.

### Backup Security

Backups use an explicit allow-list approach. Never included: raw Aadhaar images, portal cookies/sessions, browser profiles, logs, caches, secrets, OCR benchmarks.

### API Security

- Global search (`/api/search`) uses parameterized SQL and never searches Aadhaar or address fields
- Pipeline cards exclude Aadhaar and address
- Data quality drill-down excludes Aadhaar and address
- Health endpoint excludes all secrets (API keys, passwords)

## Feature Flags

Feature flags are read from environment variables (or `.env` file) at startup. All default to safe values.

| Flag | Default | Purpose |
|------|---------|---------|
| `REAL_UPLOAD_ENABLED` | false | Enables real portal uploads |
| `ESAMPARK_LIVE_TEST_MODE` | false | Enables live test upload mode |
| `COMMUNICATION_ENABLED` | false | Master switch for communications |
| `WHATSAPP_ENABLED` | false | Enables WhatsApp channel |
| `EMAIL_ENABLED` | false | Enables email channel |
| `SMS_ENABLED` | false | Enables SMS channel |
| `VOICE_ENABLED` | false | Enables voice channel |
| `ADMIN_FEATURE_ENABLED` | true | Enables admin master data page |
| `DEMO_MODE` | false | Uses mock data, skips external calls |
| `OCR_DEBUG_VIEW` | false | Shows OCR debug overlay |

## Communication Architecture

Provider pattern with abstract `BaseProvider` class:
- `WhatsAppProvider` (Gupshup adapter placeholder)
- `EmailProvider` (SMTP placeholder)
- `SMSProvider` (placeholder)
- `VoiceProvider` (placeholder)

`CommunicationService` routes messages through providers, checks feature flags, validates recipients, and records to the outbox table. All sending is dry-run by default.

Templates are defined in `app/communication.py` (`TEMPLATES` dict) with per-channel variants.

## Testing Strategy

- **Unit tests**: validation engine, evidence scoring, OCR extraction, business rules, admin master CRUD
- **Integration tests**: full pipeline (upload → OCR → resolve → generate)
- **Template smoke tests**: all 25 templates render without errors
- **UI foundation tests**: page load, navigation, search
- **Portal tests**: selector verification, upload flow (mocked)
- **Live upload tests**: safety guard verification
- **Regression tests**: specific bug fixes
- **Manual validation**: read-only test cases against production resolver
- **UAT catalog**: manual acceptance test checklist with run tracking

Run tests:

```powershell
.\venv\Scripts\Activate.ps1
pytest tests/ -v
```
