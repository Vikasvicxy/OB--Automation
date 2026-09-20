# TeamHR Automation — Windows Quick Start

A 5-minute guide to run TeamHR Automation on Windows 10/11.

**Requirements:** Python 3.12 (3.10–3.13 also works), PowerShell 5.1+.
Install Python from <https://www.python.org/downloads/> and tick
**"Add python.exe to PATH"**. You also need `git` if you clone the
repository (optional — you can copy the source folder instead).

---

## 1. Get the source

Clone the GitHub repository:

```powershell
git clone https://github.com/Vikasvicxy/OB--Automation.git
cd OB--Automation
```

Or unzip `TeamHR-Windows-Source.zip` / copy the source folder, then:

```powershell
cd C:\TeamHR-Automation
```

## 2. Create the virtual environment (one time)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` is pinned — Python 3.12 plus these versions is the tested
combination. Do **not** use bare `pip` if PowerShell complains
(`python -m pip ...` is the safe form).

Optional, only for the eSampark portal automation:

```powershell
python -m playwright install chromium
```

## 3. Start the app

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

(or use the one-click launcher `.\scripts\Start-TeamHR.bat`, which loads the
venv, installs deps the first time only, and opens the browser).

Open <http://127.0.0.1:8000> in your browser.

## 4. Stop the app

Press **Ctrl+C** in the server window, or:

```powershell
.\scripts\Stop-TeamHR.ps1
```

---

## Port 8000 already in use?

```powershell
Get-NetTCPConnection -LocalPort 8000 | Select-Object LocalAddress, LocalPort, OwningProcess
Get-Process -Id <PID> | Stop-Process -Force
# or just run on a different port:
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

## Verify it works

1. <http://127.0.0.1:8000/> — dashboard loads.
2. <http://127.0.0.1:8000/health> — health cards OK/WARNING (no ERROR).
3. **New Onboarding / Smart Upload** loads; Facility and Role searchable
   dropdowns work.
4. **All Candidates**, **Generated Files**, **Settings → Excel Output** load.

---

## Configuration (optional)

```powershell
Copy-Item .env.example .env
notepad .env
```

- Set `SECRET_KEY` to a real random value.
- Keep all six safety flags **off** (`REAL_UPLOAD_ENABLED`,
  `ESAMPARK_LIVE_TEST_MODE`, `COMMUNICATION_ENABLED`, `EMAIL_ENABLED`,
  `WHATSAPP_ENABLED`, `SMS_ENABLED`, `VOICE_ENABLED`) unless a specific
  feature is deliberately being switched on.
- Everything you need is described in `docs/CONFIGURATION.md`.

## Where data lives

All runtime data stays under the project `data\` folder:

| Path | Contents |
|------|----------|
| `data\masters\` | Master Excel files (ships with the app) |
| `data\templates\Excel Generation.xlsx` | Generation template (ships with the app) |
| `data\database\teamhr.db` | SQLite database (candidates) |
| `data\generated\` | Generated Excel workbooks |
| `data\backups\` | Backups |
| `data\logs\` | Server logs |

## Getting updates

```powershell
git pull origin master
python -m pip install -r requirements.txt   # re-sync dependencies if changed
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Back up `data\database\teamhr.db` (and any generated workbooks you need)
before pulling.

## Troubleshooting

- **"Python not found"** — reinstall Python, tick *Add to PATH*, reopen terminal.
- **Execution policy blocks scripts** —
  `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
- **Port 8000 busy** — use `Get-NetTCPConnection -LocalPort 8000` / run on 8001.
- **Browser not opening** — open <http://127.0.0.1:8000> manually.
- **pip errors** — always use `python -m pip ...` (venv-bound).
- More: `docs/TROUBLESHOOTING.md`, full install `docs/INSTALL_WINDOWS.md`.