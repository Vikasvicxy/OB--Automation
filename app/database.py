"""SQLite persistence layer for TeamHR Automation.

Replaces the previous in-memory ``candidate_store`` with durable storage so
candidates and batches survive browser refresh, app restart, and OS restart.

Both Manual Entry and Smart Upload write through this single shared service;
there is no separate storage implementation.

Security
--------
Sensitive fields (full Aadhaar number, full address) are stored ONLY in the
local SQLite database. They are never written to browser localStorage, never
exposed in URLs, and never logged. Display/UI always uses the masked Aadhaar
form (see ``mask_aadhaar``).

Schema:
    candidates          - the single shared candidate model
    batches             - onboarding batches
    documents           - source document metadata records
    master_load_history - history of master-data reloads
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = DATA_DIR / "database"
DB_PATH = DB_DIR / "teamhr.db"

CANDIDATE_COLUMNS = [
    "candidate_id",
    "batch_id",
    "candidate_number",
    "name",
    "mobile",
    "aadhaar_filename",
    "aadhaar_number",
    "address",
    "dob",
    "doj",
    "entity",
    "cost_code",
    "operation",
    "team",
    "designation",
    "facility_type",
    "facility_name",
    "location_code",
    "salary",
    "salary_display",
    "migrant",
    "contractor",
    "lob",
    "state",
    "status",
    "created_date",
    "created_time",
    "source_files",
    "upload_filename",
    "excel_generated",
    "generated_file_id",
    "portal_status",
    "portal_remarks",
    "updated_at",
]

# Fields that must never be logged or placed in browser localStorage / URLs.
SENSITIVE_FIELDS = {"aadhaar_number", "address"}


def _now(step: str = "") -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_connection() -> sqlite3.Connection:
    """Open a connection to the SQLite database with row access by name."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the database file and all tables if they do not yet exist."""
    conn = _get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS batches (
                batch_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT,
                status          TEXT,
                candidate_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id        INTEGER NOT NULL DEFAULT 1,
                candidate_number INTEGER NOT NULL DEFAULT 1,
                name            TEXT,
                mobile          TEXT,
                aadhaar_filename TEXT,
                aadhaar_number  TEXT,
                address         TEXT,
                dob             TEXT,
                doj             TEXT,
                entity          TEXT,
                cost_code       TEXT,
                operation       TEXT,
                team            TEXT,
                designation     TEXT,
                facility_type   TEXT,
                facility_name   TEXT,
                location_code   TEXT,
                salary          INTEGER,
                salary_display  TEXT,
                migrant         TEXT,
                contractor      TEXT,
                lob             TEXT,
                state           TEXT,
                status          TEXT,
                created_date    TEXT,
                created_time    TEXT,
                source_files    TEXT,
                upload_filename TEXT,
                excel_generated TEXT,
                generated_file_id INTEGER,
                portal_status   TEXT,
                portal_remarks  TEXT,
                updated_at      TEXT,
                FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
            );

            CREATE TABLE IF NOT EXISTS generated_files (
                file_id             INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id            INTEGER NOT NULL,
                filename            TEXT NOT NULL,
                file_path           TEXT NOT NULL,
                date_folder         TEXT,
                generated_at        TEXT,
                candidate_count     INTEGER NOT NULL DEFAULT 0,
                generation_status   TEXT,
                portal_result_path  TEXT,
                portal_failure_path TEXT
            );

            CREATE TABLE IF NOT EXISTS generation_audit (
                audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id        INTEGER,
                generated_file_id INTEGER,
                generated_at    TEXT,
                candidate_ids   TEXT,
                template_name   TEXT,
                template_version TEXT,
                status          TEXT,
                error_message   TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_master (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id    INTEGER UNIQUE,
                name            TEXT,
                mobile          TEXT,
                dob             TEXT,
                doj             TEXT,
                masked_aadhaar  TEXT,
                address         TEXT,
                entity          TEXT,
                cost_code       TEXT,
                operation       TEXT,
                team            TEXT,
                designation     TEXT,
                facility_type   TEXT,
                facility_name   TEXT,
                location_code   TEXT,
                salary          TEXT,
                migrant         TEXT,
                contractor      TEXT,
                lob             TEXT,
                status          TEXT,
                created_at      TEXT,
                generated_file  TEXT,
                portal_status   TEXT,
                portal_remarks  TEXT,
                updated_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS documents (
                document_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id    INTEGER,
                filename        TEXT,
                file_type       TEXT,
                file_size       INTEGER,
                document_type   TEXT,
                extraction_status TEXT,
                uploaded_at     TEXT,
                FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
            );

            CREATE TABLE IF NOT EXISTS master_load_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                master_type     TEXT,
                filename        TEXT,
                row_count       INTEGER,
                loaded_at       TEXT,
                status          TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_candidates_batch    ON candidates(batch_id);
            CREATE INDEX IF NOT EXISTS idx_candidates_mobile   ON candidates(mobile);
            CREATE INDEX IF NOT EXISTS idx_candidates_name     ON candidates(name);
            CREATE INDEX IF NOT EXISTS idx_candidates_status   ON candidates(status);
            CREATE INDEX IF NOT EXISTS idx_candidates_facility ON candidates(facility_name);
            CREATE INDEX IF NOT EXISTS idx_generated_batch     ON generated_files(batch_id);

            CREATE TABLE IF NOT EXISTS portal_uploads (
                portal_upload_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_file_id INTEGER NOT NULL,
                batch_id          INTEGER NOT NULL,
                filename          TEXT,
                started_at        TEXT,
                completed_at      TEXT,
                portal_status     TEXT,
                portal_status_raw TEXT,
                portal_reference  TEXT,
                result_file_path  TEXT,
                error_file_path   TEXT,
                error_message     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_portal_uploads_file ON portal_uploads(generated_file_id);
            CREATE INDEX IF NOT EXISTS idx_portal_uploads_batch ON portal_uploads(batch_id);

            CREATE TABLE IF NOT EXISTS portal_audit (
                audit_id             INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_file_id    INTEGER,
                event                TEXT,
                status               TEXT,
                detail               TEXT,
                occurred_at          TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_portal_audit_file ON portal_audit(generated_file_id);

            CREATE TABLE IF NOT EXISTS live_uploads (
                live_upload_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_file_id    INTEGER NOT NULL,
                candidate_id         INTEGER,
                manual_confirmed     INTEGER NOT NULL DEFAULT 0,
                flow_status          TEXT,
                started_at           TEXT,
                submitted_at         TEXT,
                portal_status        TEXT,
                portal_status_raw    TEXT,
                portal_reference     TEXT,
                success_count        INTEGER,
                failed_count         INTEGER,
                total_count          INTEGER,
                creation_remarks     TEXT,
                result_file_path     TEXT,
                error_file_path      TEXT,
                error_message        TEXT,
                updated_at           TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_live_uploads_file ON live_uploads(generated_file_id);
            CREATE INDEX IF NOT EXISTS idx_live_uploads_candidate ON live_uploads(candidate_id);

            CREATE TABLE IF NOT EXISTS manual_validation_runs (
                validation_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id          TEXT,
                test_type        TEXT,
                status           TEXT,
                expected_summary TEXT,
                actual_summary   TEXT,
                notes            TEXT,
                validated_at     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_validation_case ON manual_validation_runs(case_id);

            CREATE TABLE IF NOT EXISTS candidate_events (
                event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id  INTEGER NOT NULL,
                event_type    TEXT NOT NULL,
                summary       TEXT,
                created_at    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_candidate_events_candidate
                ON candidate_events(candidate_id);
            CREATE INDEX IF NOT EXISTS idx_candidate_events_type
                ON candidate_events(event_type);

            CREATE TABLE IF NOT EXISTS candidate_edit_history (
                history_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id  INTEGER NOT NULL,
                field_name    TEXT NOT NULL,
                old_value     TEXT,
                new_value     TEXT,
                edited_at     TEXT,
                FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_candidate_edit_history_candidate
                ON candidate_edit_history(candidate_id);

            CREATE TABLE IF NOT EXISTS batch_events (
                event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id      INTEGER NOT NULL,
                event_type    TEXT NOT NULL,
                summary       TEXT,
                created_at    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_batch_events_batch
                ON batch_events(batch_id);

            CREATE TABLE IF NOT EXISTS master_facilities (
                facility_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                facility_name  TEXT NOT NULL,
                location_code  TEXT NOT NULL,
                entity         TEXT NOT NULL,
                operation      TEXT NOT NULL,
                cost_code      TEXT NOT NULL,
                facility_type  TEXT NOT NULL DEFAULT 'Delivery Hub',
                state          TEXT,
                active         INTEGER NOT NULL DEFAULT 1,
                source         TEXT NOT NULL DEFAULT 'Admin',
                created_at     TEXT,
                updated_at     TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_master_facilities_name
                ON master_facilities(facility_name);
            CREATE INDEX IF NOT EXISTS idx_master_facilities_cc ON master_facilities(cost_code);
            CREATE INDEX IF NOT EXISTS idx_master_facilities_entity ON master_facilities(entity);

            CREATE TABLE IF NOT EXISTS master_roles (
                role_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                official_name  TEXT NOT NULL,
                entity_scope   TEXT,
                operation      TEXT,
                active         INTEGER NOT NULL DEFAULT 1,
                source         TEXT NOT NULL DEFAULT 'Admin',
                created_at     TEXT,
                updated_at     TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_master_roles_name
                ON master_roles(official_name);

            CREATE TABLE IF NOT EXISTS master_role_cost_codes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id    INTEGER NOT NULL,
                cost_code  TEXT NOT NULL,
                FOREIGN KEY (role_id) REFERENCES master_roles(role_id)
            );
            CREATE INDEX IF NOT EXISTS idx_role_cost_codes_role ON master_role_cost_codes(role_id);
            CREATE INDEX IF NOT EXISTS idx_role_cost_codes_cc ON master_role_cost_codes(cost_code);

            CREATE TABLE IF NOT EXISTS master_role_aliases (
                alias_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id    INTEGER NOT NULL,
                alias      TEXT NOT NULL,
                active     INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (role_id) REFERENCES master_roles(role_id)
            );
            CREATE INDEX IF NOT EXISTS idx_role_aliases_role ON master_role_aliases(role_id);
            CREATE INDEX IF NOT EXISTS idx_role_aliases_alias ON master_role_aliases(alias);

            CREATE TABLE IF NOT EXISTS master_change_history (
                change_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                master_type        TEXT,
                record_id          INTEGER,
                action             TEXT,
                old_value_summary  TEXT,
                new_value_summary  TEXT,
                changed_by         TEXT DEFAULT 'local-admin',
                changed_at         TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_master_history_type ON master_change_history(master_type);
            """
        )

        # ── Migrations for pre-existing databases ─────────────────────────
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(candidates)")}
        for col, ddl in (
            ("state", "TEXT"),
            ("excel_generated", "TEXT"),
            ("generated_file_id", "INTEGER"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE candidates ADD COLUMN {col} {ddl}")

        gcols = {r["name"] for r in conn.execute("PRAGMA table_info(generated_files)")}
        for col, ddl in (
            ("portal_status", "TEXT"),
            ("portal_status_raw", "TEXT"),
            ("portal_uploaded_at", "TEXT"),
            ("portal_reference", "TEXT"),
            ("portal_upload_id", "INTEGER"),
        ):
            if col not in gcols:
                conn.execute(f"ALTER TABLE generated_files ADD COLUMN {col} {ddl}")

        conn.commit()
    finally:
        conn.close()


# ── Sensitive data helpers ───────────────────────────────────────────────────


def mask_aadhaar(value: str) -> str:
    """Mask an Aadhaar number for display (keeps last 4 digits)."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        return "XXXX XXXX XXXX"
    return f"XXXX XXXX {digits[-4:]}"


# ── Batches ──────────────────────────────────────────────────────────────────


def _ensure_batch(conn: sqlite3.Connection, batch_id: int) -> None:
    cur = conn.execute("SELECT batch_id FROM batches WHERE batch_id = ?", (batch_id,))
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO batches (batch_id, created_at, status, candidate_count) "
            "VALUES (?, ?, ?, 0)",
            (batch_id, _now(), "Draft"),
        )
        conn.commit()


def _update_batch_count(conn: sqlite3.Connection, batch_id: int) -> None:
    count = conn.execute(
        "SELECT COUNT(*) FROM candidates WHERE batch_id = ?", (batch_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE batches SET candidate_count = ? WHERE batch_id = ?", (count, batch_id)
    )
    conn.commit()


def create_batch(status: str = "Draft") -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO batches (created_at, status, candidate_count) VALUES (?, ?, 0)",
            (_now(), status),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_or_create_batch(batch_id: int) -> int:
    conn = _get_connection()
    try:
        _ensure_batch(conn, batch_id)
        return batch_id
    finally:
        conn.close()


def get_batch(batch_id: int) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_batches() -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM batches ORDER BY batch_id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_batch_status(batch_id: int, status: str) -> bool:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "UPDATE batches SET status = ? WHERE batch_id = ?", (status, batch_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Batch detail / summary / timeline ────────────────────────────────────────


def get_batch_summary(batch_id: int) -> Optional[dict]:
    """Aggregate read-only metrics for one batch (safe fields only)."""
    batch = get_batch(batch_id)
    if batch is None:
        return None
    candidates = list_candidates(batch_id=batch_id)
    ready = sum(1 for c in candidates if (c.get("status") or "").lower() == "ready")
    needs_review = sum(
        1 for c in candidates if (c.get("status") or "").lower() in
        ("needs_attention", "needs_review")
    )
    generated_files = list_generated_files(batch_id)

    portal_files = [f for f in generated_files
                    if (f.get("portal_status") or "").lower() == "success"]
    portal_status = "Success" if portal_files else \
        ("Processing" if any((f.get("portal_status") or "").lower() in
                             ("processing", "pending", "portal uploaded")
                             for f in generated_files) else ("Failed" if any(
            (f.get("portal_status") or "").lower() == "failed"
            for f in generated_files) else ("Not Started" if generated_files else None)))

    return {
        "batch_id": batch_id,
        "created_at": batch.get("created_at"),
        "status": batch.get("status"),
        "candidate_count": len(candidates),
        "ready_count": ready,
        "needs_review_count": needs_review,
        "generated_file_count": len(generated_files),
        "portal_status": portal_status,
    }


def record_batch_event(batch_id: int, event_type: str, summary: str = "") -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO batch_events (batch_id, event_type, summary, created_at) "
            "VALUES (?, ?, ?, ?)",
            (batch_id, event_type, summary, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_batch_events(batch_id: int, limit: int = 200) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM batch_events WHERE batch_id = ? "
            "ORDER BY event_id DESC LIMIT ?",
            (batch_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_generated_files_with_portal(batch_id: int, limit: int = 50) -> list[dict]:
    """Generated files for a batch joined with portal upload info (safe only)."""
    files = list_generated_files(batch_id, limit=limit)
    conn = _get_connection()
    try:
        out = []
        for f in files:
            row = dict(f)
            upload = conn.execute(
                "SELECT portal_upload_id, portal_status, portal_reference, "
                "started_at, completed_at, result_file_path, error_file_path "
                "FROM portal_uploads WHERE generated_file_id = ? "
                "ORDER BY portal_upload_id DESC LIMIT 1",
                (f["file_id"],),
            ).fetchone()
            row["portal_upload"] = dict(upload) if upload else None
            out.append(row)
        return out
    finally:
        conn.close()


# ── Candidates ───────────────────────────────────────────────────────────────


def _row_to_candidate(row: sqlite3.Row) -> dict:
    data = dict(row)
    src = data.get("source_files")
    if isinstance(src, str):
        try:
            data["source_files"] = json.loads(src)
        except Exception:  # noqa: BLE001
            data["source_files"] = []
    else:
        data["source_files"] = data.get("source_files") or []
    return data


def insert_candidate(data: dict) -> int:
    """Insert a new candidate. Returns the new candidate_id."""
    now = datetime.now()
    row = {k: data.get(k, "") for k in CANDIDATE_COLUMNS}
    row["created_date"] = row.get("created_date") or now.strftime("%Y-%m-%d")
    row["created_time"] = row.get("created_time") or now.strftime("%H:%M:%S")
    row["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    row["candidate_number"] = data.get("candidate_number", 1)
    row["migrant"] = data.get("migrant", "No") or "No"

    if isinstance(row.get("source_files"), (list, tuple)):
        row["source_files"] = json.dumps(row["source_files"])

    conn = _get_connection()
    try:
        batch_id = int(row.get("batch_id") or 1)
        _ensure_batch(conn, batch_id)
        # candidate_id is auto-increment; never insert it.
        cols = [k for k in row.keys() if k != "candidate_id"]
        placeholders = ", ".join(["?"] * len(cols))
        colnames = ", ".join(cols)
        values = [row[c] for c in cols]
        cur = conn.execute(
            f"INSERT INTO candidates ({colnames}) VALUES ({placeholders})", values
        )
        conn.commit()
        new_id = cur.lastrowid
        _update_batch_count(conn, batch_id)
        conn.execute(
            "INSERT INTO candidate_events (candidate_id, event_type, summary, created_at) "
            "VALUES (?, ?, ?, ?)",
            (new_id, "Candidate Created",
             f"Candidate #{new_id} created (batch {batch_id}).", _now()),
        )
        conn.commit()
        record_batch_event(batch_id, "Candidate Added",
                           f"Candidate #{new_id} added to batch.")
        return new_id
    finally:
        conn.close()


def update_candidate(candidate_id: int, data: dict) -> bool:
    """Update an existing candidate with the provided fields. Returns True if found."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            return False
        current = dict(row)
        update_sets = []
        values = []
        for k, v in data.items():
            if k not in CANDIDATE_COLUMNS:
                continue
            if k == "source_files" and isinstance(v, (list, tuple)):
                v = json.dumps(v)
            update_sets.append(f"{k} = ?")
            values.append(v if v is not None else "")
        update_sets.append("updated_at = ?")
        values.append(_now())
        values.append(candidate_id)
        if update_sets:
            conn.execute(
                f"UPDATE candidates SET {', '.join(update_sets)} WHERE candidate_id = ?",
                values,
            )
            conn.commit()
            _update_batch_count(conn, current.get("batch_id") or 1)
            # Safe edit history + timeline event (only when values actually changed).
            changes = record_edit_history(candidate_id, current, data)
            if changes:
                conn.execute(
                    "INSERT INTO candidate_events (candidate_id, event_type, summary, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (candidate_id, "Edited",
                     f"Updated {changes} field(s).", _now()),
                )
                conn.commit()
        return True
    finally:
        conn.close()


def get_candidate(candidate_id: int) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        return _row_to_candidate(row) if row else None
    finally:
        conn.close()


def list_candidates(
    search: str = "",
    status: str = "",
    batch_id: Optional[int] = None,
    facility_name: Optional[str] = None,
) -> list[dict]:
    """Return candidates, optionally filtered by search / status / batch / facility.

    search matches against name, mobile, facility, or batch id (case-insensitive).
    """
    conn = _get_connection()
    try:
        sql = "SELECT * FROM candidates WHERE 1=1"
        params: list = []
        if batch_id is not None:
            sql += " AND batch_id = ?"
            params.append(batch_id)
        if status and status.lower() != "all":
            sql += " AND lower(status) = ?"
            params.append(status.lower())
        if facility_name:
            sql += " AND lower(facility_name) = ?"
            params.append(facility_name.lower())
        if search:
            like = f"%{search.lower()}%"
            sql += (
                " AND (lower(name) LIKE ? OR mobile LIKE ? OR "
                "lower(facility_name) LIKE ? OR CAST(batch_id AS TEXT) LIKE ?)"
            )
            params += [like, like, like, like]
        sql += " ORDER BY candidate_id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_candidate(r) for r in rows]
    finally:
        conn.close()


def search_candidates(query: str) -> list[dict]:
    """Convenience: full-text-ish search across the whole database."""
    return list_candidates(search=query)


def search_global(query: str, limit: int = 10) -> dict:
    """Global search across SAFE fields only, using parameterized SQL.

    Candidates match on name / mobile / candidate_id / batch_id / facility /
    role(designation). Generated files match on filename / batch_id.

    NEVER searches sensitive fields (Aadhaar number, address, raw OCR text).
    Returns at most ``limit`` candidates and ``limit`` files, and only exposes
    safe metadata (no Aadhaar, no address).
    """
    query = (query or "").strip()
    result = {"candidates": [], "files": []}
    if not query:
        return result
    conn = _get_connection()
    try:
        like = f"%{query.lower()}%"
        cand_rows = conn.execute(
            "SELECT candidate_id, name, mobile, status, batch_id, facility_name, "
            "designation, cost_code, candidate_number "
            "FROM candidates "
            "WHERE lower(COALESCE(name, '')) LIKE ? "
            "   OR COALESCE(mobile, '') LIKE ? "
            "   OR CAST(candidate_id AS TEXT) LIKE ? "
            "   OR CAST(CAST(batch_id AS INTEGER) AS TEXT) LIKE ? "
            "   OR lower(COALESCE(facility_name, '')) LIKE ? "
            "   OR lower(COALESCE(designation, '')) LIKE ? "
            "ORDER BY candidate_id DESC LIMIT ?",
            (like, like, like, like, like, like, limit),
        ).fetchall()
        result["candidates"] = [dict(r) for r in cand_rows]

        file_rows = conn.execute(
            "SELECT file_id, filename, batch_id, generated_at, candidate_count "
            "FROM generated_files "
            "WHERE lower(COALESCE(filename, '')) LIKE ? "
            "   OR CAST(CAST(batch_id AS INTEGER) AS TEXT) LIKE ? "
            "ORDER BY file_id DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()
        result["files"] = [dict(r) for r in file_rows]
        return result
    finally:
        conn.close()


def get_batch_candidates(batch_id: int) -> list[dict]:
    return list_candidates(batch_id=batch_id)


def count_candidates() -> dict:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM candidates GROUP BY status"
        ).fetchall()
        counts = {"draft": 0, "ready": 0, "generated": 0, "needs_attention": 0, "total": 0}
        total = 0
        for r in rows:
            s = (r["status"] or "").lower()
            counts[s] = counts.get(s, 0) + r["c"]
            total += r["c"]
        counts["total"] = total
        return counts
    finally:
        conn.close()


def dashboard_stats() -> dict:
    """Aggregate KPIs + lightweight chart series for the dashboard.

    Only safe (non-sensitive) values are returned — never Aadhaar/address.
    """
    conn = _get_connection()
    try:
        today = datetime.now().strftime("%Y-%m-%d")

        all_rows = conn.execute(
            "SELECT status, cost_code, facility_name, portal_status, created_date "
            "FROM candidates"
        ).fetchall()

        status_all = {"draft": 0, "ready": 0, "generated": 0, "needs_attention": 0}
        status_today = dict(status_all)
        by_cost = {}
        by_facility = {}
        portal = {"success": 0, "failed": 0, "pending": 0}
        today_total = 0

        for r in all_rows:
            s = (r["status"] or "").lower()
            if s in status_all:
                status_all[s] += 1
                if r["created_date"] == today:
                    status_today[s] += 1
                    today_total += 1
            cc = r["cost_code"] or "Unknown"
            by_cost[cc] = by_cost.get(cc, 0) + 1
            fac = r["facility_name"] or "Unknown"
            by_facility[fac] = by_facility.get(fac, 0) + 1
            ps = (r["portal_status"] or "").lower()
            if ps == "success":
                portal["success"] += 1
            elif ps == "failed":
                portal["failed"] += 1
            elif ps and ps not in ("none", "", "null"):
                portal["pending"] += 1

        total = sum(status_all.values())

        batches_today = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE created_at LIKE ?",
            (f"{today}%",),
        ).fetchone()["c"]

        dup_mobile = conn.execute(
            "SELECT mobile FROM candidates WHERE mobile != '' "
            "GROUP BY mobile HAVING COUNT(*) > 1"
        ).fetchall()
        duplicate_warnings = len(dup_mobile)

        excel_files = conn.execute(
            "SELECT COUNT(*) AS c FROM generated_files"
        ).fetchone()["c"]

        return {
            "total": total,
            "today_total": today_total,
            "status": status_all,
            "status_today": status_today,
            "needs_review": status_all.get("needs_attention", 0),
            "ready": status_all.get("ready", 0),
            "generated": status_all.get("generated", 0),
            "portal": portal,
            "by_cost_code": by_cost,
            "by_facility": {
                k: v for k, v in sorted(by_facility.items(), key=lambda kv: kv[1], reverse=True)[:8]
            },
            "daily_volume": {
                "today": today_total,
            },
            "batches_today": batches_today,
            "duplicate_warnings": duplicate_warnings,
            "excel_files": excel_files,
        }
    finally:
        conn.close()


def delete_candidate(candidate_id: int) -> bool:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT batch_id FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            return False
        # Remove dependent rows first (candidate edit history, events, documents)
        # so the candidates foreign keys never block deletion.
        conn.execute("DELETE FROM candidate_events WHERE candidate_id = ?", (candidate_id,))
        conn.execute("DELETE FROM candidate_edit_history WHERE candidate_id = ?", (candidate_id,))
        conn.execute("DELETE FROM documents WHERE candidate_id = ?", (candidate_id,))
        conn.execute("DELETE FROM candidates WHERE candidate_id = ?", (candidate_id,))
        conn.commit()
        _update_batch_count(conn, row["batch_id"])
        record_batch_event(row["batch_id"], "Candidate Removed",
                           f"Candidate #{candidate_id} removed from batch.")
        return True
    finally:
        conn.close()


remove_candidate = delete_candidate


def find_duplicate_mobile(mobile: str, exclude_id: Optional[int] = None) -> Optional[dict]:
    """Database-wide duplicate check by mobile number (whole SQLite database)."""
    if not mobile:
        return None
    conn = _get_connection()
    try:
        sql = "SELECT * FROM candidates WHERE mobile = ?"
        params: list = [mobile]
        if exclude_id is not None:
            sql += " AND candidate_id != ?"
            params.append(exclude_id)
        sql += " ORDER BY candidate_id ASC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        return _row_to_candidate(row) if row else None
    finally:
        conn.close()


def find_duplicate_aadhaar(aadhaar: str, exclude_id: Optional[int] = None) -> Optional[dict]:
    if not aadhaar:
        return None
    conn = _get_connection()
    try:
        sql = "SELECT * FROM candidates WHERE aadhaar_number = ?"
        params: list = [aadhaar]
        if exclude_id is not None:
            sql += " AND candidate_id != ?"
            params.append(exclude_id)
        sql += " ORDER BY candidate_id ASC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        return _row_to_candidate(row) if row else None
    finally:
        conn.close()


# ── Candidate timeline / audit ───────────────────────────────────────────────
# A single lightweight event log per candidate. No historical fabrication —
# events are written only by real application actions. Never stores full
# Aadhaar / full address / credentials.


def record_candidate_event(candidate_id: int, event_type: str,
                           summary: str = "") -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO candidate_events (candidate_id, event_type, summary, created_at) "
            "VALUES (?, ?, ?, ?)",
            (candidate_id, event_type, summary, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_candidate_events(candidate_id: int, limit: int = 200) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM candidate_events WHERE candidate_id = ? "
            "ORDER BY event_id DESC LIMIT ?",
            (candidate_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Safe edit history ────────────────────────────────────────────────────────
# Tracks safe old -> new values for ordinary fields. Sensitive fields
# (Aadhaar number, address) are NEVER stored — callers instead log the
# sanitised summary via record_candidate_event. The *_sensitive helpers below
# exist so callers never accidentally persist raw sensitive values.


SAFE_EDIT_FIELDS = {
    "name", "mobile", "entity", "operation", "cost_code", "designation",
    "facility", "facility_name", "location_code", "salary", "salary_display",
    "status", "batch_id",
}
SENSITIVE_FIELD_LABELS = {"aadhaar_number": "Aadhaar", "address": "Address"}


def record_edit_history(candidate_id: int, old_row: dict, new_row: dict) -> int:
    """Record safe old -> new values for the safe edit fields.

    Sensitive fields (Aadhaar number, full address) are skipped and their
    values are never written to the history table. Returns the number of
    change rows written.
    """
    written = 0
    conn = _get_connection()
    try:
        for field in SAFE_EDIT_FIELDS:
            if field not in new_row:
                continue
            old_v = old_row.get(field, "")
            new_v = new_row.get(field, "")
            if old_v == new_v:
                continue
            if field == "facility" and new_row.get("facility_name") == old_row.get("facility_name"):
                # "facility" is an alias of facility_name; avoid duplicate rows.
                continue
            conn.execute(
                "INSERT INTO candidate_edit_history "
                "(candidate_id, field_name, old_value, new_value, edited_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (candidate_id, field, str(old_v), str(new_v), _now()),
            )
            written += 1
        # Record sensitive-field change summaries WITHOUT the values.
        for field, label in SENSITIVE_FIELD_LABELS.items():
            if field not in new_row:
                continue
            old_v = str(old_row.get(field, "") or "").strip()
            new_v = str(new_row.get(field, "") or "").strip()
            if old_v != new_v and new_v:
                conn.execute(
                    "INSERT INTO candidate_edit_history "
                    "(candidate_id, field_name, old_value, new_value, edited_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (candidate_id, field, label + " updated", label + " updated", _now()),
                )
                written += 1
        if written:
            conn.commit()
        return written
    finally:
        conn.close()


def list_edit_history(candidate_id: int, limit: int = 100) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM candidate_edit_history WHERE candidate_id = ? "
            "ORDER BY history_id DESC LIMIT ?",
            (candidate_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Previous / next candidate navigation ─────────────────────────────────────


def get_neighbor_candidates(candidate_id: int) -> dict:
    """Return previous/next candidate ids in deterministic order (by id).

    Used when a filtered-list order is not practical. Returns None for the
    bound neighbours (no previous / no next).
    """
    conn = _get_connection()
    try:
        prev = conn.execute(
            "SELECT candidate_id FROM candidates WHERE candidate_id < ? "
            "ORDER BY candidate_id DESC LIMIT 1", (candidate_id,)
        ).fetchone()
        next = conn.execute(
            "SELECT candidate_id FROM candidates WHERE candidate_id > ? "
            "ORDER BY candidate_id ASC LIMIT 1", (candidate_id,)
        ).fetchone()
        return {
            "prev_id": prev["candidate_id"] if prev else None,
            "next_id": next["candidate_id"] if next else None,
        }
    finally:
        conn.close()


# ── Documents ───────────────────────────────────────────────────────────────


def insert_document(meta: dict) -> int:
    conn = _get_connection()
    try:
        row = {
            "candidate_id": meta.get("candidate_id"),
            "filename": meta.get("filename", ""),
            "file_type": meta.get("file_type", ""),
            "file_size": meta.get("file_size", 0),
            "document_type": meta.get("document_type", "Other"),
            "extraction_status": meta.get("extraction_status", "pending"),
            "uploaded_at": _now(),
        }
        cur = conn.execute(
            "INSERT INTO documents (candidate_id, filename, file_type, file_size, "
            "document_type, extraction_status, uploaded_at) "
            "VALUES (:candidate_id, :filename, :file_type, :file_size, "
            ":document_type, :extraction_status, :uploaded_at)",
            row,
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_documents() -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM documents ORDER BY document_id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_documents_for_candidate(candidate_id: int) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM documents WHERE candidate_id = ? ORDER BY document_id",
            (candidate_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Master load history ─────────────────────────────────────────────────────


def record_master_load(master_type: str, filename: str, row_count: int, status: str = "ok") -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO master_load_history (master_type, filename, row_count, loaded_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (master_type, filename, row_count, _now(), status),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def last_master_load(master_type: str) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM master_load_history WHERE master_type = ? ORDER BY id DESC LIMIT 1",
            (master_type,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_master_load_history(limit: int = 20) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM master_load_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Manual validation history ────────────────────────────────────────────────
# Validation runs are stored SEPARATELY from real candidate records. They never
# create candidates, never touch production batches, and never store Aadhaar
# images / full addresses / raw OCR text.


def save_validation_run(
    case_id: str,
    test_type: str,
    status: str,
    expected_summary: str,
    actual_summary: str,
    notes: str = "",
) -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO manual_validation_runs "
            "(case_id, test_type, status, expected_summary, actual_summary, notes, validated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, test_type, status, expected_summary, actual_summary, notes, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_validation_runs(limit: int = 200) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM manual_validation_runs ORDER BY validation_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_validation_runs() -> int:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM manual_validation_runs").fetchone()
        return int(row["c"]) if row else 0
    finally:
        conn.close()


# ── Generated files ──────────────────────────────────────────────────────────


def create_generated_file(
    batch_id: int,
    filename: str,
    file_path: str,
    date_folder: str,
    candidate_count: int = 0,
    generation_status: str = "generated",
    portal_result_path: str = "",
    portal_failure_path: str = "",
) -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO generated_files (batch_id, filename, file_path, date_folder, "
            "generated_at, candidate_count, generation_status, portal_result_path, "
            "portal_failure_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, filename, file_path, date_folder, _now(), candidate_count,
             generation_status, portal_result_path, portal_failure_path),
        )
        conn.commit()
        new_id = cur.lastrowid
        record_batch_event(batch_id, "Excel Generated",
                           f"Onboarding file '{filename}' generated.")
        return new_id
    finally:
        conn.close()


def get_generated_file(file_id: int) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM generated_files WHERE file_id = ?", (file_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_generated_files(batch_id: Optional[int] = None, limit: int = 20) -> list[dict]:
    conn = _get_connection()
    try:
        if batch_id is not None:
            rows = conn.execute(
                "SELECT * FROM generated_files WHERE batch_id = ? ORDER BY file_id DESC LIMIT ?",
                (batch_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM generated_files ORDER BY file_id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_candidates_generated(candidate_ids: list[int], generated_file_id: int) -> None:
    if not candidate_ids:
        return
    conn = _get_connection()
    try:
        for cid in candidate_ids:
            conn.execute(
                "UPDATE candidates SET excel_generated = 'true', generated_file_id = ? "
                "WHERE candidate_id = ?",
                (generated_file_id, cid),
            )
        conn.commit()
    finally:
        conn.close()


# ── Generation audit ─────────────────────────────────────────────────────────


def record_generation_audit(
    batch_id: int,
    generated_file_id: int,
    candidate_ids: list[int],
    template_name: str,
    template_version: str,
    status: str,
    error_message: str = "",
) -> int:
    conn = _get_connection()
    try:
        import json as _json
        cur = conn.execute(
            "INSERT INTO generation_audit (batch_id, generated_file_id, generated_at, "
            "candidate_ids, template_name, template_version, status, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, generated_file_id, _now(), _json.dumps(candidate_ids),
             template_name, template_version, status, error_message),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_generation_history(limit: int = 20) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM generation_audit ORDER BY audit_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Daily master export tracking ─────────────────────────────────────────────


def upsert_daily_master(row: dict) -> None:
    """Insert or update (by candidate_id) a row in the daily master table.

    ``row`` must contain ``candidate_id``; SQLite remains the source of truth
    and this table is only an operational export mirror.
    """
    cid = row.get("candidate_id")
    if cid is None:
        return
    columns = [
        "candidate_id", "name", "mobile", "dob", "doj", "masked_aadhaar", "address",
        "entity", "cost_code", "operation", "team", "designation", "facility_type",
        "facility_name", "location_code", "salary", "migrant", "contractor", "lob",
        "status", "created_at", "generated_file", "portal_status", "portal_remarks",
    ]
    conn = _get_connection()
    try:
        exists = conn.execute(
            "SELECT id FROM daily_master WHERE candidate_id = ?", (cid,)
        ).fetchone()
        if exists:
            sets = ", ".join(f"{c} = ?" for c in columns)
            vals = [row.get(c, "") for c in columns] + [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), cid]
            conn.execute(f"UPDATE daily_master SET {sets}, updated_at = ? WHERE candidate_id = ?", vals)
        else:
            cols = ", ".join(columns)
            placeholders = ", ".join(["?"] * len(columns))
            vals = [row.get(c, "") for c in columns]
            conn.execute(
                f"INSERT INTO daily_master ({cols}) VALUES ({placeholders})", vals
            )
        conn.commit()
    finally:
        conn.close()


def get_daily_master_mirror() -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM daily_master ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Generated file portal tracking ──────────────────────────────────────────


def update_generated_file(file_id: int, **fields) -> bool:
    """Update portal-tracking fields on a generated_file record."""
    allowed = {
        "portal_status", "portal_status_raw", "portal_uploaded_at", "portal_reference",
        "portal_result_path", "portal_failure_path", "portal_upload_id",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return False
    conn = _get_connection()
    try:
        set_sql = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(
            f"UPDATE generated_files SET {set_sql} WHERE file_id = ?",
            [*sets.values(), file_id],
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_candidates_for_generated_file(generated_file_id: int) -> list[dict]:
    """Candidates linked to a generated file (via generated_file_id)."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM candidates WHERE generated_file_id = ?",
            (generated_file_id,),
        ).fetchall()
        return [_row_to_candidate(r) for r in rows]
    finally:
        conn.close()


def get_portal_history_for_candidate(candidate_id: int, limit: int = 20) -> list[dict]:
    """Safe portal activity for a candidate, via its generated file.

    Only exposes non-sensitive portal bookkeeping fields; never credentials,
    cookies, tokens, or passwords.
    """
    conn = _get_connection()
    try:
        file_id = conn.execute(
            "SELECT generated_file_id FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if not file_id or not file_id["generated_file_id"]:
            return []
        gid = file_id["generated_file_id"]
        uploads = conn.execute(
            "SELECT portal_upload_id, generated_file_id, batch_id, filename, "
            "started_at, completed_at, portal_status, portal_reference, "
            "result_file_path, error_file_path "
            "FROM portal_uploads WHERE generated_file_id = ? "
            "ORDER BY portal_upload_id DESC LIMIT ?",
            (gid, limit),
        ).fetchall()
        live = conn.execute(
            "SELECT live_upload_id, generated_file_id, candidate_id, "
            "flow_status, started_at, submitted_at, portal_status, "
            "portal_reference, success_count, failed_count, total_count, "
            "creation_remarks "
            "FROM live_uploads WHERE candidate_id = ? "
            "ORDER BY live_upload_id DESC LIMIT ?",
            (candidate_id, limit),
        ).fetchall()
        return {
            "uploads": [dict(r) for r in uploads],
            "live": [dict(r) for r in live],
        }
    finally:
        conn.close()


def get_generated_files_for_candidate(candidate_id: int, limit: int = 20) -> list[dict]:
    """Generated files linked to a candidate (via generated_file_id) or its batch."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT batch_id, generated_file_id FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if not row:
            return []
        files = []
        if row["generated_file_id"]:
            files = conn.execute(
                "SELECT * FROM generated_files WHERE file_id = ? "
                "ORDER BY file_id DESC LIMIT 1", (row["generated_file_id"],),
            ).fetchall()
        if not files and row["batch_id"]:
            files = conn.execute(
                "SELECT * FROM generated_files WHERE batch_id = ? "
                "ORDER BY file_id DESC LIMIT ?", (row["batch_id"], limit),
            ).fetchall()
        return [dict(r) for r in files]
    finally:
        conn.close()


# ── Portal uploads ───────────────────────────────────────────────────────────


def create_portal_upload(generated_file_id: int, batch_id: int, filename: str,
                         started_at: str, portal_status: str) -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO portal_uploads (generated_file_id, batch_id, filename, "
            "started_at, completed_at, portal_status, portal_status_raw, "
            "portal_reference, result_file_path, error_file_path, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (generated_file_id, batch_id, filename, started_at, None, portal_status,
             "", "", "", "", ""),
        )
        conn.commit()
        new_id = cur.lastrowid
        record_batch_event(batch_id, "Portal Submitted",
                           f"Portal upload started for '{filename}'.")
        return new_id
    finally:
        conn.close()


def get_portal_upload(portal_upload_id: int) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM portal_uploads WHERE portal_upload_id = ?",
            (portal_upload_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_portal_upload_for_file(generated_file_id: int) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM portal_uploads WHERE generated_file_id = ? "
            "ORDER BY portal_upload_id DESC LIMIT 1",
            (generated_file_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_portal_upload(portal_upload_id: int, **fields) -> bool:
    allowed = {
        "started_at", "completed_at", "portal_status", "portal_status_raw",
        "portal_reference", "result_file_path", "error_file_path", "error_message",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return False
    conn = _get_connection()
    try:
        set_sql = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(
            f"UPDATE portal_uploads SET {set_sql} WHERE portal_upload_id = ?",
            [*sets.values(), portal_upload_id],
        )
        conn.commit()
        if "portal_status" in sets:
            up = conn.execute(
                "SELECT b.batch_id, b.filename FROM portal_uploads b "
                "WHERE b.portal_upload_id = ?", (portal_upload_id,)
            ).fetchone()
            if up:
                bstatus = str(sets["portal_status"] or "").lower()
                if bstatus == "success":
                    record_batch_event(up["batch_id"], "Portal Completed",
                                       f"Portal upload succeeded for '{up['filename']}'.")
                elif bstatus == "failed":
                    record_batch_event(up["batch_id"], "Portal Failed",
                                       f"Portal upload failed for '{up['filename']}'.")
        return True
    finally:
        conn.close()


def list_portal_uploads(batch_id: Optional[int] = None, limit: int = 50) -> list[dict]:
    conn = _get_connection()
    try:
        if batch_id is not None:
            rows = conn.execute(
                "SELECT * FROM portal_uploads WHERE batch_id = ? "
                "ORDER BY portal_upload_id DESC LIMIT ?", (batch_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM portal_uploads ORDER BY portal_upload_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Portal audit ─────────────────────────────────────────────────────────────


def record_portal_audit(event: str, status: str = "info",
                        generated_file_id: Optional[int] = None,
                        detail: str = "") -> int:
    """Record a non-sensitive portal automation event.

    Never log credentials, full Aadhaar, full address, or session cookies here.
    """
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO portal_audit (generated_file_id, event, status, detail, "
            "occurred_at) VALUES (?, ?, ?, ?, ?)",
            (generated_file_id, event, status, detail, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_portal_audit(limit: int = 50) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM portal_audit ORDER BY audit_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Live single-candidate upload (controlled live-test mode) ────────────────


def create_live_upload(generated_file_id: int, candidate_id: Optional[int],
                       flow_status: str = "pending") -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO live_uploads (generated_file_id, candidate_id, "
            "manual_confirmed, flow_status, portal_status, updated_at) "
            "VALUES (?, ?, 0, ?, NULL, ?)",
            (generated_file_id, candidate_id, flow_status, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_live_upload(live_upload_id: int, **fields) -> bool:
    allowed = {
        "candidate_id", "manual_confirmed", "flow_status", "started_at",
        "submitted_at", "portal_status", "portal_status_raw", "portal_reference",
        "success_count", "failed_count", "total_count", "creation_remarks",
        "result_file_path", "error_file_path", "error_message",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return False
    sets["updated_at"] = _now()
    conn = _get_connection()
    try:
        set_sql = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(
            f"UPDATE live_uploads SET {set_sql} WHERE live_upload_id = ?",
            [*sets.values(), live_upload_id],
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_live_upload(live_upload_id: int) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM live_uploads WHERE live_upload_id = ?",
            (live_upload_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_live_upload_for_file(generated_file_id: int) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM live_uploads WHERE generated_file_id = ? "
            "ORDER BY live_upload_id DESC LIMIT 1",
            (generated_file_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_live_uploads(limit: int = 50) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM live_uploads ORDER BY live_upload_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Admin Master Management (facilities / roles / aliases) ──────────────────
# These tables are managed through the Admin Master Data UI. They never modify
# the source Excel masters; admin records are a separate layer that is merged
# into the effective master view used by the production resolver.


def _row_to_int(row, col, default=0):
    try:
        return int(row[col] or default)
    except (TypeError, ValueError):
        return default


# ── Facilities ───────────────────────────────────────────────────────────────


def create_master_facility(data: dict) -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO master_facilities "
            "(facility_name, location_code, entity, operation, cost_code, "
            " facility_type, state, active, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["facility_name"], data.get("location_code", ""),
                data.get("entity", ""), data.get("operation", ""),
                data.get("cost_code", ""),
                data.get("facility_type", "Delivery Hub"),
                data.get("state", ""), 1 if data.get("active", 1) else 0,
                data.get("source", "Admin"), _now(), _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_master_facility(facility_id: int) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM master_facilities WHERE facility_id = ?", (facility_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_master_facility_by_name(facility_name: str) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM master_facilities WHERE facility_name = ?",
            (facility_name,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_master_facilities(active_only: bool = False) -> list[dict]:
    conn = _get_connection()
    try:
        sql = "SELECT * FROM master_facilities"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY facility_name"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_master_facility_editable(facility_id: int, data: dict) -> bool:
    """Update only the editable (non-identity) fields + Active state.

    Identity fields (facility_name, entity, operation, cost_code) are immutable
    after creation — callers must deactivate + recreate instead. Changing them
    is therefore disallowed here.
    """
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE master_facilities SET location_code = ?, state = ?, "
            "active = ?, updated_at = ? WHERE facility_id = ?",
            (
                data.get("location_code", ""), data.get("state", ""),
                1 if data.get("active", 1) else 0, _now(), facility_id,
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def set_master_facility_active(facility_id: int, active: bool) -> bool:
    return update_master_facility_editable(
        facility_id, {"active": 1 if active else 0}
    )


def facility_name_in_admin(name: str) -> Optional[dict]:
    return get_master_facility_by_name(name)


# ── Roles ────────────────────────────────────────────────────────────────────


def create_master_role(official_name: str, entity_scope: str, operation: str,
                       cost_codes: list[str], aliases: list[str],
                       source: str = "Admin") -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO master_roles "
            "(official_name, entity_scope, operation, active, source, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?, ?)",
            (official_name, entity_scope, operation, source, _now(), _now()),
        )
        role_id = cur.lastrowid
        for cc in cost_codes:
            if cc:
                conn.execute(
                    "INSERT INTO master_role_cost_codes (role_id, cost_code) VALUES (?, ?)",
                    (role_id, cc),
                )
        for alias in aliases:
            a = (alias or "").strip().lower()
            if a:
                conn.execute(
                    "INSERT INTO master_role_aliases (role_id, alias, active) VALUES (?, ?, 1)",
                    (role_id, a),
                )
        conn.commit()
        return role_id
    finally:
        conn.close()


def get_master_role(role_id: int) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM master_roles WHERE role_id = ?", (role_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_master_role_by_name(official_name: str) -> Optional[dict]:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM master_roles WHERE official_name = ?", (official_name,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_master_roles(active_only: bool = False) -> list[dict]:
    conn = _get_connection()
    try:
        sql = "SELECT * FROM master_roles"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY official_name"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_master_role(role_id: int, entity_scope: str, operation: str,
                       cost_codes: list[str], aliases: list[str],
                       active: bool = True) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE master_roles SET entity_scope = ?, operation = ?, "
            "active = ?, updated_at = ? WHERE role_id = ?",
            (entity_scope, operation, 1 if active else 0, _now(), role_id),
        )
        conn.execute("DELETE FROM master_role_cost_codes WHERE role_id = ?", (role_id,))
        for cc in cost_codes:
            if cc:
                conn.execute(
                    "INSERT INTO master_role_cost_codes (role_id, cost_code) VALUES (?, ?)",
                    (role_id, cc),
                )
        conn.execute("DELETE FROM master_role_aliases WHERE role_id = ?", (role_id,))
        for alias in aliases:
            a = (alias or "").strip().lower()
            if a:
                conn.execute(
                    "INSERT INTO master_role_aliases (role_id, alias, active) VALUES (?, ?, 1)",
                    (role_id, a),
                )
        conn.commit()
        return True
    finally:
        conn.close()


def set_master_role_active(role_id: int, active: bool) -> bool:
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE master_roles SET active = ?, updated_at = ? WHERE role_id = ?",
            (1 if active else 0, _now(), role_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_role_cost_codes(role_id: int) -> list[str]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT cost_code FROM master_role_cost_codes WHERE role_id = ? ORDER BY cost_code",
            (role_id,),
        ).fetchall()
        return [r["cost_code"] for r in rows]
    finally:
        conn.close()


def get_role_aliases(role_id: int, active_only: bool = True) -> list[str]:
    conn = _get_connection()
    try:
        sql = "SELECT alias FROM master_role_aliases WHERE role_id = ?"
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY alias"
        rows = conn.execute(sql, (role_id,)).fetchall()
        return [r["alias"] for r in rows]
    finally:
        conn.close()


def get_roles_for_cost_code_admin(cost_code: str) -> list[str]:
    """Admin-defined official roles compatible with a cost code."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT r.official_name FROM master_roles r "
            "JOIN master_role_cost_codes c ON c.role_id = r.role_id "
            "WHERE c.cost_code = ? AND r.active = 1 ORDER BY r.official_name",
            (cost_code,),
        ).fetchall()
        return [r["official_name"] for r in rows]
    finally:
        conn.close()


def get_admin_role_aliases() -> dict:
    """Flatten all active admin role aliases -> official role names."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT r.official_name, a.alias FROM master_role_aliases a "
            "JOIN master_roles r ON r.role_id = a.role_id "
            "WHERE a.active = 1 AND r.active = 1"
        ).fetchall()
        return {r["alias"]: r["official_name"] for r in rows}
    finally:
        conn.close()


def role_name_in_admin(name: str) -> Optional[dict]:
    return get_master_role_by_name(name)


# ── Change history ───────────────────────────────────────────────────────────


def record_master_change(master_type: str, record_id: int, action: str,
                         old_value_summary: str, new_value_summary: str,
                         changed_by: str = "local-admin") -> int:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO master_change_history "
            "(master_type, record_id, action, old_value_summary, new_value_summary, "
            " changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (master_type, record_id, action, old_value_summary,
             new_value_summary, changed_by, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_master_changes(limit: int = 200) -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM master_change_history ORDER BY change_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


init_db()
