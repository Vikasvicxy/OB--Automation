# TeamHR Automation — Friend Quick Start

## What it does

TeamHR Automation turns candidate screenshots (Aadhaar + recruiter details) into **TWO Excel workbooks**:

1. **Self Onboarding** (14 columns) — share/upload this to eSampark
2. **TeamHR Backend Mail** (16 columns) — email this to the backend team

You do NOT need to know anything about batches, admin, or portals to use it.

## First-time setup (once)

1. Copy the **TeamHR-Friend** folder to your laptop (anywhere, e.g. `C:\TeamHR-Friend`)
2. Double-click `scripts\Setup-Windows.bat` or run in PowerShell:
   ```powershell
   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
   .\scripts\setup_windows.ps1
   ```
   This installs dependencies and creates a clean empty database.

## Start

Double-click **`scripts\Start-TeamHR.bat`** (or run `scripts\Start-TeamHR.ps1`).

Your browser opens **http://localhost:8000**.

## Normal daily workflow

1. **Set Recruiter Name** (only the first time):
   - Dashboard → **Settings** (sidebar, bottom)
   - Type your Recruiter Name → **Save**
   - It is used in every TeamHR Backend Mail workbook. Not hardcoded anywhere.

2. Dashboard → **+ New Onboarding** (Smart Upload)

3. **Paste** the Aadhaar screenshot AND any screenshot with recruiter details (offer letter / chat)

4. **Review every field** — OCR is not always perfect. Check in particular:
   - **Date of Joining** (DOJ) — often missing from screenshots
   - **Gender** — from Aadhaar
   - **PIN Code**
   - **Father Name**
   - **Mobile** — must be 10 digits, no leading zero
   - **Aadhaar** — 12 digits
   - **Salary** — displayed as marked in the offer

5. **Fill any missing fields** directly in the review form. If the review page shows a red
   "Missing: ..." note next to a candidate, click **Review** and fix exactly that field.

6. Click **Save & Approve** (one button — saves and marks the candidate ready)

7. Go to **Batch Review** (sidebar): candidates that are **Ready** can be generated.

8. Click **Generate Onboarding Files**.

   If any candidate is missing a required Backend field, the page shows **exactly which
   field** is missing (e.g. "Date of Joining required", "Gender required", "PIN Code required").
   Click **Review** next to that candidate, fix it, click Save & Approve, and return here.

9. Both workbooks are generated from the **same approved candidates**, in the **same order**,
   with the **same timestamp**:

   - `data\generated\YYYY-MM-DD\uploads\Self_Onboarding_YYYY-MM-DD_HH-MM-SS.xlsx`
   - `data\generated\YYYY-MM-DD\backend_mail\TeamHR_OB_YYYY-MM-DD_HH-MM-SS.xlsx`

10. On the result panel use **Open File** / **Open Folder** / **Copy Path** to find them.

11. **Self Onboarding** → manually upload the file to eSampark.

12. **TeamHR Backend Mail** → manually email the file to the backend team.

> Nothing is uploaded or emailed automatically.

## Where generated files are stored

```
data\
  generated\
    2026-09-10\                        ← date of generation
      uploads\                         ← Self Onboarding workbooks (.xlsx)
        Self_Onboarding_2026-09-10_14-30-05.xlsx
      backend_mail\                    ← TeamHR Backend Mail workbooks (.xlsx)
        TeamHR_OB_2026-09-10_14-30-05.xlsx
```

Each pair shares the same timestamp so they are instantly recognizable as a pair.

## Key pages

| Page | What it's for |
|------|---------------|
| Dashboard `/` | Start here — "+ New Onboarding" |
| Smart Upload `/smart-upload` | Paste/upload screenshots, review, save & approve |
| Manual Entry `/manual-entry` | Type data by hand (or fix a candidate) |
| Batch Review `/batch-review` | See Ready candidates, generate the two workbooks |
| Generated Files `/generated-files` | View/download all generated workbooks |
| Settings `/settings` | Set Recruiter Name once |

## Safety (always ON — do not change)

| Setting | Value |
|---------|-------|
| Real portal uploads | **OFF** |
| eSampark live mode | **OFF** |
| WhatsApp/SMS/Email/Voice | **OFF** |

All uploads and emails are done manually by you.

## Notes

- Full Aadhaar appears ONLY in the TeamHR Backend Mail workbook and the candidate detail
  page — never in filenames, folders, lists, logs, or the Self Onboarding workbook.
- If anything looks wrong, click **Review** on the candidate and correct it before generating.
- See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if the app does not start.