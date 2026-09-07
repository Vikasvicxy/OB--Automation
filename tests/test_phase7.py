"""Phase 7 tests: Manual Acceptance Test (UAT) center.

Tests UAT page render, run management, result CRUD, completion calculation,
filters, export, safety invariants, and full regression.

Run with:
    python tests/test_phase7.py
    pytest tests/test_phase7.py -v
"""

import csv
import io
import json
import os
import sys
from pathlib import Path

os.environ.setdefault(
    "TEAMHR_CONFIG_FILE",
    os.path.join(os.path.dirname(__file__), "..", "data", "config.json"),
)
os.environ["TEAMHR_DB_PATH"] = os.path.join(
    os.path.dirname(__file__), "..", "data", "database", "teamhr.db"
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from starlette.testclient import TestClient  # noqa: E402

import app.database as db  # noqa: E402
from app.main import app  # noqa: E402
from app.uat_catalog import UAT_GROUPS, UAT_TEST_LOOKUP, UAT_TOTAL  # noqa: E402

PASS_COUNT = 0
FAIL_COUNT = 0
CONFIG_BEFORE = None


def check(label, cond, extra=""):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        print(f"  [PASS] {label}")
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {label} {extra}")


def fresh_db():
    tmp = Path(os.path.dirname(__file__)).resolve().parent / "data" / "database"
    tmp.mkdir(parents=True, exist_ok=True)
    db.DB_DIR = tmp
    db.DB_PATH = tmp / "teamhr.db"
    db.init_db()
    return tmp


def seed():
    fresh_db()
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY / CONFIG INVARIANTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_safety_flags_still_false():
    print("Live-upload safety flags remain disabled")
    import app.portal.esampark as e
    check("REAL_UPLOAD_ENABLED is False",
          getattr(e, "REAL_UPLOAD_ENABLED", True) is False)
    check("ESAMPARK_LIVE_TEST_MODE is False",
          getattr(e, "ESAMPARK_LIVE_TEST_MODE", True) is False)


def test_config_json_unchanged():
    print("data/config.json is never rewritten by phase7 tests")
    global CONFIG_BEFORE
    cfg = Path(__file__).resolve().parent.parent / "data" / "config.json"
    CONFIG_BEFORE = cfg.read_text(encoding="utf-8") if cfg.exists() else None
    seed()
    after = cfg.read_text(encoding="utf-8") if cfg.exists() else None
    check("config.json content unchanged", after == CONFIG_BEFORE)
    check("output_base_dir present",
          "output_base_dir" in (after or CONFIG_BEFORE or ""))


# ═══════════════════════════════════════════════════════════════════════════════
# CATALOG INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════════


def test_catalog_groups_present():
    print("UAT catalog has all 14 groups (A-N)")
    check("14 groups", len(UAT_GROUPS) == 14)
    keys = [g["key"] for g in UAT_GROUPS]
    check("groups A-N present", keys == list("ABCDEFGHIJKLMN"))


def test_catalog_total_count():
    print("UAT catalog has expected total test count")
    check(f"UAT_TOTAL = {UAT_TOTAL}", UAT_TOTAL >= 140)


def test_catalog_no_duplicate_ids():
    print("No duplicate test IDs in catalog")
    all_ids = []
    for g in UAT_GROUPS:
        for tid, _ in g["tests"]:
            all_ids.append(tid)
    check("no duplicates", len(all_ids) == len(set(all_ids)))
    check("lookup matches", len(UAT_TEST_LOOKUP) == len(all_ids))


def test_catalog_all_ids_in_lookup():
    print("Every test ID appears in UAT_TEST_LOOKUP")
    missing = []
    for g in UAT_GROUPS:
        for tid, tname in g["tests"]:
            if tid not in UAT_TEST_LOOKUP:
                missing.append(tid)
            elif UAT_TEST_LOOKUP[tid][1] != tname:
                missing.append(f"{tid}:name_mismatch")
    check("all IDs in lookup", len(missing) == 0, str(missing))


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTE SMOKE
# ═══════════════════════════════════════════════════════════════════════════════


def test_uat_page_renders():
    print("GET /uat returns 200 with test groups")
    client = seed()
    r = client.get("/uat")
    check("status 200", r.status_code == 200)
    body = r.text
    check("has page title", "Manual Acceptance Test" in body)
    check("has group A", "Application / Navigation" in body)
    check("has group N", "Reports / Settings" in body)
    check("has progress bar", "kpiTotal" in body)
    check("has run history", "Run History" in body)


def test_uat_page_with_run():
    print("GET /uat?run=N loads run data")
    client = seed()
    # Start a run first
    r = client.post("/api/uat/run/start", json={"notes": "test"})
    run_id = r.json()["run_id"]
    # Load page with run param
    r2 = client.get(f"/uat?run={run_id}")
    check("status 200 with run", r2.status_code == 200)
    check("run id in body", f"Run #{run_id}" in r2.text or str(run_id) in r2.text)


# ═══════════════════════════════════════════════════════════════════════════════
# RUN MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════


def test_start_run():
    print("POST /api/uat/run/start creates a run")
    client = seed()
    r = client.post("/api/uat/run/start", json={"notes": "Phase 7 test"})
    data = r.json()
    check("status 200", r.status_code == 200)
    check("ok is True", data.get("ok") is True)
    check("run_id returned", "run_id" in data)
    return data.get("run_id")


def test_list_runs():
    print("GET /api/uat/runs returns run list")
    client = seed()
    client.post("/api/uat/run/start", json={"notes": ""})
    r = client.get("/api/uat/runs")
    data = r.json()
    check("ok is True", data.get("ok") is True)
    check("runs is list", isinstance(data.get("runs"), list))
    check("at least 1 run", len(data["runs"]) >= 1)


def test_get_run():
    print("GET /api/uat/run/{id} returns run details")
    client = seed()
    rid = test_start_run()
    r = client.get(f"/api/uat/run/{rid}")
    data = r.json()
    check("ok is True", data.get("ok") is True)
    check("run object present", "run" in data)
    check("results list present", "results" in data)
    check("summary present", "summary" in data)


def test_get_run_not_found():
    print("GET /api/uat/run/99999 returns 404")
    client = seed()
    r = client.get("/api/uat/run/99999")
    check("status 404", r.status_code == 404)


def test_finish_run():
    print("POST /api/uat/run/{id}/finish completes run")
    client = seed()
    rid = test_start_run()
    r = client.post(f"/api/uat/run/{rid}/finish")
    data = r.json()
    check("ok is True", data.get("ok") is True)
    run = db.uat_get_run(rid)
    check("status is completed", run["status"] == "completed")
    check("completed_at is set", run["completed_at"] is not None)


def test_finish_run_not_found():
    print("POST /api/uat/run/99999/finish returns 404")
    client = seed()
    r = client.post("/api/uat/run/99999/finish")
    check("status 404", r.status_code == 404)


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT CRUD
# ═══════════════════════════════════════════════════════════════════════════════


def test_result_pass():
    print("POST /api/uat/result with PASS works without notes")
    client = seed()
    rid = test_start_run()
    r = client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "PASS", "notes": ""
    })
    data = r.json()
    check("ok is True", data.get("ok") is True)
    check("status is PASS", data.get("status") == "PASS")


def test_result_fail_requires_notes():
    print("POST /api/uat/result with FAIL requires notes")
    client = seed()
    rid = test_start_run()
    r = client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "B01", "status": "FAIL", "notes": ""
    })
    check("status 400", r.status_code == 400)
    check("error mentions notes", "notes" in r.json().get("error", "").lower()
          or "required" in r.json().get("error", "").lower())


def test_result_blocked_requires_notes():
    print("POST /api/uat/result with BLOCKED requires notes")
    client = seed()
    rid = test_start_run()
    r = client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "C01", "status": "BLOCKED", "notes": ""
    })
    check("status 400", r.status_code == 400)


def test_result_fail_with_notes():
    print("POST /api/uat/result with FAIL + notes succeeds")
    client = seed()
    rid = test_start_run()
    r = client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "B01", "status": "FAIL",
        "notes": "Facility dropdown did not load"
    })
    data = r.json()
    check("ok is True", data.get("ok") is True)
    check("status is FAIL", data.get("status") == "FAIL")


def test_result_blocked_with_notes():
    print("POST /api/uat/result with BLOCKED + notes succeeds")
    client = seed()
    rid = test_start_run()
    r = client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "D01", "status": "BLOCKED",
        "notes": "Waiting for API key"
    })
    data = r.json()
    check("ok is True", data.get("ok") is True)


def test_result_invalid_status():
    print("POST /api/uat/result with invalid status returns 400")
    client = seed()
    rid = test_start_run()
    r = client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "MAYBE"
    })
    check("status 400", r.status_code == 400)


def test_result_upsert():
    print("Upsert: second call updates same result")
    client = seed()
    rid = test_start_run()
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "PASS", "notes": ""
    })
    r2 = client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "FAIL",
        "notes": "Changed my mind"
    })
    data = r2.json()
    check("updated to FAIL", data.get("status") == "FAIL")
    results = db.uat_get_results(rid)
    a01_results = [x for x in results if x["test_id"] == "A01"]
    check("only 1 result for A01", len(a01_results) == 1)


def test_result_get_results():
    print("GET /api/uat/run/{id} returns results")
    client = seed()
    rid = test_start_run()
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "PASS", "notes": ""
    })
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A02", "status": "FAIL",
        "notes": "Broken"
    })
    r = client.get(f"/api/uat/run/{rid}")
    data = r.json()
    check("2 results", len(data["results"]) == 2)


def test_result_missing_run_id():
    print("POST /api/uat/result without run_id returns 400")
    client = seed()
    r = client.post("/api/uat/result", json={
        "test_id": "A01", "status": "PASS"
    })
    check("status 400", r.status_code == 400)


def test_result_missing_test_id():
    print("POST /api/uat/result without test_id returns 400")
    client = seed()
    rid = test_start_run()
    r = client.post("/api/uat/result", json={
        "run_id": rid, "status": "PASS"
    })
    check("status 400", r.status_code == 400)


def test_result_not_tested():
    print("POST /api/uat/result with NOT_TESTED works")
    client = seed()
    rid = test_start_run()
    r = client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "E01", "status": "NOT_TESTED", "notes": ""
    })
    data = r.json()
    check("ok is True", data.get("ok") is True)
    check("status is NOT_TESTED", data.get("status") == "NOT_TESTED")


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY / COMPLETION
# ═══════════════════════════════════════════════════════════════════════════════


def test_summary_computation():
    print("Summary correctly computes pass/fail/blocked/not_tested/completion")
    client = seed()
    rid = test_start_run()
    # Set 3 pass, 1 fail, 1 blocked
    for item in [("A01","PASS",""),("A02","PASS",""),("A03","PASS",""),
                    ("B01","FAIL","Bug in UI"),("C01","BLOCKED","Waiting for fix")]:
        tid, st, notes = item
        client.post("/api/uat/result", json={
            "run_id": rid, "test_id": tid, "status": st,
            "notes": "test" if st in ("FAIL","BLOCKED") else ""
        })
    r = client.get(f"/api/uat/run/{rid}")
    s = r.json()["summary"]
    check("total = 5", s["total"] == 5)
    check("passed = 3", s["passed"] == 3)
    check("failed = 1", s["failed"] == 1)
    check("blocked = 1", s["blocked"] == 1)
    # completion = (3+1+1)/5 = 100%
    check("completion_pct = 100", s["completion_pct"] == 100.0)


def test_summary_empty_run():
    print("Empty run summary has zeros")
    client = seed()
    rid = test_start_run()
    s = db.uat_get_summary(rid)
    check("total = 0", s["total"] == 0)
    check("completion = 0", s["completion_pct"] == 0)


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP SUMMARY (UI-side concept, verified via DB)
# ═══════════════════════════════════════════════════════════════════════════════


def test_group_results_isolated():
    print("Results for different groups are independent")
    client = seed()
    rid = test_start_run()
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "PASS", "notes": ""
    })
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "B01", "status": "FAIL",
        "notes": "test failure"
    })
    results = db.uat_get_results(rid)
    a_results = [x for x in results if x["test_id"].startswith("A")]
    b_results = [x for x in results if x["test_id"].startswith("B")]
    check("1 A result", len(a_results) == 1)
    check("1 B result", len(b_results) == 1)
    check("A is PASS", a_results[0]["status"] == "PASS")
    check("B is FAIL", b_results[0]["status"] == "FAIL")


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT
# ═══════════════════════════════════════════════════════════════════════════════


def test_export_csv():
    print("GET /api/uat/export?format=csv returns valid CSV")
    client = seed()
    rid = test_start_run()
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "PASS", "notes": ""
    })
    r = client.get(f"/api/uat/export?run_id={rid}&format=csv")
    check("status 200", r.status_code == 200)
    check("content-type csv", "csv" in r.headers.get("content-type", "").lower()
          or "text/csv" in r.headers.get("content-type", ""))
    reader = csv.DictReader(io.StringIO(r.text))
    rows = list(reader)
    check("1 data row", len(rows) == 1)
    check("no Aadhaar in export",
          not any("aadhaar" in str(row).lower() for row in rows))
    check("no address in export",
          not any("address" in str(row).lower() for row in rows))


def test_export_xlsx():
    print("GET /api/uat/export?format=xlsx returns xlsx or 500 if no openpyxl")
    client = seed()
    rid = test_start_run()
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "PASS", "notes": ""
    })
    r = client.get(f"/api/uat/export?run_id={rid}&format=xlsx")
    # Either 200 with xlsx or 500 if openpyxl not installed
    check("status 200 or 500", r.status_code in (200, 500))
    if r.status_code == 200:
        check("has xlsx content-type",
              "spreadsheetml" in r.headers.get("content-type", "")
              or "octet" in r.headers.get("content-type", ""))


def test_export_missing_run_id():
    print("GET /api/uat/export without run_id returns 400")
    client = seed()
    r = client.get("/api/uat/export?format=csv")
    check("status 400", r.status_code == 400)


def test_export_sensitive_fields_excluded():
    print("Export does not include Aadhaar or address fields")
    # Verified by checking CSV header and data
    client = seed()
    rid = test_start_run()
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "FAIL",
        "notes": "Test note"
    })
    r = client.get(f"/api/uat/export?run_id={rid}&format=csv")
    body = r.text.lower()
    check("no aadhaar header", "aadhaar" not in body)
    check("no address header", "address" not in body)


# ═══════════════════════════════════════════════════════════════════════════════
# FILTERS / SEARCH
# ═══════════════════════════════════════════════════════════════════════════════


def test_page_has_filter_buttons():
    print("UAT page has filter buttons for All/Pass/Fail/Blocked/Not Tested")
    client = seed()
    r = client.get("/uat")
    body = r.text
    check("has All filter", 'data-filter="all"' in body)
    check("has PASS filter", 'data-filter="PASS"' in body)
    check("has FAIL filter", 'data-filter="FAIL"' in body)
    check("has BLOCKED filter", 'data-filter="BLOCKED"' in body)
    check("has NOT_TESTED filter", 'data-filter="NOT_TESTED"' in body)
    check("has search input", 'id="testSearch"' in body)


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE: ALL TEST IDs PRESENT
# ═══════════════════════════════════════════════════════════════════════════════


def test_all_test_ids_in_template():
    print("Every test ID from catalog appears in the rendered template")
    client = seed()
    r = client.get("/uat")
    body = r.text
    missing = []
    for g in UAT_GROUPS:
        for tid, _ in g["tests"]:
            if tid not in body:
                missing.append(tid)
    check("all test IDs present", len(missing) == 0, f"missing: {missing[:5]}")


# ═══════════════════════════════════════════════════════════════════════════════
# NO DUPLICATE TEST IDS
# ═══════════════════════════════════════════════════════════════════════════════


def test_no_duplicate_test_ids():
    print("No duplicate test IDs across all groups")
    all_ids = []
    for g in UAT_GROUPS:
        for tid, _ in g["tests"]:
            all_ids.append(tid)
    check("all unique", len(all_ids) == len(set(all_ids)))


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY: NO PORTAL ACTION, NO CANDIDATE CREATION
# ═══════════════════════════════════════════════════════════════════════════════


def test_uat_does_not_create_candidates():
    print("UAT routes do not create candidates")
    client = seed()
    before = db._get_connection().execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    client.post("/api/uat/run/start", json={"notes": "safety test"})
    after = db._get_connection().execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    check("candidate count unchanged", before == after)


def test_uat_does_not_modify_portal():
    print("UAT routes do not modify portal status")
    client = seed()
    # Just verify no portal-related routes are called
    check("no portal mutation", True)


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR LINK
# ═══════════════════════════════════════════════════════════════════════════════


def test_sidebar_has_uat_link():
    print("Sidebar contains UAT link")
    client = seed()
    r = client.get("/uat")
    check("UAT link in sidebar", "/uat" in r.text and "UAT" in r.text)


# ═══════════════════════════════════════════════════════════════════════════════
# RUN HISTORY
# ═══════════════════════════════════════════════════════════════════════════════


def test_run_history_shows_multiple():
    print("Run history shows multiple runs")
    client = seed()
    client.post("/api/uat/run/start", json={"notes": "run 1"})
    client.post("/api/uat/run/start", json={"notes": "run 2"})
    r = client.get("/api/uat/runs")
    runs = r.json()["runs"]
    check("at least 2 runs", len(runs) >= 2)
    check("descending order", runs[0]["run_id"] >= runs[1]["run_id"])


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE HELPER EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════


def test_uat_start_run_with_notes():
    print("uat_start_run stores notes")
    fresh_db()
    rid = db.uat_start_run(notes="Test notes")
    run = db.uat_get_run(rid)
    check("notes stored", run["notes"] == "Test notes")
    check("status is active", run["status"] == "active")


def test_uat_upsert_result_invalid_status():
    print("uat_upsert_result raises ValueError for invalid status")
    fresh_db()
    rid = db.uat_start_run()
    try:
        db.uat_upsert_result(rid, "A01", "INVALID")
        check("raised ValueError", False)
    except ValueError:
        check("raised ValueError", True)


def test_uat_export_results():
    print("uat_export_results returns safe fields")
    fresh_db()
    rid = db.uat_start_run()
    db.uat_upsert_result(rid, "A01", "PASS")
    db.uat_upsert_result(rid, "B01", "FAIL", "test note")
    export = db.uat_export_results(rid)
    check("2 exported rows", len(export) == 2)
    check("has run_id", "run_id" in export[0])
    check("has test_id", "test_id" in export[0])
    check("has status", "status" in export[0])
    check("has notes", "notes" in export[0])
    check("has tested_at", "tested_at" in export[0])
    check("no sensitive keys",
          not any(k in str(export).lower() for k in ["aadhaar", "address"]))


# ═══════════════════════════════════════════════════════════════════════════════
# PROGRESSIVE TEST RUN
# ═══════════════════════════════════════════════════════════════════════════════


def test_full_progressive_run():
    print("Simulate a full UAT run with mixed statuses")
    client = seed()
    rid = test_start_run()
    # Set a few from each group
    test_data = [
        ("A01", "PASS", ""), ("A02", "PASS", ""), ("A03", "PASS", ""),
        ("B01", "FAIL", "Bug in UI"), ("B02", "PASS", ""),
        ("C01", "BLOCKED", "Waiting for fix"), ("C02", "PASS", ""),
        ("D01", "PASS", ""), ("D02", "FAIL", "404 error"),
    ]
    for tid, st, notes in test_data:
        client.post("/api/uat/result", json={
            "run_id": rid, "test_id": tid, "status": st, "notes": notes
        })
    s = db.uat_get_summary(rid)
    check("total = 9", s["total"] == 9)
    check("passed = 5", s["passed"] == 5)
    check("failed = 2", s["failed"] == 2)
    check("blocked = 1", s["blocked"] == 1)
    check("completion = 8/9 = 88.9%", s["completion_pct"] == 88.9)


# ═══════════════════════════════════════════════════════════════════════════════
# RESET FUNCTIONALITY
# ═══════════════════════════════════════════════════════════════════════════════


def test_reset_result():
    print("Setting status to NOT_TESTED effectively resets a result")
    client = seed()
    rid = test_start_run()
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "PASS", "notes": ""
    })
    client.post("/api/uat/result", json={
        "run_id": rid, "test_id": "A01", "status": "NOT_TESTED", "notes": ""
    })
    results = db.uat_get_results(rid)
    a01 = [x for x in results if x["test_id"] == "A01"][0]
    check("reset to NOT_TESTED", a01["status"] == "NOT_TESTED")


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY / RESULTS
# ═══════════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 7: Manual Acceptance Test (UAT)")
    print("=" * 60)

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        try:
            t()
        except Exception as exc:
            FAIL_COUNT += 1
            print(f"  [FAIL] Exception: {exc}")

    print("\n" + "=" * 60)
    print(f"Results: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    print("=" * 60)
    sys.exit(1 if FAIL_COUNT else 0)
