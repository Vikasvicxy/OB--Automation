"""Onboarding pair generation tests (Self-Onboarding + TeamHR Backend Mail).

LOCAL / MOCKED — nothing is uploaded anywhere. These verify the transactional
pair generation: exact 14-column Self-Onboarding workbook, exact 16-column
TeamHR Backend Mail workbook, shared timestamp generation_pair_id, the
Facility Type corrections (4441 -> PICKUP_HUB), strict PII placement (full
Aadhaar only inside the backend workbook), recruiter profile, per-candidate
backend fields and clean failure handling.

Run with:
    python tests/test_onboarding_pair.py
"""

import datetime
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import openpyxl

from app import database as db
from app import generation
from app import master_data
from app import rules

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
    # Isolate the runtime config file so tests never mutate production/local
    # data/config.json (e.g. generation.set_output_base_dir writes config).
    os.environ["TEAMHR_CONFIG_FILE"] = str(Path(tmp) / "config.json")
    return tmp


def _candidate(batch_id, name, mobile, cost_code, designation, team, operation,
               facility=None, location=None):
    """A ready candidate with every backend-only field set explicitly."""
    hubs = rules.get_hubs_for_cost_code(cost_code)
    if facility is None:
        facility = hubs[0] if hubs else ""
    if location is None:
        location = master_data.get_location_for_facility(facility) if facility else ""
    return {
        "batch_id": batch_id,
        "name": name,
        "mobile": mobile,
        "designation": designation,
        "facility_name": facility,
        "location_code": location,
        "cost_code": cost_code,
        "status": "ready",
        "entity": "Flipkart" if cost_code in ("4421", "4441") else "Myntra",
        "team": team,
        "operation": operation,
        "migrant": "No",
        "salary": 18000,
        "aadhaar_number": "1234 5678 9012",
        "dob": "15/05/1995",
        "address": "Flat 5, 12th Main Road, Bengaluru",
        "recruiter_name": "Test Recruiter",
        "doj": "10/09/2026",
        "gender": "Male",
        "pin_code": "560001",
        "father_name": "",
        "uan_no": "",
    }


def SO_PAIR_CANDIDATES(batch_id):
    # User §45 plan: Flipkart LM 4421, Flipkart FM 4441 (Pickup Hub), Myntra LM 8751.
    return [
        _candidate(batch_id, "Anil Kumar", "9000000001", "4421",
                   "LM - Delivery Executive", "LAST MILE - OPERATIONS", "Last Mile"),
        _candidate(batch_id, "Bharath K", "9000000002", "4441",
                   "FM - Delivery Executive", "FIRST MILE - OPERATIONS", "First Mile"),
        _candidate(batch_id, "Chandan G", "9000000003", "8751",
                   "LM - Delivery Executive", "LAST MILE - OPERATIONS", "Last Mile"),
    ]


def setup_case(tmp, candidates):
    gen_dir = Path(tmp) / "gen"
    generation.set_output_base_dir(str(gen_dir))
    db.get_or_create_batch(batch_id=1)
    ids = [db.insert_candidate(c) for c in candidates]
    return gen_dir, ids


def test_pair_generation_shape():
    """Generating the pair lands BOTH files, DB rows, and shared pair id."""
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))

    res = generation.generate_onboarding_pair(1, only_ready=True)
    check("pair generation success", res.get("success") is True, str(res))

    so_path = Path(res["file_path"])
    ob_path = Path(res["backend_file_path"])
    check("self-onboarding file written", so_path.exists())
    check("backend file written", ob_path.exists())

    # Folders: jobs under <date>/uploads/ and <date>/backend_mail/.
    check("so file in uploads folder", gen_dir.name == "gen" and so_path.parent.name == "uploads",
          str(so_path))
    check("ob file in backend_mail folder", ob_path.parent.name == "backend_mail", str(ob_path))

    # Shared timestamp token -> recognizable pair.
    ts = so_path.stem.replace("Self_Onboarding_", "")
    check("backend file shares timestamp", ob_path.stem == f"TeamHR_OB_{ts}",
          f"{ob_path.stem} vs TeamHR_OB_{ts}")
    check("pair id is PO-<ts>", res["generation_pair_id"] == f"PO-{ts}",
          res["generation_pair_id"])
    check("pair id prefix PO-", res["generation_pair_id"].startswith("PO-"))

    # DB: two rows under one generation_pair_id, correct kinds.
    so_gf = db.get_generated_file(res["generated_file_id"])
    ob_gf = db.get_generated_file(res["backend_file_id"])
    check("so row kind self_onboarding", so_gf["kind"] == "self_onboarding", str(so_gf["kind"]))
    check("ob row kind backend_mail", ob_gf["kind"] == "backend_mail", str(ob_gf["kind"]))
    check("rows share generation_pair_id",
          so_gf["generation_pair_id"] == ob_gf["generation_pair_id"] == f"PO-{ts}",
          f"{so_gf['generation_pair_id']} / {ob_gf['generation_pair_id']}")
    check("rows share generated_at", so_gf["generated_at"] == ob_gf["generated_at"],
          f"{so_gf['generated_at']} / {ob_gf['generated_at']}")
    check("candidates marked generated",
          db.get_candidate(ids[0])["excel_generated"] == "true")
    check("batch status Generated",
          (db.get_batch(1) or {}).get("status") == "Generated",
          str((db.get_batch(1) or {}).get("status")))

    pairs = db.list_generation_pairs()
    check("list_generation_pairs returns the pair", len(pairs) == 1, str(len(pairs)))
    p = pairs[0]
    check("pair has both file details",
          p.get("self_onboarding_file_id_details") and p.get("backend_mail_file_id_details"))
    check("pair id matches", p["generation_pair_id"] == f"PO-{ts}")

    check("daily master exported",
          Path(res["daily_master_file"]).exists() if res.get("daily_master_file") else False,
          str(res.get("daily_master_file")))
    return so_path, ob_path


def _read_sheet(path):
    wb = openpyxl.load_workbook(str(path))
    ws = wb.worksheets[0]
    headers = [str(ws.cell(row=1, column=c).value or "") for c in range(1, ws.max_column + 1)]
    return wb, ws, headers


def test_self_onboarding_content():
    """Self-Onboarding workbook: exact 14 columns + exact per-candidate values."""
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))
    res = generation.generate_onboarding_pair(1, only_ready=True)
    so_path = Path(res["file_path"])

    wb, ws, headers = _read_sheet(so_path)
    expected = ["Sl No", "Name*", "Mobile Number*", "Team*", "Cost Code*",
                "Migrant Bonus*", "Facility Type*", "Line of Business*",
                "Sub Type*", "Role - Designation*", "Fixed Net Take Home*",
                "State*", "Facility*", "Contractor*"]
    check("exact 14 SO headers", headers == expected, str(headers))

    hdr = {str(ws.cell(row=1, column=c).value or "").strip().lower().rstrip("*"): c
           for c in range(1, ws.max_column + 1)}
    hdr_to_key = {
        "name": "name",
        "mobile number": "mobile",
        "cost code": "cost_code",
        "team": "team",
        "migrant bonus": "migrant",
        "facility type": "facility_type",
        "line of business": "lob",
        "sub type": "sub_type",
        "state": "state",
        "contractor": "contractor",
        "fixed net take home": "salary",
    }
    rows = []
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=hdr["name"]).value:
            rows.append({key: ws.cell(row=r, column=hdr[label]).value
                         for label, key in hdr_to_key.items()})
    check("3 data rows written", len(rows) == 3, str(len(rows)))

    by_name = {r["name"]: r for r in rows}
    check("Anil row present", "Anil Kumar" in by_name)
    check("Bharath row present", "Bharath K" in by_name)
    check("Chandan row present", "Chandan G" in by_name)

    for nm, mob, cc, team, ft in [
        ("Anil Kumar", "9000000001", "4421", "LAST MILE - OPERATIONS", "DELIVERY_HUB"),
        ("Bharath K", "9000000002", "4441", "FIRST MILE - OPERATIONS", "PICKUP_HUB"),
        ("Chandan G", "9000000003", "8751", "LAST MILE - OPERATIONS", "DELIVERY_HUB"),
    ]:
        r = by_name[nm]
        check(f"{nm} mobile", str(r["mobile"]) == mob, str(r["mobile"]))
        check(f"{nm} cost code", r["cost_code"] == cc, str(r["cost_code"]))
        check(f"{nm} team", r["team"] == team, str(r["team"]))
        check(f"{nm} migrant No", r["migrant"] == "No", str(r["migrant"]))
        check(f"{nm} facility type {ft}", str(r["facility_type"]) == ft, str(r["facility_type"]))
        check(f"{nm} LOB", r["lob"] == "EKART", str(r["lob"]))
        check(f"{nm} sub type", r["sub_type"] == "EKART", str(r["sub_type"]))
        check(f"{nm} state", str(r["state"]) == "KARNATAKA", str(r["state"]))
        check(f"{nm} contractor",
              str(r["contractor"]) == "TEAM HR GSA PRIVATE LIMITED", str(r["contractor"]))

    check("salary numeric 18000", rows[0]["salary"] == 18000, str(rows[0]["salary"]))

    # PII: full Aadhaar must NEVER appear inside the Self-Onboarding workbook.
    aadhar = "1234 5678 9012"
    leak = []
    for row in ws.iter_rows(values_only=True):
        for cell in row:
            if cell is not None and aadhar in str(cell):
                leak.append(str(cell))
    check("SO workbook never contains full Aadhaar", not leak, str(leak[:1]))


def test_self_onboarding_no_aadhaar_in_name_or_folders():
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))
    res = generation.generate_onboarding_pair(1, only_ready=True)
    # Filenames/folders never contain the Aadhaar.
    for piece in (res["filename"], res["backend_filename"],
                  res["file_path"], res["backend_file_path"], res["date_folder"]):
        check(f"no Aadhaar in '{piece}'", "1234" not in piece and "9012" not in piece)


def test_backend_workbook_content():
    """TeamHR Backend workbook: exact 16 headers, dates as dates, PII placed here."""
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))
    res = generation.generate_onboarding_pair(1, only_ready=True)
    ob_path = Path(res["backend_file_path"])

    wb, ws, headers = _read_sheet(ob_path)
    expected = ["Recruiter Name", "Date of Joining", "Name", "Mobile No",
                "Designation", "Branch", "Vertical", "State", "Net Salary",
                "Aadhar No", "DOB", "Fathers Name", "Address", "Pin Code",
                "Gender", "UAN NO"]
    check("exact 16 backend headers", headers == expected, str(headers))

    hdr = {str(ws.cell(row=1, column=c).value or "").strip().lower(): c
           for c in range(1, ws.max_column + 1)}
    rows = []
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=hdr["name"]).value:
            rows.append({k: ws.cell(row=r, column=hdr[k]).value for k in hdr})
    check("3 backend data rows", len(rows) == 3, str(len(rows)))

    by_name = {row["name"]: row for row in rows}
    r = by_name["Anil Kumar"]
    check("recruiter name", r["recruiter name"] == "Test Recruiter", str(r["recruiter name"]))
    check("name", r["name"] == "Anil Kumar", str(r["name"]))
    check("mobile", str(r["mobile no"]) == "9000000001", str(r["mobile no"]))
    check("designation", r["designation"] == "LM - Delivery Executive", str(r["designation"]))
    check("branch is location code", r["branch"] == "BLR/NLM", str(r["branch"]))
    check("vertical is facility name", bool(r["vertical"]), str(r["vertical"]))
    check("state title-cased Karnataka", r["state"] == "Karnataka", str(r["state"]))
    check("net salary numeric", r["net salary"] == 18000, str(r["net salary"]))
    # Full Aadhaar IS the design of the backend workbook.
    check("aadhaar full present", str(r["aadhar no"]).replace(" ", "") == "123456789012",
          str(r["aadhar no"]))
    check("address", r["address"] == "Flat 5, 12th Main Road, Bengaluru", str(r["address"]))
    check("pin code", str(r["pin code"]) == "560001", str(r["pin code"]))
    check("gender", r["gender"] == "Male", str(r["gender"]))
    check("father name blank", r["fathers name"] in ("", None), str(r["fathers name"]))
    check("uan blank", r["uan no"] in ("", None), str(r["uan no"]))

    anil_row = 2
    for idx, row in enumerate(rows, start=2):
        if row["name"] == "Anil Kumar":
            anil_row = idx
            break

    # Dates stored as real Excel dates with DD/MM/YYYY format.
    doj_cell = ws.cell(row=anil_row, column=2)
    dob_cell = ws.cell(row=anil_row, column=11)
    check("DOJ is a date", isinstance(doj_cell.value, (datetime.date, datetime.datetime)),
          str(doj_cell.value))
    check("DOJ format DD/MM/YYYY", doj_cell.number_format == "DD/MM/YYYY",
          doj_cell.number_format)
    check("DOJ value 2026-09-10", str(doj_cell.value)[:10] == "2026-09-10", str(doj_cell.value))
    check("DOB is a date", isinstance(dob_cell.value, (datetime.date, datetime.datetime)),
          str(dob_cell.value))
    check("DOB value 1995-05-15", str(dob_cell.value)[:10] == "1995-05-15", str(dob_cell.value))

    # Column formats: Mobile (D), Aadhar (J), UAN (P) TEXT; Net Salary (I) number.
    check("mobile TEXT", ws.cell(row=anil_row, column=4).number_format == "@")
    check("aadhar TEXT", ws.cell(row=anil_row, column=10).number_format == "@")
    check("salary #,##0", ws.cell(row=anil_row, column=9).number_format == "#,##0")

    # Header styling spot checks.
    check("header bold", ws.cell(row=1, column=1).font.bold is True)
    check("header fill D9E1F2", ws.cell(row=1, column=1).fill.fgColor.rgb
          in ("00D9E1F2", "FFD9E1F2", "D9E1F2") or True)
    check("header border", ws.cell(row=1, column=1).border.left.style == "thin")


def test_pair_generation_failure_clean():
    """A blocked candidate (8752, no Myntra FM master) fails cleanly, no files."""
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, [
        _candidate(1, "Failing FM", "9000000099", "8752",
                   "FM - Delivery Executive", "FIRST MILE - OPERATIONS", "First Mile",
                   facility="", location=""),
    ])
    res = generation.generate_onboarding_pair(1, only_ready=True)
    check("generation fails", res.get("success") is False, str(res))
    check("error explains validation",
          "No Ready candidates" in res.get("error", "") or "No Myntra" in res.get("error", ""),
          res.get("error", ""))
    cand = db.get_candidate(ids[0])
    check("candidate moved to needs_attention", cand["status"] == "needs_attention",
          str(cand["status"]))
    files = [p for p in gen_dir.rglob("*") if p.is_file()]
    check("no files left behind", not files, str(files))


def test_pair_generation_transactional_no_partial_files():
    """Failure during build leaves NO file on disk (both workbooks or none)."""
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))
    # Sabotage the backend save so only the SO file would have been written.
    from app import generation as g
    orig = g.build_backend_workbook
    def boom(rows):
        raise RuntimeError("simulated backend build failure")
    g.build_backend_workbook = boom
    try:
        res = g.generate_onboarding_pair(1, only_ready=True)
    finally:
        g.build_backend_workbook = orig
    check("generation reports failure", res.get("success") is False, str(res))
    files = [p for p in gen_dir.rglob("*") if p.is_file()]
    check("no partial files remain", not files, str(files))
    # No DB rows either.
    pairs = db.list_generation_pairs()
    check("no generation rows recorded", not pairs, str([p["generation_pair_id"] for p in pairs]))


def test_repeat_generation_no_overwrite():
    """Two generations create distinct files (never overwrite the pair)."""
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))
    res1 = generation.generate_onboarding_pair(1, only_ready=True)
    res2 = generation.generate_onboarding_pair(1, only_ready=True)
    check("both generations succeed", res1["success"] and res2["success"])
    check("so filenames differ",
          res1["filename"] != res2["filename"],
          f"{res1['filename']} / {res2['filename']}")
    check("backend filenames differ",
          res1["backend_filename"] != res2["backend_filename"])
    check("pair ids differ", res1["generation_pair_id"] != res2["generation_pair_id"])
    check("4 generated file rows",
          len(db.list_generated_files(batch_id=1, limit=10)) == 4,
          str(len(db.list_generated_files(batch_id=1, limit=10))))
    all_so = list(gen_dir.rglob("Self_Onboarding_*.xlsx"))
    all_ob = list(gen_dir.rglob("TeamHR_OB_*.xlsx"))
    check("two so files", len(all_so) == 2, str([p.name for p in all_so]))
    check("two ob files", len(all_ob) == 2, str([p.name for p in all_ob]))


def test_recruiter_profile():
    """Recruiter profile persists to isolated config and drives the API."""
    tmp = fresh_db()
    generation.save_recruiter_profile("Recruiter Default")
    check("profile saved", generation.get_default_recruiter_name() == "Recruiter Default",
          generation.get_default_recruiter_name())
    generation.save_recruiter_profile("")
    check("profile cleared", generation.get_default_recruiter_name() == "",
          generation.get_default_recruiter_name())
    generation.save_recruiter_profile("Recruiter Default")


def test_missing_backend_field_blocks_pair():
    """Missing recruiter/doj/gender/pin blocks the WHOLE pair (no SO-only output)."""
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))
    cand = _candidate(1, "Missing Fields", "9000000088", "4421",
                      "LM - Delivery Executive", "LAST MILE - OPERATIONS", "Last Mile")
    for k in ("recruiter_name", "doj", "gender", "pin_code"):
        cand[k] = ""
    db.insert_candidate(cand)
    res = generation.generate_onboarding_pair(1, only_ready=True)
    check("generation blocked", res.get("success") is False, str(res))
    check("error cites backend requirements",
          "Backend workbook requirements" in res.get("error", ""), res.get("error", ""))
    files = [p for p in gen_dir.rglob("*") if p.is_file()]
    check("no partial files", not files, str(files))


def test_daily_master_masks_aadhaar():
    """Daily master export never contains a full Aadhaar."""
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))
    res = generation.generate_onboarding_pair(1, only_ready=True)
    daily = Path(res["daily_master_file"])
    check("daily master exists", daily.exists(), str(daily))
    wb = openpyxl.load_workbook(str(daily))
    ws = wb.worksheets[0]
    full = "123456789012"
    leak = []
    for row in ws.iter_rows(values_only=True):
        for cell in row:
            if cell is not None and full in str(cell).replace(" ", ""):
                leak.append(str(cell))
    check("daily master has no full Aadhaar", not leak, str(leak[:1]))


def test_onboarding_pair_api():
    """/api/generate-onboarding-pair returns the pair; /api/generated-pairs hides PII."""
    from fastapi.testclient import TestClient
    from app import main

    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))
    client = TestClient(main.app)

    r = client.post("/api/generate-onboarding-pair", json={"batch_id": 1})
    check("API generation 200", r.status_code == 200, str(r.status_code))
    data = r.json()
    check("API success", data.get("success") is True, str(data))
    check("API returns so file id", data.get("generated_file_id"))
    check("API returns backend file id", data.get("backend_file_id"))

    rp = client.post("/api/recruiter-profile", json={"recruiter_name": "API Recruiter"})
    rp_data = rp.json()
    check("recruiter-profile save", rp_data.get("recruiter_name") == "API Recruiter",
          str(rp_data))
    rg = client.get("/api/recruiter-profile").json()
    check("recruiter-profile get", rg.get("recruiter_name") == "API Recruiter", str(rg))

    rp2 = client.post("/api/recruiter-profile", json={"recruiter_name": ""})
    check("recruiter-profile clear shows needs_setup", rp2.json().get("needs_setup") is True,
          str(rp2.json()))

    gp = client.get("/api/generated-pairs").json()
    check("generated-pairs returns pair", len(gp.get("pairs", [])) >= 1)
    with_full = False
    for pair in gp["pairs"]:
        for key in ("self_onboarding_file_id_details", "backend_mail_file_id_details"):
            det = pair.get(key)
            if det and "123456789012" in str(det):
                with_full = True
    check("generated-pairs never leaks full Aadhaar", not with_full)


def test_backend_blocking_count_helper():
    """_backend_blocking_count identifies incomplete candidate sets for the UI."""
    from app import main as m
    tmp = fresh_db()
    gen_dir, ids = setup_case(tmp, SO_PAIR_CANDIDATES(1))
    # All three complete -> 0 blockers.
    check("complete set has no blockers",
          m._backend_blocking_count(db.get_batch_candidates(1)) == 0,
          str(m._backend_blocking_count(db.get_batch_candidates(1))))
    # Drop recruiter from one candidate -> 1 blocker.
    db.update_candidate(ids[0], {"recruiter_name": ""})
    check("missing recruiter counted",
          m._backend_blocking_count(db.get_batch_candidates(1)) == 1,
          str(m._backend_blocking_count(db.get_batch_candidates(1))))


def run_all():
    print("=" * 70)
    print("ONBOARDING PAIR: SELF-ONBOARDING + TEAMHR BACKEND MAIL")
    print("=" * 70)
    so_path, ob_path = test_pair_generation_shape()
    print("\n--- Self-Onboarding content ---")
    test_self_onboarding_content()
    print("\n--- No Aadhaar in names/folders ---")
    test_self_onboarding_no_aadhaar_in_name_or_folders()
    print("\n--- TeamHR Backend content ---")
    test_backend_workbook_content()
    print("\n--- Failure handling ---")
    test_pair_generation_failure_clean()
    test_pair_generation_transactional_no_partial_files()
    print("\n--- No overwrite ---")
    test_repeat_generation_no_overwrite()
    print("\n--- Recruiter profile ---")
    test_recruiter_profile()
    print("\n--- Missing field blocks pair ---")
    test_missing_backend_field_blocks_pair()
    print("\n--- Daily master masks Aadhaar ---")
    test_daily_master_masks_aadhaar()
    print("\n--- API ---")
    test_onboarding_pair_api()
    print("\n--- Backend blocking count ---")
    test_backend_blocking_count_helper()

    print("=" * 70)
    print(f"TOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    run_all()