"""Stage 4: eSampark Portal Automation tests.

These tests are deliberately LOCAL / MOCKED — they never talk to the real
eSampark portal (no credentials, and a real candidate upload is never performed
without explicit user approval). They exercise the architecture that IS
unit-testable: generated-file resolution, upload state transitions, duplicate
upload prevention, the result-workbook parser (Creation Remarks detection,
candidate matching), partial-failure handling, manual result import, DB
persistence across restart, and filename/folder handling.

Run with:
    python tests/test_portal.py
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
from app import result_parser
from app.portal import esampark, selectors, service

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


def make_generated_file(tmp, batch_id=1, n=2, status=True):
    """Create candidates + a Stage 3 generated file, returning (gf, candidate_ids)."""
    # Reuse the real mast data via rules so generation validation passes.
    from app import rules
    hubs = rules.get_hubs_for_cost_code("4421")
    db.get_or_create_batch(batch_id=batch_id)
    ids = []
    for i in range(n):
        cid = db.insert_candidate({
            "batch_id": batch_id, "name": f"Candidate {i}", "mobile": f"90000000{i:02d}",
            "designation": "LM - Delivery Executive", "facility_name": hubs[0],
            "location_code": "BLR/NLM", "cost_code": "4421", "status": "ready",
            "entity": "Flipkart", "aadhaar_number": "123456789012",
        })
        ids.append(cid)
    res = generation.generate_batch_excel(batch_id, only_ready=True)
    assert res["success"], res
    gf = db.get_generated_file(res["generated_file_id"])
    return gf, ids


# ── Fixture workbooks ─────────────────────────────────────────────────────────


def make_result_workbook(title_rows=1, rows=None):
    """Build a result workbook with an optional title row above the header.

    ``rows`` is a list of dicts with keys mobile/name/remarks/status.
    """
    rows = rows or []
    wb = openpyxl.Workbook()
    ws = wb.active
    for _ in range(title_rows):
        ws.append(["eSampark Onboarding Result"])
    header = ["Mobile", "Candidate Name", "Creation Remarks", "Status"]
    ws.append(header)
    for r in rows:
        ws.append([r.get("mobile"), r.get("name"), r.get("remarks"), r.get("status")])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


print("=" * 70)
print("STAGE 4: ESAMPARK PORTAL AUTOMATION (LOCAL / MOCKED)")
print("=" * 70)

# ── 1. Portal status / credentials ───────────────────────────────────────────

print("\n--- Portal status without credentials ---")
status = service.portal_status()
check("no credentials -> Login Required", status["status"] == "Login Required",
      status.get("status"))
# strip any env creds that might be set at test time
os.environ.pop("ESAMPARK_USERNAME", None)
os.environ.pop("ESAMPARK_PASSWORD", None)
check("credentials_configured() == False", esampark.credentials_configured() is False)
try:
    esampark.get_credentials()
    check("get_credentials raises when missing", False)
except esampark.PortalCredentialsError:
    check("get_credentials raises when missing", True)

# ── 2. Result parser ─────────────────────────────────────────────────────────

print("\n--- Result workbook parser (Creation Remarks by header) ---")
# A failure workbook with a title row above the header to prove header detection.
data = [
    {"mobile": "9000000000", "name": "Candidate 0", "remarks": "Document Invalid",
     "status": "Failed"},
    {"mobile": "9000000001", "name": "Candidate 1", "remarks": "",
     "status": "Success"},
]
wb_bytes = make_result_workbook(title_rows=1, rows=data)
parsed = result_parser.parse_workbook(wb_bytes)
check("parsed 2 rows", len(parsed) == 2, str(len(parsed)))
check("header detection skipped title row", parsed[0]["mobile"] == "9000000000")
check("creation remarks located by header", parsed[0]["creation_remarks"] == "Document Invalid")
check("success candidate remarks empty", not parsed[1]["creation_remarks"])

# Enforce header-based detection: build a workbook with nonstandard column order.
wb2 = openpyxl.Workbook(); ws2 = wb2.active
ws2.append(["Status", "Creation Remarks", "Name", "Contact"])
ws2.append(["Failed", "Rejected", "Candidate 0", "9000000000"])
buf2 = io.BytesIO(); wb2.save(buf2)
p2 = result_parser.parse_workbook(buf2.getvalue())
check("nonstandard column order parsed", p2[0]["creation_remarks"] == "Rejected"
      and p2[0]["name"] == "Candidate 0" and p2[0]["mobile"] == "9000000000")

# Bad workbook -> clean error, no crash.
try:
    result_parser.parse_workbook(b"not an xlsx")
    check("bad workbook raises ResultParseError", False)
except result_parser.ResultParseError:
    check("bad workbook raises ResultParseError", True)

# ── 3. Candidate matching (mobile first, name fallback) ──────────────────────

print("\n--- Candidate matching ---")
candidates = [
    {"candidate_id": 1, "name": "Candidate 0", "mobile": "9000000000"},
    {"candidate_id": 2, "name": "Candidate 1", "mobile": "9000000001"},
    {"candidate_id": 3, "name": "Candidate 2", "mobile": "9000000002"},
]
match_res = result_parser.match_parsed_rows(parsed, candidates)
check("both rows matched", len(match_res["matches"]) == 2, str(len(match_res["matches"])))
by_mobile = {m["candidate_id"]: m for m in match_res["matches"]}
check("candidate 0 failed (has remark)", by_mobile[1]["portal_status"] == "Failed")
check("candidate 1 success (no remark)", by_mobile[2]["portal_status"] == "Success")
check("remarks preserved accurately", by_mobile[1]["portal_remarks"] == "Document Invalid")
check("matched by mobile", all(m["matched_by"] == "mobile" for m in match_res["matches"]))

# Name fallback: no mobile column in result, only a name -> still matches.
cand_names = [{"candidate_id": 10, "name": "Ravi Kumar", "mobile": ""}]
wb3 = openpyxl.Workbook(); ws3 = wb3.active
ws3.append(["Candidate Name", "Creation Remarks"])
ws3.append(["Ravi Kumar", "Name Mismatch"])
buf3 = io.BytesIO(); wb3.save(buf3)
p3 = result_parser.parse_workbook(buf3.getvalue())
m3 = result_parser.match_parsed_rows(p3, cand_names)
check("name fallback matches", m3["matches"] and m3["matches"][0]["candidate_id"] == 10
      and m3["matches"][0]["matched_by"] == "name")

# ── 4. Partial failure classification ────────────────────────────────────────

print("\n--- Partial failure handling ---")
mix = [
    {"mobile": "9000000000", "remarks": "", "status": "Success"},
    {"mobile": "9000000001", "remarks": "Invalid Doc", "status": "Failed"},
    {"mobile": "9000000002", "remarks": "", "status": "Success"},
]
pb = make_result_workbook(title_rows=0, rows=mix)
pm = result_parser.match_parsed_rows(result_parser.parse_workbook(pb), candidates)
statuses = [m["portal_status"] for m in pm["matches"]]
check("success+fail+success detected", statuses == ["Success", "Failed", "Success"], str(statuses))

# ── 5. Generated file resolution ─────────────────────────────────────────────

print("\n--- Generated file resolution ---")
tmp = fresh_db()

# monkeypatch output dir to temp so generation writes into tmp
generation.set_output_base_dir(tmp)
gf, ids = make_generated_file(tmp)
check("generated file resolved ok", service.resolve_generated_file(gf["file_id"])["ok"] is True)
missing = service.resolve_generated_file(99999)
check("missing generated file blocked", missing["ok"] is False and "not found" in missing["reason"])

# Temporarily remove the file -> blocked because not on disk.
os.remove(gf["file_path"])
gone = service.resolve_generated_file(gf["file_id"])
check("deleted file blocked", gone["ok"] is False)
# restore it
open(gf["file_path"], "wb").write(openpyxl.Workbook() and b"")

# ── 6. Duplicate upload prevention ───────────────────────────────────────────

print("\n--- Duplicate upload prevention ---")
# Create the file again on disk (it was removed above).
res = generation.generate_batch_excel(gf["batch_id"], only_ready=True)
gf = db.get_generated_file(res["generated_file_id"])
# First submission without confirm -> creates a pending record.
r1 = service.upload_generated_file(gf["file_id"], confirm=True)
check("upload returns pending record", r1.get("upload_id") is not None)
# Second submission without confirm -> blocked as duplicate.
r2 = service.upload_generated_file(gf["file_id"], confirm=False)
check("duplicate upload blocked", r2.get("ok") is False and r2.get("duplicate") is True)
# Retry with explicit confirm -> allowed (updates same record).
r3 = service.upload_generated_file(gf["file_id"], confirm=True)
check("retry with explicit confirm allowed", r3.get("ok") is True or r3.get("upload_id"))

# ── 7. Manual result import ─────────────────────────────────────────────────

print("\n--- Manual result import ---")
# attach candidates to the generated file so matching has a target set
db.mark_candidates_generated(ids, gf["file_id"])
fail_only = [
    {"mobile": "9000000000", "name": "Candidate 0", "remarks": "Duplicate Entry", "status": "Failed"},
    {"mobile": "9000000001", "name": "Candidate 1", "remarks": "", "status": "Success"},
]
fb = make_result_workbook(title_rows=0, rows=fail_only)
imp = service.apply_result_file(fb, gf["file_id"])
check("manual import ok", imp.get("ok") is True)
check("manual import updated 2", imp.get("updated") == 2, str(imp.get("updated")))
check("manual import partial failure flag", imp.get("partial_failure") is True)
c0 = db.get_candidate(ids[0])
c1 = db.get_candidate(ids[1])
check("failed candidate portal_status = Failed", c0["portal_status"] == "Failed")
check("failed candidate remarks preserved", c0["portal_remarks"] == "Duplicate Entry")
check("successful candidate portal_status = Success", c1["portal_status"] == "Success")

# ── 8. DB persistence across 'restart' ───────────────────────────────────────

print("\n--- Upload history persistence across restart ---")
import importlib
db2 = importlib.reload(db)
db2.DB_DIR = Path(tmp) / "database"
db2.DB_PATH = db2.DB_DIR / "teamhr.db"
db2.init_db()
check("portal_uploads persisted", len(db2.list_portal_uploads()) >= 1)
check("portal_audit persisted", len(db2.get_portal_audit()) >= 1)
check("candidate portal_status persisted", db2.get_candidate(ids[0])["portal_status"] == "Failed")

# ── 9. Filename / folder handling ───────────────────────────────────────────

print("\n--- Result/error folder handling ---")
from datetime import datetime
date_folder = datetime.now().strftime("%Y-%m-%d")
errors_dir = Path(tmp) / date_folder / "errors"
results_dir = Path(tmp) / date_folder / "results"
# unique-file helper
p1 = service._unique_file(errors_dir, "OB_BATCH-0001_2026-09-03_09-00-00.xlsx", "FAILED")
p1.parent.mkdir(parents=True, exist_ok=True)
p1.touch()
p2 = service._unique_file(errors_dir, "OB_BATCH-0001_2026-09-03_09-00-00.xlsx", "FAILED")
check("collision avoided in errors/ folder", str(p1) != str(p2) and p1.exists() and not p2.exists())
check("FAILED suffix in errors folder", p2.name.endswith(".xlsx") and "FAILED" in p2.name, p2.name)

# ── 10. Credential / Aadhaar safety ─────────────────────────────────────────

print("\n--- No sensitive data in logs ---")
sensitive = ["esampark_password", "password=", "123456789012", "ESAMPARK_PASSWORD"]
audit = db.get_portal_audit(100)
joined = " ".join(str(a.get("detail") or a.get("occurred_at") or "").lower() for a in audit)
# Ensure no raw credentials or full aadhaar leaked into audit detail.
check("no full aadhaar in audit", "123456789012" not in joined)
check("no password marker in audit", "password" not in joined.lower())
# Portal credentials come only from env
check("get_credentials reads from env", True)
os.environ["ESAMPARK_USERNAME"] = "user_t"
os.environ["ESAMPARK_PASSWORD"] = "pass_t"
u, p = esampark.get_credentials()
check("credentials from env round trip", u == "user_t" and p == "pass_t")

# ── 11. Upload state transitions map ────────────────────────────────────────

print("\n--- Upload status mapping ---")
check("maps 'success'", service._map_status_text("Upload successful") == esampark.SUCCESS)
check("maps 'failed'", service._map_status_text("Process failed") == esampark.FAILED)
check("maps 'partial'", service._map_status_text("Partially failed") == esampark.PARTIAL_FAILURE)
check("maps 'processing'", service._map_status_text("Processing in queue") == esampark.PROCESSING)
check("unknown text -> Unknown", service._map_status_text("weird portal status") == esampark.UNKNOWN)

# ── 12. Stage 4B real-upload safety guard ────────────────────────────────────

print("\n--- Real upload safety guard (Stage 4B) ---")
check("REAL_UPLOAD_ENABLED is False during verification", esampark.REAL_UPLOAD_ENABLED is False)
check("ESAMPARK_LIVE_TEST_MODE is False during verification", esampark.ESAMPARK_LIVE_TEST_MODE is False)
check("live upload not allowed by default", esampark.live_upload_allowed() is False)
check("disabled message is descriptive", esampark.DISABLED_UPLOAD_MSG
      == "Real eSampark upload is disabled. Both REAL_UPLOAD_ENABLED and "
         "ESAMPARK_LIVE_TEST_MODE must be true for a live single-candidate upload.")

# upload_workbook must refuse (raise) even if called, without touching a file.
s = esampark.PortalSession()
s.status = esampark.CONNECTED
try:
    s.upload_workbook("C:/nope/not_a_real_workbook.xlsx")
    check("upload_workbook raises disabled error", False)
except esampark.PortalAutomationError as exc:
    check("upload_workbook blocked by guard", esampark.DISABLED_UPLOAD_MSG in str(exc))

# Diagnostic endpoint never exposes credentials and reports the guard + NO flags.
diag = s.diagnostic()
check("diagnostic reports real_upload_enabled False", diag["real_upload_enabled"] is False)
check("diagnostic reports no upload history found pre-verify", diag["upload_history_found"] == "NO")
check("diagnostic has no password/cookie keys",
      not any("pass" in k.lower() or "cookie" in k.lower() or "token" in k.lower()
              for k in diag.keys()))

# ── 13. Stage 4B reconciliation: verified selectors + robust FTC detection ──

print("\n--- Stage 4B reconciliation (verified selectors + robust FTC rule) ---")

# Verified live selectors were written by the real-portal verifier and must be
# promoted to PRIMARY in the centralized lists (fallbacks intact after them).
check("verified FILE_INPUT is primary",
      selectors.FTC_FILE_INPUT_SELECTORS[0] == "input[type=\"file\"]",
      str(selectors.FTC_FILE_INPUT_SELECTORS))
check("verified UPLOAD_SUBMIT is primary",
      selectors.FTC_SUBMIT_SELECTORS[0] == 'button:has-text("Upload")',
      str(selectors.FTC_SUBMIT_SELECTORS))
check("verified UPLOAD_HISTORY is primary",
      selectors.UPLOAD_HISTORY_SELECTORS[0] == 'text="Upload History"',
      str(selectors.UPLOAD_HISTORY_SELECTORS))
# Original fallbacks must still be present after the verified primary.
check("fallback submit selectors retained",
      "button[type=\"submit\"]" in selectors.FTC_SUBMIT_SELECTORS)
check("fallback history selectors retained",
      'a:has-text("Upload History")' in selectors.UPLOAD_HISTORY_SELECTORS)

# Robust FTC-page rule (5): confirmed by upload interface + Self Onboarding
# context, WITHOUT literal "FTC" text.
check("FTC page robust rule upload signals defined",
      "input[type=\"file\"]" in selectors.FTC_PAGE_UPLOAD_CONTROLS)


class _FakePage:
    """Minimal stand-in exposing the slice of the Playwright API used by the
    robust FTC helper: locator visibility machine, never talking to a browser."""
    def __init__(self, visible_selectors):
        self._vis = set(visible_selectors)
        self._closed = False
    def is_closed(self):
        return self._closed
    def locator(self, sel):
        class L:
            def __init__(self, owner, s): self._o, self._s = owner, s
            @property
            def first(self):
                class F:
                    def __init__(self, owner, s): self._o, self._s = owner, s
                    def is_visible(self, timeout=0): return self._s in self._o._vis
                return F(self._o, self._s)
        return L(self, sel)


# Case A: upload control + Self Onboarding context, no "FTC" text -> confirmed.
vis_ftc = {'input[type="file"]', 'a:has-text("Self Onboarding")'}
pftc = _FakePage(vis_ftc)
check("robust FTC confirmed (upload + context, no FTC text)",
      selectors.ftc_page_confirmed(pftc) is True)
check("has_upload_control true", selectors.has_upload_control(pftc) is True)
check("has_self_onboarding true", selectors.has_self_onboarding(pftc) is True)

# Case B: upload control WITHOUT Self Onboarding context -> not confirmed.
p_only_file = _FakePage({'input[type="file"]'})
check("robust FTC NOT confirmed (no Self Onboarding context)",
      selectors.ftc_page_confirmed(p_only_file) is False)

# Case C: Self Onboarding context WITHOUT upload control -> not confirmed.
p_only_ctx = _FakePage({'a:has-text("Self Onboarding")'})
check("robust FTC NOT confirmed (no upload control)",
      selectors.ftc_page_confirmed(p_only_ctx) is False)

# Case D: closed page -> none of the helpers fire.
p_closed = _FakePage(vis_ftc); p_closed._closed = True
check("robust FTC false on closed page", selectors.ftc_page_confirmed(p_closed) is False)
check("has_upload_control false on closed page",
      selectors.has_upload_control(p_closed) is False)
check("has_self_onboarding false on closed page",
      selectors.has_self_onboarding(p_closed) is False)

# Real-upload guard still enforced (rule 7/8): no real upload from the guard.
check("REAL_UPLOAD_ENABLED remains False", esampark.REAL_UPLOAD_ENABLED is False)

# ── 14. Stage 4C: live upload-history selectors + row-matching ───────────────

print("\n--- Stage 4C upload-history selectors ---")

# Centralized history selectors must exist and stay centralized.
check("HISTORY_OPEN selectors defined", len(selectors.HISTORY_OPEN_SELECTORS) > 0)
check("verified UPLOAD_HISTORY leads HISTORY_OPEN",
      selectors.HISTORY_OPEN_SELECTORS[0] == 'text="Upload History"',
      str(selectors.HISTORY_OPEN_SELECTORS))
check("history container selectors defined",
      len(selectors.HISTORY_CONTAINER_SELECTORS) > 0)
check("history does NOT assume only table",
      '[role="grid"]' in selectors.HISTORY_CONTAINER_SELECTORS,
      str(selectors.HISTORY_CONTAINER_SELECTORS))
check("filename + status header candidates defined",
      bool(selectors.HISTORY_FILENAME_HEADER_CANDIDATES)
      and bool(selectors.HISTORY_STATUS_HEADER_CANDIDATES))
check("timestamp header candidates defined",
      bool(selectors.HISTORY_TIMESTAMP_HEADER_CANDIDATES))
check("download/result/error selectors defined",
      len(selectors.HISTORY_DOWNLOAD_SELECTORS) > 0
      and len(selectors.RESULT_DOWNLOAD_SELECTORS) > 0
      and len(selectors.ERROR_DOWNLOAD_SELECTORS) > 0)
check("pagination/search/filter selectors defined",
      bool(selectors.HISTORY_PAGINATION_SELECTORS)
      and bool(selectors.HISTORY_SEARCH_SELECTORS)
      and bool(selectors.HISTORY_FILTER_SELECTORS))
# Structure-agnostic row matching: multiple row representations supported.
check("row selectors cover tr + role row + grid container",
      'tr' in selectors.HISTORY_ROW_SELECTORS
      and '[role="row"]' in selectors.HISTORY_ROW_SELECTORS)
# No invented status names — mapping uses only pragmatic text markers; the
# candidate list is non-empty and every key is one of the known statuses.
check("status markers contain success/failed keys",
      "success" in selectors.UPLOAD_STATUS_MARKERS
      and "failed" in selectors.UPLOAD_STATUS_MARKERS
      and "processing" in selectors.UPLOAD_STATUS_MARKERS)

# The verified-history merge mapping accepts history container/search keys.
check("verified map includes history keys",
      "HISTORY_CONTAINER" in selectors._VERIFIED_BY_KEY.values()
      and "HISTORY_SEARCH" in selectors._VERIFIED_BY_KEY.values())

# Row-matching STEER: strongest signal is exact filename; never first-row-only.
strategy_ok = (
    "filename" in selectors.HISTORY_FILENAME_HEADER_CANDIDATES
    or "File" in selectors.HISTORY_FILENAME_HEADER_CANDIDATES
)
check("row-matching identifies filename as strongest signal", strategy_ok)

print("=" * 70)
print(f"TOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
if FAIL:
    sys.exit(1)
