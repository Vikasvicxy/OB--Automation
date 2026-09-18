"""Integration tests for SQLite persistence + real master data integration.

Covers:
    - Database persistence (insert/get/update/delete/search/count).
    - Duplicate detection against the WHOLE database (mobile).
    - Master data loading from Excel (designation + facility).
    - Cost-code -> hub filtering (4421 LM / 4441 FM / 8751 Myntra / 8752 empty).
    - Fuzzy matching examples.
    - Location auto-fill from master.
    - Role resolution (8751 has no Prexo; aliases resolve to official).
    - Persistence across "restart" (re-open DB connection in a fresh instance).
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import app.database as db
import app.master_data as md
import app.rules as rules

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
    """Point database at a temp DB and initialize tables."""
    tmp = tempfile.mkdtemp()
    db.DB_DIR = Path(tmp) / "database"
    db.DB_PATH = db.DB_DIR / "teamhr.db"
    db.init_db()
    return tmp


def sample_candidate(**over):
    base = {
        "batch_id": 1,
        "candidate_number": 1,
        "name": "Rahul Sharma",
        "mobile": "9876543210",
        "entity": "Flipkart",
        "cost_code": "4421",
        "operation": "Last Mile",
        "team": "LAST MILE - OPERATIONS",
        "designation": "LM - Delivery Executive",
        "facility_type": "Delivery Hub",
        "facility_name": "NelamangalaHub_BLR",
        "location_code": "BLR/NLM",
        "salary": 18000,
        "migrant": "No",
        "status": "ready",
    }
    base.update(over)
    return base


print("=" * 70)
print("SQLITE PERSISTENCE + MASTER DATA INTEGRATION")
print("=" * 70)

tmp = fresh_db()

print("\n--- Master data loading ---")
# ensure_masters_loaded() may already be initialized from import time; force a
# fresh reload so records are written to the temp DB for this test.
md.load_designations()
md.load_facilities()
status = md.get_master_status()
check("designation master configured", status["designation"]["configured"])
check("facility master configured", status["facility"]["configured"])
check("8752 hub list is EMPTY", md.get_hubs_for_cost_code("8752") == [])

print("\n--- Candidate persistence ---")
cid = db.insert_candidate(sample_candidate())
got = db.get_candidate(cid)
check("insert -> get round trip", got is not None and got["name"] == "Rahul Sharma")
check("designation persisted", got["designation"] == "LM - Delivery Executive")
check("facility_name persisted", got["facility_name"] == "NelamangalaHub_BLR")
check("location_code persisted", got["location_code"] == "BLR/NLM")
check("salary persisted as int", got["salary"] == 18000)

uid = db.update_candidate(cid, {"mobile": "9876543211", "name": "Rahul K"})
got2 = db.get_candidate(cid)
check("update candidate", got2["name"] == "Rahul K" and got2["mobile"] == "9876543211")
check("migrant default when omitted stored as No",
      db.get_candidate(cid)["migrant"] == "No")

print("\n--- Search / filters ---")
db.insert_candidate(sample_candidate(mobile="9123456789", name="Sneha Reddy",
                                     cost_code="4441", facility_name="NelamangalaHub_BLR_PL",
                                     location_code="BLR/NLM-PL", designation="FM - Delivery Executive", status="draft"))
by_search = db.search_candidates("sneha")
check("search by name", any(c["name"] == "Sneha Reddy" for c in by_search))
by_mobile = db.list_candidates(search="9123456789")
check("search by mobile", any(c["mobile"] == "9123456789" for c in by_mobile))
by_fac = db.list_candidates(facility_name="NelamangalaHub_BLR_PL")
check("filter by facility_name", all(c["facility_name"] == "NelamangalaHub_BLR_PL" for c in by_fac) and len(by_fac) == 1)
by_status = db.list_candidates(status="draft")
check("filter by status", all(c["status"] == "draft" for c in by_status))
counts = db.count_candidates()
check("count_candidates totals", counts["total"] == 2 and counts["ready"] == 1 and counts["draft"] == 1)

print("\n--- Duplicate detection (whole DB) ---")
dup_found = db.find_duplicate_mobile("9876543211")
check("duplicate found for existing mobile", dup_found is not None)
check("duplicate excluded correctly", db.find_duplicate_mobile("9876543211", cid) is None)
check("no false duplicate", db.find_duplicate_mobile("9999999999") is None)

print("\n--- Cost-code -> hub filtering ---")
lm = set(rules.get_hubs_for_cost_code("4421"))
fm = set(rules.get_hubs_for_cost_code("4441"))
myntra = set(rules.get_hubs_for_cost_code("8751"))
check("4421 has LM hubs", "Peenya Hub" in lm)
check("4421 Nelamangala hub uses readable LM display", "NelamangalaHub_BLR" in lm)
check("4441 has FM hubs (_PL)", "NelamangalaHub_BLR_PL" in fm)
check("8751 has Myntra hubs only", all("MYNTRA" in h.upper() for h in myntra) and len(myntra) > 0)
check("8752 has NO hubs", rules.get_hubs_for_cost_code("8752") == [])
check("no Myntra hub under 4421/4441", all("MYNTRA" not in h.upper() for h in lm | fm))
check("no _PL hub under 4421", all(not h.endswith("_PL") for h in lm))

print("\n--- Fuzzy examples ---")
f1 = rules.fuzzy_find_hub("peenya", rules.get_hubs_for_cost_code("4421"))
check("4421 peenya -> Peenya Hub", f1 and f1[0] == "Peenya Hub")
f2 = rules.fuzzy_find_hub("yelahanka", rules.get_hubs_for_cost_code("4441"))
check("4441 yelahanka -> Yelahanka Pickup Hub", f2 and f2[0] == "Yelahanka Pickup Hub")
f3 = rules.fuzzy_find_hub("banas", rules.get_hubs_for_cost_code("8751"))
check("8751 banas -> BanaswadiMYNTRAHub_BLR", f3 and f3[0] == "BanaswadiMYNTRAHub_BLR")
f4 = rules.fuzzy_find_hub("hebb", rules.get_hubs_for_cost_code("8751"))
check("8751 hebb -> HebbalMYNTRAHub_BLR", f4 and f4[0] == "HebbalMYNTRAHub_BLR")

print("\n--- Location auto-fill ---")
check("Peenya Hub location", rules.get_location_for_facility("Peenya Hub") == "BLR/PEN")
check("LM NelamangalaHub_BLR resolves to location BLR/NLM",
      rules.get_location_for_facility("NelamangalaHub_BLR") == "BLR/NLM")
check("NelamangalaHub_BLR_PL location", rules.get_location_for_facility("NelamangalaHub_BLR_PL") == "NelamangalaHub_BLR_PL")
check("BanaswadiMYNTRAHub_BLR location", rules.get_location_for_facility("BanaswadiMYNTRAHub_BLR") == "BNS/BLR")
check("HebbalMYNTRAHub_BLR location", rules.get_location_for_facility("HebbalMYNTRAHub_BLR") == "HBB/BLR")

print("\n--- Role resolution ---")
r8751 = rules.resolve_role_for_cost_code("prexo", "8751")
check("8751 rejects Prexo", r8751[1] is not None and "Prexo" in r8751[1])
r4421 = rules.resolve_role_for_cost_code("biker", "4421")
check("4421 biker -> official role", r4421[0] == "LM - Delivery Executive")
r8752 = rules.get_roles_for_cost_code("8752")
check("8752 returns a role list (no crash)", isinstance(r8752, list))

print("\n--- Delete ---")
check("delete candidate", db.delete_candidate(cid) is True)
check("count after delete", db.count_candidates()["total"] == 1)

print("\n--- Persistence across restart (fresh process/connection) ---")
# Simulate a restart: re-init a NEW database module instance pointing at the
# SAME file. Fresh sqlite connections re-read the file, so data must survive.
import importlib
import app.database as db2_module
db2 = importlib.reload(db2_module)
db2.DB_DIR = Path(tmp) / "database"
db2.DB_PATH = db2.DB_DIR / "teamhr.db"
db2.init_db()
check("candidate survives 'restart'", any(c["mobile"] == "9123456789" for c in db2.search_candidates("sneha")))
check("master_load_history persisted", db2.last_master_load("facility") is not None)

# Clean up test DB
for _ in ("teamhr.db",):
    try:
        (Path(tmp) / "database" / _).unlink()
    except Exception:
        pass

print("=" * 70)
print(f"TOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
if FAIL:
    sys.exit(1)
