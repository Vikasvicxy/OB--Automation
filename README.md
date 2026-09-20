# TeamHR Automation

Local recruitment/onboarding automation tool for extracting candidate
information, reviewing it safely, and generating onboarding Excel workbooks.

- Paste or upload a candidate's documents (Aadhaar card, WhatsApp chat/export).
- OCR runs **100 % locally** — documents never leave the machine.
- Review and correct the extracted fields by hand, then save as Draft or
  Approve.
- Generate **one Excel workbook with two sheets** (OB Format + Mail Format).
- **Copy Details** lets you copy the six personal fields straight into another
  tool (TAB-separated, Excel-ready).
- Runs fully **offline** on a Windows work PC. No cloud APIs at runtime.

> External actions (eSampark live upload, email, WhatsApp, SMS, voice) are
> **disabled by default** and require explicit safety flags to be turned on.

---

## Main Features

| Feature | What it does |
|---------|--------------|
| Dashboard | Sidebar navigation to all modules |
| New Onboarding (Smart Upload) | Paste/upload documents → OCR → review → Save Draft / Save & Approve → Resume/Discard |
| All Candidates | List + search (full Aadhaar/address only on the single detail page) |
| Pipeline | Read-only kanban of candidate state |
| Generated Files | History of generated workbooks (download / open folder) |
| Manual Entry | Type a candidate in without documents |
| Data Quality | Command center for problematic/duplicate candidates |
| Admin / Masters | Manage facilities, roles, aliases; import preview → apply; reload masters |
| Backup / Restore | Zip backups (allow-list), verify, safe restore |
| Health / Release | Health dashboard + release-readiness gates |
| Copy Details | Exact TAB order `Aadhar No · DOB · Fathers Name · Address · Pin Code · Gender` |
| Multi-candidate session | Approve several candidates, then Generate Excel for the batch |

## Architecture

```
Browser → FastAPI → Jinja/JS frontend → resolver/rules → OCR/parser → SQLite → Excel generation
```

Full detail: `docs/ARCHITECTURE.md` (with ASCII diagram).

## Tech Stack

- **Python 3.12** / **FastAPI 0.115** / **Uvicorn**
- **Jinja2** server-rendered templates + vanilla JavaScript/CSS
- **SQLite** (auto-initialised, no server)
- **RapidOCR (ONNX Runtime)** local OCR + layout-aware Aadhaar parser
- **openpyxl** Excel generation
- **rapidfuzz** fuzzy master matching
- **Playwright** (optional) — only for the eSampark portal automation

## Repository Structure

```
app/                     application source (routes, rules, OCR, generation, portal…)
  main.py                FastAPI entry point + all routes
  templates/             Jinja2 pages (New Onboarding, candidates, pipeline, …)
  static/                CSS / JS
data/
  masters/               HubName.xlsx, Designation_Master.xlsx, Facility_Master.xlsx
  templates/             Excel Generation.xlsx (OB Format + Mail Format)
  config.json            runtime output-dir override (empty = project-relative)
  database/            → SQLite (created at runtime, not committed)
  generated/           → workbooks (created at runtime, not committed)
scripts/                 Windows setup/start/stop + dev tooling
tests/                   standalone test scripts (pytest-compatible)
docs/                    full documentation set
.github/workflows/       CI + manual release workflows
```

## Prerequisites

- **Windows 10/11** (recommended runtime) — Windows PowerShell 5.1+
- **Python 3.12** from <https://www.python.org/downloads/> (tick "Add to PATH").
  Python 3.10–3.13 expected to work.
- Optional: **git** only if you clone instead of unzipping the source release.
- Optional: **Node.js** for the JS-harness Copy Details test.

## Windows Setup

Manual (5 minutes):

```powershell
git clone https://github.com/Vikasvicxy/OB--Automation.git
cd OB--Automation
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

One-click (does the same automatically):

```powershell
.\scripts\setup_windows.ps1
```

Optional, only for portal automation: `python -m playwright install chromium`.

## Linux Development Setup

```bash
python3.12 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Running the Application

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

or: `.\scripts\Start-TeamHR.bat`

Open **http://127.0.0.1:8000**. Stop with `Ctrl+C` or
`.\scripts\Stop-TeamHR.ps1`.

## New Onboarding Workflow

1. Open **New Onboarding / Smart Upload**.
2. **Paste or upload** the candidate's Aadhaar + WhatsApp document(s).
3. Review the **extracted fields**: Name, Mobile, DOB, Gender, Aadhaar,
   Father Name, Address, PIN, Facility, Location, Role, Salary, DOJ,
   Cost Code, Facility Type.
4. **Facility** and **Role** are searchable dropdowns over the **complete
   master** — a manual facility selection overrides any stale OCR LM/FM guess.
5. **Copy Details** → paste the six fields into another tool if needed.
6. **Save Draft** (resume later) or **Save & Approve**.
7. Approve several candidates, then **Generate Excel**.

## Facility Master

`data/masters/HubName.xlsx` — three columns:

| Column | Header | Meaning |
|--------|--------|---------|
| A | FACILITY | system reference value (e.g. `BLR/NLM (NelamangalaHub_BLR)`) |
| B | LOCATION | branch code (e.g. `BLR/NLM`) — used as Location/Branch in workbooks |
| C | FACILITY NAME | display label for search/dropdown |

Roles come from `data/masters/Designation_Master.xlsx`.

## Excel Output

One workbook, two sheets:

- **OB Format** (13 cols): `Sl No, Name*, Mobile Number*, Team*, Cost Code*,
  Facility Type*, Line of Business*, Sub Type*, Role - Designation*,
  Fixed Net Take Home*, State*, Facility*, Contractor*`
- **Mail Format** (14 cols): `Date of Joining, Name, Mobile No, Designation,
  Branch, Vertical, State, Net Salary, Aadhar No, DOB, Fathers Name, Address,
  Pin Code, Gender`

Generated under `data/generated/<date>/`. Full Aadhaar appears **only** in the
Mail Format sheet. Templates are never modified.

## Copy Details

Buttons on New Onboarding copy exactly this TAB-separated row:
`Aadhar No · DOB · Fathers Name · Address · Pin Code · Gender`
Paste directly into Excel. `Copy With Headers` adds the label row.

## Testing

```powershell
python -m pip install -r requirements-dev.txt   # test extras (pytest, httpx) - one time
python -m pytest tests/ --ignore=tests/benchmark_ocr.py -q
```

~380 tests, ~2–3 minutes. See `docs/TESTING.md`.

## Configuration

Optional. Copy `.env.example` to `.env` and edit.

```powershell
Copy-Item .env.example .env
notepad .env
```

- `SECRET_KEY` — set a real random value.
- All safety flags default off: `REAL_UPLOAD_ENABLED`, `ESAMPARK_LIVE_TEST_MODE`,
  `COMMUNICATION_ENABLED`, `EMAIL_ENABLED`, `WHATSAPP_ENABLED`, `SMS_ENABLED`,
  `VOICE_ENABLED`.

See `docs/CONFIGURATION.md`.

## Safety / External Actions

| Channel | Default | How enabled |
|---------|---------|-------------|
| eSampark live upload | **off** | `REAL_UPLOAD_ENABLED=true` **and** `ESAMPARK_LIVE_TEST_MODE=true` |
| Email / WhatsApp / SMS / Voice | **off** | `COMMUNICATION_ENABLED=true` + per-channel flag |

Credentials (eSampark/LDAP, SMTP, Gupshup) come **only** from the
environment (`.env`), never from source, DB, logs, or generated files.

## CI/CD

- **CI** (`.github/workflows/ci.yml`): every push/PR on `ubuntu-latest` +
  `windows-latest`, Python 3.12 — install → import checks → focused + full
  cross-platform tests, no credentials/candidate data required.
- **Release** (`.github/workflows/release.yml`): manual — tests → clean source
  archive → GitHub Release. A Windows EXE build is a documented TODO.

See `docs/CI_CD.md`.

## Troubleshooting

Quick lookup — full guide in `docs/TROUBLESHOOTING.md`:

| Problem | Fix |
|---------|-----|
| Port 8000 in use | `Get-NetTCPConnection -LocalPort 8000` → kill PID, or use `--port 8001` |
| PowerShell blocks scripts | `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| pip/NativeCommandError | always use `python -m pip ...` (venv-bound) |
| Buttons do nothing | F12 → Console; hard refresh Ctrl+F5 |
| Stale draft | Resume or Discard it |
| Wrong hub | manually select the Facility (overrides OCR) |
| Missing masters/template | verify the tracked assets exist (clean clone ships them) |
| OCR wrong | manual review always takes precedence |

## Documentation Index

| Document | Contents |
|----------|----------|
| `docs/BUSINESS_RULES.md` | Authoritative business rules (cost codes, masters, workbook columns, Copy Details order, safety gates) |
| `docs/ARCHITECTURE.md` | System architecture + diagram + data flows + security |
| `docs/WINDOWS_QUICK_START.md` | 5-minute Windows run guide |
| `docs/DEVELOPMENT.md` | Developer guide + git conventions |
| `docs/TESTING.md` | Test groups + exact commands |
| `docs/TROUBLESHOOTING.md` | Common issues + fixes |
| `docs/CI_CD.md` | CI/CD design + future EXE path |
| `docs/PROJECT_HISTORY.md` | Phase history + decisions |
| `docs/PROJECT_INVENTORY.md` | Every path: purpose, runtime/dev, safety |
| `docs/FINAL_HANDOFF.md` | "Start here" handoff document |
| `docs/BACKUP_MANIFEST.md` | Backup/handoff record |
| `docs/VM_RETIREMENT_CHECKLIST.md` | Retirement checklist |
| `docs/SECURITY_PRIVACY.md`, `docs/ADMIN_GUIDE.md`, `docs/USER_GUIDE.md`, `docs/CONFIGURATION.md`, `docs/COMMUNICATIONS.md`, `docs/RELEASE_CHECKLIST.md` | Additional guides |

## Known Limitations

- **8752 (Myntra First Mile)** has no hub master yet; Facility Type stays
  *Needs Review* and is not auto-assigned.
- No user accounts/authentication — this is a single-operator local tool.
- OCR quality depends on document image quality.
- Full Aadhaar is displayed only on the candidate detail page and the Mail
  Format sheet.
- No Windows EXE yet — run from source (documented EXE path in `docs/CI_CD.md`).
- Some test fixtures in Git history (pre-sanitization) contain
  realistic-looking sample values; see `docs/SECURITY_PRIVACY.md`.

## Future Improvements

- Windows PyInstaller EXE packaging (validated on a real Windows box).
- Optional: authenticated admin mode.
- Additional entity/cost-code masters as the business grows.
- Optional CI-native coverage for the UI (Playwright) once browsers are
  reliably supported in CI.