# TeamHR Automation — Project History & Decisions

A factual, technical history of how this project evolved, reconstructed from
the Git history, commit messages, and code evidence. No private conversation
content is included.

---

## 1. Origins (pre-commit development)

Before the first Git commit, the project was developed as a local recruitment
automation tool with these foundations:

- **Initial recruitment workflow** — a fast path to capture a candidate's
  details (name, mobile, Aadhaar, DOB, address, facility, role, salary) and
  turn them into onboarding spreadsheets that previously had to be typed by
  hand or copied between tools.
- **OCR architecture** — documents (Aadhaar card, WhatsApp screenshot/chat
  export) are read with **local** OCR (RapidOCR/ONNX, later a layout parser),
  so candidate documents never leave the machine.
- **Resolver / validation layer** — raw OCR text is resolved into structured
  fields by business rules and scored by evidence confidence, so the operator
  reviews rather than retypes.

These components predate the initial commit `e477959` ("Stable baseline after
UI recovery").

## 2. UI recovery incident (`e477959`, 2026-09-04)

The Git history begins with *"Stable baseline after UI recovery"*. The UI had
to be rebuilt/recovered, and a clean, stable baseline was established:

- **Why it matters:** the recovery produced a clean, verifiable foundation from
  which all later work is traceable.
- **Decision:** rebuild the UI on vanilla HTML/CSS/JS with server-rendered
  Jinja2 templates rather than introducing a frontend framework. This keeps the
  app simple, dependency-light, and easy to run fully offline on a work PC.

## 3. Core feature build-out (2026-09-04 → 2026-09-07)

A sequence of focused commits added the operational surface:

| Commit | What landed |
|--------|-------------|
| `50fb79b` | Candidate detail page, timeline and audit trail |
| `dc57a99` | Batch detail and candidate pipeline (kanban) |
| `3b97a32` | Backup / restore, health dashboard, draft recovery |
| `776e5d4` | Admin + candidate review experience polish |
| `b936f2e` | Manual acceptance test center |
| `e1b08df` | UI foundation: dashboard, search, data-quality views |

### Key decisions this phase

- **Single SQLite DB** (`data/database/teamhr.db`) — no DB server; auto-init on
  first request; easy to back up and move.
- **Candidate states** — `draft / needs_attention / needs_review / ready /
  generated` with explicit approval actions; nothing is auto-finalised.
- **PII minimisation by design** — full Aadhaar and full address are surfaced
  only on the candidate detail page; list views, search, pipeline, and exports
  mask or omit them.

## 4. Production hardening, comm architecture, ops (`215648c`, 2026-09-08)

- Operational features: follow-ups, issues, notifications, diagnostics,
  outbox, health, version endpoint.
- **Communication architecture** — a provider pattern
  (`app/communication.py`) for email / WhatsApp / SMS / voice, all of which are
  **dry-run and disabled by default** via `COMMUNICATION_ENABLED` and per-channel
  flags. No external channel is ever contacted unless explicitly switched on.
- **Security tests** — SQL injection, XSS, path traversal, malformed ZIPs,
  feature-flag guards, PII-leak checks integrated into the test suite.

## 5. Release candidate validation (`a7dc35b`, 2026-09-08)

- First release gate pass: master files, OCR backend, portal selectors,
  dangerous-flag detection, backup, health all checked by a readiness module
  (`app/release_readiness.py`, exposed at `/release`).
- Packaged and documented the release checklist.

## 6. eSampark automation architecture

Designed and implemented alongside the above (module `app/portal/`):

- **Playwright** drives the eSampark portal (`esampark.beeforce.in`) with LDAP
  login; **credentials come only from the environment** (`.env`) and are never
  stored in source, SQLite, logs, or generated files.
- Selector library (`selectors.py`, `verified_selectors.py`), upload service
  (`service.py`), and a *single-candidate* live-upload flow
  (`live_upload.py`).
- **Hard safety gate:** real upload requires **both**
  `REAL_UPLOAD_ENABLED=true` and `ESAMPARK_LIVE_TEST_MODE=true` (both default
  false). The live-upload page is locked unless both are set.

## 7. Admin / master management

- Admin-managed facilities and roles with CRUD, deactivate/activate, import
  preview → apply, and a change-history audit (`app/admin_master.py`,
  `data/masters/` Excel files as the base).
- **Decision:** the Excel masters stay read-only; admin edits land in SQLite
  and the *effective view* merges SQLite overrides over Excel. Excel files are
  never rewritten by the app.

## 8. Two-file generation phase (`1aa9208`, `459d09a`, 2026-09-10)

"Generate Self Onboarding and TeamHR backend workbooks".

- First production generation built **two separate workbooks**: the
  Self-Onboarding workbook and a TeamHR backend-mail workbook.
- **Decision:** full Aadhaar appears **only** in the backend/mail workbook;
  the daily master export uses a masked Aadhaar.

## 9. Change to a single workbook with two sheets (`9c40150`, 2026-09-16)

"Fix onboarding OCR facility mapping and two-sheet workbook".

- Consolidated the two files into **one workbook with two sheets**:
  - **OB Format** (13 columns) — the onboarding payload.
  - **Mail Format** (14 columns) — the backend/HR mail row.
- Switching to one file reduced copy/paste steps and kept the OB and Mail rows
  for each candidate aligned by row order.
- Same commit fixed OCR **facility mapping** toward the master.

## 10. Review persistence + official role dropdown (`745a796`, 2026-09-17)

- Fixed review-workflow persistence (drafts survive navigation/reload;
  Resume/Discard).
- Added the **official role/designation dropdown** backed by
  `Designation_Master.xlsx`, with the business rule that *Prexo Delivery
  Executive* is **not** offered under 8751 (Myntra LM).

## 11. Restore New Onboarding actions + OCR regression fix (`a0861e7`, 2026-09-17)

- Restored the Save Draft / Save & Approve / Resume / Discard actions in New
  Onboarding.
- Fixed a **real OCR regression** found by re-running OCR against real
  documents after the layout-parser work.

## 12. UI polish + searchable controls (`9461dcc`, 2026-09-17)

- Polished the New Onboarding UI; facility and role controls became
  searchable typeahead dropdowns.

## 13. HubName full dropdown + copy-ready details (`792d3bc`, 2026-09-18)

The two features that defined the final on-boarding UX:

- **Full-master facility dropdown** — facility search browses the **complete
  HubName.xlsx master**, never pre-filtered by the current LM/FM cost-code
  guess. LM-inferred candidates can pick an FM `_PL` hub and vice versa.
  Readable LM names (from the parenthesized reference) replaced raw codes;
  duplicate display names are shown per row so the exact master row can be
  picked.
- **Copy Details block** — copies the six personal fields to the clipboard in
  the exact TAB-separated order `Aadhar No · DOB · Fathers Name · Address ·
  Pin Code · Gender`, formatted for direct Excel paste. *Copy With Headers*
  prepends the header row.
- Nelamangala LM/FM correctly resolves to `NelamangalaHub_BLR` (4421,
  Delivery Hub) and `NelamangalaHub_BLR_PL` (4441, Pickup Hub).

## 14. Final Windows handoff pass (`5f65942`, 2026-09-18)

- **PII sanitization:** the tracked template `data/templates/Excel
  Generation.xlsx` previously contained real candidate sample rows; these were
  removed, leaving only the header rows with formatting preserved. The
  application never relied on those rows (it strips sample rows at generation).
  The old PII-bearing file remains in **published history** — it was not
  rewritten (no history rewrite / force push / no secrets rotated by this
  project). See the Security note in `docs/SECURITY_PRIVACY.md`.
- **Portable output config:** `data/config.json` no longer pins a Windows path;
  empty `output_base_dir` means the project-relative `data/generated` is used.
- **Windows scripts hardened:** venv-bound `python -m pip`, idempotent setup,
  fast every-day starts (dependency marker), stop script using the PID file.
- **Windows-local focus:** all setup/run/stop instructions target a Windows
  operator's machine; Ubuntu was the development environment only and is being
  retired (this pass).

## 15. Final archive / CI/CD pass (this commit, `teamhr-final-v1.0.0`, 2026-09-20)

- Full documentation set (inventory, business rules, architecture, history,
  development, testing, troubleshooting, CI/CD, backup manifest, retirement
  checklist).
- Editable Word project document.
- GitHub Actions CI (`ubuntu-latest` + `windows-latest`, Python 3.12) and a
  manual release workflow.
- CHANGELOG + semantic versioning.
- Rebuilt clean Windows source release + ZIP.
- Fresh-install validation on a clean Linux directory (no dependency on this
  VM or `/home/azureuser`).
- Final stable tag `teamhr-final-v1.0.0`.

## 16. Decision log (summary)

| Decision | Rationale |
|----------|-----------|
| FastAPI + Jinja2 + vanilla JS | Simple, offline, dependency-light, easy to run on any work PC |
| SQLite, auto-init | No server, portable, hard to break |
| Local OCR (ONNX/layout parser) | Candidate documents never leave the machine |
| Resolver + evidence scoring | Operator reviews scored fields instead of retyping |
| Excel masters read-only + SQLite overrides | Admin edits never corrupt official masters |
| Single workbook, two sheets | One file, aligned OB + Mail rows, fewer copy steps |
| Full-master facility dropdown | User can always pick the exact master row; OCR guesses never lock the choice |
| Copy Details TAB row | Direct paste into the downstream tool without reformatting |
| Full Aadhaar only in Mail Format | Operational requirement confined to one sheet; everywhere else masked/omitted |
| Portal/comm disabled by flags | No external side effects unless a human explicitly enables them |
| Two safety flags for live upload | Uploading requires deliberate, documented double opt-in |

## 17. Things intentionally not done

- No cloud APIs at runtime for OCR.
- No automatic eSampark submission, email, WhatsApp, SMS, or voice (all
  disabled by default).
- No AI-generated answers in recorded docs (this document describes only what
  the code and history show).
- No rewrite of published Git history (the PII in the pre-sanitization
  template is disclosed, not silently deleted).