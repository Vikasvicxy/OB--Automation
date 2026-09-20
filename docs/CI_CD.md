# TeamHR Automation — CI/CD Architecture

How CI and (future) CD are designed for a **local desktop application** — there
is no server production deployment.

## 1. Recommended lifecycle

```
Developer
   │  feature branch (feature/*, fix/*)
   ▼
Pull Request  ──►  GitHub Actions CI (ci.yml)
   │                 ubuntu-latest + windows-latest · Python 3.12
   │                 install → import/route checks → focused regression
   │                 → facility → persistence → Excel generation
   │                 → security/path tests → JS syntax
   ▼
merge to master   (CI re-runs; master must stay green)
   │
   ▼
version tag       (semver: MAJOR.MINOR.PATCH — see CHANGELOG)
   │
   ▼
release packaging (release.yml, manual workflow_dispatch)
   │                 tests → source archive
   │                 → (future) Windows PyInstaller EXE → upload
   ▼
GitHub Release    artifact
   │
   ▼
Windows operator  downloads source/EXE, runs locally
```

## 2. What CI validates (ci.yml)

Every push and PR runs **without any access to this VM, candidate data,
credentials, or hosted services**:

- Environment: Python 3.12 on `ubuntu-latest` and `windows-latest`.
- `python -m pip install -r requirements-dev.txt` (pinned runtime versions +
  test extras).
- Import checks (`app.main` imports; app constructs).
- Fresh-DB + master/template loading.
- Focused regression tests (see `docs/TESTING.md` for the exact groups):
  - facility dropdown + HubName mapping + `_PL` rows (includes the Node-based
    Copy Details JS-harness test)
  - review/persistence (save draft / approve / resume / discard)
  - Excel generation (two-sheet OB + Mail)
  - security/path-traversal + PII-leak + feature-flag tests (cross-platform)
  - template smoke + JS syntax
- The full cross-platform suite is run; tests known to need platform-specific
  tooling are split rather than hidden (Node ships on both GitHub runner
  images, so the JS tests run on both).

`benchmark_ocr.py` is excluded (benchmark, not a pass/fail test).

## 3. CI security

- No LDAP/eSampark credentials, candidate data, Azure credentials, or GitHub
  PATs are required or stored in the repo.
- External actions (upload / email / WhatsApp / SMS / voice) stay disabled —
  the feature-flag tests assert they are off by default.
- `.env.example` has only placeholders; no real secrets are tracked.

## 4. What CD means for this app

TeamHR Automation is a **local desktop tool**. There is currently **no server
production deployment**. Future CD should therefore primarily mean:

1. **Validated release packaging** — run the full suite, build a clean source
   artifact (and eventually a Windows EXE), and publish it as a **GitHub
   Release**.
2. **Versioning** — annotated semver tags that map to Release assets.
3. **Windows installer / EXE artifact** — the eventual delivery unit for the
   operators.

It does **not** mean automatic eSampark submission, and it never means
untested auto-deployments.

## 5. Release workflow (release.yml) — manual

`.github/workflows/release.yml` is triggered by `workflow_dispatch` (manual)
or a version tag. Its current scope:

1. Re-run the CI suite.
2. Build a clean **source archive** (`release/TeamHR-Windows-Source.zip`
   equivalent) into the Release.
3. `TODO` (documented in the workflow + this file): produce a Windows
   PyInstaller EXE. **Not yet validated** — do not claim EXE support until it
   has been built and run on a real Windows machine.

## 6. Future Windows EXE path (documented TODO)

Suggested steps when someone validates EXE packaging:

1. Add a `pyinstaller` dev dependency and a Windows build step in
   `release.yml` that runs `python -m PyInstaller --onefile --name TeamHR
   --add-data "data;data" --add-data "app/templates;app/templates"
   --add-data "app/static;app/static" app/main.py`.
2. Note EXE runtime requirements: venv is embedded; the masters/templates must
   ship next to the executable (`data/masters/`, `data/templates/`); outputs go
   under `<exe-dir>/data/generated`.
3. Run the full test suite against the packaged app on a clean Windows VM
   before marking EXE as supported.

## 7. Versioning

Semantic versioning `MAJOR.MINOR.PATCH`:

- New non-breaking feature → **MINOR** (`1.1.0`)
- Bug fix → **PATCH** (`1.1.1`)
- Breaking data/workflow change → **MAJOR** (`2.0.0`)

Record every release in `CHANGELOG.md`; tag with an annotated tag.

## 8. Repository hygiene for CI

- `master` is the only long-lived branch and is always green.
- CI secrets: none required today. If a future release job needs to publish a
  GitHub Release, add `permissions: contents: write` only on that job and use
  the built-in `GITHUB_TOKEN` (never a hardcoded PAT).
- PII/secret guard: the security tests assert full Aadhaar/address never leak
  and no password/token/cookie values appear in diagnostics; CI runs those
  tests on every push.