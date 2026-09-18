# TeamHR Automation

Local recruitment onboarding application for Windows.

## Tech Stack

- Python 3.12 / FastAPI
- Jinja2 HTML templates
- Vanilla JavaScript + CSS
- SQLite
- RapidFuzz
- openpyxl (Excel generation)

## Setup

Windows (recommended — see `docs/WINDOWS_QUICK_START.md`):

```powershell
cd C:\TeamHR-Automation
.\scripts\setup_windows.ps1
.\scripts\Start-TeamHR.bat
```

Manual setup:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 in your browser.

## Project Structure

```
TeamHR-Automation/
  app/
    main.py            # FastAPI entry point
    generation.py      # Excel workbook generation
    rules.py           # Business rules / normalization
    master_data.py     # Official masters (designations, facilities)
    database.py        # SQLite persistence
    templates/         # Jinja2 pages
    static/            # CSS / JS
  data/
    masters/           # Official Excel masters (HubName, Designation_Master, ...)
    templates/         # Excel Generation.xlsx template
  scripts/
    setup_windows.ps1  # One-time Windows setup (venv, deps, DB)
    Start-TeamHR.ps1   # Start server + open browser
    Start-TeamHR.bat   # Double-click launcher
    Stop-TeamHR.ps1    # Stop server
  tests/               # Test suite
  docs/                # Guides and handoff documentation
  requirements.txt
  .env.example
```

## Documentation

- `docs/WINDOWS_QUICK_START.md` — 5-minute Windows setup guide
- `docs/BUSINESS_RULES.md` — authoritative business rules
- `docs/INSTALL_WINDOWS.md` — full Windows install guide
- `docs/ADMIN_GUIDE.md`, `docs/USER_GUIDE.md`, `docs/CONFIGURATION.md`,
  `docs/SECURITY_PRIVACY.md`, `docs/FINAL_HANDOFF.md`

## Testing

```bash
for t in tests/test_*.py; do python "$t"; done
```

All tests are self-contained (throwaway DB + isolated config) and never modify
the production `data/config.json` or the local database.