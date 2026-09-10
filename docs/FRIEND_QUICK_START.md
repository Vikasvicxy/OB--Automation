# TeamHR Automation — Quick Start

## What it does

TeamHR Automation streamlines HR onboarding for Flipkart/Myntra Last Mile and First Mile operations. It:

1. **Pastes/uploads** candidate screenshots (Aadhaar, offer letters)
2. **Extracts** data via OCR and rules
3. **Resolves** entities, cost codes, hubs, roles
4. **Generates TWO Excel workbooks** per batch:
   - **Self Onboarding** (14 columns) — shared with candidates
   - **TeamHR Backend Mail** (16 columns) — internal, with full Aadhaar

## Prerequisites

- Python 3.10+
- Windows (tested on Windows 10/11)

## Setup

```bash
cd C:\TeamHR-Automation
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
# Double-click method:
scripts\Start-TeamHR.bat

# Or from PowerShell:
.\scripts\Start-TeamHR.ps1
```

Then open **http://localhost:8000** in your browser.

## Quick workflow

1. Go to **Smart Upload** (paste or upload screenshots)
2. Review extracted fields → fill Recruiter, DOJ, Gender, PIN
3. Click **Save & Approve** (saves to draft + approves in one step)
4. Go to **Batch Review** → click **Generate Onboarding Files**
5. Both workbooks appear in `data/generated/YYYY-MM-DD/`

## Key pages

| Page | URL | Purpose |
|------|-----|---------|
| Dashboard | `/` | Overview |
| Smart Upload | `/smart-upload` | Paste/upload screenshots |
| Manual Entry | `/manual-entry` | Enter data manually |
| Batch Review | `/batch-review` | Review candidates, generate workbooks |
| Generated Files | `/generated-files` | View/download generated workbooks |
| Settings | `/settings` | Output paths, Recruiter Profile |

## Safety flags (never enable in local dev)

| Flag | Default | Effect |
|------|---------|--------|
| `REAL_UPLOAD_ENABLED` | `false` | Live portal uploads |
| `ESAMPARK_LIVE_TEST_MODE` | `false` | eSampark test mode |
| `COMMUNICATION_ENABLED` | `false` | WhatsApp/SMS |
| `EMAIL_ENABLED` | `false` | Email sends |

## Output structure

```
data/generated/
  YYYY-MM-DD/
    uploads/          ← Self Onboarding workbooks
    backend_mail/     ← TeamHR Backend Mail workbooks
    results/          ← Portal results (when connected)
    errors/           ← Portal errors (when connected)
    TeamHR_Master_YYYY-MM-DD.xlsx  ← Daily operational master
```

## Running tests

```bash
# Quick sanity:
python tests/test_onboarding_pair.py
python tests/test_fixes.py

# Full suite:
python -m pytest tests/test_production_hardening.py -q
```

## Key conventions

- Full Aadhaar appears ONLY in the Backend Mail workbook and candidate detail page — never in filenames, folders, lists, logs, or the Self Onboarding workbook.
- DOJ must be explicitly entered and reviewed (never confused with DOB).
- Gender comes from Aadhaar only (manual entry only — OCR not yet verified for new fields).
- PIN must be 6 digits; Father Name from S/O/D/O/C/O prefixes only.
- Recruiter Profile is set once in Settings and used as default for new candidates.

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues.
