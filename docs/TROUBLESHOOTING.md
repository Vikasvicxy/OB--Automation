# TeamHR Automation — Troubleshooting Guide

Common issues, their causes, and exact fixes. Windows commands are shown for
operators; Linux commands where noted are for development machines.

---

## 1. Port 8000 already in use

**Symptom:** `[Errno 98] Address already in use` (Linux) or
`OSError: [WinError 10048]` (Windows), or `bind() to 127.0.0.1:8000 failed`.

**Windows — find and kill the process:**

```powershell
Get-NetTCPConnection -LocalPort 8000 | Select-Object LocalAddress, LocalPort, OwningProcess
Get-Process -Id <PID> | Stop-Process -Force
```

or with classic `netstat`:

```cmd
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

**Linux — find and kill the process:**

```bash
ss -ltnp | grep :8000      # or: lsof -i :8000
kill <PID>
```

**Fix (simplest):** start the app on another port:

```powershell
.\scripts\Start-TeamHR.ps1 -Port 8001
# or directly
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Then open http://127.0.0.1:8001.

## 2. PowerShell blocks the scripts (execution policy)

**Symptom:** "Running scripts is disabled on this system" when running
`.\\scripts\\setup_windows.ps1` or `Start-TeamHR.ps1`.

**Fix:**

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Close and reopen the terminal, then retry. This applies to the current user
only and is the standard, safe setting.

## 3. `pip.exe` NativeCommandError / "pip is not recognized"

**Symptom:** PowerShell prints `NativeCommandError` or a red error when running
`pip install ...`, or `pip` is not found on PATH.

**Cause:** Global `pip.exe` on PATH is a different Python's pip, or pip is not
exposed. The project has pinned, venv-bound install scripts.

**Fix — always use the venv-bound Python module form:**

```powershell
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

(Or activate first: `.\venv\Scripts\Activate.ps1`, then
`python -m pip install -r requirements.txt`.)

Avoid bare `pip` / `pip.exe` entirely. Reports like
`NativeCommandError` come from PowerShell invoking pip.exe incorrectly; the
`python -m pip` form avoids that class of error.

## 4. App starts but buttons do nothing

**Symptom:** The dashboard loads but clicks have no effect — no toasts, no
navigation, nothing.

**Fix — check the browser JS console:**

1. Press **F12** (DevTools) → **Console** tab.
2. Look for red errors (e.g. `TypeError`, `ReferenceError`, failed fetch).
3. Common causes and fixes:
   - A stale browser cache of old JS → hard-refresh with **Ctrl+F5**.
   - An extension blocking scripts → test in an InPrivate/Incognito window.
   - An API path answered non-200 → check the URL shown in the console and the
     server log (`data\logs\server_error.log`).
   - If the Smart Upload page controls are dead, ensure the page's inline
     script loaded (no `Uncaught SyntaxError` in the console).

## 5. Stale draft keeps coming back

**Symptom:** An old partly-filled form reappears; edits keep reverting, or a
candidate you don't want keeps showing in drafts.

**Fix — Resume or Discard the draft:**

- On New Onboarding / Manual Entry, the draft banner offers **Resume**
  (continue where you left off) and **Discard** (delete the draft).
- Discard then start a new session if you want a clean slate.
- Drafts are per-session/persisted; discarding is the intended way to clear
  them. Check `POST /api/drafts/discard` in the UI actions if the banner is
  hidden.

## 6. Wrong hub / facility selected

**Symptom:** The inferred facility or cost code is not the one the candidate
actually belongs to (e.g. an LM hub auto-filled when the candidate is FM).

**Cause:** OCR inferred LM/FM from the document text. Inference is a guess and
can be stale.

**Fix — manually select the facility:**

- Use the searchable **Facility** dropdown in New Onboarding. It searches the
  **complete master** (never pre-filtered by the guess), so you can pick any
  hub — including an `_PL` (Pickup/FM) row when the inference said LM.
- Selecting a facility **overrides** the OCR LM/FM guess and updates
  location, cost code, facility type, and the role list.
- If the list does not show what you expect, verify the master (see section 8).

## 7. Role dropdown looks wrong / Prexo missing

- Roles come from `Designation_Master.xlsx` per cost code. **Prexo Delivery
  Executive is intentionally not offered under 8751 (Myntra LM)** — that is a
  business rule, not a bug.
- If a designation is missing entirely, add it via **Admin → Masters → Roles**
  (SQLite override) or update the Excel master, then reload masters.

## 8. Missing master / template

**Symptom:** Facility search is empty, generation fails with a missing-file
error, health shows master/template missing.

**Fix — verify runtime assets:**

| Asset | Path | Check |
|-------|------|-------|
| Facility master | `data\masters\HubName.xlsx` | exists, has ≥256 facility rows (FACILITY / LOCATION / FACILITY NAME) |
| Role master | `data\masters\Designation_Master.xlsx` | exists (DESIGNATION / COST CODE / PREFIX) |
| Generation template | `data\templates\Excel Generation.xlsx` | exists, has `OB Format` (13 cols) + `Mail Format` (14 cols) sheets |
| Config | `data\config.json` | optional; empty `output_base_dir` = project-relative default |

These files are tracked in Git — a clean clone ships them. If they are missing,
you are either in a partial copy or they were deleted. Restore from the
repository (or from a backup) rather than recreating them by hand.

After fixing or importing masters, use **Settings/Admin → Reload Masters** (or
restart the server) so in-memory lookups refresh. Generation also needs the
output folder to exist; it is created automatically under
`data\generated\...`.

## 9. Excel generation issue (template / master paths)

**Symptom:** "Template … not found", empty workbook, or candidate rows missing.

**Checks / fixes:**

1. Confirm `data\templates\Excel Generation.xlsx` exists (see section 8).
2. Confirm the candidates you selected are **Ready** — Draft and
   Needs Attention candidates are excluded by design (they are reported on the
   generate dialog).
3. Confirm the Mail-format fields are complete: DOJ, Name, Mobile, Designation,
   Branch, State, Net Salary, Aadhaar, DOB, Address, Pin Code, Gender. Missing
   any blocks generation **rather than guessing**.
4. Confirm the output folder setting: **Settings → Excel Output** shows a
   writable directory (default `<project>\data\generated`).
5. If an earlier run produced a corrupt file, the next generation writes a new
   timestamped file; it never overwrites an existing workbook.

## 10. OCR incorrect / fields unreadable

**Symptom:** Smart Upload shows low confidence, wrong name/DOB/facility, or
empty fields.

**Fixes:**

1. **Manual review takes precedence.** All extracted fields are proposals —
   edit any field, then Save & Approve. If a field arrives with wrong data,
   correct it in the form; approved values are what generation uses.
2. Image quality: Aadhaar photos must be clear, straight, and well lit; JPG /
   PNG / PDF supported.
3. Check the Health page → **OCR Backend** card (should be **OK**).
4. Enable OCR debug view if needed (`OCR_DEBUG_VIEW=true`, then
   `/debug/ocr`).
5. If extraction regressed after a code change, run the OCR tests:
   `python -m pytest tests/test_layout_parser.py tests/test_ramesh_fixture.py -q`.

## 11. DOB comes from the wrong date

**Symptom:** DOB look wrongs (e.g. shows the Aadhaar *issue date*).

**Rule:** the **Issue Date must never replace DOB**. The parser only accepts
DOB-labelled dates and applies a confidence threshold; below threshold the
field is left for review. If you see an issue date in DOB, correct it manually.
This is covered by regression tests (`test_ramesh_fixture.py`,
`test_layout_parser.py`).

## 12. Copy Details / clipboard

**Symptom:** Copy Details button copies nothing, or copies into one cell.

- Use the **Copy Details** button (6 values, TAB-separated) — paste into Excel
  to fill one row. **Copy With Headers** prepends the labels.
- If the browser blocks clipboard access (older browsers/plain HTTP), the page
  falls back to the legacy copy path; if that also fails, re-copy from the
  fields manually.
- The order is fixed: `Aadhar No · DOB · Fathers Name · Address · Pin Code ·
  Gender`.

## 13. Backups

**Symptom:** Backup verification fails.

- `manifest.json missing` → incomplete backup; create a new one.
- `checksum mismatch` → file changed after backup; create a new backup.
- `restored db integrity: error` → DB corrupt; restore from the most recent
  valid backup (a pre-restore backup is created automatically).

## 14. Playwright / portal

- Portal automation needs Chromium:
  `.\venv\Scripts\python.exe -m playwright install chromium`.
- OCR, Excel generation, and everything else work without it.
- Credentials for eSampark come only from `.env`; never put them in committed
  files.
- Live upload stays **locked** unless `REAL_UPLOAD_ENABLED=true` and
  `ESAMPARK_LIVE_TEST_MODE=true` are both set.

## 15. Logs & diagnostics

- Server logs: `data\logs\server.log` (stdout) and `data\logs\server_error.log`
  (errors/tracebacks).
- Structured logs + diagnostics: `GET /api/diagnostics/logs` and
  `GET /api/diagnostics/bundle` (safe, non-secret).
- `/health` shows feature flags, masters, OCR backend, DB, and readiness
  without leaking secrets.