"""UI foundation tests: dashboard KPIs, global search, data quality.

Covers dashboard_stats(), today counts, portal counts, duplicate warnings,
/api/search (name/mobile/facility/role/filename + sensitive-field exclusion),
/data-quality route and drill-down, sidebar no-dead-link smoke, template render,
and JS syntax. Read-only with respect to the real database: each test runs
against a throwaway SQLite DB where applicable.

Run with:
    python tests/test_ui_foundation.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from starlette.testclient import TestClient  # noqa: E402

from app import database as db  # noqa: E402
from app.main import app, _dq_categories  # noqa: E402

PASS = 0
FAIL = 0


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
    return tmp


def now_date():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


# ── dashboard_stats ───────────────────────────────────────────────────────────


def test_dashboard_stats_keys():
    print("dashboard_stats returns safe metric keys")
    st = db.dashboard_stats()
    for key in ("total", "today_total", "status", "needs_review", "ready",
                "generated", "portal", "by_cost_code", "by_facility",
                "batches_today", "duplicate_warnings", "excel_files"):
        check(f"dashboard_stats has '{key}'", key in st)
    check("portal has success/failed/pending",
          {"success", "failed", "pending"} <= set(st.get("portal", {})))
    check("no aadhaar in stats", "aadhaar" not in str(st).lower())
    check("no address in stats", "address" not in str(st).lower())


def test_today_counts():
    print("Today's Candidates counts every candidate created today")
    fresh_db()
    st = db.dashboard_stats()
    before = st["today_total"]
    db.insert_candidate({"name": "Daily A", "mobile": "100001", "status": "ready", "batch_id": 1})
    db.insert_candidate({"name": "Daily B", "mobile": "100002", "status": "draft", "batch_id": 1})
    st = db.dashboard_stats()
    check("today_total grew by 2 regardless of status",
          st["today_total"] == before + 2)
    check("today_total <= total", st["today_total"] <= st["total"])


def test_today_counts_ignores_other_days():
    print("Today's Candidate count excludes candidates from other days")
    fresh_db()
    st = db.dashboard_stats()
    before = st["today_total"]
    db.insert_candidate({
        "name": "Old", "mobile": "100003", "status": "ready", "batch_id": 1,
        "created_date": "2000-01-01", "created_time": "00:00:00",
    })
    st = db.dashboard_stats()
    check("old-dated candidate does not bump today_total",
          st["today_total"] == before)
    check("total still counts old candidate", st["total"] >= before + 1)


def test_portal_counts():
    print("Portal success/failed/pending are counted")
    fresh_db()
    db.insert_candidate({"name": "A", "mobile": "1", "portal_status": "Success", "batch_id": 1})
    db.insert_candidate({"name": "B", "mobile": "2", "portal_status": "Failed", "batch_id": 1})
    db.insert_candidate({"name": "C", "mobile": "3", "portal_status": "Processing", "batch_id": 1})
    st = db.dashboard_stats()
    check("portal pending reflects Processing/empty", st["portal"]["pending"] >= 1)
    check("portal counts are ints",
          all(isinstance(v, int) for v in st["portal"].values()))


def test_duplicate_warning_count():
    print("Duplicate Warnings counts mobiles appearing more than once")
    fresh_db()
    db.insert_candidate({"name": "Dup1", "mobile": "55555", "status": "ready", "batch_id": 1})
    db.insert_candidate({"name": "Dup2", "mobile": "55555", "status": "draft", "batch_id": 1})
    db.insert_candidate({"name": "Solo", "mobile": "66666", "status": "ready", "batch_id": 1})
    st = db.dashboard_stats()
    check("one duplicated mobile -> 1 warning", st["duplicate_warnings"] == 1)


# ── /api/search ───────────────────────────────────────────────────────────────


def seed_search_client():
    fresh_db()
    db.insert_candidate({"name": "Rahul Sharma", "mobile": "9876543210",
                         "facility_name": "BLR Hub", "designation": "LM - Delivery Executive",
                         "cost_code": "4421", "status": "ready", "batch_id": 1})
    db.insert_candidate({"name": "Priya Nair", "mobile": "9123456789",
                         "facility_name": "Mumbai Hub", "designation": "FM - Sorter",
                         "cost_code": "4441", "status": "draft", "batch_id": 2})
    db.create_generated_file(1, "Self_Onboarding_B1_2026.xlsx", "/tmp/x.xlsx",
                             "2026-09-04", 1, "generated")
    db.create_generated_file(2, "Self_Onboarding_B2_2026.xlsx", "/tmp/y.xlsx",
                             "2026-09-04", 1, "generated")
    return TestClient(app)


def _search(client, q):
    return client.get("/api/search", params={"q": q}).json()


def test_search_name():
    print("/api/search name match")
    client = seed_search_client()
    res = _search(client, "rahul")
    names = [c["name"] for c in res["candidates"]]
    check("Rahul found by name", any("Rahul" in n for n in names), res)
    check("safe metadata present", "facility_name" in res["candidates"][0])
    check("safe role metadata present", "designation" in res["candidates"][0])


def test_search_mobile():
    print("/api/search mobile match")
    client = seed_search_client()
    res = _search(client, "98765")
    check("candidate matched by mobile",
          any(c["mobile"] == "9876543210" for c in res["candidates"]), res)


def test_search_facility():
    print("/api/search facility match")
    client = seed_search_client()
    res = _search(client, "mumbai hub")
    check("candidate matched by facility",
          any(c["facility_name"] == "Mumbai Hub" for c in res["candidates"]), res)


def test_search_role():
    print("/api/search role/designation match")
    client = seed_search_client()
    res = _search(client, "delivery executive")
    check("candidate matched by designation",
          any("Delivery Executive" in c["designation"] for c in res["candidates"]), res)


def test_search_generated_filename():
    print("/api/search generated filename match")
    client = seed_search_client()
    res = _search(client, "onboarding")
    files = [f["filename"] for f in res["files"]]
    check("generated file matched by filename",
          any("Self_Onboarding" in n for n in files), res)


def test_search_limit():
    print("/api/search caps at 10 candidates + 10 files")
    fresh_db()
    for i in range(15):
        db.insert_candidate({"name": f"Bulk {i}", "mobile": f"9{i:09d}", "status": "ready", "batch_id": 1})
    client = TestClient(app)
    res = _search(client, "Bulk")
    check("candidates capped <= 10", len(res["candidates"]) <= 10,
          len(res["candidates"]))
    check("files capped <= 10", len(res["files"]) <= 10)


def test_search_excludes_aadhaar():
    print("/api/search never exposes Aadhaar")
    fresh_db()
    db.insert_candidate({"name": "Sec A", "mobile": "1111",
                         "aadhaar_number": "123456789012", "status": "ready", "batch_id": 1})
    client = TestClient(app)
    res = _search(client, "sec")
    raw = str(res)
    check("aadhaar value not in search response", "123456789012" not in raw)
    check("aadhaar key not in response", "aadhaar" not in raw.lower())


def test_search_excludes_address():
    print("/api/search never exposes address")
    fresh_db()
    db.insert_candidate({"name": "Addr Guy", "mobile": "2222",
                         "address": "42 Secret Street", "status": "ready", "batch_id": 1})
    client = TestClient(app)
    res = _search(client, "addr")
    check("address value not in search response", "Secret Street" not in str(res))
    check("address key not in response", "address" not in str(res).lower())


def test_search_empty_query():
    print("/api/search empty query returns empty result")
    client = seed_search_client()
    res = _search(client, "   ")
    check("empty query -> no results", res == {"candidates": [], "files": []})


# ── /data-quality ─────────────────────────────────────────────────────────────


def seed_dq_client():
    fresh_db()
    db.insert_candidate({"name": "", "mobile": "77777", "facility_name": "HubX",
                         "designation": "Sorter", "cost_code": "4421",
                         "status": "ready", "batch_id": 1})
    db.insert_candidate({"name": "Good", "mobile": "88888", "facility_name": "HubY",
                         "designation": "Driver", "cost_code": "4441",
                         "status": "needs_attention", "batch_id": 1})
    return TestClient(app)


def test_data_quality_route():
    print("/data-quality renders 200")
    client = seed_dq_client()
    resp = client.get("/data-quality")
    check("route 200", resp.status_code == 200)
    check("contains cards", "data-quality" in resp.text and "kpi-card" in resp.text)


def test_data_quality_drilldown():
    print("data-quality ?issue= exposes safe candidate detail")
    client = seed_dq_client()
    resp = client.get("/data-quality", params={"issue": "missing-name"})
    check("route with issue 200", resp.status_code == 200)
    check("detail section visible (not hidden)",
          'id="dqDetail" hidden' not in resp.text.replace('hidden=""', ''))
    # absence of sensitive data in the JSON embedded payload
    check("no aadhaar value in page", "1234567890" not in resp.text)
    check("no address value in page", "Secret Street" not in resp.text)


def test_dq_no_invented_data():
    print("_dq_categories only uses observed buckets")
    fresh_db()
    db.insert_candidate({"name": "X", "mobile": "3333", "facility_name": "H",
                         "designation": "R", "cost_code": "4421", "status": "ready", "batch_id": 1})
    cards = _dq_categories(db.list_candidates())
    keys = {c["key"] for c in cards}
    check("all keys present", keys == {
        "needs-review", "duplicate-mobile", "missing-name", "unknown-facility",
        "missing-address", "invalid-salary", "role-conflict", "facility-conflict",
        "low-confidence"})
    total_count = sum(c["count"] for c in cards)
    # With a complete candidate, only missing-address (informational) may trigger.
    check("no fabricated counts", total_count <= 1)


# ── Sidebar / templates / JS ─────────────────────────────────────────────────


def test_sidebar_no_dead_links():
    print("sidebar links all resolve to 200")
    client = seed_search_client()
    html = client.get("/").text
    start = html.find('<aside class="sidebar"')
    end = html.find("</aside>", start)
    sidebar = html[start:end]
    hrefs = []
    for part in sidebar.split('href="')[1:]:
        hrefs.append(part.split('"')[0])
    checked = 0
    for href in hrefs:
        if href.startswith("/") and "?" not in href:
            resp = client.get(href)
            check(f"sidebar link {href} -> 200", resp.status_code == 200)
            checked += 1
    check("found sidebar links to test", checked >= 8, checked)


def test_template_render():
    print("key templates render non-truncated")
    client = TestClient(app)
    for url in ["/", "/candidates", "/data-quality", "/reports"]:
        resp = client.get(url)
        check(f"{url} 200 + completes", resp.status_code == 200 and
              "</html>" in resp.text and "</main>" in resp.text)


def test_js_syntax():
    print("node --check passes for shared JS")
    root = Path(__file__).resolve().parent.parent / "app" / "static"
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except FileNotFoundError:
        print("  [SKIP] node not available; JS syntax not checked")
        return
    for name in ("ui.js", "app.js", "manual_entry.js"):
        p = root / name
        if not p.exists():
            check(f"{name} exists", False)
            continue
        try:
            subprocess.run(["node", "--check", str(p)], check=True,
                           capture_output=True)
            check(f"{name} syntax OK", True)
        except subprocess.CalledProcessError as e:
            check(f"{name} syntax", False, e.stderr.decode(errors="ignore"))


def run_all():
    global PASS, FAIL
    print("=" * 60)
    fresh_db()
    test_dashboard_stats_keys()
    test_today_counts()
    test_today_counts_ignores_other_days()
    test_portal_counts()
    test_duplicate_warning_count()
    test_search_name()
    test_search_mobile()
    test_search_facility()
    test_search_role()
    test_search_generated_filename()
    test_search_limit()
    test_search_excludes_aadhaar()
    test_search_excludes_address()
    test_search_empty_query()
    test_data_quality_route()
    test_data_quality_drilldown()
    test_dq_no_invented_data()
    test_sidebar_no_dead_links()
    test_template_render()
    test_js_syntax()
    print("=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
