# Windows Installation Guide

## Prerequisites

- **Python 3.10+** — download from [python.org](https://www.python.org/downloads/)
- **pip** — comes bundled with Python
- **Windows 10/11** — PowerShell 5.1+ or PowerShell 7+

Verify Python is installed:

```powershell
python --version
```

## Quick Setup (Recommended)

Run the automated setup script:

```powershell
cd C:\TeamHR-Automation
.\scripts\setup_windows.ps1
```

This script:
1. Checks Python is available
2. Creates a virtual environment (`venv\`)
3. Installs all dependencies from `requirements.txt`
4. Installs the Playwright Chromium browser (for portal automation)
5. Creates required data directories
6. Initializes the SQLite database

To skip browser installation:

```powershell
.\scripts\setup_windows.ps1 -SkipBrowser
```

## Manual Setup Steps

If the setup script does not work, do each step manually:

```powershell
cd C:\TeamHR-Automation

# 1. Create virtual environment
python -m venv venv

# 2. Activate it
.\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright browser (only needed for portal automation)
playwright install chromium

# 5. Create data directories
New-Item -ItemType Directory -Force -Path data\database, data\generated, data\backups, data\uploads, data\logs, data\masters | Out-Null

# 6. Initialize the database
python -c "from app.database import init_db; init_db()"
```

## Starting the Application

**Option A: PowerShell script (recommended)**

```powershell
.\scripts\Start-TeamHR.ps1
```

Opens the browser automatically to `http://127.0.0.1:8000`.

**Option B: Batch file (double-click friendly)**

```
scripts\Start-TeamHR.bat
```

**Option C: Direct uvicorn command**

```powershell
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in your browser.

## Stopping the Application

```powershell
.\scripts\Stop-TeamHR.ps1
```

Or press `Ctrl+C` in the terminal running the server.

## Verify Installation

1. Start the app (see above).
2. Open `http://127.0.0.1:8000` — the Dashboard should load.
3. Open `http://127.0.0.1:8000/health` — all health cards should show OK or WARNING (not ERROR).

## Environment Configuration

Copy `.env.example` to `.env` and edit as needed:

```powershell
Copy-Item .env.example .env
notepad .env
```

See [CONFIGURATION.md](CONFIGURATION.md) for all available settings.

## Master Data Files

Place the official Excel master files in `data\masters\`:

- `Designation_Master.xlsx` — role/designation list per cost code
- `Facility_Master.xlsx` — hub/facility names with location codes
- `Self_Onboarding_Template.xlsx` — template used for Excel generation

The app loads these automatically on startup. Without them, the master status shows "Not Configured" but the app still runs.

## Common Issues and Fixes

### "Python not found"

Install Python 3.10+ from python.org. Make sure "Add Python to PATH" is checked during installation. Restart your terminal.

### "pip is not recognized"

```powershell
python -m ensurepip --upgrade
```

### Port 8000 already in use

Either stop the other process, or start on a different port:

```powershell
.\scripts\Start-TeamHR.ps1 -Port 8001
```

### Playwright browser missing

```powershell
.\venv\Scripts\Activate.ps1
playwright install chromium
```

### "ModuleNotFoundError" after install

Make sure the virtual environment is activated:

```powershell
.\venv\Scripts\Activate.ps1
```

### Database errors

The SQLite database is created at `data\database\teamhr.db`. If it becomes corrupted, delete it and the app will recreate it on next startup (data will be lost).

### PowerShell execution policy blocks scripts

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
