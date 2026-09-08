"""Backup, verify and safe-restore service for TeamHR Automation.

Backup folder:  ``data/backups/``
Filename:       ``TeamHR_Backup_YYYY-MM-DD_HH-MM-SS.zip``

Composition
-----------
Backups are built from an explicit allow-list, never by archiving the whole
``data/`` directory, so no unexpected secrets, raw documents, browser
profiles, cookies or session files leak into a backup.

A backup ZIP always contains:

* ``teamhr.db``  - a consistent snapshot of the SQLite database taken with the
  native :func:`sqlite3.Connection.backup` API (safe against concurrent
  writes). The restored DB must pass ``PRAGMA integrity_check``.
* ``manifest.json`` - metadata: backup_version, created_at, git commit,
  database filename/size, SHA-256 checksums for every file, and whether
  generated files were included.

Generated Excel files are **excluded by default** (only referenced by
metadata in the DB). They can be included explicitly via ``include_generated``.

Security
--------
Sensitive artifacts (raw Aadhaar images, recruiter screenshots, portal
auth_state/cookies/session, browser profiles, logs, caches, secrets) are
never included. Secrets are never written to the manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from app import database

BACKUP_VERSION = 1
BACKUP_PREFIX = "TeamHR_Backup_"
BACKUP_FILENAME_FMT = "TeamHR_Backup_%Y-%m-%d_%H-%M-%S.zip"

# Application state is captured entirely by the SQLite database. Generated
# files are macro/Excel outputs (large, regenerable) and are excluded unless
# explicitly requested. Raw documents are never included.
REQUIRED_TABLES = [
    "batches", "candidates", "generated_files", "portal_uploads",
    "candidate_events", "batch_events", "candidate_drafts", "system_events",
]

# Paths / globs we must NEVER place inside a backup, regardless of the
# allow-list logic (extra defense in depth).
SENSITIVE_TOKEN_FRAGMENTS = (
    "auth_state", "cookie", "session", "profile", "log", "cache",
    "secret", "token", "password", "credential", "ocr_benchmark",
    "self_onboarding", "masters", ".pytest", ".pycharm", "venv", "node_modules",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def backup_dir() -> Path:
    root = _project_root()
    return root / "data" / "backups"


def git_commit() -> str:
    """Return the current short git commit, or empty if unavailable."""
    root = _project_root()
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def human_size(num: float) -> str:
    """Human-readable byte size."""
    try:
        num = float(num or 0)
    except (TypeError, ValueError):
        num = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} TB"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_sqlite_snapshot(conn: sqlite3.Connection, dest: Path) -> None:
    """Write a consistent snapshot of ``conn`` to ``dest`` using the native
    SQLite backup API (supports concurrent writes safely)."""
    backup_conn = sqlite3.connect(str(dest))
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()


def _unique_backup_name(bdir: Path, base: str) -> str:
    """Return a non-colliding backup filename. If ``base`` already exists
    (e.g. two backups in the same second), append a numeric suffix so we never
    silently overwrite an existing backup."""
    if not (bdir / base).exists():
        return base
    stem = base[:-len(".zip")]
    i = 2
    while (bdir / f"{stem}-{i}.zip").exists():
        i += 1
    return f"{stem}-{i}.zip"


def _db_open() -> sqlite3.Connection:
    database.DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(database.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _resource_path(name: str) -> Path:
    mapping = {
        "self_onboarding": "data/masters/Self_Onboarding_Template.xlsx",
    }
    return _project_root() / mapping.get(name, name)


def list_backups() -> list[dict]:
    """Return backup records sorted newest-first. Never returns secrets."""
    bdir = backup_dir()
    records = []
    if not bdir.exists():
        return records
    for p in sorted(bdir.glob(f"{BACKUP_PREFIX}*.zip"), reverse=True):
        info = {
            "filename": p.name,
            "path": str(p),
            "created": p.stat().st_mtime,
            "size": p.stat().st_size,
            "human_size": human_size(p.stat().st_size),
            "include_generated": _zip_flag(p, "include_generated_files"),
            "verification": None,
        }
        records.append(info)
    return records


def _zip_flag(path: Path, key: str):
    try:
        with zipfile.ZipFile(str(path)) as zf:
            raw = zf.read("manifest.json")
            return json.loads(raw.decode("utf-8")).get(key)
    except Exception:
        return None


def last_backup_summary() -> Optional[dict]:
    recs = list_backups()
    return recs[0] if recs else None


def create_backup(include_generated: bool = False) -> dict:
    """Create a backup ZIP and return a summary dict (no secrets)."""
    bdir = backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime(BACKUP_FILENAME_FMT)
    filename = _unique_backup_name(bdir, base)
    zip_path = bdir / filename

    # 1. Consistent DB snapshot.
    db_snap = bdir / f".{filename}.db"
    conn = _db_open()
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"database integrity check failed: {integrity}")
        _safe_sqlite_snapshot(conn, db_snap)
        db_size = db_snap.stat().st_size
    finally:
        conn.close()

    # Optional: include generated Excel outputs (excluded by default).
    generated_entries = {}
    if include_generated:
        from app import generation
        out_root = generation.get_output_base_dir()
        if out_root.exists():
            for f in sorted(out_root.rglob("*.xlsx")):
                if f.is_file():
                    arc = f"generated/{f.relative_to(out_root).as_posix()}"
                    generated_entries[arc] = str(f)

    # 2. Assemble allow-listed files with checksums.
    files = {
        "teamhr.db": {"path": str(db_snap), "size": db_size,
                      "sha256": _file_sha256(db_snap)},
    }
    if generated_entries:
        for arc, real in generated_entries.items():
            files[arc] = {"path": real, "size": os.path.getsize(real),
                          "sha256": _file_sha256(Path(real))}

    manifest = {
        "backup_version": BACKUP_VERSION,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": "1.0.0-rc1",
        "git_commit": git_commit(),
        "database_filename": "teamhr.db",
        "database_size": db_size,
        "include_generated_files": include_generated,
        "schema_version": BACKUP_VERSION,
        "files": {arc: f["sha256"] for arc, f in files.items()},
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest["manifest_sha256"] = manifest_sha

    try:
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            for arc, spec in files.items():
                zf.write(spec["path"], arc)
    finally:
        try:
            db_snap.unlink(missing_ok=True)
        except Exception:
            pass

    database.record_system_event(
        "Backup Created",
        f"Backup {filename} created "
        f"({'with generated files' if include_generated else 'metadata only'}).",
    )

    return {
        "ok": True,
        "filename": filename,
        "path": str(zip_path),
        "size": zip_path.stat().st_size,
        "human_size": human_size(zip_path.stat().st_size),
        "created_at": manifest["created_at"],
        "include_generated_files": include_generated,
        "git_commit": manifest["git_commit"],
    }


def _extract_zip_safely(zip_path: Path, dest: Path) -> dict:
    """Extract a backup zip to ``dest`` guarding against path traversal."""
    entries = {}
    with zipfile.ZipFile(str(zip_path)) as zf:
        for member in zf.namelist():
            clean = Path(member)
            if clean.is_absolute() or ".." in clean.parts:
                return {"ok": False, "error": "unsafe archive path",
                        "entries": {}}
            target = dest / clean
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            entries[member] = target
    return {"ok": True, "entries": entries}


def verify_backup(zip_path: Path) -> dict:
    """Verify a backup archive.

    Returns a dict with ``valid`` (bool), ``status`` ("Valid"/"Invalid"),
    and ``warnings`` (list of strings). Requires the ZIP to be readable, hold a
    supported manifest, contain required files, match checksums, and pass the
    SQLite integrity check plus required-tables presence.
    """
    warnings = []
    status = "Valid"
    valid = True

    def fail(msg):
        nonlocal valid, status
        valid = False
        status = "Invalid"
        return {"ok": False, "valid": False, "status": status,
                "error": msg, "warnings": warnings}

    if not zip_path.exists():
        return fail("backup file not found")
    try:
        with zipfile.ZipFile(str(zip_path)) as zf:
            bad = zf.testzip()
            if bad is not None:
                return fail(f"archive corrupt: {bad}")
            names = zf.namelist()
            if "manifest.json" not in names:
                return fail("manifest.json missing")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError) as e:
        return fail(f"unreadable archive/manifest: {e}")

    if int(manifest.get("backup_version", 0)) != BACKUP_VERSION:
        return fail(f"unsupported backup_version: {manifest.get('backup_version')}")

    if "teamhr.db" not in manifest.get("files", {}):
        return fail("required database missing from manifest.files")
    required = {"manifest.json", "teamhr.db"}
    missing_required = required - set(names)
    if missing_required:
        return fail(f"required files missing: {sorted(missing_required)}")

    # Checksum check.
    tmpdir = Path(zip_path).parent / f".verify_{zip_path.stem}"
    try:
        tmpdir.mkdir(parents=True, exist_ok=True)
        ex = _extract_zip_safely(zip_path, tmpdir)
        if not ex["ok"]:
            return fail(ex.get("error", "extraction failed"))
        for arc, sha in manifest.get("files", {}).items():
            real = ex["entries"].get(arc)
            if real is None or not real.is_file():
                return fail(f"manifest file missing on disk: {arc}")
            if _file_sha256(real) != sha:
                return fail(f"checksum mismatch: {arc}")
        db_file = ex["entries"]["teamhr.db"]
        conn = sqlite3.connect(str(db_file))
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                return fail(f"restored db integrity: {integrity}")
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing_tables = [t for t in REQUIRED_TABLES if t not in tables]
            if missing_tables:
                return fail(f"required tables missing: {missing_tables}")
        finally:
            conn.close()
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    if not manifest.get("created_at"):
        warnings.append("manifest has no created_at")

    database.record_system_event("Backup Verified",
                                 f"Backup {zip_path.name} verified: {status}.")
    return {"ok": True, "valid": valid, "status": status, "warnings": warnings,
            "manifest": manifest}


def verify_backup_by_name(filename: str) -> dict:
    return verify_backup(backup_dir() / filename)


def _restore_blocked_reason() -> Optional[str]:
    """Return a blocker reason, or None if restore may proceed."""
    from app.portal import esampark
    if getattr(esampark, "REAL_UPLOAD_ENABLED", False) or \
       getattr(esampark, "ESAMPARK_LIVE_TEST_MODE", False):
        return "live upload mode is active"
    return None


def restore_backup(filename: str) -> dict:
    """Safely restore a backup.

    Flow: verify -> pre-restore backup -> final guard -> atomic replace ->
    reopen DB -> verify restored DB -> audit. Never overwrites silently.
    """
    zip_path = backup_dir() / filename
    if not zip_path.exists():
        return {"ok": False, "stage": "locate", "error": "backup not found"}

    blocked = _restore_blocked_reason()
    if blocked:
        return {"ok": False, "stage": "guard", "error": f"restore blocked: {blocked}"}

    check = verify_backup(zip_path)
    if not check.get("ok") or not check.get("valid"):
        return {"ok": False, "stage": "verify", "error": check.get("error", "invalid backup")}

    # Create an automatic PRE-RESTORE backup so the current state can never be
    # lost, even if the restore itself fails.
    pre = create_backup()
    if not pre.get("ok"):
        return {"ok": False, "stage": "pre-restore",
                "error": "could not create pre-restore backup"}
    database.record_system_event("Pre-Restore Backup Created",
                                 f"Pre-restore backup {pre.get('filename')}.")

    # Extract the validated DB into a temp location for an atomic swap.
    tmpdir = backup_dir() / f".restore_{Path(filename).stem}"
    new_db_tmp = None
    try:
        tmpdir.mkdir(parents=True, exist_ok=True)
        ex = _extract_zip_safely(zip_path, tmpdir)
        if not ex["ok"] or "teamhr.db" not in ex["entries"]:
            return {"ok": False, "stage": "extract",
                    "error": "could not extract database"}
        new_db_tmp = ex["entries"]["teamhr.db"]

        # Integrity + tables re-check on the file we will actually install.
        conn = sqlite3.connect(str(new_db_tmp))
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return {"ok": False, "stage": "verify-restored",
                        "error": "restored db failed integrity check"}
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = [t for t in REQUIRED_TABLES if t not in tables]
            if missing:
                return {"ok": False, "stage": "verify-restored",
                        "error": f"restored db missing tables: {missing}"}
        finally:
            conn.close()

        # Atomic replacement: write to a sibling temp, then swap.
        backup_target = database.DB_PATH.with_name(f"{database.DB_PATH.name}.before_restore")
        if database.DB_PATH.exists():
            shutil.copy2(str(database.DB_PATH), str(backup_target))
        swap_tmp = database.DB_PATH.with_name(f"{database.DB_PATH.name}.restoring")
        shutil.copy2(str(new_db_tmp), str(swap_tmp))
        os.replace(str(swap_tmp), str(database.DB_PATH))

        # Re-open and re-verify the live DB connection target.
        conn = _db_open()
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                return {"ok": False, "stage": "reopen",
                        "error": f"installed db integrity: {integrity}"}
        finally:
            conn.close()

    except Exception as e:  # pragma: no cover - defensive
        database.record_system_event("Restore Failed",
                                     f"Restore of {filename} failed at restore.")
        return {"ok": False, "stage": "restore", "error": str(e)}
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    database.record_system_event("Restore Completed",
                                 f"Restore from {filename} completed "
                                 f"(pre-restore {pre.get('filename')}).")
    return {"ok": True, "filename": filename,
            "pre_restore": pre.get("filename")}


def open_backup_folder() -> dict:
    bdir = backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(bdir))  # type: ignore[attr-defined]
        elif sys_platform() == "darwin":
            subprocess.Popen(["open", str(bdir)])
        else:
            subprocess.Popen(["xdg-open", str(bdir)])
        return {"ok": True, "path": str(bdir)}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": str(bdir)}


def sys_platform() -> str:
    import sys as _sys
    return _sys.platform


def bundle_file_count() -> int:
    """Total stored file entries across all backups (for overview)."""
    total = 0
    bdir = backup_dir()
    if not bdir.exists():
        return total
    for p in bdir.glob(f"{BACKUP_PREFIX}*.zip"):
        try:
            with zipfile.ZipFile(str(p)) as zf:
                total += len(zf.namelist())
        except Exception:
            continue
    return total
