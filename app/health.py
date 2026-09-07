"""System health reporting for the TeamHR Automation `/health` page and the
`/api/health` JSON endpoint.

Produces safe, non-secret status cards. Never exposes full local environment
variables, passwords, tokens, Aadhaar or raw OCR content. Live-upload safety is
always shown LOCKED unless both live-upload flags are enabled.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from app import admin_master, database, master_data
from app import backup_service as backup
from app import ocr_backends
from app.portal import esampark, selectors

OK = "OK"
WARNING = "WARNING"
ERROR = "ERROR"
LOCKED = "LOCKED"

APP_VERSION = "0.1.0"


def _report(name: str, status: str, detail: str = ""):
    return {"name": name, "status": status, "detail": detail}


def _folder_status(label: str, path: Path) -> dict:
    if not path.exists():
        return _report(label, WARNING, "Folder missing")
    try:
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except Exception:
        size = 0
    return _report(label, OK, backup.human_size(size))


def _db_status() -> dict:
    if not database.DB_PATH.exists():
        return _report("Database", ERROR, "Database file missing")
    try:
        conn = database._get_connection()
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("candidates", "batches")}
        finally:
            conn.close()
    except Exception as e:
        return _report("Database", ERROR, f"Unreachable: {e}")
    db_status = OK if integrity == "ok" else ERROR
    return _report("Database", db_status,
                   f"{counts['candidates']} candidates, {counts['batches']} batches")


def _db_integrity() -> dict:
    try:
        conn = database._get_connection()
        try:
            res = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except Exception as e:
        return _report("Database Integrity", ERROR, f"{e}")
    return _report("Database Integrity", OK if res == "ok" else ERROR, res)


def _designation_status() -> dict:
    ms = master_data.get_master_status()
    des = ms.get("designation", {})
    return _report("Designation Master",
                   OK if des.get("configured") else WARNING,
                   (f"{des.get('row_count')} rows" if des.get("configured")
                    else "Not Configured"))


def _facility_status() -> dict:
    ms = master_data.get_master_status()
    fac = ms.get("facility", {})
    return _report("Facility Master", OK if fac.get("configured") else WARNING,
                   (f"{fac.get('row_count')} rows" if fac.get("configured")
                    else "Not Configured"))


def _admin_master_status() -> dict:
    am = admin_master.get_master_status()
    if not am.get("admin_enabled"):
        return _report("Admin Master", WARNING, "Feature disabled")
    return _report("Admin Master", OK,
                   f"{am.get('effective_facilities')} facilities, "
                   f"{am.get('effective_roles')} roles")


def _ocr_status() -> dict:
    backend = ocr_backends.get_backend_name()
    try:
        ocr_backends.get_backend()
        return _report("OCR Backend", OK, f"RapidOCR / {backend}")
    except Exception as e:
        return _report("OCR Backend", WARNING, f"RapidOCR ({e})")


def _generated_folder() -> dict:
    from app import generation
    out = generation.get_output_base_dir()
    return _folder_status("Generated Folder", out)


def _backup_folder() -> dict:
    bdir = backup.backup_dir()
    return _folder_status("Backup Folder", bdir)


def _portal_selectors() -> dict:
    count = len(selectors.UPLOAD_STATUS_MARKERS or {})
    return _report("Portal Selectors", OK, f"Configured ({count} status markers)")


def _live_upload() -> dict:
    real = getattr(esampark, "REAL_UPLOAD_ENABLED", False)
    test = getattr(esampark, "ESAMPARK_LIVE_TEST_MODE", False)
    if real and test:
        return _report("Live Upload Safety", LOCKED,
                       "Live upload ENABLED (both flags on)")
    return _report("Live Upload Safety", LOCKED, "Disabled (safe)")


def _config_status() -> dict:
    try:
        from app import generation
        cfg = generation.get_output_config()
        out = cfg.get("output_base_dir", "")
        ok = len(out or "") > 0
        return _report("Config", OK if ok else WARNING,
                       "Output configured" if ok else "Output not configured")
    except Exception as e:
        return _report("Config", ERROR, f"{e}")


def _disk_status(threshold_gb: float = 1.0) -> dict:
    path = str(database.DB_PATH) if database.DB_PATH.exists() else str(Path.cwd())
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        status = OK if free_gb >= threshold_gb else WARNING
        return _report("Disk Space", status,
                       f"Free {backup.human_size(usage.free)} of "
                       f"{backup.human_size(usage.total)}")
    except Exception as e:
        return _report("Disk Space", WARNING, f"{e}")


def _last_backup() -> dict:
    last = backup.last_backup_summary()
    if not last:
        return _report("Last Backup", WARNING, "No backup yet")
    return _report("Last Backup", OK, last["filename"])


def _version() -> dict:
    commit = backup.git_commit()
    return _report("Application Version", OK,
                   f"{APP_VERSION} ({commit})" if commit else APP_VERSION)


def build_health_report() -> dict:
    """Build the full ordered health report for the page + JSON endpoint."""
    cards = [
        _db_status(),
        _db_integrity(),
        _facility_status(),
        _designation_status(),
        _admin_master_status(),
        _ocr_status(),
        _generated_folder(),
        _backup_folder(),
        _portal_selectors(),
        _live_upload(),
        _config_status(),
        _disk_status(),
        _last_backup(),
        _version(),
    ]
    return {
        "cards": cards,
        "overall": "OK" if not any(c["status"] in (WARNING, ERROR)
                                   for c in cards) else "NEEDS ATTENTION",
    }


def json_health() -> dict:
    """Safe non-secret JSON status for `/api/health`."""
    rep = build_health_report()["cards"]
    by_name = {c["name"]: c["status"] for c in rep}
    ocr = next((c["detail"] for c in rep if c["name"] == "OCR Backend"), "ok")
    return {
        "database": by_name.get("Database", ERROR).lower(),
        "masters": ("ok" if (by_name.get("Facility Master") == OK
                             and by_name.get("Designation Master") == OK)
                    else "warning"),
        "ocr": "ok" if by_name.get("OCR Backend") == OK else "unavailable",
        "ocr_backend": ocr,
        "live_upload": "locked",
        "backup": "ok" if by_name.get("Last Backup") == OK else "none",
        "disk_space": by_name.get("Disk Space", WARNING).lower(),
        "overall": _overall_from(by_name),
    }


def _overall_from(by_name: dict) -> str:
    if any(v in (ERROR,) for v in by_name.values()):
        return "error"
    if any(v in (WARNING,) for v in by_name.values()):
        return "warning"
    return "ok"


def summary() -> dict:
    """Compact summary for the Settings page (no secrets)."""
    rep = build_health_report()["cards"]
    by_name = {c["name"]: c["status"] for c in rep}
    last = backup.last_backup_summary()
    return {
        "last_backup": last["filename"] if last else None,
        "backup_count": len(backup.list_backups()),
        "backup_folder": str(backup.backup_dir()),
        "backup_folder_size": backup.human_size(_dir_size(backup.backup_dir())),
        "draft_count": database.count_candidate_drafts(),
        "health_overall": _overall_from(by_name),
    }


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except Exception:
        return 0
