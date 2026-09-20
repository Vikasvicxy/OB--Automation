# TeamHR Automation — Backup Manifest

Record of where everything lives and what has been preserved for handoff, plus
recommendations for ongoing backups.

> No passwords, tokens, or credentials are listed in this document.

---

## 1. Primary backup: GitHub repository

| Item | Value |
|------|-------|
| Repository | <https://github.com/Vikasvicxy/OB--Automation.git> |
| Branch | `master` |
| Final commit | `722c500` — "Finalize TeamHR documentation CI and Windows handoff" (tag commit) |
| Final tag | `teamhr-final-v1.0.0` |
| Push status | pushed; local `HEAD == origin/master` (verified) |

The repository holds the complete application source, tests, documentation,
clean runtime masters/templates, CI workflows, and `.env.example`. A fresh
`git clone` reproduces the entire project.

## 2. Files that MUST be preserved (in Git)

| Path | Why |
|------|-----|
| `data/masters/HubName.xlsx` | Facility master (256 rows) — required at runtime |
| `data/masters/Designation_Master.xlsx` | Role/designation master — required at runtime |
| `data/masters/Facility_Master.xlsx` | `_PL` pickup reference |
| `data/templates/Excel Generation.xlsx` | Generation template (OB Format + Mail Format) |
| `data/config.json` | Runtime config (empty output dir = portable) |
| `app/` | All application source |
| `tests/` | Full automated test suite |
| `scripts/` | Windows setup/start/stop + dev tooling |
| `docs/` | Full documentation set |
| `.github/workflows/` | CI (`ci.yml`) + release workflow |
| `requirements.txt`, `README.md`, `CHANGELOG.md`, `.env.example`, `.gitignore` | Project essentials |

## 3. Runtime data (NOT in Git — live only on the machine)

Runtime candidate data must be backed up separately because it is gitignored:

| Path | Contents | Backup advice |
|------|----------|---------------|
| `data/database/teamhr.db` | Candidates, batches, drafts, audit | Copy after each working day; or use the Backup page |
| `data/generated/` | Generated workbooks | Copy what you need; regenerable from the DB, but keep the original files |
| `data/backups/` | Application backups (zip) | Keep a copy off-device |
| `data/uploads/` | Uploaded candidate documents | Treat as PII — encrypted storage only |
| `data/portal/` | Playwright auth state | Treat as PII — usually not needed to keep |

Use the in-app **Backup** page (creates an allow-listed zip and verifies it) for
routine backups. It never includes raw Aadhaar images, browser state, logs, or
secrets by design.

## 4. Release artifacts (built from source)

| Artifact | Location | Regenerable |
|----------|----------|-------------|
| Clean Windows source folder | `release/TeamHR-Windows-Source/` | yes — from `git clone` |
| Windows source ZIP | `release/TeamHR-Windows-Source.zip` | yes — `python .github/workflows/build_source_archive.py` |
| Word project document | `docs/TeamHR_Automation_Project_Documentation.docx` | yes — `python scripts/build_docx.py` |

These are built outputs and are gitignored; the builders that regenerate them
are committed.

## 5. Documentation set (all in Git, under `docs/`)

- README · PROJECT_INVENTORY · BUSINESS_RULES · ARCHITECTURE · PROJECT_HISTORY
- WINDOWS_QUICK_START · INSTALL_WINDOWS · DEVELOPMENT · TESTING ·
  TROUBLESHOOTING · CI_CD · FINAL_HANDOFF · BACKUP_MANIFEST ·
  VM_RETIREMENT_CHECKLIST
- ADMIN_GUIDE · USER_GUIDE · CONFIGURATION · SECURITY_PRIVACY ·
  COMMUNICATIONS · RELEASE_CHECKLIST · FRIEND_QUICK_START ·
  DEVELOPER_HANDOFF · FINAL_REPORT · OPEN_SOURCE_RESEARCH
- `TeamHR_Automation_Project_Documentation.docx` (editable Word document)

## 6. CI / release workflows (in Git)

- `.github/workflows/ci.yml` — Linux + Windows CI, Python 3.12.
- `.github/workflows/release.yml` — manual release packaging.
- `.github/workflows/build_source_archive.py` — zip builder used by release.

## 7. Recommended Python version

**Python 3.12** (3.10–3.13 expected to work). Dependencies are pinned in
`requirements.txt`.

## 8. Final handoff record

- **Date of final handoff:** 20 September 2026.
- **Final commit:** `722c500` ("Finalize TeamHR documentation CI and Windows
  handoff"), pushed to `master`; local `HEAD == origin/master` (verified 20 Sep 2026).
- **Final stable tag:** `teamhr-final-v1.0.0`
  (message: *TeamHR final documented Windows-ready source before VM retirement*).
- **Branch:** `master` (all commits pushed; working tree clean).
- **Fresh-install validation:** passed on a clean directory with a fresh venv
  (no dependency on `/home/azureuser`, the VM, or developer home dirs).
- **Known history note:** a pre-sanitization version of
  `data/templates/Excel Generation.xlsx` (with real sample rows) exists in
  early Git history; it was not rewritten. See `docs/SECURITY_PRIVACY.md`.

## 9. On-going backup recommendation

For each operating day:

1. In-app: **Backup** page → Create backup → Verify.
2. Copy `data/backups/<latest>.zip` and `data/database/teamhr.db` to a second
   device / encrypted location.
3. Keep generated `Onboarding_*.xlsx` files separately if they are needed for
   business records.

For code changes: push to GitHub (`git push origin master`), so the repository
remains the durable backup of the source.