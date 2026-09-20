# TeamHR Automation — Testing Guide

What the test suite covers, how to run it, and known environment quirks.

## 1. Overview

`tests/test_*.py` are **self-contained** scripts (each builds a throwaway DB /
isolated config; they never touch `data/config.json` or a real local database).
They are also pytest-compatible, so both invocation styles work.

**Prerequisite:** install the dev/test extras once (pytest + httpx, needed by
the HTTP test clients):

```bash
python -m pip install -r requirements-dev.txt
```

Baseline on the development VM (Python 3.12.3, Node 20, Ubuntu):

```
378 passed in ~2.6 minutes
```

## 2. Exact commands

Full suite (recommended):

```bash
source venv/bin/activate                # Windows: .\venv\Scripts\Activate.ps1
python -m pytest tests/ --ignore=tests/benchmark_ocr.py -q
```

Full suite, historical style (each script has its own runner):

```bash
for t in tests/test_*.py; do python "$t"; done
```

Focused groups:

```bash
# OCR + Aadhaar parsing + layout safety
python -m pytest tests/test_layout_parser.py tests/test_evidence_scoring.py -q
python -m pytest tests/test_ramesh_fixture.py -q
python -m pytest tests/test_fixes.py -q

# Facility dropdown / HubName mapping / Copy Details (needs Node)
python -m pytest tests/test_facility_dropdown.py -v

# Role dropdown / salary / PIN / duplicate detection
python -m pytest tests/test_integration.py -q

# Review / persistence (Save Draft, Save & Approve, Resume/Discard)
python -m pytest tests/test_review_workflow.py -q

# Excel generation (two sheets: OB Format + Mail Format)
python -m pytest tests/test_onboarding_pair.py -q

# Security / path traversal / PII / feature flags / fresh DB
python -m pytest tests/test_production_hardening.py -q

# Portal + live upload safety (mocked)
python -m pytest tests/test_portal.py tests/test_live_upload.py -q

# Admin master management (facilities/roles, import preview → apply)
python -m pytest tests/test_admin_master.py -q

# Template rendering smoke + JS syntax
python -m pytest tests/test_template_smoke.py tests/test_ui_foundation.py -q

# Regression phases
python -m pytest tests/test_phase3.py tests/test_phase4.py tests/test_phase5.py \
               tests/test_phase6.py tests/test_phase7.py -q

# Validation engine (SET A/B/C, ~90 cases)
python -m pytest tests/test_validation.py -q
```

JS syntax check alone:

```bash
node --check app/static/app.js
node --check app/static/ui.js
node --check app/static/manual_entry.js
```

## 3. Major test groups

| Group | Files | What it validates |
|-------|-------|-------------------|
| OCR / parser | `test_layout_parser.py`, `test_fixes.py`, `test_evidence_scoring.py`, `test_ramesh_fixture.py` | Aadhaar layout parsing, name/DOB/aadhaar/mobile/address extraction, confidence, **DOB vs Issue Date**, evidence scoring thresholds |
| Aadhaar parsing | `test_layout_parser.py`, `test_review_workflow.py` | 12-digit normalisation, `1234 5678 9012` forms, rejection of bad lengths, VID handling, no Aadhaar in address/evidence |
| Facility dropdown | `test_facility_dropdown.py` | Complete-master browse (`all=1`), no cost-code pre-filter, `_PL` rows searchable, Nelamangala LM/FM pair, duplicate/blank-column C fallback, bare-locality stays ambiguous |
| HubName mapping | `test_facility_dropdown.py`, `test_onboarding_pair.py`, `test_integration.py` | FACILITY/LOCATION/FACILITY NAME mapping; Branch/Facility sourced from LOCATION column |
| Role dropdown | `test_integration.py`, `test_admin_master.py` | Official roles per cost code, aliases resolve to official, **no Prexo under 8751**, 4421 includes Prexo |
| Salary | `test_review_workflow.py`, `test_integration.py` | `18k→18000`, `18.5k→18500`, `30k→30000`, invalid → Review/needs_attention |
| PIN | `test_review_workflow.py` | exactly 6 digits; others rejected; kept as text |
| Review/persistence | `test_review_workflow.py`, `test_phase4.py`, `test_phase5.py` | Draft save, approve, resume, discard, edit history (no sensitive values), state transitions |
| Excel generation | `test_onboarding_pair.py`, `test_phase3.py`, `test_validatation` coverage | OB Format (13) + Mail Format (14) columns, only-Ready inclusion, full Aadhaar only in Mail, Facility/Location sourcing, daily-master masking |
| Security / path traversal | `test_production_hardening.py` | SQL injection, XSS, path traversal (OS-independent), malformed zips, PII-free non-detail surfaces, feature-flag guards, DB integrity, failure injection |
| Fresh DB / template / master loading | `test_template_smoke.py`, `test_integration.py`, `test_production_hardening.py` | All templates render; masters load from Excel; fresh-DB init idempotent |
| Browser/UI | `test_ui_foundation.py`, `test_facility_dropdown.py` (JS harness) | Page load/navigation/search; JS syntax (`node --check`); **Copy Details exact TAB order** via `_facility_js_harness.cjs` |
| Portal / live upload | `test_portal.py`, `test_live_upload.py`, `test_phase4.py` | Mocked portal automation, diagnostics contain no secrets, live upload locked unless both safety flags set |
| Production hardening | `test_production_hardening.py` | Whole-group hardening, backups no secrets, diagnostics bundle no secrets |
| Validation scenarios | `test_validation.py` | ~90 SET A/B/C manual-validation cases resolved through production resolvers |

## 4. Known environment quirks

These are pre-existing, platform/tooling-specific behaviours — not test
failures:

1. **Node required for `test_facility_dropdown.py::test_copy_block_js`.**
   This test boots the inline Smart Upload JS with `tests/_facility_js_harness.cjs`.
   If Node.js is absent the test **errors** (it does not auto-skip). Install
   Node, or run the rest of the suite and treat that one as environment-dependent.
2. **`test_ui_foundation.py` JS-syntax check auto-skips** when Node is absent
   (it prints `[SKIP] node not available`).
3. **`tests/benchmark_ocr.py` is a benchmark**, not a pass/fail test — it is
   excluded from the CI suite (`--ignore=tests/benchmark_ocr.py`).
4. **Playwright browsers are not needed for tests.** Tests import the portal
   modules but mock the browser; no Chromium download is required in CI.
5. Some scripts emit FastAPI `on_event` deprecation warnings and
   `PytestReturnNotNoneWarning`; these are warnings only and do not affect
   results.
6. The JS Copy Details test and `test_phase5` JS-syntax test write a
   `node --check` call; on CI both GitHub-hosted `ubuntu-latest` and
   `windows-latest` images ship Node, so this is green there.

## 5. Adding tests

See `docs/DEVELOPMENT.md` → "Add tests". Keep style self-contained: throwaway
DB + isolated config, no writes to production `data/config.json` or the real
DB, and no real candidate PII in fixtures (use `123456789012`-style synthetic
values).