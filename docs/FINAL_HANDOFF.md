# TeamHR Automation — Final Handoff

**Purpose:** This document is the source of truth for handing TeamHR Automation
over to a Windows machine. The Ubuntu VM that hosted development is being
retired; everything a Windows operator needs is committed to GitHub and packaged
in this repo.

**Tag:** `teamhr-windows-handoff-2026-09-18`
**Cut from commit:** `792d3bcdeeb7cd0a13418942ebe5eebec3fc321b` (plus the
finalization commit described below).
**Application version:** `1.0.0-rc1`

---

## 1. What this repo is

Local recruitment onboarding tool: OCR (Aadhaar) → review → generate the
Self-Onboarding + TeamHR Backend Mail Excel pair. FastAPI + Jinja2 + vanilla JS
+ SQLite + openpyxl. Runs fully offline; no cloud AI/API at runtime.

## 2. Repo layout (entry points)

| Path | Purpose |
|------|---------|
| `app/main.py` | FastAPI entry point (`app.main:app`, port 8000) |
| `app/generation.py` | Excel pair generation + config (`data/config.json`) |
| `app/rules.py` | Business rules / normalization |
| `app/master_data.py` | Official masters (facilities, designations) |
| `app/database.py` | SQLite at `data/database/teamhr.db` (auto-init) |
| `data/masters/` | HubName.xlsx, Designation_Master.xlsx, self-onboarding template |
| `data/templates/` | `Excel Generation.xlsx` (OB Format + Mail Format sheets) |
| `scripts/setup_windows.ps1` | One-time Windows setup (venv, deps, Playwright, DB) |
| `scripts/Start-TeamHR.ps1` / `.bat` | Start server + open browser |
| `scripts/Stop-TeamHR.ps1` | Stop server |
| `tests/` | 20 test modules (all passing) |
| `docs/` | This handoff + quick start + business rules + full guides |

## 3. Getting it onto Windows

1. Copy the whole repo folder to, e.g., `C:\TeamHR-Automation`, or clone
   `https://github.com/Vikasvicxy/OB--Automation.git`.
2. Install Python 3.12 from <https://www.python.org/downloads/> (tick *Add to PATH*).
3. Run `.\scripts\setup_windows.ps1` (venv, dependencies, Playwright Chromium, DB).
4. Run `.\scripts\Start-TeamHR.bat` (or `.ps1`).
5. Browser opens at <http://127.0.0.1:8000>.

Full detail: `docs/WINDOWS_QUICK_START.md` and `docs/INSTALL_WINDOWS.md`.

## 4. What changed in this handoff pass

- **PII sanitization (breaking finding handled):** the tracked template
  `data/templates/Excel Generation.xlsx` contained **real candidate sample rows**
  (names, mobile numbers, Aadhaar numbers, addresses) under the headers of both
  sheets. It has been **sanitized**: all sample rows were removed and the file
  now contains only the header rows (OB Format 13 cols, Mail Format 14 cols)
  with formatting, widths, and sheet names preserved. The application already
  strips sample rows at generation time, so output is unaffected.
  - **Historical note:** the PII-bearing version exists in **published git
    history** (commit `792d3bc` and earlier). It was **not** rewritten (no
    history rewrite, no force push). Anyone who has already cloned has a copy.
    Recommendation: rotate any affected access/contact expectations and treat
    that file's data as disclosed. Going forward the tracked template is clean.
- **Portable output config:** `data/config.json` previously pinned
  `output_base_dir` to `C:\TeamHR-Automation\data\generated` (a Windows-only
  path). It is now `""`, so the app falls back to the project-relative
  `data/generated` on any machine/OS. Operators can set the output folder from
  **Settings → Excel Output**.
- **Windows scripts hardened:**
  - Use `$VenvPython -m pip` (venv-bound) instead of global `pip`.
  - `setup_windows.ps1` installs dependencies once and stamps
    `data\.deps-installed`.
  - `Start-TeamHR.ps1` only installs deps when the marker/venv is missing →
    every-day starts are fast (no `pip install` on each launch).
  - `Stop-TeamHR.ps1` unchanged (still works).
  - `.gitignore` covers `data/server.pid` and `data/.deps-installed`.
- **README** updated (removed "planned" tags, uses `python -m pip`,
  references quick-start docs).
- **New docs:** `docs/WINDOWS_QUICK_START.md`, `docs/BUSINESS_RULES.md`,
  and this file.
- **Test portability:** two path-traversal tests in
  `tests/test_production_hardening.py` were made platform-independent (they
  depended on Windows `\` separators in `Path().name`, so the suite's full
  verification now passes on Linux too).

## 5. Verification performed (all green on the Ubuntu VM)

- **Tests:** all 20 modules in `tests/` pass
  (≈8 465 assertions; includes validation variant sweeps, OCR/evidence,
  facility dropdown + JS-harness copy-details checks, portal, hardening,
  template smoke, regression phases 3–7).
- **JS syntax:** `node --check` clean on `app/static/*.js`.
- **JS harness:** `tests/_facility_js_harness.cjs` boots the real inline script
  from `smart_upload.html`; Copy Details emits exactly
  `Aadhar No\tDOB\tFathers Name\tAddress\tPin Code\tGender`
  (e.g. `246697470317\t06/05/2006\tManik\tS/O: Manik, Kusarampalli\t585307\tMale`).
- **Smoke (uvicorn on Ubuntu):** `/`, `/health`, `/smart-upload`,
  `/candidates`, `/generated-files`, `/portal`, `/api/health`, `/api/counts`,
  `/api/drafts`, `/api/generated`, `/api/search-hubs` all HTTP 200.
- **Facility master:** 256 rows; facility search returns the complete master
  (never cost-code filtered). `nelamangala` →
  `NelamangalaHub_BLR` (4421, Delivery Hub) and `NelamangalaHub_BLR_PL`
  (4441, Pickup Hub).
- **Role dropdowns:** 4421 → LM roles incl. Prexo; 8751 → LM roles **without**
  Prexo (business rule); FM roles for 4441/8752.
- **Excel generation (synthetic, sanitized template):** both sheets produced;
  OB Format Facility*=Location code, Mail Format Branch=Location & Vertical=
  facility name, full Aadhaar only in the Mail sheet.
- **Safety flags:** `REAL_UPLOAD_ENABLED`, `ESAMPARK_LIVE_TEST_MODE`,
  `COMMUNICATION_ENABLED`, `EMAIL_ENABLED`, `WHATSAPP_ENABLED`, `SMS_ENABLED`,
  `VOICE_ENABLED` all default off (code + `.env.example`).
- **PII/path scan of tracked text files:** no `/home/`, `teamhr-work`,
  `azureuser`, VM IPs, private keys, or committed secrets. Only
  non-functional docs mention the `C:\TeamHR-Automation` install folder.

## 6. Known items / things to note on Windows

- **Python 3.12 recommended.** 3.10–3.13 expected to work.
- Playwright Chromium (`playwright install chromium`) is only needed for
  eSampark portal automation; `setup_windows.ps1 -SkipBrowser` skips it.
- `eSampark` credentials come from the environment (`.env`) **only**:
  `ESAMPARK_USERNAME`, `ESAMPARK_PASSWORD`. Never put them in files committed
  to git.
- Live portal upload needs **both** `REAL_UPLOAD_ENABLED=true` **and**
  `ESAMPARK_LIVE_TEST_MODE=true`; leave them off for normal operation.
- First run: if the recruiter-name prompt appears, set it (stored in
  `data/config.json`).
- The Windows scripts assume the repo is the working directory and that
  PowerShell is allowed to run (`Set-ExecutionPolicy RemoteSigned -Scope
  CurrentUser` if blocked).

## 7. Business rules in one paragraph

Four cost codes: 4421 (Flipkart LM), 4441 (Flipkart FM), 8751 (Myntra LM),
8752 (Myntra FM, no hub master → *Needs Review*). Facility classification:
MYNTRA → 8751; `_PL`/PICKUP → 4441; else 4421. Facility Master
(`HubName.xlsx`: FACILITY | LOCATION | FACILITY NAME) provides the exact
location/branch; roles come from `Designation_Master.xlsx` (no Prexo under
8751). Generated workbooks contain only *Ready* candidates; full Aadhaar
appears only in the Mail Format sheet. Copy Details block order:
`Aadhar No, DOB, Fathers Name, Address, Pin Code, Gender` (tab-separated).
Details: `docs/BUSINESS_RULES.md`.

## 8. Final release status

**READY FOR WINDOWS PRODUCTION USE** — all gates passed:

- [x] All tracked masters/templates present and sanitized
- [x] Full automated test suite green; JS checks green
- [x] Smoke test on Ubuntu green (all key routes + APIs)
- [x] Excel generation verified against the sanitized template
- [x] No hardcoded VM/user paths or secrets in production code
- [x] Safety flags off by default
- [x] Windows setup/start/stop scripts portable and fast
- [x] Release package built under `release/TeamHR-Windows-Source/` (+ `.zip`)
- [x] Git archive tagged `teamhr-windows-handoff-2026-09-18` and pushed

Follow `docs/WINDOWS_QUICK_START.md` on the Windows machine.