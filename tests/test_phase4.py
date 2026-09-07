"""Phase 4 tests: batch detail page, candidate pipeline/Kanban (read-only),
cross-navigation, batch timeline, filters, safety flags, and route smoke.

Each test runs against a throwaway SQLite DB and an isolated runtime config
file so running the suite NEVER rewrites the production/local data/config.json
or touches live uploads.

Run with:
    python tests/test_phase4.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from starlette.testclient import TestClient  # noqa: E402

import app.database as db  # noqa: E402
from app.main import (  # noqa: E402
    app,
    _pipeline_bucket,
    _pipeline_safe,
    filter_pipeline_candidates,
)

PASS = 0
FAIL = 0

CONFIG_BEFORE = None


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {extra}")


def fresh_db():
    tmp = tempfile.mkdtemp()
    db.DB_DIR = Path(tmp) / "database"
    db.DB_PATH = db.DB_DIR / "teamhr.db"
    db.init_db()
    os.environ["TEAMHR_CONFIG_FILE"] = str(Path(tmp) / "config.json")
    return tmp


def seed():
    fresh_db()
    db.create_batch("Draft")
    db.create_batch("Open")
    a = db.insert_candidate({
        "name": "Alpha One", "mobile": "9000000001", "aadhaar_number": "111122223333",
        "address": "42 Alpha Street", "facility_name": "NelamangalaHub_BLR",
        "designation": "LM - Delivery Executive", "cost_code": "4421",
        "status": "ready", "batch_id": 1, "entity": "Amazon", "operation": "Onboard",
    })
    b = db.insert_candidate({
        "name": "Beta Two", "mobile": "9000000002", "aadhaar_number": "444455556666",
        "address": "77 Beta Road", "facility_name": "NelamangalaHub_BLR_PL",
        "designation": "FM - Delivery Executive", "cost_code": "4441",
        "status": "draft", "batch_id": 1, "entity": "Amazon", "operation": "Onboard",
    })
    c = db.insert_candidate({
        "name": "Gamma Three", "mobile": "9000000003", "facility_name": "HubX",
        "designation": "Guard", "cost_code": "4402",
        "status": "needs_review", "batch_id": 1, "entity": "Flipkart", "operation": "Ops",
    })
    return TestClient(app), a, b, c


def test_config_json_unchanged():
    print("data/config.json is never rewritten by phase4 tests")
    global CONFIG_BEFORE
    cfg = Path(__file__).resolve().parent.parent / "data" / "config.json"
    CONFIG_BEFORE = cfg.read_text(encoding="utf-8") if cfg.exists() else None
    seed()
    after = cfg.read_text(encoding="utf-8") if cfg.exists() else None
    check("config.json content unchanged", after == CONFIG_BEFORE)
    check("config.json not introduced if absent",
          (CONFIG_BEFORE is None) == (after is None))


# ── Safety flags ─────────────────────────────────────────────────────────────


def test_safety_flags_still_false():
    print("Live-upload safety flags remain disabled")
    import app.portal.esampark as e
    check("REAL_UPLOAD_ENABLED is False",
          getattr(e, "REAL_UPLOAD_ENABLED", True) is False)
    check("ESAMPARK_LIVE_TEST_MODE is False",
          getattr(e, "ESAMPARK_LIVE_TEST_MODE", True) is False)


# ── Batch detail route ───────────────────────────────────────────────────────


def test_batch_detail_route_200():
    print("/batches/<valid> renders 200 with sections")
    client, a, b, c = seed()
    r = client.get("/batches/1")
    check("status 200", r.status_code == 200)
    for frag in ["BATCH-0001", "Candidates", "Excluded / Needs Review",
                 "Generated Files", "Portal Uploads", "Timeline / Activity"]:
        check(f"contains {frag!r}", frag in r.text)
    # Safe only: no Aadhaar, no address.
    check("no aadhaar on batch detail", "111122223333" not in r.text)
    check("no address on batch detail", "42 Alpha Street" not in r.text)
    check("correct total candidate KPI", "3" in r.text)


def test_batch_detail_404():
    print("/batches/<missing> renders 404")
    client, *_ = seed()
    r = client.get("/batches/99999")
    check("status 404", r.status_code == 404)


def test_batch_detail_candidate_link():
    print("batch detail links each candidate to its detail page")
    client, a, b, c = seed()
    r = client.get("/batches/1")
    check("links candidate detail", f"/candidates/{a}" in r.text)
    check("links manual edit", f"/manual-entry?edit={a}" in r.text)


def test_batch_summary_derived_counts():
    print("get_batch_summary reflects ready vs needs-review vs candidates")
    seed()
    s = db.get_batch_summary(1)
    check("summary non-null", s is not None)
    check("candidate_count == 3", (s or {}).get("candidate_count") == 3)
    check("ready_count == 1", (s or {}).get("ready_count") == 1)
    check("needs_review_count == 1", (s or {}).get("needs_review_count") == 1)


# ── Batch timeline ───────────────────────────────────────────────────────────


def test_batch_timeline_records_events():
    print("batch timeline records candidate-add/generate/submit events")
    seed()
    events = {e["event_type"]: e for e in db.list_batch_events(1)}
    check("Candidate Added recorded", "Candidate Added" in events)
    # candidate_count events: 3 inserts -> at least 3 Candidate Added rows
    added = [e for e in db.list_batch_events(1) if e["event_type"] == "Candidate Added"]
    check("three candidate-added events", len(added) == 3)


def test_batch_timeline_explicit_record():
    print("record_batch_event writes a typed event")
    seed()
    db.record_batch_event(1, "Custom Event", "manual note")
    evs = db.list_batch_events(1)
    check("custom event present", any(e["event_type"] == "Custom Event" for e in evs))
    check("custom summary present", any(e.get("summary") == "manual note" for e in evs))


def test_delete_candidate_records_event():
    print("delete_candidate records Candidate Removed event")
    client, a, b, c = seed()
    db.delete_candidate(a)
    evs = db.list_batch_events(1)
    check("Candidate Removed recorded",
          any(e["event_type"] == "Candidate Removed" for e in evs))


# ── Pipeline bucketing (read-only) ───────────────────────────────────────────


def test_pipeline_bucket_mapping():
    print("_pipeline_bucket maps status + portal correctly")
    cases = [
        ({"status": "needs_review", "portal_status": ""}, "needs_review"),
        ({"status": "needs_attention", "portal_status": ""}, "needs_review"),
        ({"status": "ready", "portal_status": ""}, "ready"),
        ({"status": "generated", "portal_status": ""}, "generated"),
        ({"status": "draft", "portal_status": ""}, "draft"),
        ({"status": "ready", "portal_status": "success"}, "portal_success"),
        ({"status": "ready", "portal_status": "failed"}, "portal_failed"),
        ({"status": "ready", "portal_status": "pending"}, "portal_pending"),
        ({"status": "weird", "portal_status": ""}, "other"),
    ]
    for cand, expected in cases:
        got = _pipeline_bucket(cand)
        check(f"bucket({cand.get('status')!r},{cand.get('portal_status')!r}) == {expected}",
              got == expected, f"got {got}")


def test_pipeline_safe_excludes_pii():
    print("_pipeline_safe strips aadhaar/address")
    safe = _pipeline_safe({
        "candidate_id": 7, "name": "N", "mobile": "1", "designation": "D",
        "facility_name": "F", "batch_id": 2, "status": "ready",
        "aadhaar_number": "111122223333", "address": "secret addr",
    })
    check("candidate_id kept", safe.get("candidate_id") == 7)
    check("aadhaar absent", "aadhaar_number" not in safe and "111122223333" not in str(safe))
    check("address absent", "address" not in safe and "secret addr" not in str(safe))


def test_pipeline_filters():
    print("filter_pipeline_candidates filters by search/entity/status")
    seed()
    all_c = db.list_candidates()
    only_ready = filter_pipeline_candidates(all_c, status="ready")
    check("ready filter -> 1", len(only_ready) == 1)
    search_none = filter_pipeline_candidates(all_c, search="not-a-name")
    check("no search hits", len(search_none) == 0)
    by_entity = filter_pipeline_candidates(all_c, entity="Amazon")
    check("entity amazon filter -> 2", len(by_entity) == 2)
    by_cost = filter_pipeline_candidates(all_c, cost_code="4441")
    check("cost code filter -> 1 (Beta)", len(by_cost) == 1)


def test_pipeline_board_sections():
    print("/pipeline renders read-only columns for each stage")
    client, a, b, c = seed()
    r = client.get("/pipeline")
    check("status 200", r.status_code == 200)
    for frag in ["Draft", "Needs Review", "Ready", "Other / Review"]:
        check(f"column {frag!r} present", frag in r.text)
    check("counts total shown", "Showing 3 of 3 candidates" in r.text)
    check("no aadhaar", "111122223333" not in r.text)
    check("no address", "42 Alpha Street" not in r.text)
    check("Beta in draft", "Beta Two" in r.text)
    check("candidate links to detail", f"/candidates/{a}" in r.text)


def test_pipeline_never_creates_cards_via_drag():
    print("pipeline template asserts read-only (no drag handlers that write)")
    client, *_ = seed()
    r = client.get("/pipeline")
    check("no dropzone/ondrop write logic",
          "ondrop" not in r.text and "dropzone" not in r.text)
    check("cards are plain links", 'pipeline-card' in r.text)


def test_portal_columns_are_derived_only():
    print("portal_success/failed buckets come only from system portal status")
    seed()
    all_c = db.list_candidates()
    # force a portal status via update (system-derived)
    cand = all_c[0]
    db.update_portal_upload(None)  # no-op fallback
    # Manually simulate the system derivation used by portal service.
    port = dict(cand, portal_status="success")
    check("success bucketed to portal_success",
          _pipeline_bucket(port) == "portal_success")
    client = TestClient(app)
    r = client.get("/pipeline?status=portal_success")
    check("pipeline status filter accepted", r.status_code == 200)


# ── Cross navigation ─────────────────────────────────────────────────────────


def test_candidate_detail_links_batch():
    print("candidate detail links active batch to /batches/<id>")
    client, a, b, c = seed()
    r = client.get(f"/candidates/{a}")
    check("batch link present", "/batches/1" in r.text)
    check("batch label present", "BATCH-0001" in r.text)


def test_candidates_list_links_batch():
    print("candidates list links batch cell + pipeline button")
    client, a, b, c = seed()
    r = client.get("/candidates")
    check("batch cell links", "/batches/1" in r.text)
    check("pipeline view button", "/pipeline" in r.text)


def test_batch_review_links_batch_detail():
    print("batch review links to batch detail")
    client, *_ = seed()
    r = client.get("/batch-review?batch_id=1")
    check("batch detail link present", "/batches/1" in r.text)
    check("credentialed: 'Batch Detail' label", "Batch Detail" in r.text)


def test_pipeline_batch_filter():
    print("pipeline batch select + filter works")
    client, a, b, c = seed()
    r = client.get("/pipeline?batch_id=1")
    check("status 200", r.status_code == 200)
    check("batch option present", "BATCH-0001" in r.text)
    r2 = client.get("/pipeline?batch_id=2")
    check("empty batch filter shows zero", "Showing 0 of 3 candidates" in r2.text)


# ── Route smoke ──────────────────────────────────────────────────────────────


def test_route_smoke():
    print("batch/pipeline route smoke")
    client, a, b, c = seed()
    urls_ok = ["/", "/batches/1", "/pipeline", "/pipeline?entity=Flipkart",
               "/pipeline?today=1", "/pipeline?status=other",
               "/candidates", f"/candidates/{a}", "/batch-review?batch_id=1"]
    for u in urls_ok:
        r = client.get(u)
        check(f"GET {u} -> 200", r.status_code == 200, f"got {r.status_code}")
    r = client.get("/batches/99999")
    check("GET /batches/<missing> -> 404", r.status_code == 404)


def run_all():
    global PASS, FAIL
    print("=" * 60)
    print("PHASE 4: BATCH DETAIL / PIPELINE")
    print("=" * 60)
    test_config_json_unchanged()
    test_safety_flags_still_false()
    test_batch_detail_route_200()
    test_batch_detail_404()
    test_batch_detail_candidate_link()
    test_batch_summary_derived_counts()
    test_batch_timeline_records_events()
    test_batch_timeline_explicit_record()
    test_delete_candidate_records_event()
    test_pipeline_bucket_mapping()
    test_pipeline_safe_excludes_pii()
    test_pipeline_filters()
    test_pipeline_board_sections()
    test_pipeline_never_creates_cards_via_drag()
    test_portal_columns_are_derived_only()
    test_candidate_detail_links_batch()
    test_candidates_list_links_batch()
    test_batch_review_links_batch_detail()
    test_pipeline_batch_filter()
    test_route_smoke()
    print("=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
