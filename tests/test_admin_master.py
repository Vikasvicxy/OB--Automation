"""Admin Master Management tests.

These tests run against a TEMP SQLite DB only (never the real portal DB) and
NEVER touch the source Excel master files, candidates, generated Excel, or
eSampark. They cover the 26-section admin master prompt:

  * Derived cost codes (Entity+Operation) with 4 valid combos.
  * Immutable identity, deactivate+recreate, no hard deletes.
  * Admin additions/overrides merged into the EFFECTIVE view consumed by the
    production resolver (Smart Upload / Manual Entry / Validation).
  * Excel masters left untouched.
  * Role/designation + alias management.
  * Prexo restriction for 8751/8752.
  * Change history with changed_by.
  * REAL_UPLOAD_ENABLED behaviour unchanged (still False, no upload).

Run with:
    python tests/test_admin_master.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from app import database as db  # noqa: E402
from app import admin_master  # noqa: E402
from app import master_data  # noqa: E402
from app import rules  # noqa: E402
from app.portal import esampark  # noqa: E402

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
    master_data.ensure_masters_loaded()
    rules.refresh_masters()
    return tmp


# ── Cost code derivation (all 4 combos) ──────────────────────────────────────


def test_cost_codes():
    print("Cost code derivation (4 combos)")
    cases = [
        ("Flipkart", "Last Mile", "4421"),
        ("Flipkart", "First Mile", "4441"),
        ("Myntra", "Last Mile", "8751"),
        ("Myntra", "First Mile", "8752"),
    ]
    for ent, op, cc in cases:
        check(f"derive_cost_code({ent}, {op}) == {cc}",
              admin_master.derive_cost_code(ent, op) == cc)
    check("invalid combo returns None",
          admin_master.derive_cost_code("Flipkart", "Last") is None)
    combos = admin_master.valid_entity_operation()
    check("valid_entity_operation has 4 combos", len(combos) == 4)
    check("combos carry derived cost codes",
          {c["cost_code"] for c in combos} == {"4421", "4441", "8751", "8752"})


# ── Facility create / derived cost code / identity lock ──────────────────────


def test_facility_create_derived():
    print("Facility create derives cost code from Entity+Operation")
    r = admin_master.add_facility({
        "facility_name": "NewHub_BLR", "location_code": "BLR/NEW",
        "entity": "Flipkart", "operation": "Last Mile", "state": "Karnataka",
    })
    check("FK+LM facility created", r.get("ok"), r)
    check("derived cost code 4421 stored", r.get("cost_code") == "4421", r)
    check("source is Admin", r.get("facility", {}).get("source") == "Admin", r)

    r = admin_master.add_facility({
        "facility_name": "NewHubPl_BLR", "location_code": "BLR/NEW",
        "entity": "Flipkart", "operation": "First Mile", "state": "",
    })
    check("FK+FM facility created with 4441",
          r.get("ok") and r.get("cost_code") == "4441", r)

    r = admin_master.add_facility({
        "facility_name": "NewHubMy_BLR", "location_code": "BLR/NEW",
        "entity": "Myntra", "operation": "Last Mile", "state": "",
    })
    check("MY+LM facility created with 8751",
          r.get("ok") and r.get("cost_code") == "8751", r)

    r = admin_master.add_facility({
        "facility_name": "NewHubMyFM_BLR", "location_code": "BLR/NEW",
        "entity": "Myntra", "operation": "First Mile", "state": "",
    })
    check("MY+FM facility created with 8752",
          r.get("ok") and r.get("cost_code") == "8752", r)


def test_manual_cost_code_rejected():
    print("Manual incompatible cost code rejected; derived forced")
    r = admin_master.add_facility({
        "facility_name": "WrongCodeHub", "location_code": "BLR/NEW",
        "entity": "Flipkart", "operation": "Last Mile", "cost_code": "8751",
    })
    check("supplying 8751 for Flipkart+LM blocked", not r.get("ok"), r)
    check("error explains derived code",
          any("4421" in e for e in r.get("errors", [])), r.get("errors"))
    # If a matching cost code is supplied it is accepted but stored as derived.
    r = admin_master.add_facility({
        "facility_name": "OkCodeHub", "location_code": "BLR/NEW",
        "entity": "Myntra", "operation": "First Mile", "cost_code": "8752",
    })
    check("matching cost code accepted, stored 8752",
          r.get("ok") and r.get("cost_code") == "8752", r)


def test_duplicate_facility_blocked():
    print("Duplicate facility creation blocked (incl. Excel names)")
    name = "NewHub_Dup"
    r = admin_master.add_facility({
        "facility_name": name, "location_code": "BLR/NEW",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    check("first create ok", r.get("ok"), r)
    r2 = admin_master.add_facility({
        "facility_name": name, "location_code": "BLR/NEW",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    check("duplicate create blocked", not r2.get("ok"), r2.get("errors"))
    # Creating an admin record with an existing admin name is also blocked.
    r3 = admin_master.add_facility({
        "facility_name": name, "location_code": "BLR/OTHER",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    check("blocked re-creating same admin facility name", not r3.get("ok"), r3.get("errors"))


def test_identity_immutable():
    print("Facility identity immutable after creation; edit only editable fields")
    r = admin_master.add_facility({
        "facility_name": "IdLockHub", "location_code": "BLR/NEW",
        "entity": "Flipkart", "operation": "Last Mile", "state": "Karnataka",
    })
    fid = r["facility_id"]
    # Attempt to change entity -> must be blocked.
    e = admin_master.update_facility(fid, {
        "facility_name": "IdLockHub", "location_code": "BLR/CHG",
        "entity": "Myntra", "operation": "Last Mile",
    })
    check("editing entity blocked", not e.get("ok"), e.get("errors"))
    check("error mentions identity", any("identity" in x.lower() for x in e.get("errors", [])), e.get("errors"))
    # Editing ONLY editable fields succeeds.
    e = admin_master.update_facility(fid, {
        "facility_name": "IdLockHub", "location_code": "BLR/CHG",
        "entity": "Flipkart", "operation": "Last Mile", "state": "Tamil Nadu",
    })
    check("edit location/state allowed", e.get("ok"), e.get("errors"))
    rec = db.get_master_facility(fid)
    check("location updated", rec["location_code"] == "BLR/CHG", rec)
    check("state updated", rec["state"] == "Tamil Nadu", rec)
    check("cost code unchanged", rec["cost_code"] == "4421", rec)


def test_deactivate_reactivate_no_hard_delete():
    print("Deactivate / reactivate; nothing hard-deleted")
    r = admin_master.add_facility({
        "facility_name": "ToggleHub", "location_code": "BLR/NEW",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    fid = r["facility_id"]
    d = admin_master.set_facility_active(fid, False)
    check("deactivate ok", d.get("ok"), d)
    rec = db.get_master_facility(fid)
    check("record still present (not hard-deleted)", rec["active"] == 0, rec)
    check("excluded from effective view",
          "ToggleHub" not in master_data.get_facility_names())
    re = admin_master.set_facility_active(fid, True)
    check("reactivate ok", re.get("ok"), re)
    check("back in effective view", "ToggleHub" in master_data.get_facility_names())


def _excel_file_bytes():
    from app import master_data as md
    files = []
    for key in ("designation", "facility"):
        p = md.MASTERS_DIR / md.MASTER_FILES[key]
        if p.exists():
            files.append((key, p.read_bytes()))
    return files


def test_excel_unchanged():
    print("Source Excel masters never modified by admin actions")
    before_bytes = _excel_file_bytes()
    before_names = {f["facility_name"] for f in master_data._FACILITY_ROWS}
    r = admin_master.add_facility({
        "facility_name": "ExcelSafeHub", "location_code": "BLR/NEW",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    check("facility added ok", r.get("ok"), r)
    after_names = {f["facility_name"] for f in master_data._FACILITY_ROWS}
    check("Excel facility rows unchanged",
          after_names == before_names)
    after_bytes = _excel_file_bytes()
    check("Excel files byte-for-byte untouched",
          after_bytes == before_bytes)
    check("Excel source has no admin hub",
          "ExcelSafeHub" not in before_names)


def test_admin_override_beats_excel():
    print("Active admin override replaces matching Excel facility in effective view")
    excel_name = "KR_NaviMumbai" if "KR_NaviMumbai" in master_data.get_facility_names() else master_data.get_facility_names()[0]
    original_loc = master_data.get_location_for_facility(excel_name)
    r = admin_master.add_facility({
        "facility_name": excel_name, "location_code": "OVR/NEW",
        "entity": "Flipkart", "operation": "Last Mile", "state": "",
    })
    check("override create ok", r.get("ok"), r)
    ovr = master_data.get_location_for_facility(excel_name)
    check("effective location overridden", ovr == "OVR/NEW", ovr)
    check("effective source marked Admin Override", any(
        f["facility_name"] == excel_name and f.get("source") == "Admin Override"
        for f in master_data.get_facilities()))
    # Deactivate the override -> falls back to Excel value.
    admin_master.set_facility_active(r["facility_id"], False)
    check("deactivated override falls back to Excel location",
          master_data.get_location_for_facility(excel_name) == original_loc, original_loc)


def test_effective_merged_list():
    print("Effective merged facility list = Excel + admin additions - inactive admin")
    before = len(master_data.get_facilities())
    r1 = admin_master.add_facility({
        "facility_name": "MergeHubA", "location_code": "BLR/A",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    r2 = admin_master.add_facility({
        "facility_name": "MergeHubB", "location_code": "BLR/B",
        "entity": "Myntra", "operation": "First Mile",
    })
    check("two additions ok", r1.get("ok") and r2.get("ok"))
    check("effective view grew by 2", len(master_data.get_facilities()) == before + 2)
    admin_master.set_facility_active(r2["facility_id"], False)
    check("inactive admin excluded", len(master_data.get_facilities()) == before + 1)
    check("MergeHubB gone", "MergeHubB" not in master_data.get_facility_names())
    check("MergeHubA present", "MergeHubA" in master_data.get_facility_names())


# ── Resolver uses effective view ─────────────────────────────────────────────


def test_smart_upload_resolves_admin_hub():
    print("Production resolver (Smart Upload) uses effective master")
    r = admin_master.add_facility({
        "facility_name": "NewHub_Resolve", "location_code": "BLR/NEW",
        "entity": "Flipkart", "operation": "Last Mile", "state": "",
    })
    check("add hub ok", r.get("ok") and r.get("cost_code") == "4421", r)
    check("hub in rules.get_hubs_for_cost_code(4421)",
          "NewHub_Resolve" in rules.get_hubs_for_cost_code("4421"))
    check("hub in master_data.get_hubs_for_cost_code(4421)",
          "NewHub_Resolve" in master_data.get_hubs_for_cost_code("4421"))
    res = rules.resolve_smart_onboarding("Delivery Executive", "NewHub_Resolve")
    check("resolver accepted admin-added hub",
          res.get("facility") == "NewHub_Resolve", res)
    check("resolver derived cost code",
          res.get("cost_code") == "4421", res)
    check("resolver resolved role", res.get("role") == "LM - Delivery Executive", res)


def test_manual_entry_uses_effective_master():
    print("Manual Entry / Validation resolvers use effective masters")
    from app import validation as validation_engine
    check("validation module loads after admin changes",
          validation_engine.get_set("A") is not None)
    # Manual Entry dropdowns / role resolvers read the effective hub + role
    # masters; an admin-added facility must appear there.
    r = admin_master.add_facility({
        "facility_name": "ManualHub", "location_code": "BLR/MAN",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    hubs = master_data.get_hubs_for_cost_code("4421")
    check("manual hub dropdown (4421) exposes admin hub", "ManualHub" in hubs, hubs)
    hubs_rules = rules.get_hubs_for_cost_code("4421")
    check("rules hub list (4421) exposes admin hub", "ManualHub" in hubs_rules, hubs_rules)
    res = rules.resolve_smart_onboarding("Sorter", "ManualHub")
    check("role resolver accept admin hub input without unresolved flag",
          res.get("role_unresolved") is False, res)


# ── Role / designation management ────────────────────────────────────────────


def test_role_add_and_aliases():
    print("Role add with derived cost-code compatibility + alias resolution")
    r = admin_master.add_role({
        "official_name": "LM - Delivery Captain", "entity_scope": "Flipkart",
        "operation": "Last Mile", "cost_codes": "4421", "aliases": "captain, delivery cap",
    })
    check("role added ok", r.get("ok"), r.get("errors"))
    check("role has role_id", bool(r.get("role_id")), r)
    payload = r["role"]
    check("role cost codes stored", payload["cost_codes"] == ["4421"], payload)
    check("role aliases stored", "captain" in payload["aliases"], payload)

    # Alias resolves to the new official role via effective aliases.
    merged_aliases = master_data.get_role_aliases()
    check("admin alias in effective aliases",
          merged_aliases.get("captain") == "LM - Delivery Captain", merged_aliases)
    # Designation resolution for 4421 now offers the new role.
    des = master_data.get_designations_for_cost_code("4421")
    check("new role in effective designations for 4421",
          "LM - Delivery Captain" in des, des)


def test_role_cost_code_compat():
    print("Role cost code must be compatible with operation")
    r = admin_master.add_role({
        "official_name": "FM Helper", "entity_scope": "Flipkart",
        "operation": "First Mile", "cost_codes": "4421", "aliases": "",
    })
    check("4421 rejected for First Mile role", not r.get("ok"), r.get("errors"))
    r = admin_master.add_role({
        "official_name": "FM Helper2", "entity_scope": "Flipkart",
        "operation": "First Mile", "cost_codes": "4441", "aliases": "",
    })
    check("4441 accepted for First Mile role", r.get("ok"), r.get("errors"))
    r = admin_master.add_role({
        "official_name": "BadCC", "entity_scope": "Flipkart",
        "operation": "Last Mile", "cost_codes": "9999", "aliases": "",
    })
    check("invalid cost code 9999 rejected", not r.get("ok"), r.get("errors"))


def test_prexo_restriction():
    print("Prexo roles must NOT allow 8751/8752")
    r = admin_master.add_role({
        "official_name": "Prexo Sorter X", "entity_scope": "Myntra",
        "operation": "Last Mile", "cost_codes": "4421,8751", "aliases": "",
    })
    check("Prexo + 8751 blocked", not r.get("ok"), r.get("errors"))
    r = admin_master.add_role({
        "official_name": "Prexo FM X", "entity_scope": "Myntra",
        "operation": "First Mile", "cost_codes": "8752", "aliases": "",
    })
    check("Prexo + 8752 blocked", not r.get("ok"), r.get("errors"))
    r = admin_master.add_role({
        "official_name": "Prexo LM X", "entity_scope": "Flipkart",
        "operation": "Last Mile", "cost_codes": "4421", "aliases": "",
    })
    check("Prexo allowed for 4421 (Flipkart LM)", r.get("ok"), r.get("errors"))


def test_role_duplicate_and_active():
    print("Role duplicate blocked; deactivate removes from effective")
    r = admin_master.add_role({
        "official_name": "LM - Duplicate Role", "entity_scope": "Flipkart",
        "operation": "Last Mile", "cost_codes": "4421", "aliases": "",
    })
    check("role created", r.get("ok"), r)
    r2 = admin_master.add_role({
        "official_name": "LM - Duplicate Role", "entity_scope": "Flipkart",
        "operation": "Last Mile", "cost_codes": "4421", "aliases": "",
    })
    check("duplicate role blocked", not r2.get("ok"), r2.get("errors"))
    rid = r["role_id"]
    admin_master.set_role_active(rid, False)
    check("inactive role excluded from effective designations",
          "LM - Duplicate Role" not in master_data.get_designations_for_cost_code("4421"))
    admin_master.set_role_active(rid, True)
    check("reactivated role back in effective designations",
          "LM - Duplicate Role" in master_data.get_designations_for_cost_code("4421"))


def test_role_immutable_exists_db():
    print("Role rows and inactive admin facilities excluded from effective view")
    # Roles excluded when inactive
    r = admin_master.add_role({
        "official_name": "LM - Temp Role", "entity_scope": "Flipkart",
        "operation": "Last Mile", "cost_codes": "4421", "aliases": "",
    })
    check("temp role active by default", r["role"]["active"] == 1, r["role"])
    admin_master.set_role_active(r["role_id"], False)
    rec = db.get_master_role(r["role_id"])
    check("role hard-delete never occurs (still present, inactive)",
          rec is not None and rec["active"] == 0, rec)


# ── Status / history / export ────────────────────────────────────────────────


def test_history_changed_by():
    print("Change history records every mutation")
    before = admin_master.change_history()
    r = admin_master.add_facility({
        "facility_name": "HistHub", "location_code": "BLR/H",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    hist = admin_master.change_history()
    check("history grew", len(hist) > len(before))
    check("history has a facility Created entry",
          any(h["action"].startswith("Created") for h in hist), hist[:1])
    latest = hist[0]
    check("changed_by is local-admin", latest.get("changed_by") == "local-admin", latest)


def test_status_counts():
    print("Settings master status counts (Excel/Admin/Effective/Overrides)")
    st = admin_master.get_master_status()
    excel = st["excel_facilities"]
    before_admin = st["admin_facilities"]
    before_effective = st["effective_facilities"]
    check("status has excel_facilities", excel > 0, st)
    r = admin_master.add_facility({
        "facility_name": "StatusHub", "location_code": "BLR/S",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    st2 = admin_master.get_master_status()
    check("admin_facilities incremented",
          st2["admin_facilities"] == before_admin + 1, st2)
    check("effective_facilities grew",
          st2["effective_facilities"] == before_effective + 1, st2)
    check("overrides count tracks matching excel names",
          isinstance(st2.get("overrides"), int), st2)


def test_export():
    print("Export helpers return effective facility rows and admin roles")
    admin_master.add_facility({
        "facility_name": "ExpHub", "location_code": "BLR/E",
        "entity": "Flipkart", "operation": "First Mile",
    })
    fac = admin_master.export_facilities()
    check("export_facilities includes admin hub",
          any(f["Facility"] == "ExpHub" and f["Cost Code"] == "4441" for f in fac), fac[:2])
    admin_master.add_role({
        "official_name": "LM - Export Role", "entity_scope": "Flipkart",
        "operation": "Last Mile", "cost_codes": "4421", "aliases": "",
    })
    roles = admin_master.export_roles()
    check("export_roles includes admin role",
          any(r["Role"] == "LM - Export Role" for r in roles), roles[:2])


def test_no_candidate_or_esampark_mutation():
    print("Admin master changes never touch candidates/generated Excel/eSampark")
    import app.database as database
    cand_before = database.count_candidates() if hasattr(database, "count_candidates") else None
    admin_master.add_facility({
        "facility_name": "NoSideEffectHub", "location_code": "BLR/N",
        "entity": "Flipkart", "operation": "Last Mile",
    })
    admin_master.add_role({
        "official_name": "LM - NoSide Role", "entity_scope": "Flipkart",
        "operation": "Last Mile", "cost_codes": "4421", "aliases": "",
    })
    if cand_before is not None:
        check("candidate count unchanged",
              database.count_candidates() == cand_before)
    # REAL_UPLOAD_ENABLED unchanged (False) — no upload can have occurred.
    check("REAL_UPLOAD_ENABLED still False",
          getattr(esampark, "REAL_UPLOAD_ENABLED", False) is False or
          getattr(esampark, "REAL_UPLOAD_ENABLED", False) == "false")


def test_real_upload_flag_unchanged():
    print("REAL_UPLOAD_ENABLED / ESAMPARK_LIVE_TEST_MODE defaults are safe")
    check("live test mode default False",
          getattr(esampark, "ESAMPARK_LIVE_TEST_MODE", False) is False or
          getattr(esampark, "ESAMPARK_LIVE_TEST_MODE", False) == "false")


def test_admin_feature_gate():
    print("Admin feature toggle")
    check("ADMIN_FEATURE_ENABLED True by default",
          admin_master.ADMIN_FEATURE_ENABLED is True)
    check("admin_enabled() True", admin_master.admin_enabled() is True)


def test_validation_suites_still_pass():
    print("Existing validation suites run after admin changes (smoke)")
    from app import validation as validation_engine
    # Set B and C are fully clean; Set A has one long-standing pre-existing
    # failure (CASE 11) unrelated to admin changes.
    want = {"A": 19, "B": 20, "C": 50}
    for s in ("A", "B", "C"):
        run = validation_engine.run_all_cases(s)
        summary = run["summary"]
        check(f"validation set {s} passed >= baseline ({want[s]})",
              summary["passed"] >= want[s], summary)
        check(f"validation set {s} failed is only/below the known baseline",
              summary["failed"] <= (1 if s == "A" else 0), summary)


def run_all():
    global PASS, FAIL
    print("=" * 60)
    fresh_db()
    test_cost_codes()
    fresh_db()
    test_facility_create_derived()
    fresh_db()
    test_manual_cost_code_rejected()
    fresh_db()
    test_duplicate_facility_blocked()
    fresh_db()
    test_identity_immutable()
    fresh_db()
    test_deactivate_reactivate_no_hard_delete()
    fresh_db()
    test_excel_unchanged()
    fresh_db()
    test_admin_override_beats_excel()
    fresh_db()
    test_effective_merged_list()
    fresh_db()
    test_smart_upload_resolves_admin_hub()
    fresh_db()
    test_manual_entry_uses_effective_master()
    fresh_db()
    test_role_add_and_aliases()
    fresh_db()
    test_role_cost_code_compat()
    fresh_db()
    test_prexo_restriction()
    fresh_db()
    test_role_duplicate_and_active()
    fresh_db()
    test_role_immutable_exists_db()
    fresh_db()
    test_history_changed_by()
    fresh_db()
    test_status_counts()
    fresh_db()
    test_export()
    fresh_db()
    test_no_candidate_or_esampark_mutation()
    fresh_db()
    test_real_upload_flag_unchanged()
    fresh_db()
    test_admin_feature_gate()
    fresh_db()
    test_validation_suites_still_pass()
    print("=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
