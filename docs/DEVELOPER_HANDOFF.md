# Developer Handoff

## How to Run Locally

```powershell
cd C:\TeamHR-Automation
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The database auto-initializes on first request.

## Architecture Boundaries

The app is a single-process FastAPI application. Key boundaries:

1. **No external services required** — everything runs locally (OCR, database, Excel generation). Portal upload and communications are placeholder/dry-run.
2. **Single SQLite database** — `data/database/teamhr.db`. No separate DB server.
3. **Template rendering** — server-side Jinja2, minimal client-side JS (vanilla, no framework).
4. **OCR runs locally** — documents never leave the machine. Uses RapidOCR (ONNX) by default.
5. **Master data is two-layered** — Excel files in `data/masters/` provide the base; admin records in SQLite provide overrides/additions. The effective view merges both.
6. **Business rules are centralized** in `app/rules.py`. The resolver function `resolve_smart_onboarding()` is the core logic for Smart Upload.
7. **Feature flags gate dangerous operations** — portal uploads, communications, and admin features all require explicit opt-in.

## Key Files and Responsibilities

| File | Purpose |
|------|---------|
| `app/main.py` | All FastAPI routes (2500+ lines). Dashboard, forms, API endpoints. |
| `app/config.py` | Environment loading, feature flags, config classes |
| `app/database.py` | SQLite schema (30+ tables), all CRUD operations, masking |
| `app/rules.py` | Business rules: cost codes, role resolution, hub matching, validation |
| `app/master_data.py` | Excel master loading, effective view merge, fuzzy search |
| `app/admin_master.py` | Admin facility/role CRUD, cost code derivation, validation |
| `app/ocr.py` | Document intelligence: OCR + field extraction + rule passes |
| `app/ocr_backends.py` | OCR backend abstraction (RapidOCR / PaddleOCR) |
| `app/ocr_models.py` | `OCRLine` and `FieldEvidence` data models |
| `app/evidence.py` | Evidence scoring, multi-candidate ranking, confidence margins |
| `app/generation.py` | Excel generation from template + daily master export |
| `app/result_parser.py` | Parse eSampark result/error workbooks |
| `app/validation.py` | Read-only validation engine with test case fixtures |
| `app/backup_service.py` | Backup/verify/restore with checksums and safety guards |
| `app/health.py` | System health cards (DB, masters, OCR, disk, backup) |
| `app/communication.py` | Provider pattern: WhatsApp/Email/SMS/Voice (all dry-run) |
| `app/logging_config.py` | Structured logging with PII sanitization |
| `app/uat_catalog.py` | UAT test case definitions |
| `app/debug_view.py` | Development-only OCR debug overlay |
| `app/portal/esampark.py` | Playwright automation for eSampark portal |
| `app/portal/service.py` | Portal orchestration layer |
| `app/portal/selectors.py` | DOM selectors for portal (single source of truth) |
| `app/portal/live_upload.py` | Controlled single-candidate live upload with safety guards |
| `app/document_parsers/aadhaar.py` | Layout-aware Aadhaar field extraction |

## How to Add a New Hub/Role

### Adding a Hub (Facility)

**Via Excel**: Add a row to `data/masters/Facility_Master.xlsx` with columns: FACILITY, LOCATION, FACILITY NAME. The facility name determines the cost code:
- Contains "MYNTRA" → Myntra Last Mile (8751)
- Ends with `_PL` → Flipkart First Mile (4441)
- Otherwise → Flipkart Last Mile (4421)

Then reload masters from the Settings page or call `/api/reload-masters`.

**Via Admin UI**: Go to `/admin/master-data`, Facilities tab, click Add. Fill in name, location code, entity, and operation. Cost code is auto-derived.

### Adding a Role (Designation)

**Via Excel**: Add a row to `data/masters/Designation_Master.xlsx` with the designation name and cost code columns.

**Via Admin UI**: Go to `/admin/master-data`, Roles tab, click Add. Fill in official name, entity scope, operation, allowed cost codes, and aliases.

### After Adding

Call `/api/reload-masters` or click Reload on the Settings page. The resolver picks up changes immediately.

## How to Add a Communication Provider

1. Create a new class in `app/communication.py` inheriting from `BaseProvider`:
   ```python
   class MyProvider(BaseProvider):
       def validate_recipient(self, recipient):
           ...
       def send(self, recipient, template_name, payload, dry_run=True):
           ...
   ```

2. Register it in `CommunicationService.__init__`:
   ```python
   self.providers['my_channel'] = MyProvider(self.config)
   ```

3. Add templates to the `TEMPLATES` dict with a `'my_channel'` key.

4. Add a feature flag in `app/config.py` `FeatureFlags` class and `.env.example`.

5. Wire the flag check into `is_channel_enabled()`.

## How to Update Portal Selectors

1. Open `app/portal/selectors.py`.
2. Update the CSS/XPath/text selectors for the changed portal elements.
3. If the portal structure changed significantly, update `app/portal/verified_selectors.py`.
4. Run the verification script:
   ```powershell
   python scripts/verify_real_portal.py
   ```
5. Run portal tests:
   ```powershell
   pytest tests/test_portal.py -v
   ```

## Database Migration Approach

The app uses `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ADD COLUMN` for schema management. Migrations run automatically in `app/database.py:init_db()`:

1. Tables are created if they don't exist (all DDL is in `init_db()`).
2. New columns are added via `ALTER TABLE ... ADD COLUMN` if missing (checked via `PRAGMA table_info`).
3. No migration framework — changes are idempotent and additive.

To add a new table: add the `CREATE TABLE IF NOT EXISTS` statement to `init_db()`.
To add a new column: add an `ALTER TABLE` migration block in `init_db()` after the table creation.

## Testing Approach

```powershell
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_validation.py -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing
```

Test categories:
- **Unit tests** (`test_phase*.py`, `test_evidence_scoring.py`, `test_admin_master.py`): test individual modules in isolation
- **Integration tests** (`test_integration.py`): test full workflows
- **Smoke tests** (`test_template_smoke.py`): verify all templates render
- **Regression tests** (`test_fixes.py`): prevent known bugs from returning

## Build/Release Process

There is no formal build step — the app runs directly from source.

1. All tests pass: `pytest tests/ -v`
2. Set version in `app/config.py` (`APP_VERSION`)
3. Create a git tag: `git tag v0.1.0`
4. Verify health: `GET /api/health` returns `"overall": "ok"`
5. Create a backup as a release artifact

The `scripts/Start-TeamHR.ps1` script handles deployment on a single Windows machine.
