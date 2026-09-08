# Admin Guide

## Overview

The admin features let you manage master data (facilities, roles, aliases) directly from the UI, layered on top of the Excel master files. Admin changes are stored in a separate SQLite layer and merged into the effective master view consumed by the resolver. The Excel files are never modified.

Admin features are enabled by default (`ADMIN_FEATURE_ENABLED=true` in `.env`).

## Master Data Management

Navigate to **Admin Master Data** (`/admin/master-data`).

### Facilities Tab

View all effective facilities (Excel + Admin merged). Each shows:
- Facility Name, Location Code, Entity, Operation, Cost Code
- Source (Excel Import / Admin / Admin Override)
- Active/Inactive status

**Add a Facility**:
1. Enter Facility Name (required, must be unique)
2. Enter Location Code (required)
3. Select Entity (Flipkart or Myntra)
4. Select Operation (Last Mile or First Mile)
5. Cost code is automatically derived from Entity + Operation (not manually set)

**Edit a Facility**:
- Only Location Code, State, and Active status can be changed
- Identity fields (Name, Entity, Operation, Cost Code) are immutable — to correct them, deactivate the old one and create a new one

**Deactivate/Reactivate**: soft-delete that removes the facility from the effective view without losing history.

### Roles Tab

View all effective roles (Excel designations + Admin roles).

**Add a Role**:
1. Enter Official Role Name (required, must be unique)
2. Select Entity Scope (Flipkart, Myntra, or Both)
3. Select Operation (Last Mile or First Mile)
4. Enter Allowed Cost Codes (comma-separated, must be valid and compatible)
5. Enter Aliases (one per line, e.g., "tl", "biker")

**Rules**:
- Prexo roles are restricted from Myntra cost codes (8751, 8752)
- Cost codes must be compatible with the selected operation
- Identity fields are immutable after creation

**Deactivate/Reactivate**: same as facilities.

### Cost Code Combinations

| Entity | Operation | Cost Code |
|--------|-----------|-----------|
| Flipkart | Last Mile | 4421 |
| Flipkart | First Mile | 4441 |
| Myntra | Last Mile | 8751 |
| Myntra | First Mile | 8752 |

### Change History

The History tab shows all admin changes (create, update, activate, deactivate) with timestamps and changed values.

### Export

Export effective facilities or roles to JSON for external use.

## Import Preview and Apply

From the Admin Master Data page, use the **Master Import** tab:

1. **Preview** — dry-run that shows what would change if you import the Excel masters (new, changed, unchanged, duplicate, override conflicts). No state is modified.
2. **Apply Import** — reloads the Excel master files and updates the in-memory resolver. Same as clicking Reload on the Settings page.

## Backup and Restore

Navigate to **Backups** (`/backups`).

### Creating Backups

Click **Create Backup**. The backup is a ZIP file containing:
- `teamhr.db` — consistent SQLite snapshot (safe against concurrent writes)
- `manifest.json` — metadata with checksums, app version, git commit

Generated Excel files are **excluded by default** (they're large and regenerable). Check "Include generated files" to include them.

Backups never contain: raw Aadhaar images, portal cookies/sessions, browser profiles, logs, secrets, or OCR benchmarks.

### Verifying Backups

Click **Verify** on any backup to check:
- Archive integrity
- Manifest validity
- File checksums (SHA-256)
- SQLite integrity check
- Required table presence

### Restoring Backups

Click **Restore** on a verified backup. The restore flow:
1. Verifies the backup
2. Creates a pre-restore backup (automatic safety net)
3. Checks that live upload mode is not active
4. Atomically replaces the database
5. Verifies the restored database

Restore is blocked when `REAL_UPLOAD_ENABLED` or `ESAMPARK_LIVE_TEST_MODE` is true.

### Backup Audit Trail

All backup and restore operations are recorded in the system events table.

## Health Monitoring

Navigate to **Health** (`/health`). Shows status cards for:
- Database (connectivity, integrity, candidate/batch counts)
- Facility Master and Designation Master (configured/row count)
- Admin Master (effective facilities/roles)
- OCR Backend (RapidOCR or PaddleOCR)
- Generated Folder and Backup Folder (size)
- Portal Selectors (configured status markers)
- Live Upload Safety (LOCKED = safe, ENABLED = both flags on)
- Config (output directory)
- Disk Space
- Last Backup
- Application Version (with git commit)

The JSON endpoint at `/api/health` returns a machine-readable summary (no secrets).

## Safety Flags and Feature Flags

All feature flags default to safe values (off/disabled). See `.env.example` or [CONFIGURATION.md](CONFIGURATION.md) for the full list.

Key safety flags:
- **REAL_UPLOAD_ENABLED** — must be true for any real portal upload
- **ESAMPARK_LIVE_TEST_MODE** — must be true for live test uploads
- Both must be true for a live submission to proceed
- **COMMUNICATION_ENABLED** + individual channel flags — master switch + per-channel control
- **ADMIN_FEATURE_ENABLED** — gates the Admin Master Data page

## Communication Configuration

Navigate to **Communications** (`/communications`).

Configure channels in `.env`:
- WhatsApp: Gupshup API key and app name
- Email: SMTP host, port, username, password, from address
- SMS: same provider as WhatsApp (Gupshup)
- Voice: placeholder for telephony adapter

All sending is dry-run by default. Real sends require explicit provider credentials AND the channel feature flag enabled.

## Issue Center

Navigate to **Issues** (`/issues`). Track and resolve issues:
- Create issues with type (validation, portal, data quality, etc.), severity (info, warning, error), title, and detail
- Link to a candidate or batch
- Resolve with resolution notes and timestamp
- Filter by status and type
