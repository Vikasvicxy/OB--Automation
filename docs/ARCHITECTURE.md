# TeamHR Automation — Architecture

Technical architecture of the TeamHR Automation system, verified against the
source at tag `teamhr-final-v1.0.0`.

## 1. System overview

```
                 +----------------------------------------------------+
                 |                  Operator's browser                 |
                 |        (Chrome/Edge — local, 127.0.0.1)            |
                 +------------------------+---------------------------+
                                          |
                                          | HTTP (FastAPI / Jinja2 / JSON API)
                                          v
                 +----------------------------------------------------+
                 |                    FastAPI (app/main.py)           |
                 |  • Jinja2 HTML pages        • /api/* JSON routes   |
                 |  • Session/upload handling  • Generation endpoints |
                 +------------------------+---------------------------+
                                          |
                 +------------------------+---------------------------+
                 |      Jinja / JS frontend (templates + static JS)   |
                 |   New Onboarding, Manual Entry, review, Copy        |
                 |   Details, facility+role searchable dropdowns      |
                 +------------------------+---------------------------+
                                          |
                                          v
                 +----------------------------------------------------+
                 |         Resolver / rules layer (app/rules.py)      |
                 |   cost-code table (4421/4441/8751/8752), role map, |
                 |   salary/PIN/Aadhaar/DOB normalisation, validation |
                 +------------------------+---------------------------+
                                          |
                 +----------------------------------------------------+
                 |     OCR / parser (app/ocr.py, evidence.py,         |
                 |     document_parsers/aadhaar.py, ocr_backends.py)  |
                 |            local OCR only — never cloud            |
                 +------------------------+---------------------------+
                                          |
                 +----------------------------------------------------+
                 |      SQLite (app/database.py) — data/database/     |
                 |   candidates, batches, drafts, events, generated,  |
                 |   admin masters, audit, validation/UAT runs        |
                 +------------------------+---------------------------+
                                          |
                                          v
                 +----------------------------------------------------+
                 |        Excel generation (app/generation.py)        |
                 |   single workbook -> OB Format + Mail Format       |
                 |   from data/templates/Excel Generation.xlsx        |
                 +----------------------------------------------------+
```

## 2. Tech stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.12, FastAPI 0.115, Uvicorn |
| Templates | Jinja2 3.1 (server-rendered HTML) |
| Frontend | Vanilla HTML/CSS/JS (no framework) |
| Database | SQLite 3 via stdlib `sqlite3` |
| OCR | RapidOCR (ONNX Runtime) — local; layout parser for Aadhaar |
| PDF/images | PyMuPDF (fitz), Pillow |
| Excel | openpyxl |
| Fuzzy matching | rapidfuzz |
| Browser automation (optional) | Playwright (Chromium), only for the eSampark portal |
| Logs | stdlib logging with PII redaction |

## 3. Module map

### Entry point & routes — `app/main.py`

Single FastAPI app; every page route renders a Jinja2 template and every
`/api/*` route returns JSON. Key route groups:

- **Pages**: `/` (dashboard), `/smart-upload` (New Onboarding),
  `/manual-entry`, `/candidates`, `/candidates/{id}`,
  `/batch-review`, `/batches/{id}`, `/pipeline`, `/data-quality`,
  `/reports`, `/settings`, `/backups`, `/health`, `/release`,
  `/admin/master-data`, `/validation`, `/uat`, `/generated-files`,
  `/portal`, `/live-upload`, `/debug/ocr`, ops pages.
- **Candidate APIs**: `POST /api/save-draft`, `POST /api/confirm-candidate`
  (Save & Approve), `/api/check-duplicate`, `/api/drafts`,
  `/api/drafts/discard`, `/api/edit-candidate/{id}`, bulk actions.
- **OCR/upload**: `POST /api/smart-extract`, `/api/manual-ocr-extract`,
  `/api/upload-document`, `/api/evidence-score`.
- **Masters**: `GET /api/search-hubs` (full-master typeahead),
  `/api/location` (facility → location/cost-code/entity/type),
  `/api/reload-masters`, `/api/master-status`.
- **Generation**: `POST /api/generate-excel`, preview, pairs, download,
  open-folder endpoints, `/api/candidates/export-selected` (PII-safe CSV).
- **Admin**: `/api/admin/*` facility/role CRUD + import/history/export.
- **Backup/health/release/validation/UAT/portal/ops** endpoints as listed in
  `app/main.py`.

### Business rules — `app/rules.py`

- Cost-code table: **4421** Flipkart LM, **4441** Flipkart FM, **8751**
  Myntra LM, **8752** Myntra FM (no hub master → *Needs Review*).
- Normalisation: salary (`18k→18000`), PIN (exactly 6 digits), Aadhaar
  (exactly 12 digits), DOB formats, gender (Male/Female/Transgender).
- Role resolution: official designations per cost code; aliases (biker,
  delivery, sort, prexo, …) resolve only to official names; **no Prexo for
  8751**.

### Masters — `app/master_data.py`

- Loads `data/masters/HubName.xlsx`
  (FACILITY / LOCATION / FACILITY NAME), `Designation_Master.xlsx`
  (DESIGNATION / COST CODE / PREFIX), and `Facility_Master.xlsx`.
- `resolve_facility_selection(facility, location, facility_ref)` returns the
  canonical location + cost code + entity + operation + facility type from the
  master — the source of truth used by generation.
- `fuzzy_search_facilities` powers `/api/search-hubs` over the **complete**
  master (never pre-filtered by LM/FM inference).
- Effective view: SQLite admin overrides (`master_facilities`,
  `master_roles`, role aliases) merge **over** the Excel files; Excel files are
  read-only.

### Frontend — templates + static JS

- `smart_upload.html` — the New Onboarding page: paste/upload zone, evidence
  review form, facility + role searchable dropdowns, **Copy Details** block
  (inline `COPY_FIELD_DEFS`: Aadhar No · DOB · Fathers Name · Address · Pin
  Code · Gender, TAB-separated), Save Draft / Save & Approve, multi-candidate
  session bar, Generate Excel.
- `manual_entry.js` — manual entry flow with draft restore/discard.
- `ui.js` — toasts, modals, sidebar, global search.
- `app.js` — small bootstrap stub.

### OCR pipeline — `app/ocr.py`, `app/evidence.py`, `app/document_parsers/aadhaar.py`

```
uploaded files (image / pdf / text)
      → local OCR (RapidOCR→OCRLine with bboxes+confidence) or text pass-through
      → document classification & field extractors
      → evidence scorer (per-field best candidate + margin thresholds)
      → resolver/normalisation (rules.py)
      → review payload with confidence level (High / Review / Missing / Conflict)
      → operator reviews → Save Draft / Save & Approve
```

- **DOB safety:** a date labelled *Issue Date* can never be selected as DOB;
  only DOB-labelled dates qualify, and threshold `DOB_AUTO_SELECT_MIN=80` must
  be met (else Review).
- Aadhaar is auto-selected only above `AADHAAR_AUTO_SELECT_MIN=90`; elsewhere
  it is masked in UI/API payloads.

### Persistence — `app/database.py`

SQLite at `data/database/teamhr.db` (gitignored; auto-initialised). Core
tables: `candidates`, `batches`, `generated_files`, `generation_audit`,
`daily_master`, `documents`, `candidate_events`, `candidate_edit_history`
(sensitive fields logged as "updated", never values), `candidate_drafts`,
`master_facilities`, `master_roles`, `master_role_cost_codes`,
`master_role_aliases`, `master_change_history`, `master_load_history`,
`portal_uploads`, `portal_audit`, `live_uploads`, `manual_validation_runs`,
`uat_runs`, `uat_results`, `follow_ups`, `issues`, `notifications`,
`communication_outbox`, `system_events`, `saved_views`.

Candidate workflow states: `draft → needs_attention / needs_review → ready →
generated`, plus portal status columns (`pending / success / failed`).

### Excel generation — `app/generation.py`

- Reads the **template** `data/templates/Excel Generation.xlsx` (never
  modifies it), clears any sample rows, writes **Ready** candidates, saves a
  timestamped `Onboarding_<date>_<time>.xlsx` under `data/generated/...`.
- **OB Format** (13 cols): `Sl No, Name*, Mobile Number*, Team*, Cost Code*,
  Facility Type*, Line of Business*, Sub Type*, Role - Designation*, Fixed Net
  Take Home*, State*, Facility*, Contractor*`.
- **Mail Format** (14 cols): `Date of Joining, Name, Mobile No, Designation,
  Branch, Vertical, State, Net Salary, Aadhar No, DOB, Fathers Name, Address,
  Pin Code, Gender`.
- **Sourcing:** `Facility` (OB) and `Branch` (Mail) come from the master's
  **LOCATION** column via `resolve_facility_selection`; `Vertical` = facility
  display name; `State` = KARNATAKA / Karnataka; `Contractor` = TEAM HR GSA
  PRIVATE LIMITED; LOB/Sub Type = EKART.
- Full Aadhaar **only** in Mail Format. Daily master export uses masked
  Aadhaar.

### Backup / health / release readiness

- `app/backup_service.py` — ZIP backups with an allow-list (DB + config +
  generated metadata; **never** raw Aadhaar docs, browser state, secrets);
  digest-verified; safe restore that re-asserts safety flags off.
- `app/health.py` — safe status; never exposes secrets.
- `app/release_readiness.py` — ordered release gates incl. detection of
  `DANGEROUS_FLAGS` (REAL_UPLOAD_ENABLED / ESAMPARK_LIVE_TEST_MODE /
  COMMUNICATION_ENABLED yet true).

## 4. Candidate workflow

```
Upload/paste docs → OCR + review payload
  ├─ Save Draft  ──────────► draft (resumable via Resume / Discard)
  ├─ Save & Approve ───────► ready (validated; errors shown inline if failing)
  └─ (manual entry) Save Draft / Approve ──► draft / ready
Ready candidates → Generate Excel (only_ready) → Onboarding workbook (OB+Mail)
  └─ daily master export (masked) exists alongside
```

Multi-candidate sessions track approved counts in `sessionStorage` and offer a
single Generate for the current batch.

## 5. Optional eSampark Playwright architecture

```
Generated workbook
      → Playwright (Chromium) → eSampark portal login (LDAP from .env only)
      → navigate → upload workbook → poll upload history → download result
      → parse result/error workbooks → update candidate portal_status
```

- Credentials come **only** from the environment (`ESAMPARK_USERNAME` /
  `ESAMPARK_PASSWORD` etc.); cookies/localStorage are the only state persisted
  (never passwords).
- Selector definitions centralised in `app/portal/selectors.py` +
  `verified_selectors.py`.

## 6. Communication architecture (disabled by default)

- Provider pattern `app/communication.py`: `WhatsAppProvider` (Gupshup),
  `EmailProvider` (SMTP), `SMSProvider`, `VoiceProvider` — all **placeholder /
  dry-run** adapters.
- Controlled by `COMMUNICATION_ENABLED` (master) + per-channel flags. All
  default **false**. Even when enabled in code, nothing is sent without a real
  provider configuration.

## 7. Security model

- Full Aadhaar / address appear **only** on the candidate detail page and in
  the Mail Format sheet. Masked or omitted everywhere else (search, pipeline,
  data quality, exports, backups listing, health, logs).
- Logging redacts auth/token/cookie/password/secret/api-key values.
- Backups exclude sensitive file types by allow-list.
- Feature flags default safe; live upload additionally requires **both**
  `REAL_UPLOAD_ENABLED` and `ESAMPARK_LIVE_TEST_MODE`.
- SQL is parameterised; upload paths are sanitised to prevent traversal;
  generated downloads are digest-checked.

## 8. Config surface

See `docs/CONFIGURATION.md`. Highlights: `APP_HOST`, `APP_PORT`, `SECRET_KEY`,
`OUTPUT_DIR`, `ADMIN_FEATURE_ENABLED`, `OCR_DEBUG_VIEW`, `DEMO_MODE`, the six
safety flags, Gupshup + SMTP placeholders. `.env` is gitignored; defaults come
from `app/config.py` and are mirrored in `.env.example`.

## 9. Testing architecture

Standalone test scripts (`tests/test_*.py`) plus pytest compatibility
(`python -m pytest tests/`). See `docs/TESTING.md`. The JS Copy Details block
is verified headlessly with `tests/_facility_js_harness.cjs` under Node.