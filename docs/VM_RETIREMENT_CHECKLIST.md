# TeamHR Automation — VM Retirement Checklist

Checklist for retiring the Ubuntu development VM. Only recommend deleting/
stopping the VM when **all** required items are checked.

Verification performed during the final archive pass (20 September 2026).

---

## Checklist

- [x] Latest commit pushed
- [x] Final tag pushed (`teamhr-final-v1.0.0`)
- [x] `data/masters/HubName.xlsx` in Git
- [x] `data/templates/Excel Generation.xlsx` in Git (sanitized — headers only)
- [x] README complete (GitHub landing page)
- [x] Word project document created
- [x] Business rules documented
- [x] Architecture documented
- [x] Windows setup documented
- [x] Troubleshooting documented
- [x] CI workflow committed
- [x] Source release created
- [x] Release ZIP created
- [x] No secrets committed
- [x] No candidate PII committed (new content verified clean; historical note in
  `docs/SECURITY_PRIVACY.md`)
- [x] `origin/master` synchronized (fetch verified; `HEAD == origin/master`)
- [x] Clean working tree (`git status` clean after final commit)
- [x] GitHub clone / fresh-install validated (clean dir + fresh venv smoke)

---

## How each check was verified

| Check | Evidence |
|-------|----------|
| Commit pushed | `git fetch origin` → `HEAD == origin/master` = `722c500` |
| Tag pushed | `git tag --list` shows `teamhr-final-v1.0.0` (points at `722c500`); pushed to origin |
| HubName in Git | `git ls-files data/masters/HubName.xlsx` |
| Excel Generation in Git | `git ls-files "data/templates/Excel Generation.xlsx"`; file contains header rows only |
| README | rewritten as the main landing page (15+ sections) |
| Word doc | `docs/TeamHR_Automation_Project_Documentation.docx` (editable, 43 sections + 7 appendices) |
| Business rules | `docs/BUSINESS_RULES.md` (verified against code/tests) |
| Architecture | `docs/ARCHITECTURE.md` (ASCII diagram + flows) |
| Windows setup | `docs/WINDOWS_QUICK_START.md` + `scripts/` |
| Troubleshooting | `docs/TROUBLESHOOTING.md` |
| CI workflow | `.github/workflows/ci.yml` (Linux + Windows) |
| Source release | `release/TeamHR-Windows-Source/` |
| Release ZIP | `release/TeamHR-Windows-Source.zip` (opened + inspected, no secrets/PII) |
| No secrets | audit of tracked files (keys/tokens/passwords/.env) — none |
| No candidate PII | audit; sample fixtures are synthetic; pre-sanitization template disclosed (not rewritten) |
| origin in sync | `git rev-parse HEAD` == `git rev-parse origin/master` |
| Clean tree | `git status` → "nothing to commit, working tree clean" |
| Fresh install | smoke test in `/tmp` clean clone + fresh venv, masters/template load, Excel generation — passed |

---

## What to do after this checklist

1. Keep neither source nor data only on this VM: everything durable is in
   GitHub; runtime candidate data backups live in `data/backups/` and should be
   copied off-VM before shutdown.
2. Copy the final release ZIP off the VM if a physical copy is wanted:
   `release/TeamHR-Windows-Source.zip`.
3. Only then decommission the VM (stop → snapshot → delete as per your
   process).

Do **not** delete the VM until items above are confirmed.