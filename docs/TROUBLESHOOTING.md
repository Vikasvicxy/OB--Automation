# Troubleshooting Guide

## App Won't Start

**Symptom**: Server exits immediately or shows an error on startup.

**Checks**:
1. Python version: `python --version` — must be 3.10+
2. Virtual environment activated: `.\venv\Scripts\Activate.ps1`
3. Dependencies installed: `pip install -r requirements.txt`
4. Port available: try a different port with `--port 8001`
5. Check `data\logs\server_error.log` for the traceback

**Fix**: Run `.\scripts\setup_windows.ps1` to re-create the environment.

## Port in Use

**Symptom**: `Address already in use` error.

**Fix**:
```powershell
# Find what's using the port
Get-NetTCPConnection -LocalPort 8000

# Stop the process
Stop-Process -Id <PID> -Force
```

Or start on a different port:
```powershell
.\scripts\Start-TeamHR.ps1 -Port 8001
```

## DB Errors

**Symptom**: `database is locked`, `disk I/O error`, or missing table errors.

**Fix**:
1. Stop the server
2. Check `data\database\teamhr.db` exists and is not zero-byte
3. Run integrity check: `python -c "import sqlite3; c=sqlite3.connect('data/database/teamhr.db'); print(c.execute('PRAGMA integrity_check').fetchone())"`
4. If corrupted, delete `teamhr.db` and restart (data will be lost)
5. If locked, ensure no other process is using the file

## OCR Fails

**Symptom**: Smart Upload shows "Could not confidently read this document" or extraction returns empty fields.

**Checks**:
1. OCR backend available: check Health page — OCR Backend card should show OK
2. Image quality: Aadhaar photos must be clear and readable
3. File format: supports JPG, PNG, PDF

**Fix**:
```powershell
# Verify RapidOCR is installed
pip show rapidocr-onnxruntime

# Reinstall if needed
pip install rapidocr-onnxruntime --force-reinstall
```

If using PaddleOCR: `pip install paddleocr paddlepaddle`

## Document Not Detected as Aadhaar

**Symptom**: Uploaded Aadhaar is classified as "Unknown" or "Screenshot".

**Cause**: The OCR text must contain keywords like "aadhaar", "uidai", "unique identification", or "government of india" to be classified as Aadhaar.

**Fix**:
- Ensure the full Aadhaar card is visible in the image
- Try a higher resolution scan/photo
- Use the OCR Debug View (`/debug/ocr` with `OCR_DEBUG_VIEW=true`) to inspect what text was extracted

## Wrong Hub/Role Resolved

**Symptom**: The resolver picks the wrong cost code, role, or facility.

**Checks**:
1. Verify the hub name exists in the master: go to Settings → Master Data
2. Check the facility classification rules in `app/rules.py`:
   - Contains "MYNTRA" → Myntra LM (8751)
   - Ends with `_PL` → Flipkart FM (4441)
   - Otherwise → Flipkart LM (4421)
3. Check role aliases: "biker" → Delivery Executive, "tl" → Team Leader, etc.

**Fix**: If the hub or role is missing from the master, add it via Admin Master Data or update the Excel master file and reload.

## Excel Generation Fails

**Symptom**: "Self Onboarding Template is not configured" or generation returns errors.

**Checks**:
1. Template file exists: `data\masters\Self_Onboarding_Template.xlsx`
2. Output directory is configured: check Settings → Excel Output
3. Candidates have status "Ready" (Draft and Needs Attention are excluded)

**Fix**:
1. Place `Self_Onboarding_Template.xlsx` in `data\masters\`
2. Create the output directory if missing: `New-Item -ItemType Directory -Force -Path data\generated`
3. Ensure candidates are marked as Ready (approve them from the review page)

## Playwright Browser Missing

**Symptom**: `playwright._impl._errors.Error: Browser channel not found` or similar.

**Fix**:
```powershell
.\venv\Scripts\Activate.ps1
playwright install chromium
```

This only affects portal automation. OCR and all other features work without Playwright.

## Portal Selector Changed

**Symptom**: Portal upload fails with "element not found" or hangs on a step.

**Cause**: The eSampark portal DOM changed.

**Fix**:
1. Open `app\portal\selectors.py`
2. Update the affected selector constants
3. Test with: `python scripts\verify_real_portal.py`
4. Run portal tests: `pytest tests/test_portal.py -v`

If the portal layout changed structurally, also check `app\portal\verified_selectors.py`.

## Backup Verify Fails

**Symptom**: Backup verification returns "Invalid" with a specific error.

**Common errors**:
- `manifest.json missing` — corrupted or incomplete backup
- `checksum mismatch` — file was modified after backup
- `restored db integrity: error` — database file is corrupted
- `required tables missing` — backup from an older schema version

**Fix**:
1. Create a new backup
2. If the database is corrupted, restore from the most recent valid backup
3. If restoring, the system automatically creates a pre-restore backup first

## Logs

Application logs are written to `data\logs\`:
- `server.log` — stdout from the server process
- `server_error.log` — stderr (errors and tracebacks)

Structured logs with categories (APP, OCR, RESOLVER, MASTER, EXCEL, PORTAL, BACKUP, COMMUNICATION, DATABASE) are available via `/api/diagnostics/logs`.

## Diagnostic Bundle

For support, generate a diagnostic bundle:
```
GET /api/diagnostics/bundle
```

Returns safe, non-secret system status information (feature flags, health, config, recent logs).
