# TeamHR Automation — Development Guide

How to work on TeamHR Automation: environment, layout, common tasks, tests,
and git conventions.

## 1. Virtual environment

Always use a virtual environment — the project pins exact dependency versions.

Linux / macOS:

```bash
cd OB--Automation
python3.12 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt   # dev extras: pytest, httpx
```

Windows PowerShell:

```powershell
cd C:\TeamHR-Automation
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt   # dev extras: pytest, httpx
```

Optional extra for the portal automation only:

```bash
playwright install chromium        # only needed for eSampark automation
```

## 2. Project layout

```
app/                 # all application code
  main.py            # FastAPI entry point + every route
  config.py          # env + feature flags
  rules.py           # business rules, normalisation, resolver
  master_data.py     # Excel master loading + effective view
  admin_master.py    # admin facility/role CRUD
  database.py        # SQLite schema + CRUD
  ocr.py, ocr_backends.py, ocr_models.py, evidence.py
  document_parsers/aadhaar.py
  generation.py      # two-sheet workbook generation
  result_parser.py   # eSampark result workbook parser
  validation.py, uat_catalog.py, release_readiness.py
  backup_service.py, health.py, communication.py
  portal/            # optional eSampark Playwright automation
  templates/         # Jinja2 pages
  static/            # CSS + JS
data/
  masters/           # HubName.xlsx, Designation_Master.xlsx, Facility_Master.xlsx
  templates/         # Excel Generation.xlsx (generation template)
  config.json        # runtime output-dir override (tracked, empty)
  database|generated|backups|uploads|portal|logs/   # runtime (gitignored)
scripts/             # Windows + dev tooling
tests/               # standalone test scripts (pytest-compatible)
docs/                # documentation
.github/workflows/   # CI / release workflows
```

## 3. Run the app locally

```bash
source venv/bin/activate            # Windows: .\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The SQLite DB auto-initialises on first request.

## 4. How to…

### Add a route

1. Open `app/main.py`.
2. For a page: define a `@app.get("/my-page")` returning
   `templates.TemplateResponse("my_page.html", {...})`.
3. For an API: define `@app.post("/api/my-action")` returning a JSON dict.
   Follow existing conventions (JSON `{"ok": bool, "error": str | None}` for
   APIs; redirects for form posts).
4. Create the template under `app/templates/` if needed and add a link on the
   dashboard (`index.html`) if it should be reachable from the UI.
5. Verify no full Aadhaar / address leaks from the new route (see Security in
   `docs/ARCHITECTURE.md`).

### Modify templates

- Templates live in `app/templates/`; shared fragments in
  `app/templates/partials/`.
- Server-side Jinja2 by default; page-specific JS goes in
  `app/static/*.js` (or inline for page-sealed logic like the New Onboarding
  Copy Details block).
- Keep "no full Aadhaar, no full address" on list/search surfaces. Only
  `candidate_detail.html` may show them.
- After editing JS, run `node --check` (or `python -m pytest
  tests/test_ui_foundation.py` which runs it).

### Modify the OCR / parser

- `app/ocr.py` — extraction orchestration (`run_extraction`), field passes.
- `app/document_parsers/aadhaar.py` — layout-aware Aadhaar parsing.
- `app/evidence.py` — evidence candidates, scoring, auto-select thresholds.
- `app/ocr_backends.py` — OCR engine abstraction (RapidOCR default).
- If you change extraction, re-run `tests/test_layout_parser.py`,
  `tests/test_evidence_scoring.py`, `tests/test_review_workflow.py`,
  `tests/test_ramesh_fixture.py`. Remember the **DOB vs Issue Date** safety
  rule and the Aadhaar masking on every non-detail surface.

### Add facility rules

Facility data comes from `data/masters/HubName.xlsx`
(FACILITY / LOCATION / FACILITY NAME). Rule changes that map names to cost
codes / entity / operation live in `app/rules.py` (cost-code table) and
`app/master_data.py` (`resolve_facility_selection`).

To change master data (not code): edit the Excel file (or use
**Admin → Masters** so it lands in SQLite as an override). Never rely on a
`C:`/`/home/...` absolute path.

Tests: `tests/test_facility_dropdown.py`, `tests/test_integration.py`,
`tests/test_onboarding_pair.py`, `tests/test_ramesh_fixture.py`.

### Add roles

Roles come from `data/masters/Designation_Master.xlsx`
(DESIGNATION / COST CODE / PREFIX). To add a role use the Admin page or edit
the Excel. Keep the **no Prexo under 8751** business rule consistent.

Tests: `tests/test_integration.py` (role resolution), `tests/test_admin_master.py`.

### Add tests

- Add a standalone script `tests/test_my_thing.py` with a `main()`
  (pytest-compatible functions are fine too — the suite runs both ways).
- Follow the existing self-contained style: throwaway DB + isolated config;
  never touch `data/config.json` or the real local DB.
- Add the file to `tests/`; it will be picked up by the CI glob.

### Run focused tests

```bash
source venv/bin/activate
python -m pytest tests/test_facility_dropdown.py -v      # facility + copy details
python -m pytest tests/test_onboarding_pair.py -v        # two-sheet generation
python -m pytest tests/test_production_hardening.py -v   # security / PII
python -m pytest tests/test_review_workflow.py -v        # draft/approve/resume
```

### Run the full suite

```bash
python -m pytest tests/ --ignore=tests/benchmark_ocr.py -q
```

or per-file style (mirrors how the suite historically ran):

```bash
for t in tests/test_*.py; do python "$t" || echo "FAIL: $t"; done
```

Expect ~380 tests / ~2–3 minutes on a normal machine.

## 5. Git workflow

### Branches

- `master` is the only protected long-lived branch; it is also what the
  Windows operator clones.
- Feature work: `feature/<topic>` (e.g. `feature/facility-copy-details`).
- Bug fixes: `fix/<topic>`.
- Keep every commit on `master` green — CI runs on every push.

### Commit conventions

- Imperative, conventional prefixes: `feat:`, `fix:`, `docs:`, `test:`,
  `chore:`, `refactor:`. E.g. `feat: add full-master facility dropdown`.
- One logical change per commit.
- Never commit `.env`, databases, generated workbooks, backups, logs,
  candidate documents, or browser state (`.gitignore` protects these).
- Run `git diff --check` before committing.
- Tag releases with annotated tags (`git tag -a v1.0.0 -m "..."`).

## 6. Versioning

Semantic versioning: `MAJOR.MINOR.PATCH`.

- New feature (non-breaking) → **MINOR** (e.g. `1.1.0`).
- Bug fix → **PATCH** (e.g. `1.1.1`).
- Breaking data/workflow change → **MAJOR** (e.g. `2.0.0`).

Bump the version in `CHANGELOG.md` when you tag.

## 7. Do / Don't

- **Do** keep safety flags off by default; never turn them on to "just try".
- **Do** verify new routes never expose full Aadhaar/address outside the
  detail page / Mail Format sheet.
- **Do** test with a throwaway DB.
- **Don't** modify the official Excel templates/masters in code at runtime.
- **Don't** commit generated workbooks or backups.
- **Don't** store eSampark / LDAP credentials anywhere but `.env` (gitignored).