# TeamHR Automation — Windows Quick Start

A 5-minute guide to run TeamHR Automation on a Windows machine.

**Requirements:** Windows 10/11, PowerShell 5.1+, and Python 3.12 (3.10–3.13 works).
Install Python from <https://www.python.org/downloads/> and tick **"Add python.exe to PATH"**.

---

## 1. Get the source

Copy the `TeamHR-Windows-Source` folder onto the Windows machine (or clone the
GitHub repo):

```powershell
cd C:\TeamHR-Automation
```

## 2. Run setup (one time)

```powershell
.\scripts\setup_windows.ps1
```

This creates `venv\`, installs the pinned dependencies, installs the Playwright
Chromium browser (portal automation), creates the `data\...` folders, and
initializes the SQLite database. Idempotent — safe to rerun.

You do not need a GitHub account, git, or any cloud service. Everything runs
locally.

Optional: skip the browser download with `-SkipBrowser` if portal automation
is not needed.

## 3. Start the app

```powershell
.\scripts\Start-TeamHR.bat
```

or

```powershell
.\scripts\Start-TeamHR.ps1
```

The browser opens automatically at <http://127.0.0.1:8000>. Dependencies are
installed only on the first start (marker file `data\.deps-installed`), so
every later start is fast.

## 4. Stop the app

```powershell
.\scripts\Stop-TeamHR.ps1
```

or press `Ctrl+C` in the server window.

---

## Verify it works

1. Open <http://127.0.0.1:8000/> — dashboard loads.
2. Open <http://127.0.0.1:8000/health> — all health cards OK/WARNING (no ERROR).
3. `Smart Upload` / `New Onboarding` loads; facility + role searchable dropdowns work.
4. `All Candidates`, `Generated Files`, and `Settings -> Excel Output` load.

---

## Configuration

Copy `.env.example` to `.env` once, then edit only what you need:

```powershell
Copy-Item .env.example .env
notepad .env
```

- `SECRET_KEY` — set a real random value.
- All safety flags (`REAL_UPLOAD_ENABLED`, `ESAMPARK_LIVE_TEST_MODE`,
  `COMMUNICATION_ENABLED`, `EMAIL_ENABLED`, `WHATSAPP_ENABLED`, `SMS_ENABLED`,
  `VOICE_ENABLED`) default to **off** and should stay off unless a specific
  feature is being switched on deliberately.
- See `docs/CONFIGURATION.md` for every option.

## Where data lives

All runtime data stays inside the project `data\` folder:

| Path | Contents |
|------|----------|
| `data\database\teamhr.db` | SQLite database (candidates, batches) |
| `data\masters\` | Official Excel masters (shipped) |
| `data\templates\` | Excel Generation template (shipped) |
| `data\generated\` | Generated Excel workbooks |
| `data\logs\` | Server logs |
| `data\backups\` | Backups |
| `data\portal\` | Portal auth state + debug screenshots |

The default output base folder is `<project>\data\generated`. It can be changed
from **Settings -> Excel Output** and is stored in `data\config.json`.

## Troubleshooting

- **"Python not found"** — reinstall Python, tick *Add to PATH*, reopen terminal.
- **Execution policy blocks scripts** —
  `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
- **Port 8000 busy** — `.\scripts\Start-TeamHR.ps1 -Port 8001`
- **Browser not opening** — type <http://127.0.0.1:8000> manually.
- See `docs/INSTALL_WINDOWS.md` and `docs/TROUBLESHOOTING.md` for more.