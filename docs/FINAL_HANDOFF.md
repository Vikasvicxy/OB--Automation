# TeamHR Automation — Final Handoff

**Purpose:** Start here if the original author is not available. This is the
concise handoff for taking over TeamHR Automation on a Windows machine. The
Ubuntu VM that hosted development is being retired; everything needed is
committed to GitHub.

**Repository:** <https://github.com/Vikasvicxy/OB--Automation.git>
**Stable branch:** `master`
**Latest stable tag:** `teamhr-final-v1.0.0`
**Recommended Python:** 3.12 (3.10–3.13 expected to work)

---

## 1. What this project is

Local recruitment / onboarding automation: paste or upload a candidate's
documents (Aadhaar card, WhatsApp chat/export) → local OCR extracts the fields →
operator reviews and corrects them → candidates are saved and approved →
**one Excel workbook** with two sheets (**OB Format** and **Mail Format**) is
generated. Plus a **Copy Details** block for direct TAB-separated Excel paste.

Runs **fully offline**. No cloud APIs, no automatic eSampark upload, no email /
WhatsApp / SMS / voice unless a human explicitly turns safety flags on.

## 2. Run it on Windows (quick)

```powershell
git clone https://github.com/Vikasvicxy/OB--Automation.git
cd OB--Automation
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Stop with Ctrl+C.

Full guide: `docs/WINDOWS_QUICK_START.md`.

## 3. Where things live

| Item | Location |
|------|----------|
| Facility master | `data/masters/HubName.xlsx` (FACILITY = A, LOCATION = B, FACILITY NAME = C) |
| Role master | `data/masters/Designation_Master.xlsx` (DESIGNATION / COST CODE / PREFIX) |
| `_PL`/pickup reference | `data/masters/Facility_Master.xlsx` |
| Generation template | `data/templates/Excel Generation.xlsx` (OB Format 13 cols + Mail Format 14 cols) |
| Candidate database | `data/database/teamhr.db` (auto-created) |
| Generated workbooks | `data/generated/<date>/Onboarding_<ts>.xlsx` |
| Backups | `data/backups/` |
| Server logs | `data/logs/` |

## 4. How facility mapping works

- Facility dropdown searches the **complete HubName.xlsx** master — it is never
  pre-filtered by the LM/FM guess, so the operator can pick any hub (including
  an FM `_PL` row when OCR guessed LM).
- Selecting a facility **overrides** stale OCR LM/FM inference and sets
  location, cost code, facility type, and the role list.
- Location/Branch in the workbook always comes from the master's **LOCATION**
  column (Column B).

## 5. How Excel generation works

- Only **Ready** candidates are generated (Draft / Needs Attention excluded).
- One workbook, two sheets from the same approved candidates:
  - **OB Format**: `Sl No, Name*, Mobile Number*, Team*, Cost Code*,
    Facility Type*, Line of Business*, Sub Type*, Role - Designation*,
    Fixed Net Take Home*, State*, Facility*, Contractor*`
  - **Mail Format**: `Date of Joining, Name, Mobile No, Designation, Branch,
    Vertical, State, Net Salary, Aadhar No, DOB, Fathers Name, Address,
    Pin Code, Gender`
- Full Aadhaar appears **only** in the Mail Format sheet.
- The template is never modified; a copy is written under a timestamped name.

## 6. Cost codes (business essentials)

| Code | Entity / Operation | Type |
|------|--------------------|------|
| 4421 | Flipkart Last Mile | Delivery Hub |
| 4441 | Flipkart First Mile | Pickup Hub |
| 8751 | Myntra Last Mile | Delivery Hub |
| 8752 | Myntra First Mile | *Needs Review* (no hub master yet — not auto-assigned) |

Full rules: `docs/BUSINESS_RULES.md`.

## 7. Copy Details

Buttons on New Onboarding copy exactly, TAB-separated:

```
Aadhar No   DOB   Fathers Name   Address   Pin Code   Gender
```

with `Copy With Headers` adding the label row. Paste straight into Excel.

## 8. Safety flags

All **off** by default (in code and `.env.example`): `REAL_UPLOAD_ENABLED`,
`ESAMPARK_LIVE_TEST_MODE`, `COMMUNICATION_ENABLED`, `EMAIL_ENABLED`,
`WHATSAPP_ENABLED`, `SMS_ENABLED`, `VOICE_ENABLED`. Live eSampark upload
requires **both** `REAL_UPLOAD_ENABLED=true` **and**
`ESAMPARK_LIVE_TEST_MODE=true`. Leave them off for normal operation.

## 9. Test it

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt   # test extras (pytest, httpx) - one time
python -m pytest tests/ --ignore=tests/benchmark_ocr.py -q
```

(~378 tests, ~2–3 minutes; Node.js is needed for the Copy Details JS-harness
test — GitHub runners and a normal Windows install both have Node.)

## 10. How to continue development

- Architecture: `docs/ARCHITECTURE.md`
- Developer guide: `docs/DEVELOPMENT.md`
- Business rules: `docs/BUSINESS_RULES.md`
- Testing guide: `docs/TESTING.md`
- Troubleshooting: `docs/TROUBLESHOOTING.md`
- Project history & decisions: `docs/PROJECT_HISTORY.md`

Branches: work on `feature/*`, open a PR, CI runs on it, merge to `master`.
Never commit `.env`, DB files, generated workbooks, backups, or candidate
documents.

## 11. Future EXE packaging

Not built yet — documented `TODO` in `.github/workflows/release.yml`. The
release workflow (manual trigger) runs tests, then builds a **source** archive.
A Windows `PyInstaller` EXE build is the intended next step; verify it on a
real Windows machine before relying on it (`docs/CI_CD.md` → "Future windows
EXE").

## 12. How CI works

`.github/workflows/ci.yml` runs on every push/PR on `ubuntu-latest` and
`windows-latest` (Python 3.12): install deps → import/route checks → focused
regression + facility + persistence + Excel + security tests → JS syntax. No
real credentials or candidate data are required. Future **CD** = validated
release packaging (source / EXE / GitHub Release) — not automatic eSampark
submission.

## 13. Release status

**READY TO RETIRE VM** — see `docs/VM_RETIREMENT_CHECKLIST.md` (all items
checked) and `docs/BACKUP_MANIFEST.md` for the exact handoff record. The final
tag `teamhr-final-v1.0.0` is pushed to GitHub; `origin/master` is synchronized.