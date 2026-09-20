# TeamHR Automation — Project Inventory

Complete inventory of every important path in the repository: what it is,
whether it is needed at runtime, needed for development, safe to commit, and
whether it is generated or source-controlled.

Legend for the **Source** column:

- **SC** — source-controlled in Git (present on clone)
- **GEN** — generated at runtime / by setup; never committed
- **OPT** — optional/scanned at runtime only when present

> This inventory was produced as part of the final archive pass
> (`teamhr-final-v1.0.0`). Values below match the repository at that tag.

---

## 1. Root files

| Path | Purpose | Runtime | Dev | Safe to commit | Source |
|------|---------|---------|-----|----------------|--------|
| `README.md` | GitHub landing page + quick orientation | no | yes | yes | SC |
| `CHANGELOG.md` | Release history (semantic versions) | no | yes | yes | SC |
| `requirements.txt` | Pinned Python runtime dependencies | yes (pip install) | yes | yes | SC |
| `requirements-dev.txt` | Test extras (pytest, httpx) for running the suite / CI | no | yes | yes | SC |
| `.env.example` | Documented environment template (no real secrets) | reference | yes | yes | SC |
| `.gitignore` | Ignore rules protecting secrets/PII/runtime data | no | yes | yes | SC |
| `LICENSE` | Not present — no license file shipped | no | no | n/a | n/a |

## 2. `app/` — application source (all SC)

| Path | Purpose | Runtime | Dev | Safe | Generated |
|------|---------|---------|-----|------|-----------|
| `app/main.py` | FastAPI entry point + all routes (pages, `/api/*`, generation, upload, admin, backup, health) | yes | yes | yes | no |
| `app/config.py` | Env/config reading + feature flags + non-secret diagnostics | yes | yes | yes | no |
| `app/database.py` | SQLite schema + CRUD, candidate persistence, drafts, PII-safe views | yes | yes | yes | no |
| `app/rules.py` | Business rules: salary/aadhaar/PIN/DOB normalization, cost-code table, role/alias resolution | yes | yes | yes | no |
| `app/master_data.py` | Excel master loading (HubName, Designation_Master, Facility_Master), fuzzy search, effective-view merge with admin overrides | yes | yes | yes | no |
| `app/admin_master.py` | Admin-managed facility/role CRUD (no auth — local tool) | yes | yes | yes | no |
| `app/generation.py` | Two-sheet workbook generation (OB Format + Mail Format), daily master, backend mail | yes | yes | yes | no |
| `app/ocr.py` | Extraction orchestration (multi-file, evidence, run_extraction) | yes | yes | yes | no |
| `app/ocr_backends.py` | OCR backend abstraction (RapidOCR default; optional) | yes | yes | yes | no |
| `app/ocr_models.py` | `OCRLine` / field-evidence data models + geometry helpers | yes | yes | yes | no |
| `app/evidence.py` | Evidence scoring + confidence/margin, per-field auto-select thresholds | yes | yes | yes | no |
| `app/result_parser.py` | eSampark result/error workbook parser | yes | yes | yes | no |
| `app/document_parsers/aadhaar.py` | Layout-aware Aadhaar parser (bounding boxes, DOB vs Issue Date safety) | yes | yes | yes | no |
| `app/validation.py` | Manual validation engine (read-only test cases against resolvers) | yes | yes | yes | no |
| `app/uat_catalog.py` | UAT test-case catalog (groups A–N, critical set) | yes | yes | yes | no |
| `app/backup_service.py` | Backup/verify/restore with allow-list + checksums | yes | yes | yes | no |
| `app/health.py` | Health endpoint + release readiness (no secrets) | yes | yes | yes | no |
| `app/communication.py` | Communication providers (DRY RUN by default; all disabled by flags) | yes (disabled) | yes | yes | no |
| `app/logging_config.py` | Structured logging with PII redaction | yes | yes | yes | no |
| `app/debug_view.py` | OCR debug overlay (gated by `OCR_DEBUG_VIEW`) | optional | yes | yes | no |
| `app/release_readiness.py` | Ordered release-gate checklist logic | yes | yes | yes | no |
| `app/portal/esampark.py` | Playwright automation for eSampark (creds from env only) | optional | yes | yes | no |
| `app/portal/service.py` | High-level portal orchestration | optional | yes | yes | no |
| `app/portal/live_upload.py` | Single-candidate live upload (locked unless both flags true) | optional | yes | yes | no |
| `app/portal/selectors.py` | DOM selector library for eSampark | optional | yes | yes | no |
| `app/portal/verified_selectors.py` | Selectors verified against live portal | optional | yes | yes | no |
| `app/portal/__init__.py` | Package marker | yes | yes | yes | no |
| `app/__init__.py` | Package marker | yes | yes | yes | no |

## 3. `app/templates/` — Jinja2 pages (all SC)

| Path | Purpose |
|------|---------|
| `index.html` | Dashboard |
| `smart_upload.html` | **New Onboarding / Smart Upload** (paste/upload, OCR review, Copy Details, Save Draft / Save & Approve, session bar, Generate) |
| `manual_entry.html` | Manual Entry form |
| `candidates.html` | All Candidates list (safe fields only) |
| `candidate_detail.html` | Only page that ever shows full Aadhaar/address |
| `batch_review.html`, `batch_detail.html` | Per-batch review/detail |
| `pipeline.html` | Read-only pipeline/kanban |
| `data_quality.html` | Data Quality command center |
| `generated_files.html` | Generated workbook history |
| `admin_master.html` | Admin / Masters page |
| `backups.html` | Backup / Restore page |
| `health.html` | Health dashboard |
| `release.html` | Release readiness |
| `portal_status.html`, `live_upload.html` | Portal status / locked live-upload screen |
| `communications.html`, `follow_ups.html`, `issue_center.html`, `notifications_center.html`, `reports.html` | Ops pages |
| `validation.html`, `uat.html` | Validation console / UAT runner |
| `debug_ocr.html` | OCR debug view (gated) |
| `settings.html` | Settings (Excel output dir, recruiter profile) |
| `404.html` | Not found |
| `partials/_head.html`, `_search.html`, `_searchbar.html`, `_sidebar.html`, `_toast.html` | Reusable fragments |

All templates render server-side with Jinja2; JavaScript is provided by
`app/static/*.js`.

## 4. `app/static/` — frontend assets (all SC)

| Path | Purpose |
|------|---------|
| `style.css`, `ui.css`, `manual_entry.css`, `smart_upload.css` | Styles |
| `ui.js` | Toasts, sidebar, modals, global search |
| `manual_entry.js` | Manual entry form logic (~1 800 lines) |
| `smart_upload.css` | (styles for smart upload) |
| `app.js` | Small bootstrap stub (dashboard uses native links) |

> The Copy Details block (Aadhar No / DOB / Fathers Name / Address / Pin Code /
> Gender, TAB-separated) lives in the inline script of `smart_upload.html`
> (`COPY_FIELD_DEFS`), exercised by `tests/_facility_js_harness.cjs`.

## 5. `data/` — runtime data

| Path | Purpose | Runtime | Dev | Safe to commit | Source |
|------|---------|---------|-----|----------------|--------|
| `data/config.json` | Runtime config (e.g. output dir overrides, recruiter profile) | yes | yes | yes (unrelated to secrets) | SC |
| `data/masters/HubName.xlsx` | **Facility master** (FACILITY / LOCATION / FACILITY NAME) — 256 rows | **required** | yes | yes (reference only) | SC |
| `data/masters/Designation_Master.xlsx` | **Role/designation master** (DESIGNATION / COST CODE / PREFIX) | **required** | yes | yes (reference only) | SC |
| `data/masters/Facility_Master.xlsx` | `_PL` pickup-hub reference (used as fallback/seed) | optional | dev/tests | yes (reference only) | SC |
| `data/masters/Self_Onboarding_Template.xlsx` | Legacy self-onboarding template (documented; superseded by `Excel Generation.xlsx`) | legacy | yes | yes | SC |
| `data/masters/TeamHR_OB_Template.xlsx` | Backend/single-sheet template (documented; superseded) | legacy | yes | yes | SC |
| `data/templates/Excel Generation.xlsx` | **Generation template**: OB Format (13 cols) + Mail Format (14 cols), headers only | **required** | yes | yes (sanitized, no candidate rows) | SC |
| `data/ocr_benchmark/ground_truth.csv` | Example OCR benchmark ground-truth row | no | debugging | yes | SC |
| `data/ocr_benchmark/input/` | Real Aadhaar benchmark images | no | no on CI | **no — PII** | GEN |
| `data/ocr_benchmark/output/` | Benchmark OCR outputs | no | no | no (artifacts) | GEN |
| `data/database/teamhr.db` | **SQLite candidate DB** | yes | no | **no — runtime candidate data** | GEN |
| `data/generated/` | Generated Excel workbooks + uploads/screenshots | yes | no | **no — candidate data** | GEN |
| `data/backups/` | Backup ZIPs (snapshots incl. DB) | yes | no | **no — candidate data** | GEN |
| `data/uploads/` | Uploaded documents (sanitized/discarded flows) | no | no | **no — PII** | GEN |
| `data/validation/` | Validation CSV exports (PII-safe) | no | no | no (artifacts) | GEN |
| `data/portal/` | Playwright auth state, screenshots | optional | no | **no — PII/session** | GEN |
| `data/logs/` | Server logs | optional | no | no | GEN |

## 6. `scripts/` — Windows + dev tooling (all SC)

| Path | Purpose |
|------|---------|
| `scripts/setup_windows.ps1` | One-time Windows setup (venv, deps, Playwright Chromium, DB init) |
| `scripts/Start-TeamHR.ps1` | Start uvicorn + open browser (fast: only installs deps when marker missing) |
| `scripts/Start-TeamHR.bat` | Double-click launcher → PowerShell script |
| `scripts/Stop-TeamHR.ps1` | Kill the running server (uses `data/server.pid`) |
| `scripts/Setup-Windows.bat` | Batch wrapper for setup |
| `scripts/verify_real_portal.py` | Portal selector verification (4B) — manual, requires creds in env |
| `scripts/verify_upload_history.py` | Upload-history verifier (4C) — manual |
| `scripts/verify_upload_history_row_structure.py` | Row-structure verifier (4D) — manual |
| `scripts/benchmark_real_ocr.py` | OCR benchmark against real images — manual |

## 7. `tests/` — test suite (all SC)

| Path | What it validates |
|------|-------------------|
| `test_validation.py` | Validation engine: 90-case SET A/B/C, resolvers, PII-safety of exports |
| `test_evidence_scoring.py` | Evidence scoring, confidence/margin, auto-select thresholds |
| `test_review_workflow.py` | Save Draft → Save & Approve → Resume/Discard, normalization, no-duplicate |
| `test_facility_dropdown.py` | Full-master dropdown, `_PL` rows, Nelamangala LM/FM, no cost-code prefilter, **JS harness Copy Details exact order** |
| `test_onboarding_pair.py` | Two-sheet generation, OB/Mail columns, facility/location sourcing, full-Aadhaar-only-in-Mail |
| `test_production_hardening.py` | Security (injection, traversal, zip, PII leak), feature flags, DB integrity, failure injection |
| `test_admin_master.py` | Admin facility/role CRUD, import preview/apply, effective view |
| `test_portal.py` | Portal automation (mocked), diagnostics no-secrets |
| `test_live_upload.py` | Live-upload safety guards (locked by default) |
| `test_integration.py` | Real-master integration, persistence, fuzzy matching, role resolution |
| `test_phase3.py` … `test_phase7.py` | Regression phases: generation / portal / admin / review workspace / UAT |
| `test_fixes.py` | Regression fixes (OCR, DOB, facility) |
| `test_layout_parser.py` | Layout-aware Aadhaar parser, DOB vs Issue Date safety |
| `test_ramesh_fixture.py` | End-to-end extraction on a realistic fixture (synthetic values) |
| `test_template_smoke.py` | All templates render without errors |
| `test_ui_foundation.py` | Dashboard/navigation/search + `node --check` JS syntax (skipped if Node absent) |
| `_facility_js_harness.cjs` | Node harness that boots the inline `smart_upload.html` script for Copy Details |
| `benchmark_ocr.py` | Optional OCR benchmark (not part of CI pass/fail) |

## 8. `docs/` — documentation (all SC, commit-safe)

| Path | Purpose |
|------|---------|
| `README.md` | ~15 sections (see Documentation Index in each doc set) |
| `PROJECT_INVENTORY.md` | This file |
| `BUSINESS_RULES.md` | Authoritative business rules (cost codes, masters, workbook columns, Copy Details order, safety gates) |
| `ARCHITECTURE.md` | System architecture + ASCII diagram + data flows + security model |
| `PROJECT_HISTORY.md` | Phase history + key technical decisions |
| `WINDOWS_QUICK_START.md` | 5-minute Windows setup/run/stop |
| `INSTALL_WINDOWS.md` | Full Windows install guide |
| `DEVELOPMENT.md` | Developer guide (layout, add routes/OCR/masters, tests, git) |
| `TESTING.md` | Test groups, exact commands, known environment quirks |
| `TROUBLESHOOTING.md` | Common issues + fixes (port, pip, PowerShell policy, OCR, drafts, hubs) |
| `CI_CD.md` | Recommended CI/CD lifecycle for a local desktop tool |
| `FINAL_HANDOFF.md` | "Start here if the author is unavailable" document |
| `BACKUP_MANIFEST.md` | What to back up / where it lives + final handoff record |
| `VM_RETIREMENT_CHECKLIST.md` | Retirement checklist for this Ubuntu VM |
| `ADMIN_GUIDE.md` | Admin master/data management |
| `USER_GUIDE.md` | End-user guide |
| `CONFIGURATION.md` | Every env/config option |
| `SECURITY_PRIVACY.md` | Security & PII privacy model |
| `COMMUNICATIONS.md` | Communication architecture + disabled channels |
| `RELEASE_CHECKLIST.md` | Release gate checklist |
| `FRIEND_QUICK_START.md` | Quick start for a colleague |
| `DEVELOPER_HANDOFF.md` | Previous developer handoff notes |
| `FINAL_REPORT.md` | Previous final report (superseded by the final report at the root task level) |
| `OPEN_SOURCE_RESEARCH.md` | Research notes (OCR libraries) |

## 9. `.github/workflows/` — CI/CD (all SC)

| Path | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | CI on every push/PR: Linux (ubuntu-latest) + Windows (windows-latest), Python 3.12, install, focused regression + cross-platform security/Excel tests |
| `.github/workflows/release.yml` | Manual (`workflow_dispatch`) release: test → source artifact → (future) Windows PyInstaller EXE → GitHub Release |

## 10. Release output (generated, never committed)

| Path | Purpose | Safe to commit | Source |
|------|---------|----------------|--------|
| `release/TeamHR-Windows-Source/` | Clean source folder for Windows handoff (this VM) | no (built from source) | GEN |
| `release/TeamHR-Windows-Source.zip` | ZIP of the above | no (built from source) | GEN |

Rebuild with the documented archive command in `docs/CI_CD.md` (or the release
workflow). Do not commit these; they are regenerable.

## 11. Not present / intentionally excluded

- `venv/`, `.venv/` — local virtualenvs (ignored)
- `.env` — local secrets (ignored; template is `.env.example`)
- `data/database/`, `data/generated/`, `data/backups/`, `data/uploads/`,
  `data/portal/`, `data/logs/`, `data/validation/` — runtime candidate data
  (ignored)
- `data/ocr_benchmark/input|output/` — real Aadhaar images (ignored)
- `release/`, `build/`, `dist/` — build output (ignored)
- Private keys, browser profiles, storage-state JSON — ignored

## 12. Verification notes (final pass)

- Required runtime assets **are tracked** and clean:
  `data/masters/HubName.xlsx`, `data/masters/Designation_Master.xlsx`,
  `data/masters/Facility_Master.xlsx`,
  `data/templates/Excel Generation.xlsx` (headers only, no candidate rows).
- No secrets, tokens, private keys, `.env`, or VM-specific paths are tracked.
- `data/config.json` holds no secret and no pinned absolute path
  (`output_base_dir` empty → project-relative default).