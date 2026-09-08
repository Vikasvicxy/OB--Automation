# Security and Privacy

## PII Handling

### Aadhaar Numbers

- **Storage**: SQLite database only (`candidates.aadhaar_number` column). Never stored in any other file, log, or browser storage.
- **Display**: Masked as `XXXX XXXX XXXX` in all UI surfaces (lists, search, pipeline, data quality, dashboard, daily master export) EXCEPT the Candidate Detail page (`/candidates/{id}`), which shows the full number for verification.
- **API**: The detail API (`/api/candidates/{id}`) includes `aadhaar_masked` alongside the full number. All other APIs never return the full Aadhaar.
- **Logs**: Aadhaar numbers are NEVER logged. The logging system (`app/logging_config.py`) sanitizes Aadhaar patterns (`\d{4}\s\d{4}\s\d{4}`) from all log output.
- **Edit history**: When Aadhaar is changed, only "Aadhaar updated" is recorded — never the old or new value.
- **Backups**: The backup contains the full database (which includes Aadhaar). Backups should be stored securely. Raw Aadhaar images are never included in backups.
- **Global search**: Never searches or matches against Aadhaar values.
- **CSV export**: Excluded from candidate CSV exports.

### Full Addresses

- **Storage**: SQLite database only (`candidates.address` column).
- **Display**: Only visible on the Candidate Detail page. Never shown in search results, pipeline cards, data quality drill-down, or any other list view.
- **Edit history**: Only "Address updated" is recorded — never the old or new value.
- **Logs**: Never logged.

### Mobile Numbers

- **Storage**: SQLite database, normalized to 10 digits.
- **Display**: Visible in candidate lists, pipeline cards, search results, and detail pages.
- **Search**: Used as the primary duplicate detection field.
- **Duplicate check**: The duplicate check API returns the matching candidate's name, facility, status, cost code, and creation date — but not Aadhaar or address.

### Other Sensitive Data

- **Portal credentials** (`ESAMPARK_USERNAME`, `ESAMPARK_PASSWORD`): read from environment only, never stored in SQLite, logs, screenshots, or generated files. Portal cookies/sessions are stored in `data/portal/` (gitignored).
- **Communication credentials** (API keys, SMTP passwords): read from environment, never included in health/diagnostic responses.
- **OCR benchmark images**: stored in `data/ocr_benchmark/` (gitignored), never committed.

## Data Storage

| Data | Location | Gitignored | Notes |
|------|----------|------------|-------|
| SQLite database | `data/database/teamhr.db` | Yes | All candidate data |
| Generated Excel | `data/generated/` | Yes | Self Onboarding files |
| Backups | `data/backups/` | Yes | ZIP archives with DB snapshot |
| Master Excel files | `data/masters/` | No | Designation, Facility, Template |
| Portal browser state | `data/portal/` | Yes | Cookies, session |
| Logs | `data/logs/` | Yes | Application logs |
| Runtime config | `data/config.json` | No | Output directory setting |
| OCR benchmarks | `data/ocr_benchmark/` | Yes | Test images |
| `.env` | Root | Yes | Secrets and feature flags |

## Feature Flags for Dangerous Operations

All dangerous operations require explicit opt-in via feature flags:

| Operation | Required Flags | Default |
|-----------|---------------|---------|
| Real portal upload | `REAL_UPLOAD_ENABLED=true` | false |
| Live test upload | `REAL_UPLOAD_ENABLED=true` AND `ESAMPARK_LIVE_TEST_MODE=true` | both false |
| WhatsApp messaging | `COMMUNICATION_ENABLED=true` AND `WHATSAPP_ENABLED=true` | both false |
| Email sending | `COMMUNICATION_ENABLED=true` AND `EMAIL_ENABLED=true` | both false |
| SMS sending | `COMMUNICATION_ENABLED=true` AND `SMS_ENABLED=true` | both false |
| Voice calls | `COMMUNICATION_ENABLED=true` AND `VOICE_ENABLED=true` | both false |

The health page shows "LOCKED" for live upload safety when both flags are not enabled.

## Backup Exclusions

Backups use an allow-list approach. The following are NEVER included:

- Raw Aadhaar images (source documents)
- Portal cookies, sessions, or auth state
- Browser profiles
- Application logs
- Caches
- Secrets or credentials
- OCR benchmark images
- Master Excel files (they are user-provided source files)
- `.env` file
- `venv/` directory

A backup always contains:
- `teamhr.db` (consistent SQLite snapshot)
- `manifest.json` (checksums, metadata)
- Optionally: generated Excel files (opt-in)

## Communication Safety

- All communication sending is **dry-run by default**. Even when provider credentials are configured, messages are not sent unless `dry_run=False` is explicitly passed.
- The `send_message()` API hardcodes `dry_run=True` in the current production route (`/api/communications/send`).
- Provider adapters (WhatsApp, Email, SMS, Voice) are placeholders — they return `not_implemented` when real sends are attempted.
- All message attempts are recorded in the `communication_outbox` table with status, payload, and timestamp.
- Recipient validation prevents sending to invalid phone numbers or email addresses.
- Outbox messages can be cancelled before sending.

## Audit Trail

The system maintains multiple audit trails:

1. **Candidate events** (`candidate_events`): creation, approval, edits, generation, portal uploads
2. **Candidate edit history** (`candidate_edit_history`): field-level old/new values (safe fields only)
3. **Batch events** (`batch_events`): candidate additions, removals, status changes
4. **Portal audit** (`portal_audit`): upload attempts, status changes, result imports
5. **Generation audit** (`generation_audit`): Excel generation with template version and candidate IDs
6. **Master change history** (`master_change_history`): admin facility/role changes with who/when
7. **System events** (`system_events`): backups, restores, draft discards
8. **Validation runs** (`manual_validation_runs`): test case results
9. **UAT results** (`uat_results`): manual acceptance test outcomes
10. **Communication outbox** (`communication_outbox`): message attempts and status

All audit records include timestamps. Sensitive values are never stored in audit records.
