"""Controlled single-candidate LIVE eSampark upload mode tests.

These tests are LOCAL / MOCKED — they never talk to the real eSampark portal.
They verify the safety gate, single-candidate limit, generated-file validation,
manual checklist guard, duplicate-submission guard, count/status mapping and the
submit orchestration using a fake portal session. No real candidate is ever
uploaded here.

Run with:
    python tests/test_live_upload.py
"""

import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import openpyxl

from app import database as db
from app import generation
from app import rules
from app.portal import esampark, live_upload, selectors

PASS = 0
FAIL = 0
_BATCH_COUNTER = 0


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


def make_single_file(tmp, status="ready", designation=None, facility=True,
                     location=True, salary=18000, cost_code="4421",
                     candidate_count=1, generation_status="generated"):
    """Create ONE ready candidate + a Stage 3 generated file for a cost code.

    Each call uses its own batch so a generated file always holds exactly one
    candidate (``only_ready`` generation would otherwise pull every ready
    candidate in a shared batch into one file).
    """
    from app import master_data
    global _BATCH_COUNTER
    _BATCH_COUNTER += 1
    batch_id = 1000 + _BATCH_COUNTER
    db.get_or_create_batch(batch_id=batch_id)
    hub = rules.get_hubs_for_cost_code("4421")[0]
    cid = db.insert_candidate({
        "batch_id": batch_id, "name": "Single Candidate", "mobile": "9000000000",
        "designation": designation or "LM - Delivery Executive",
        "facility_name": hub if facility else "",
        "location_code": ("BLR/NLM" if cost_code in ("4421", "4441") else "BLR/HEM") if location else "",
        "cost_code": cost_code, "status": status,
        "entity": "Flipkart", "aadhaar_number": "123456789012",
        "salary": salary,
        "recruiter_name": "Test Recruiter", "doj": "12/08/2026",
        "gender": "Male", "pin_code": "560001", "dob": "01/01/1995",
        "address": "12 MG Road, Bengaluru, Karnataka 560001",
    })
    res = generation.generate_batch_excel(batch_id, only_ready=True)
    assert res["success"], res
    gf = db.get_generated_file(res["generated_file_id"])

    # Override candidate_count / generation_status to simulate edge cases.
    if candidate_count != gf["candidate_count"]:
        db._get_connection().execute(
            "UPDATE generated_files SET candidate_count = ? WHERE file_id = ?",
            (candidate_count, gf["file_id"]),
        ).connection.commit()
    if generation_status != gf["generation_status"]:
        db._get_connection().execute(
            "UPDATE generated_files SET generation_status = ? WHERE file_id = ?",
            (generation_status, gf["file_id"]),
        ).connection.commit()
    db._get_connection().close()
    return gf, cid


def make_result_workbook(rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Mobile", "Candidate Name", "Creation Remarks", "Status"])
    for r in rows:
        ws.append([r.get("mobile"), r.get("name"), r.get("remarks"), r.get("status")])
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


class FakeLocator:
    def __init__(self, sel):
        self._sel = sel
    @property
    def first(self):
        return self
    def is_visible(self, timeout=0):
        return ("file" in self._sel or "Self Onboarding" in self._sel)


class FakePage:
    def __init__(self):
        self._closed = False
    def is_closed(self):
        return self._closed
    def locator(self, sel):
        return FakeLocator(sel)


class FakeSession:
    """Stand-in for PortalSession exposing the slice live_upload uses."""
    def __init__(self, status=esampark.CONNECTED, history_row=None,
                 count_repr="", error_bytes=None):
        self.status = status
        self.page = FakePage()
        self._history_row = history_row or {"row_index": 0, "reference_id": None,
                                            "filename": "OB.xlsx", "status_raw": count_repr}
        self._error_bytes = error_bytes
        self.navigated = False
        self.uploaded = False
        self.history_opened = False
    def navigate_to_ftc(self):
        self.navigated = True
    def upload_workbook(self, path):
        self.uploaded = True
    def open_upload_history(self):
        self.history_opened = True
    def find_upload_row(self, reference_id=None, filename=None, prefer_recent=True):
        return dict(self._history_row)
    def download_result_or_error(self, kind):
        return self._error_bytes


def reset_flags():
    esampark.REAL_UPLOAD_ENABLED = False
    esampark.ESAMPARK_LIVE_TEST_MODE = False


print("=" * 70)
print("CONTROLLED SINGLE-CANDIDATE LIVE UPLOAD (LOCAL / MOCKED)")
print("=" * 70)
reset_flags()

# ── 1. Safety flags default closed ──────────────────────────────────────────

print("\n--- Safety flags default to false ---")
check("REAL_UPLOAD_ENABLED default False", esampark.REAL_UPLOAD_ENABLED is False)
check("ESAMPARK_LIVE_TEST_MODE default False", esampark.ESAMPARK_LIVE_TEST_MODE is False)
check("live_upload_allowed() False by default", live_upload.mode_enabled() is False)
check("assert_live_mode raises when locked", True)
try:
    live_upload.assert_live_mode()
    check("assert_live_mode should raise", False)
except live_upload.LiveUploadError:
    check("assert_live_mode raised when locked", True)

# Neither flag alone unlocks it.
esampark.REAL_UPLOAD_ENABLED = True
check("only REAL flag -> still locked", live_upload.mode_enabled() is False)
esampark.REAL_UPLOAD_ENABLED = False
esampark.ESAMPARK_LIVE_TEST_MODE = True
check("only LIVE flag -> still locked", live_upload.mode_enabled() is False)
esampark.ESAMPARK_LIVE_TEST_MODE = False
reset_flags()

# ── 2. Generated-file validation single-candidate guard ─────────────────────

print("\n--- Generated-file validation + single-candidate guard ---")
tmp = fresh_db()
generation.set_output_base_dir(tmp)
gf, cid = make_single_file(tmp)
res = live_upload.validate_generated_file(gf["file_id"])
check("valid single-candidate file passes", res.get("ok") is True, str(res.get("errors")))

# candidate_count != 1 -> blocked with exact message.
gf2, cid2 = make_single_file(tmp)
db._get_connection().execute(
    "UPDATE generated_files SET candidate_count = 2 WHERE file_id = ?", (gf2["file_id"],)
).connection.commit()
db._get_connection().close()
res2 = live_upload.validate_generated_file(gf2["file_id"])
check("candidate_count=2 blocked", res2.get("ok") is False)
check("single-candidate limit message exact", live_upload.LIVE_ONLY_ONE_MSG in res2.get("errors", []))

# missing candidate link.
res3 = live_upload.validate_generated_file(99999)
check("unknown file blocked", res3.get("ok") is False)

# generation_status not success.
gf4, cid4 = make_single_file(tmp)
db._get_connection().execute(
    "UPDATE generated_files SET generation_status = 'failed' WHERE file_id = ?", (gf4["file_id"],)
).connection.commit()
db._get_connection().close()
res4 = live_upload.validate_generated_file(gf4["file_id"])
check("generation_status failed blocked", res4.get("ok") is False)

# ── 3. Candidate field validation ───────────────────────────────────────────

print("\n--- Candidate field validation (role/facility/location/salary/cost) ---")

# Missing facility -> blocked.
gf5, cid5 = make_single_file(tmp)
db.update_candidate(cid5, {"facility_name": ""})
res5 = live_upload.validate_generated_file(gf5["file_id"])
check("missing facility blocked", res5.get("ok") is False and any("Facility" in e for e in res5["errors"]))

# Missing location -> blocked.
gf6, cid6 = make_single_file(tmp)
db.update_candidate(cid6, {"location_code": ""})
res6 = live_upload.validate_generated_file(gf6["file_id"])
check("missing location blocked", res6.get("ok") is False and any("Location" in e for e in res6["errors"]))

# Invalid salary -> blocked.
gf7, cid7 = make_single_file(tmp)
db.update_candidate(cid7, {"salary": 0})
res7 = live_upload.validate_generated_file(gf7["file_id"])
check("invalid salary blocked", res7.get("ok") is False and any("alary" in e for e in res7["errors"]))

# Invalid cost code -> blocked.
gf8, cid8 = make_single_file(tmp)
db.update_candidate(cid8, {"cost_code": "9999"})
res8 = live_upload.validate_generated_file(gf8["file_id"])
check("invalid cost code blocked", res8.get("ok") is False and any("cost code" in e.lower() for e in res8["errors"]))

# Invalid designation for the cost code -> blocked.
gf9, cid9 = make_single_file(tmp)
db.update_candidate(cid9, {"designation": "Not A Real Role"})
res9 = live_upload.validate_generated_file(gf9["file_id"])
check("invalid designation blocked", res9.get("ok") is False and any("esignation" in e for e in res9["errors"]))

# Candidate status not Ready/Generated (needs review) -> blocked.
gf10, cid10 = make_single_file(tmp)
db.update_candidate(cid10, {"status": "needs_attention"})
res10 = live_upload.validate_generated_file(gf10["file_id"])
check("needs-review candidate blocked", res10.get("ok") is False and any("status" in e.lower() for e in res10["errors"]))

# ── 4. Manual review checklist guard ────────────────────────────────────────

print("\n--- Manual review checklist guard ---")
gf, cid = make_single_file(tmp)
# prepare provisions the live_uploads record.
p = live_upload.prepare(gf["file_id"])
check("prepare returns ok + checklist", p.get("ok") is True and p.get("checklist") == live_upload.REVIEW_CHECKLIST)
check("prepare surfaces candidate", (p.get("candidate") or {}).get("name") == "Single Candidate")

# Incomplete checklist -> blocked.
res_c = live_upload.confirm(gf["file_id"], [live_upload.REVIEW_CHECKLIST[0]])
check("incomplete checklist blocked", res_c.get("ok") is False)

# Full checklist -> confirmed.
res_full = live_upload.confirm(gf["file_id"], list(live_upload.REVIEW_CHECKLIST))
check("full checklist confirms", res_full.get("ok") is True and res_full.get("manual_confirmed") is True)

# ── 5. Duplicate submission safety ──────────────────────────────────────────

print("\n--- Duplicate submission safety ---")
# Simulate an already-submitted live record for the same file.
db.update_live_upload(res_full["live_upload_id"], flow_status="submitted")
res_dup_prep = live_upload.prepare(gf["file_id"])
check("prepare blocks already-submitted file", res_dup_prep.get("ok") is False and
      live_upload.ALREADY_SUBMITTED_MSG in res_dup_prep["errors"])
res_dup_submit = live_upload.submit(gf["file_id"])
check("submit blocks already-submitted file", not res_dup_submit.get("ok") and
      res_dup_submit.get("duplicate") is True)
reset_flags()

# ── 6. Count / status mapping ───────────────────────────────────────────────

print("\n--- Count + status mapping (Completed / Completed With Errors) ---")
check("'Completed' maps to Success",
      live_upload._map_status("Completed") == esampark.SUCCESS)
check("'Completed With Errors' maps to Failed",
      live_upload._map_status("Completed With Errors") == esampark.FAILED)
check("'Processing' maps to Processing",
      live_upload._map_status("Processing") == esampark.PROCESSING)
check("unknown maps to Unknown",
      live_upload._map_status("weird portal wording") == esampark.UNKNOWN)

c1 = live_upload._extract_counts("File Name: OB.xlsx | Status: Completed | Success Count: 1 | Failed Count: 0 | Total Count: 1")
check("success counts parsed", c1["success"] == 1 and c1["failed"] == 0 and c1["total"] == 1, str(c1))
c2 = live_upload._extract_counts("Completed With Errors | Failed Count: 1 | Total Count: 1")
check("failed counts parsed", c2["failed"] == 1 and c2["total"] == 1, str(c2))

# ── 7. Submit orchestration (fake session) ──────────────────────────────────

print("\n--- Submit orchestration (fake portal session) ---")
# Success case.
gf_s, cid_s = make_single_file(tmp)
esampark.REAL_UPLOAD_ENABLED = True
esampark.ESAMPARK_LIVE_TEST_MODE = True
live_upload.confirm(gf_s["file_id"], list(live_upload.REVIEW_CHECKLIST))
fake = FakeSession(history_row={"row_index": 0, "reference_id": "724648",
                                "filename": "OB.xlsx",
                                "status_raw": "Completed; Success Count: 1; Failure Count: 0; Total Count: 1"})
orig_session = live_upload._get_session
live_upload._get_session = lambda: fake
r = live_upload.submit(gf_s["file_id"])
live_upload._get_session = orig_session
check("success submit returns ok", r.get("ok") is True, str(r))
check("success submit portal_status Success", r.get("portal_status") == esampark.SUCCESS, str(r.get("portal_status")))
check("upload navigation performed", fake.navigated is True)
check("single upload performed", fake.uploaded is True)
check("history opened + matched", fake.history_opened is True)
full = live_upload.report(gf_s["file_id"])
check("success candidate marked Portal Uploaded", (db.get_candidate(cid_s) or {}).get("portal_status") == "Portal Uploaded",
      str((db.get_candidate(cid_s) or {}).get("portal_status")))
check("final_status audit recorded", any(a["event"] == "final_status" for a in db.get_portal_audit(200)))
reset_flags()

# Error case: Completed With Errors -> downloads error file, marks candidate Failed.
gf_e, cid_e = make_single_file(tmp)
esampark.REAL_UPLOAD_ENABLED = True
esampark.ESAMPARK_LIVE_TEST_MODE = True
live_upload.confirm(gf_e["file_id"], list(live_upload.REVIEW_CHECKLIST))
db.mark_candidates_generated([cid_e], gf_e["file_id"])
err_bytes = make_result_workbook([
    {"mobile": "9000000000", "name": "Single Candidate",
     "remarks": "Document Invalid", "status": "Failed"}
])
fake_e = FakeSession(history_row={"row_index": 0, "reference_id": "724649",
                                  "filename": "OB.xlsx",
                                  "status_raw": "Completed With Errors | Failed Count: 1 | Total Count: 1"},
                     error_bytes=err_bytes)
live_upload._get_session = lambda: fake_e
re_ = live_upload.submit(gf_e["file_id"])
live_upload._get_session = orig_session
check("error submit portal_status Failed", re_.get("portal_status") == esampark.FAILED, str(re_.get("portal_status")))
check("error file downloaded+saved", live_upload.report(gf_e["file_id"]).get("error_file_path"))
cand_e = db.get_candidate(cid_e)
check("error candidate marked Failed", (cand_e or {}).get("portal_status") == "Failed",
      str((cand_e or {}).get("portal_status")))
check("creation remarks extracted", live_upload.report(gf_e["file_id"]).get("creation_remarks") == "Document Invalid",
      str(live_upload.report(gf_e["file_id"]).get("creation_remarks")))
reset_flags()

# Two-step guard: FIRST_ACTION does not submit.
gf_p, _ = make_single_file(tmp)
esampark.REAL_UPLOAD_ENABLED = True
esampark.ESAMPARK_LIVE_TEST_MODE = True
r_first = live_upload.submit(gf_p["file_id"], action="prepare_live_upload")
check("first action does not submit", not r_first.get("ok") and "final submit action" in r_first.get("error", ""))
reset_flags()

# Locked submit (both flags off) -> blocked cleanly.
gf_l, _ = make_single_file(tmp)
reset_flags()
r_lock = live_upload.submit(gf_l["file_id"])
check("locked submit blocked", not r_lock.get("ok"), str(r_lock))
reset_flags()

print("=" * 70)
print(f"TOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
if FAIL:
    sys.exit(1)
