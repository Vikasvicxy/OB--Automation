"""Release Readiness gate computation.

Conservatively computes a release status from live, verifiable state:
version, DB integrity, master configuration, OCR availability, safety flags,
communication channels, backup, and portal selectors. It NEVER auto-asserts a
higher release rung — advancing beyond manual UAT requires an explicit operator
decision (real manual UAT run, credentials, controlled enterprise trial).

Status ladder (only ever auto-computed to the first two rungs):
    NOT READY
    READY FOR MANUAL UAT
    READY FOR CONTROLLED LOCAL TRIAL      (operator-confirmed)
    READY FOR FIRST CONTROLLED ESAMPARK TEST (operator-confirmed)
"""

from __future__ import annotations

from app import database
from app import health as health_service
from app import backup_service as backup
from app.config import APP_VERSION, FeatureFlags
from app.uat_catalog import UAT_CRITICAL
from app.communication import get_communication_service

NOT_READY = "NOT READY"
READY_MANUAL_UAT = "READY FOR MANUAL UAT"
READY_LOCAL_TRIAL = "READY FOR CONTROLLED LOCAL TRIAL"
READY_FIRST_ESAMPARK = "READY FOR FIRST CONTROLLED ESAMPARK TEST"

ALLOWED_STATUSES = [NOT_READY, READY_MANUAL_UAT, READY_LOCAL_TRIAL,
                    READY_FIRST_ESAMPARK]

DANGEROUS_FLAGS = ["REAL_UPLOAD_ENABLED", "ESAMPARK_LIVE_TEST_MODE",
                   "COMMUNICATION_ENABLED"]
COMM_CHANNELS = ["whatsapp", "email", "sms", "voice"]


def _flag(name: str) -> bool:
    return bool(getattr(FeatureFlags, name, False))


def release_gates() -> list[dict]:
    """Compute the ordered release-gate checklist. No secrets, no side effects."""
    rep = health_service.build_health_report()
    by_name = {c["name"]: c["status"] for c in rep["cards"]}
    cv = communication_status()

    def gate(key, label, passed, detail=""):
        return {"key": key, "label": label, "passed": bool(passed), "detail": detail}

    gates = [
        gate("version", "Version set (1.0.0-rc1)",
             APP_VERSION not in ("", "0.0.0", "0.1.0"), APP_VERSION),
        gate("db_integrity", "Database integrity OK",
             by_name.get("Database Integrity") == "OK",
             by_name.get("Database Integrity", "unknown")),
        gate("facility_master", "Facility master configured",
             by_name.get("Facility Master") == "OK",
             by_name.get("Facility Master", "unknown")),
        gate("designation_master", "Designation master configured",
             by_name.get("Designation Master") == "OK",
             by_name.get("Designation Master", "unknown")),
        gate("ocr", "OCR backend available",
             by_name.get("OCR Backend") == "OK",
             by_name.get("OCR Backend", "unknown")),
        gate("selectors", "Portal selectors configured",
             by_name.get("Portal Selectors") == "OK",
             by_name.get("Portal Selectors", "unknown")),
        gate("safety_flags", "All dangerous operation flags disabled",
             not any(_flag(f) for f in DANGEROUS_FLAGS),
             "enabled: " + ", ".join(f for f in DANGEROUS_FLAGS if _flag(f))
             if any(_flag(f) for f in DANGEROUS_FLAGS) else "all locked"),
        gate("comm_channels", "All communication channels disabled",
             not any(cv.get(ch) for ch in COMM_CHANNELS),
             "enabled: " + ", ".join(ch for ch in COMM_CHANNELS if cv.get(ch))
             if any(cv.get(ch) for ch in COMM_CHANNELS) else "dry-run only"),
        gate("backup", "Backup exists",
             by_name.get("Last Backup") == "OK",
             by_name.get("Last Backup", "unknown")),
        gate("health", "Health reports no ERROR",
             by_name.get("Database") != "ERROR"
             and rep.get("overall") in ("OK", "NEEDS ATTENTION"),
             rep.get("overall", "unknown")),
    ]
    return gates


def communication_status() -> dict:
    try:
        svc = get_communication_service()
        return {ch: bool(svc.is_channel_enabled(ch)) for ch in COMM_CHANNELS}
    except Exception:  # noqa: BLE001
        return {ch: False for ch in COMM_CHANNELS}


def uat_critical_summary() -> dict:
    """Best summary of the latest completed UAT run for critical tests."""
    runs = database.uat_list_runs(50)
    for r in runs:
        if r.get("status") != "completed":
            continue
        results = database.uat_get_results(r["run_id"])
        crit = {res["test_id"]: res["status"] for res in results
                if res["test_id"] in UAT_CRITICAL}
        total = len(UAT_CRITICAL)
        passed = sum(1 for st in crit.values() if st == "PASS")
        failed = sum(1 for st in crit.values() if st in ("FAIL", "BLOCKED"))
        not_tested = total - passed - failed
        return {
            "run_id": r["run_id"],
            "total": total, "passed": passed, "failed": failed,
            "not_tested": not_tested,
            "all_critical_pass": failed == 0 and not_tested == 0,
        }
    return {
        "run_id": None, "total": len(UAT_CRITICAL),
        "passed": 0, "failed": 0, "not_tested": len(UAT_CRITICAL),
        "all_critical_pass": False,
    }


def release_status() -> dict:
    """Compute the release readiness status (auto-computes to rung 2 max)."""
    gates = release_gates()
    all_pass = all(g["passed"] for g in gates)
    uat = uat_critical_summary()

    status = READY_MANUAL_UAT if all_pass else NOT_READY

    return {
        "version": APP_VERSION,
        "git_commit": backup.git_commit(),
        "status": status,
        "allowed_statuses": ALLOWED_STATUSES,
        "gates": gates,
        "gates_passed": sum(1 for g in gates if g["passed"]),
        "gates_total": len(gates),
        "uat": uat,
        "note": ("Higher rungs (controlled local trial / first controlled "
                 "eSampark test) require an explicit operator decision after "
                 "manual UAT; this page never auto-asserts them."),
    }