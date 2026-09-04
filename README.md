# TeamHR Automation

Local recruitment onboarding application for Windows.

## Tech Stack

- Python 3 / FastAPI
- Jinja2 HTML templates
- Vanilla JavaScript + CSS
- SQLite (planned)
- RapidFuzz (planned)
- openpyxl (planned)

## Setup

```powershell
cd C:\TeamHR-Automation
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 in your browser.

## Project Structure

```
TeamHR-Automation/
  app/
    main.py            # FastAPI entry point
    templates/
      index.html        # Dashboard page
    static/
      style.css         # Styles
      app.js            # Client scripts
  requirements.txt
  README.md
```
