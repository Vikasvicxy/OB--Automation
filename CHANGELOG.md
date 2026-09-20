# Changelog

All significant releases and changes for **TeamHR Automation**.

Versioning: semantic `MAJOR.MINOR.PATCH` (see `docs/CI_CD.md`).
Dates are approximate; the Git history in this repository is the authoritative
timeline. No exact dates are invented where none are recorded.

---

## [1.0.0] — 2026-09-20 — teamhr-final-v1.0.0 (final archive pass)

Final documented, CI-ready, Windows-handoff source before the development VM is
retired.

**Added**
- Full documentation set: `docs/PROJECT_INVENTORY.md`,
  `docs/PROJECT_HISTORY.md`, `docs/DEVELOPMENT.md`, `docs/TESTING.md`,
  `docs/CI_CD.md`, `docs/BACKUP_MANIFEST.md`,
  `docs/VM_RETIREMENT_CHECKLIST.md`; rewritten `README.md`, `ARCHITECTURE.md`,
  `FINAL_HANDOFF.md`, `WINDOWS_QUICK_START.md`, `TROUBLESHOOTING.md`.
- Editable Word project document
  `docs/TeamHR_Automation_Project_Documentation.docx`.
- GitHub Actions CI (`.github/workflows/ci.yml`) on `ubuntu-latest` and
  `windows-latest` (Python 3.12): install → import checks → focused regression
  (facility, persistence, Excel, security, template, review) → full
  cross-platform suite.
- Manual release workflow (`.github/workflows/release.yml`) + source-archive
  builder (`.github/workflows/build_source_archive.py`); future Windows EXE
  build documented as TODO (not claimed as supported).
- `CHANGELOG.md`.

**Changed**
- `.gitignore` extended (logs, caches, browser/auth state, build output,
  temporary candidate files).
- Clean Windows source release rebuilt under `release/`.

## [1.0.0-rc1] — 2026-09-18 — teamhr-windows-handoff-2026-09-18

First Windows handoff cut.

**Added**
- `docs/WINDOWS_QUICK_START.md`, `docs/BUSINESS_RULES.md`, final handoff docs.
- Windows setup/start/stop scripts; venv-bound `python -m pip` everywhere.
- Portable output config (`data/config.json` no longer pins a Windows path).

**Fixed / hardened**
- Sanitized `data/templates/Excel Generation.xlsx` (removed real candidate
  sample rows; headers + formatting preserved). Historical note: the
  PII-bearing version remains in earlier Git history (not rewritten).
- Windows path-traversal tests made platform-independent.

## [0.9.0] — 2026-09-18 — commit 792d3bc

**Full-master facility dropdown, readable LM names, copy-ready details**

- Facility search browses the complete `HubName.xlsx` master (never
  cost-code-filtered); `_PL` pickup rows searchable for any candidate.
- Readable LM display names from the master reference; duplicate display names
  shown per explicit row.
- **Copy Details** block: exact TAB order
  `Aadhar No · DOB · Fathers Name · Address · Pin Code · Gender`; *Copy With
  Headers*; address collapsed to one line; plain-text + Excel-ready paste.
- Nelamangala LM/FM pair resolution verified.

## [0.8.0] — 2026-09-17 — commit 9461dcc

**New Onboarding UI polish + searchable controls**

- Searchable typeahead for Facility and Role dropdowns; debounced master
  search; duplicate-name feedback; cost-code roles with prefixes.

## [0.7.0] — 2026-09-17 — commit a0861e7

**Restore New Onboarding actions + OCR regression fix**

- Save Draft / Save & Approve / Resume / Discard restored.
- Real OCR regression fixed (verified against real Aadhaar documents).

## [0.6.0] — 2026-09-17 — commit 745a796

**Review persistence + official role dropdown**

- Draft persistence across navigation/reload; Resume/Discard flows.
- Official designation dropdown from `Designation_Master.xlsx`; **no Prexo
  under 8751** rule.

## [0.5.0] — 2026-09-16 — commit 9c40150

**Single workbook / two sheets**

- Consolidated two export files into **one workbook**: `OB Format` (13 cols)
  + `Mail Format` (14 cols) from the same approved candidates.
- Fixed onboarding OCR-to-facility mapping toward the master.

## [0.4.0] — 2026-09-10 — commits 1aa9208 / 459d09a

**Generate Self Onboarding and TeamHR backend workbooks**

- Two-file generation phase: Self Onboarding workbook + TeamHR backend mail
  workbook; daily master export with masked Aadhaar.

## [0.3.0] — 2026-09-08 — commit a7dc35b

**Release candidate validation and packaging**

- Release gates: DB integrity, masters, OCR backend, portal selectors,
  `DANGEROUS_FLAGS` detection, backup, health.
- Release checklist + packaging.

## [0.2.0] — 2026-09-08 — commit 215648c

**Production hardening**

- Ops features (follow-ups, issues, notifications, diagnostics), comm
  architecture (disabled by default), structured logging with PII redaction,
  security test suite.

## [0.1.0] — 2026-09-04 … 2026-09-07

**Foundation and core features**

- UI recovery baseline; dashboard, search, data-quality.
- Candidate detail + timeline/audit; batches + pipeline; backup/restore,
  health, draft recovery; admin master management; manual acceptance center.
- OCR architecture, resolver/validation layer, eSampark automation design,
  safety-flag gating.

---

Pre-commit development (unversioned): initial recruitment workflow, OCR
architecture, resolver/validation, facility/masters integration, eSampark
automation architecture, Windows-local focus. These predate the Git history
(see `docs/PROJECT_HISTORY.md`).