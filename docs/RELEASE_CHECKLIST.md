# Release Checklist

Before any release, verify each item. All must pass.

## Automated Tests

- [ ] All tests green: `pytest tests/ -v`
- [ ] No test failures or errors

## Database

- [ ] DB migration test: `init_db()` runs cleanly on a fresh database
- [ ] DB migration test: `init_db()` runs cleanly on an existing database (idempotent)

## PII

- [ ] PII leak test: search, pipeline, data quality, and export endpoints never return full Aadhaar or full address
- [ ] PII leak test: logs never contain Aadhaar numbers or full addresses
- [ ] PII leak test: edit history never stores Aadhaar or address values

## Backup

- [ ] Backup created successfully
- [ ] Backup verified as valid (checksums, integrity check, required tables)
- [ ] Restore round-trip: create backup → restore → verify restored database matches
- [ ] Pre-restore backup is created automatically during restore
- [ ] Restore blocked when live upload flags are enabled

## Masters

- [ ] Masters loaded: Designation Master Excel parsed without errors
- [ ] Masters loaded: Facility Master Excel parsed without errors
- [ ] Effective view merge: Excel + Admin records produce correct effective list
- [ ] Role resolution: all cost codes resolve valid roles
- [ ] Hub resolution: all cost codes resolve valid hubs

## OCR

- [ ] OCR health: RapidOCR backend initializes without errors
- [ ] OCR extraction: test Aadhaar document extracts name, DOB, Aadhaar number, address
- [ ] OCR extraction: test screenshot extracts mobile, role, hub, salary

## Portal

- [ ] Portal flags false: `REAL_UPLOAD_ENABLED=false`
- [ ] Portal flags false: `ESAMPARK_LIVE_TEST_MODE=false`
- [ ] Live upload safety shows LOCKED on health page
- [ ] Portal selectors configured (UPLOAD_STATUS_MARKERS present)

## Communications

- [ ] Communications false: `COMMUNICATION_ENABLED=false`
- [ ] All channel flags false: WhatsApp, Email, SMS, Voice
- [ ] All providers return dry-run or not-implemented (no real external calls)

## Git

- [ ] Git clean: `git status` shows no uncommitted changes
- [ ] No secrets in committed files (`.env` is gitignored)
- [ ] No generated data in committed files (`data/database/`, `data/generated/`, `data/backups/` are gitignored)

## Version

- [ ] Version set: `APP_VERSION` in `app/config.py` matches release version
- [ ] Version shown in sidebar (`app/templates/partials/_sidebar.html`)
- [ ] Version embedded in backup manifest (`app/backup_service.py`)
- [ ] Release readiness page shows an allowed status string (`GET /api/release/readiness`)
- [ ] Version tag created: `git tag teamhr-local-v<version>`

## Health

- [ ] Health endpoint: `GET /api/health` returns `"overall": "ok"`
- [ ] All health cards show OK or WARNING (no ERROR)
- [ ] Disk space available (at least 1 GB free)

## Release Readiness

- [ ] `GET /api/release/readiness` returns one of the four allowed status strings
- [ ] All automated release gates pass (`gates_passed == gates_total`)
- [ ] No auto-assertion of controlled-local-trial / eSampark readiness without operator decision
- [ ] Manual UAT Critical set (31 tests) is not auto-marked PASS

## Final

- [ ] Backup created as release artifact
- [ ] Release notes written
