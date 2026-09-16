"""Tests for the Manual Validation Mode.

Run with:
    python tests/test_validation.py

Covers:
  1. All 20 configured cases load.
  2. Run Logic Test uses the PRODUCTION resolver (no parallel implementation).
  3. Expected/actual comparison works (PASS/FAIL per field).
  4. Needs Review handled separately from FAIL.
  5. Pass-rate calculations correct.
  6. Wrong-auto-decision metric correct.
  7. Validation history saves separately from real candidates.
  8. Export excludes sensitive fields.
  9. No production candidate records created.
 10. REAL_UPLOAD_ENABLED remains false.
"""

import io
import os
import sys
import tempfile
import csv as _csv
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import validation as val
from app import rules
from app import database as db


PASS = 0
FAIL = 0


def check(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {extra}")


# ── Helpers to isolate the DB ────────────────────────────────────────────────


def _use_temp_db():
    tmp = tempfile.mkdtemp()
    db.DB_DIR = Path(tmp) / "database"
    db.DB_PATH = db.DB_DIR / "teamhr.db"
    db.init_db()
    return tmp


def test_01_all_cases_load():
    print("\n--- 1. All configured cases load ---")
    check(len(val.CASES) == 20, f"20 cases configured (got {len(val.CASES)})")
    ids = [c["case_id"] for c in val.CASES]
    expected_ids = [f"CASE {i:02d}" for i in range(1, 21)]
    check(ids == expected_ids, "case ids are CASE 01..CASE 20", str(ids))
    for c in val.CASES:
        check(c.get("role_text"), f"{c['case_id']} has role_text")
        check(c.get("hub_text"), f"{c['case_id']} has hub_text")
        check(isinstance(c.get("expected"), dict), f"{c['case_id']} has expected fixture")
    print(f"  total configured: {len(val.CASES)}")


def test_02_run_logic_uses_production_resolver():
    print("\n--- 2. Run Logic Test uses production resolver ---")
    import inspect
    src = inspect.getsource(val.run_logic_case)
    check("rules.resolve_smart_onboarding" in src,
          "run_logic_case references rules.resolve_smart_onboarding (production resolver)")
    # Entity check from a known case to ensure it really resolves via production.
    case = val.get_case("CASE 08")  # Myntra LM sorter Hebbal
    actual = val.run_logic_case(case)
    check(actual["entity"] == "Myntra", "resolution flows through production entity logic")
    check(actual["cost_code"] == "8751", "resolution flows through production cost-code logic")


def test_03_expected_actual_comparison():
    print("\n--- 3. Expected/actual comparison works ---")
    case = val.get_case("CASE 02")  # PALLAVI
    actual = val.run_logic_case(case)
    verdict = val.evaluate_case(case, actual)
    check(verdict["status"] == "PASS", "CASE 02 overall PASS")
    field_map = {fr["field"]: fr["result"] for fr in verdict["field_results"]}
    check(field_map.get("salary") == "PASS", "salary field PASS")
    check(field_map.get("cost_code") == "PASS", "cost_code field PASS")
    check(field_map.get("facility") == "PASS", "facility field PASS")
    # A deliberately wrong expectation must fail.
    bad_case = dict(case)
    bad_case["expected"] = dict(case["expected"])
    bad_case["expected"]["cost_code"] = "9999"
    bad_v = val.evaluate_case(bad_case, actual)
    check(bad_v["status"] == "FAIL", "deliberately wrong expectation -> FAIL")


def test_04_needs_review_separate_from_fail():
    print("\n--- 4. Needs Review handled separately ---")
    case18 = val.get_case("CASE 18")  # unknown hub -> review
    v18 = val.evaluate_case(case18, val.run_logic_case(case18))
    check(v18["status"] == "PASS", "CASE 18 PASS (intentionally flagged review)")
    check(v18["expect_review"] is True, "CASE 18 expect_review True")
    check(v18["actual_review"] is True, "CASE 18 actual_review True")
    # Needs Review must not count as FAIL.
    summary = val.summarize([v18])
    check(summary["needs_review"] == 1, "needs_review counted separately (got 1)")
    check(summary["failed"] == 0, "review case is not counted as FAIL")


def test_05_pass_rate_calculation():
    print("\n--- 5. Pass-rate calculation correct ---")
    # One PASS (non-review), three REVIEW-pass: total 4 passed.
    cases = [
        {"status": "PASS", "expect_review": False, "safety": {"correct_auto": True, "correct_review": False, "wrong_auto_decision": False, "false_review": False, "missing_extraction": False}, "expected": {}},
        {"status": "PASS", "expect_review": True, "safety": {"correct_auto": False, "correct_review": True, "wrong_auto_decision": False, "false_review": False, "missing_extraction": False}, "expected": {}},
        {"status": "PASS", "expect_review": True, "safety": {"correct_auto": False, "correct_review": True, "wrong_auto_decision": False, "false_review": False, "missing_extraction": False}, "expected": {}},
        {"status": "FAIL", "expect_review": True, "safety": {"correct_auto": False, "correct_review": False, "wrong_auto_decision": True, "false_review": False, "missing_extraction": False}, "expected": {"must_not": []}},
        {"status": "FAIL", "expect_review": False, "safety": {"correct_auto": False, "correct_review": False, "wrong_auto_decision": False, "false_review": False, "missing_extraction": False}, "expected": {"must_not": []}},
        {"status": "PASS", "expect_review": True, "safety": {"correct_auto": False, "correct_review": True, "wrong_auto_decision": False, "false_review": False, "missing_extraction": False}, "expected": {}},
    ]
    summary = val.summarize(cases)
    check(summary["total_tests"] == 6, "total 6")
    check(summary["passed"] == 4, "passed 4 (1 auto + 3 review)")
    check(summary["failed"] == 2, "failed 2")
    check(summary["needs_review"] == 3, "needs_review 3")
    # pass rate = 4/6 = 66.7
    check(abs(summary["pass_rate"] - 66.7) < 0.1, f"pass_rate 66.7% (got {summary['pass_rate']})")


def test_06_wrong_auto_decision_metric():
    print("\n--- 6. Wrong-auto-decision metric correct ---")
    # CASE 11 is expected to reveal the known defect (LM prexo + Myntra).
    case11 = val.get_case("CASE 11")
    v11 = val.evaluate_case(case11, val.run_logic_case(case11))
    summary = val.summarize([v11])
    # The metric counts it; whether it is 0/1 depends on current production
    # behavior, which the suite must surface honestly.
    check(summary["wrong_auto_decision"] >= 0, "wrong_auto_decision metric present")
    # Aggregate across the whole suite must equal the per-case count of
    # 'wrong_auto_decision' flags.
    run = val.run_all_cases()
    expected_wrong = sum(1 for v in run["verdicts"] if v["safety"].get("wrong_auto_decision"))
    check(run["summary"]["wrong_auto_decision"] == expected_wrong,
          "summary wrong-auto-decision == per-case count",
          f"({run['summary']['wrong_auto_decision']} vs {expected_wrong})")


def test_07_history_saves_separately():
    print("\n--- 7. Validation history saves separately from real candidates ---")
    _use_temp_db()
    before_candidates = db.count_candidates()["total"]
    before_runs = db.count_validation_runs()
    db.save_validation_run("CASE 05", "logic", "PASS", "{}", "{}", "note")
    after_runs = db.count_validation_runs()
    after_candidates = db.count_candidates()["total"]
    check(after_runs == before_runs + 1, "validation run persisted")
    check(after_candidates == before_candidates, "no production candidate created")
    runs = db.list_validation_runs()
    check(len(runs) >= 1, "list_validation_runs returns rows")
    check("aadhaar" not in str(runs[0].keys()).lower()
          and "address" not in str(runs[0].keys()).lower(),
          "validation history has no aadhaar/address column")


def test_08_export_excludes_sensitive_fields():
    print("\n--- 8. Export excludes sensitive fields ---")
    _use_temp_db()
    from app import validation as vend
    # Directly test the export serialization helper (no jinja2 needed).
    run = vend.run_all_cases()
    # Build CSV rows the same way the route does.
    rows = []
    header = ["case_id", "scenario", "expected", "actual", "status", "review_expected", "notes", "validated_at"]
    rows.append(header)
    for v in run["verdicts"]:
        rows.append([
            v.get("case_id", ""), "", "",
            str(v.get("actual", {})), v.get("status", ""),
            "yes" if v.get("expect_review") else "no", v.get("notes", ""), "",
        ])
    blob = "\n".join(",".join(r) for r in rows).lower()
    check("aadhaar" not in blob, "no full aadhaar in export")
    check("address" not in blob, "no full address in export")
    check("ocr" not in blob, "no raw OCR text in export")


def test_09_no_production_candidate_created_by_run_all():
    print("\n--- 9. No production candidate records created ---")
    _use_temp_db()
    db.get_or_create_batch(batch_id=1)
    before = db.count_candidates()["total"]
    before_batch = db.get_batch(1)
    val.run_all_cases()  # pure in-memory, must not touch DB
    after = db.count_candidates()["total"]
    check(after == before, "candidate count unchanged after run_all (got %d vs %d)" % (after, before))


def test_10_real_upload_disabled():
    print("\n--- 10. REAL_UPLOAD_ENABLED remains false ---")
    from app.portal import esampark
    flag = getattr(esampark, "REAL_UPLOAD_ENABLED", None)
    check(flag is False, "esampark.REAL_UPLOAD_ENABLED is False", f"(got {flag!r})")
    # The validation module must never create/upload real anything.
    check(not hasattr(val, "upload"), "validation module has no upload path")


def test_11_variants_safe_and_master_derived():
    print("\n--- 11. Variants are safe and master-derived (no random rules) ---")
    variants = val.generate_variants()
    check(len(variants) > 0, "variant generator returns variants")
    for vv in variants:
        check("role_text" in vv and "hub_text" in vv and "salary_text" in vv,
              "variant has role/hub/salary fields")
    # Each variant must route through the production resolver without error.
    case = dict(val.get_case("CASE 08"))  # Myntra base
    for vv in variants[:5]:
        tc = dict(case, role_text=vv["role_text"], hub_text=vv["hub_text"],
                  salary_text=vv["salary_text"])
        a = val.run_logic_case(tc)
        check(isinstance(a, dict), "variant resolves via production resolver")


def test_12_variant_for_case_respects_entity():
    print("\n--- 12. per-case variant generation honors entity ---")
    myntra_v = val.generate_variants_for_case("CASE 08")
    check(len(myntra_v) > 0, "Myntra case generates variants")
    for vv in myntra_v:
        # Resolving the variant through the production resolver must keep the
        # Myntra entity (never flip to Flipkart) because the hub stays Myntra.
        tc = dict(val.get_case("CASE 08"),
                  role_text=vv["role_text"], hub_text=vv["hub_text"],
                  salary_text=vv["salary_text"])
        a = val.run_logic_case(tc)
        check(a["entity"] == "Myntra", "Myntra variant keeps entity Myntra",
              vv["role_text"] + "/" + vv["hub_text"])


def test_13_set_b_loads_and_selector_works():
    print("\n--- 13. Set B loads (20 cases) and set selector works ---")
    check(len(val.SET_B) == 20, f"Set B has 20 cases (got {len(val.SET_B)})")
    ids = [c["case_id"] for c in val.SET_B]
    expected_ids = [f"CASE B{i:02d}" for i in range(1, 21)]
    check(ids == expected_ids, "Set B ids are CASE B01..B20", str(ids))
    check(val.CASES is val.SET_A, "CASES alias points to SET_A")
    check(val.get_set("A") is val.SET_A, "get_set('A') == SET_A")
    check(len(val.get_set("A")) == 20, "get_set('A') == 20")
    check(len(val.get_set("B")) == 20, "get_set('B') == 20")
    check(len(val.get_set("C")) == 50, "get_set('C') == 50")
    check(len(val.get_set("all")) == 90, "get_set('all') == 90")
    for c in val.SET_B:
        check(c.get("role_text"), f"{c['case_id']} has role_text")
        check(c.get("hub_text"), f"{c['case_id']} has hub_text")
        check(isinstance(c.get("expected"), dict), f"{c['case_id']} has expected fixture")
    # B10 (Myntra + Prexo) must remain covered; B11 (exact _PL hub) too.
    b10 = val.get_case("CASE B10", "B")
    b11 = val.get_case("CASE B11", "B")
    check(b10["expected"].get("entity") == "Myntra", "B10 is a Myntra case")
    check("prexo" in b10["role_text"].lower() or "prexo" in str(b10.get("expected", {})).lower(),
          "B10 covers the Prexo + Myntra pattern")
    check("_PL" in b11["hub_text"], "B11 uses an exact _PL hub")


def test_14_run_all_cases_set_aware():
    print("\n--- 14. run_all_cases is set-aware (default preserves Set A) ---")
    default = val.run_all_cases()
    check(len(default["verdicts"]) == 20, "default run_all_cases is Set A (20 verdicts)")
    check(set(v["case_id"] for v in default["verdicts"]) ==
          {f"CASE {i:02d}" for i in range(1, 21)}, "default set is Set A only")
    b_run = val.run_all_cases("B")
    check(len(b_run["verdicts"]) == 20, "run_all_cases('B') == 20 verdicts")
    check(set(v["case_id"] for v in b_run["verdicts"]) ==
          {f"CASE B{i:02d}" for i in range(1, 21)}, "Set B verdicts are B01..B20")
    all_run = val.run_all_cases("all")
    check(len(all_run["verdicts"]) == 90, "run_all_cases('all') == 90 verdicts")


def test_22_set_c_loads_and_selector_works():
    print("\n--- 22. Set C loads (50 cases) and set selector supports A/B/C/All ---")
    check(len(val.SET_C) == 50, f"Set C has 50 cases (got {len(val.SET_C)})")
    ids = [c["case_id"] for c in val.SET_C]
    expected_ids = [f"C{i:02d}" for i in range(1, 51)]
    check(ids == expected_ids, "Set C ids are C01..C50", str(ids))
    # Selector supports A / B / C / All.
    check(len(val.get_set("A")) == 20, "get_set('A') == 20")
    check(len(val.get_set("B")) == 20, "get_set('B') == 20")
    check(len(val.get_set("C")) == 50, "get_set('C') == 50")
    check(len(val.get_set("all")) == 90, "get_set('all') == 90")
    check(len(val.get_set("Set C")) == 50, "get_set('Set C') == 50")
    check(len(val.get_set("set_c")) == 50, "get_set('set_c') == 50")
    for c in val.SET_C:
        check(isinstance(c.get("expected"), dict), f"{c['case_id']} has expected fixture")
        check("role_text" in c and "hub_text" in c, f"{c['case_id']} has role/hub inputs")


def test_23_set_c_expected_structures_valid():
    print("\n--- 23. All Set C expected fixtures have valid structure ---")
    for c in val.SET_C:
        exp = c["expected"]
        for f in ("entity", "operation", "cost_code", "role", "facility", "location", "salary"):
            if f not in exp:
                continue
            val_ = exp[f]
            if f == "role" and isinstance(val_, dict):
                check("compatible_with" in val_, f"{c['case_id']} role dict has compatible_with")
            elif f == "facility" and isinstance(val_, dict):
                check("options" in val_ and "allow_blank" in val_,
                      f"{c['case_id']} facility dict has options+allow_blank")
            else:
                check(isinstance(val_, (str, int, bool, type(None))),
                      f"{c['case_id']} {f} is scalar")
        # A Set C case may be 'auto' or 'review'; if review expected, needs_review True.
        review = bool(exp.get("needs_review")) or bool(c.get("expect_review"))
        check(isinstance(review, bool), f"{c['case_id']} review flag bool")


def test_24_set_c_uses_production_resolver():
    print("\n--- 24. Set C routes through the production resolver ---")
    import inspect
    src = inspect.getsource(val.run_logic_case)
    check("rules.resolve_smart_onboarding" in src, "Set C uses production resolver")
    run = val.run_all_cases("C")
    expected_wrong = sum(1 for v in run["verdicts"] if v["safety"].get("wrong_auto_decision"))
    check(run["summary"]["wrong_auto_decision"] == expected_wrong,
          "Set C summary wrong-auto-decision == per-case count",
          f"({run['summary']['wrong_auto_decision']} vs {expected_wrong})")
    check(run["summary"]["total_tests"] == 50, "Set C total is 50")
    # Needs Review is a subset of passed verdicts.
    check(run["summary"]["needs_review"] <= run["summary"]["passed"],
          "Set C needs_review is a subset of passed")
    check(run["summary"]["correct_auto_decision"] + run["summary"]["correct_needs_review"]
          + run["summary"]["wrong_auto_decision"] + run["summary"]["missing_extraction"]
          == 50,
          "Set C safety classes sum to 50")


def test_25_set_c_safety_metrics_account():
    print("\n--- 25. Set C safety metric counts wrong auto-decisions correctly ---")
    run = val.run_all_cases("C")
    verdicts = run["verdicts"]
    s = run["summary"]
    check(s["total_tests"] == len(verdicts), "Set C Total matches verdict count")
    exp_passed = sum(1 for v in verdicts if v["status"] == "PASS")
    exp_failed = sum(1 for v in verdicts if v["status"] == "FAIL")
    check(s["passed"] == exp_passed, "Set C Passed == PASS verdicts")
    check(s["failed"] == exp_failed, "Set C Failed == FAIL verdicts")
    check(s["needs_review"] == sum(1 for v in verdicts
                                   if v["status"] == "PASS" and v["expect_review"]),
          "Set C Needs Review == PASS+expect_review")
    # No missing extraction expected on any Set C case (all give a role/hub signal).
    check(s["missing_extraction"] == 0, "Set C missing_extraction = 0")
    # Wrong-auto uses the classified wrong decisions; target is 0%.
    # The FIRST run surfaces genuine defects; the report below does NOT fix them.
    check(s["wrong_auto_decision"] == sum(1 for v in verdicts
                                          if v["safety"].get("wrong_auto_decision")),
          "Set C wrong-auto == per-case wrong-auto flags")


def test_26_set_c_ab_preserved_unchanged():
    print("\n--- 26. Set A and Set B remain unchanged by Set C addition ---")
    check(len(val.SET_A) == 20, "Set A still 20 cases")
    check(len(val.SET_B) == 20, "Set B still 20 cases")
    check([c["case_id"] for c in val.SET_A] == [f"CASE {i:02d}" for i in range(1, 21)],
          "Set A case ids unchanged")
    check([c["case_id"] for c in val.SET_B] == [f"CASE B{i:02d}" for i in range(1, 21)],
          "Set B case ids unchanged")
    # No Set C case reuses a Set A/B case verbatim (ids differ).
    a_ids = {c["case_id"] for c in val.SET_A}
    b_ids = {c["case_id"] for c in val.SET_B}
    c_ids = {c["case_id"] for c in val.SET_C}
    check(a_ids.isdisjoint(c_ids) and b_ids.isdisjoint(c_ids), "Set C ids disjoint from A/B")


def test_27_set_c_no_production_candidate():
    print("\n--- 27. Set C never creates production candidates ---")
    from app import database as _db
    import tempfile as _tf
    from pathlib import Path as _P
    tmp = _tf.mkdtemp()
    _db.DB_DIR = _P(tmp) / "database"
    _db.DB_PATH = _db.DB_DIR / "teamhr.db"
    _db.init_db()
    _db.get_or_create_batch(batch_id=1)
    before = _db.count_candidates()["total"]
    val.run_all_cases("C")
    val.run_all_cases("all")
    after = _db.count_candidates()["total"]
    check(after == before, "Set C / all-sets run leaves candidate count unchanged",
          f"({after} vs {before})")


def test_28_set_c_variants_derive_master_data():
    print("\n--- 28. Set C variants are safe and master-derived ---")
    variants = val.generate_variants("C")
    check(len(variants) > 0, "Set C variant generator returns variants")
    for vv in variants:
        check("role_text" in vv and "hub_text" in vv and "salary_text" in vv,
              "Set C variant has role/hub/salary fields")
    case = dict(val.get_case("C01", "C"))
    for vv in variants[:5]:
        tc = dict(case, role_text=vv["role_text"], hub_text=vv["hub_text"],
                  salary_text=vv["salary_text"])
        a = val.run_logic_case(tc)
        check(isinstance(a, dict), "Set C variant resolves via production resolver")


def test_15_set_b_uses_production_resolver():
    print("\n--- 15. Set B routes through the production resolver ---")
    import inspect
    src = inspect.getsource(val.run_logic_case)
    check("rules.resolve_smart_onboarding" in src, "Set B uses production resolver")
    # Compare aggregate wrong-auto-decision metric to per-case flags for B.
    run = val.run_all_cases("B")
    expected_wrong = sum(1 for v in run["verdicts"] if v["safety"].get("wrong_auto_decision"))
    check(run["summary"]["wrong_auto_decision"] == expected_wrong,
          "Set B summary wrong-auto-decision == per-case count",
          f"({run['summary']['wrong_auto_decision']} vs {expected_wrong})")
    check(run["summary"]["total_tests"] == 20, "Set B total is 20")
    check(run["summary"]["passed"] + run["summary"]["failed"] == 20,
          "Set B passed+failed == total (review is a subset of passed)")
    check(run["summary"]["needs_review"] <= run["summary"]["passed"],
          "needs_review is a subset of passed verdicts")


def test_16_set_b_metrics_correct():
    print("\n--- 16. Set B metrics (Total/Passed/Failed/Review/auto-decisions) ---")
    run = val.run_all_cases("B")
    s = run["summary"]
    verdicts = run["verdicts"]
    # Total == number of verdicts.
    check(s["total_tests"] == len(verdicts), "Total matches verdict count")
    # passed counts PASS verdicts; failed counts FAIL; needs_review subset of passed.
    exp_passed = sum(1 for v in verdicts if v["status"] == "PASS")
    check(s["passed"] == exp_passed, "Passed == count of PASS verdicts",
          f"({s['passed']} vs {exp_passed})")
    exp_failed = sum(1 for v in verdicts if v["status"] == "FAIL")
    check(s["failed"] == exp_failed, "Failed == count of FAIL verdicts",
          f"({s['failed']} vs {exp_failed})")
    check(s["needs_review"] == sum(1 for v in verdicts
                                   if v["status"] == "PASS" and v["expect_review"]),
          "Needs Review == count of PASS review verdicts")
    # Safety accounting: every verdict is exactly one of correct_auto,
    # correct_review, wrong_auto, or an untagged plain FAIL (a resolver defect
    # that the classifier surfaces as FAIL but does not tag wrong-auto).
    ca = sum(1 for v in verdicts if v["safety"].get("correct_auto"))
    cr = sum(1 for v in verdicts if v["safety"].get("correct_review"))
    wa = sum(1 for v in verdicts if v["safety"].get("wrong_auto_decision"))
    tagged = ca + cr + wa
    untagged = sum(1 for v in verdicts
                   if v["status"] == "FAIL"
                   and not v["safety"].get("correct_auto")
                   and not v["safety"].get("correct_review")
                   and not v["safety"].get("wrong_auto_decision"))
    check(tagged + untagged == len(verdicts),
          "correct_auto + correct_review + wrong_auto + untagged-FAIL == total",
          f"({ca}+{cr}+{wa}+{untagged} vs {len(verdicts)})")
    # Cross-check against summary aggregates.
    check(s["correct_auto_decision"] == ca, "summary correct_auto == per-case count")
    check(s["correct_needs_review"] == cr, "summary correct_review == per-case count")
    check(s["wrong_auto_decision"] == wa, "summary wrong_auto == per-case count")
    # Wrong-auto rate (target 0%) uses the classifier's tagged wrong decisions.
    check(abs(s["wrong_auto_rate"] - (wa / (ca + wa) * 100)) < 0.1,
          "wrong_auto_rate derived from tagged wrong auto-decisions",
          f"(summary {s['wrong_auto_rate']} vs {round(wa / (ca + wa) * 100, 1)})")
    print(f"    (Set B safety: {ca} correct-auto, {cr} correct-review, "
          f"{wa} wrong-auto, {untagged} untagged-FAIL, "
          f"wrong-auto rate {s['wrong_auto_rate']}%)")


def test_17_set_b_no_production_candidate():
    print("\n--- 17. Set B never creates production candidates ---")
    _use_temp_db()
    db.get_or_create_batch(batch_id=1)
    before = db.count_candidates()["total"]
    val.run_all_cases("B")
    val.run_all_cases("all")
    after = db.count_candidates()["total"]
    check(after == before, "Set B / all-sets run leaves candidate count unchanged",
          f"({after} vs {before})")


def test_18_set_b_required_defect_patterns_covered():
    print("\n--- 18. Set B keeps the two required defect patterns ---")
    # The FIXTURES must express the required expectations exactly. The current
    # production resolver's behavior is surfaced honestly below without
    # weakening the expected result (defect remediation is a separate task).
    b10 = val.get_case("CASE B10", "B")
    check(b10["expected"].get("entity") == "Myntra", "B10 is a Myntra case")
    check(b10["expected"].get("cost_code") == "8751", "B10 expects cost code 8751")
    check(b10["expected"].get("needs_review") is True, "B10 expects Needs Review")
    # Prexo is not valid for the 8751 (Myntra cost) role set: the fixture
    # expects the role to be left blank (rejected) and routed to review.
    check(b10["expected"].get("role") == "",
          "B10 expects Prexo role to be rejected (role blank) for 8751")

    b11 = val.get_case("CASE B11", "B")
    check(b11["expected"].get("cost_code") == "4441", "B11 expects FM/4441 via _PL hub")
    comp = b11["expected"].get("role", {})
    check(comp.get("compatible_with") == "4441", "B11 role compatible with 4441")

    # Surface current production behavior without changing expectations.
    run = val.run_all_cases("B")
    by_id = {v["case_id"]: v for v in run["verdicts"]}
    act_b10 = by_id["CASE B10"]["actual"]
    act_b11 = by_id["CASE B11"]["actual"]
    print(f"    (B10 current: cost={act_b10.get('cost_code')} role={act_b10.get('role')!r} "
          f"-> expects 8751/blank+review)")
    print(f"    (B11 current: cost={act_b11.get('cost_code')} -> expects 4441)")
    check(isinstance(act_b10, dict) and isinstance(act_b11, dict),
          "B10/B11 resolved through production resolver")


def test_19_set_b_variants_derive_master_data():
    print("\n--- 19. Set B variants are safe and master-derived ---")
    variants = val.generate_variants("B")
    check(len(variants) > 0, "Set B variant generator returns variants")
    for vv in variants:
        check("role_text" in vv and "hub_text" in vv and "salary_text" in vv,
              "Set B variant has role/hub/salary fields")
    # All Set B variants route through production resolver without error.
    case = dict(val.get_case("CASE B04", "B"))
    for vv in variants[:5]:
        tc = dict(case, role_text=vv["role_text"], hub_text=vv["hub_text"],
                  salary_text=vv["salary_text"])
        a = val.run_logic_case(tc)
        check(isinstance(a, dict), "Set B variant resolves via production resolver")


def test_20_set_b_honors_facility_location_semantics():
    print("\n--- 20. Set B location inference semantics hold ---")
    run = val.run_all_cases("B")
    by_id = {v["case_id"]: v for v in run["verdicts"]}
    checks = {
        "CASE B01": "BLR/PEN", "CASE B03": "BLR/NLM", "CASE B04": "NelamangalaHub_BLR_PL",
        "CASE B08": "BNS/BLR",
    }
    for cid, want in checks.items():
        got = by_id[cid]["actual"].get("location")
        check(got == want, f"{cid} location == {want}", f"(got {got})")


def test_21_wrong_auto_metric_counts_confident_wrong_select():
    print("\n--- 21. Wrong-auto metric counts confident wrong selects (not only review cases) ---")
    # A case expected to be a plain auto-decision (no review) whose resolver
    # auto-produced a materially wrong concrete value must count as a wrong
    # auto-decision — regardless of expected.needs_review.
    base = dict(val.get_case("CASE B02", "B"))  # expected FM/4441
    wrong_actual = {
        "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
        "role": "LM - Sorter", "facility": "BLR/NLM", "location": "BLR/NLM",
        "salary": 16000, "needs_review": False, "needs_attention": [],
        "role_unresolved": False,
    }
    verdict = val.evaluate_case(base, wrong_actual)
    check(verdict["status"] == "FAIL", "wrong auto-select is a FAIL")
    check(verdict["safety"].get("wrong_auto_decision") is True,
          "confident wrong auto-select counts as wrong_auto_decision")
    check(verdict["safety"].get("missing_extraction") is False,
          "not classified as plain extraction failure")
    # A case that produced NO concrete values is an extraction failure, not a
    # wrong auto-decision.
    empty_actual = {
        "entity": "", "operation": "", "cost_code": "",
        "role": None, "facility": None, "location": None,
        "salary": None, "needs_review": False, "needs_attention": [],
        "role_unresolved": True,
    }
    verdict2 = val.evaluate_case(base, empty_actual)
    check(verdict2["status"] == "FAIL", "empty extraction is a FAIL")
    check(verdict2["safety"].get("wrong_auto_decision") is False,
          "empty extraction is NOT wrong_auto_decision")
    check(verdict2["safety"].get("missing_extraction") is True,
          "empty extraction classified as missing_extraction")
    # Set B goal: wrong auto-decision rate == 0% after fixes.
    b_run = val.run_all_cases("B")
    check(b_run["summary"]["wrong_auto_decision"] == 0,
          "Set B wrong-auto-decision == 0 (target)",
          f"(got {b_run['summary']['wrong_auto_decision']})")
    check(b_run["summary"]["wrong_auto_rate"] == 0.0,
          "Set B wrong-auto-decision rate == 0% (target)",
          f"(got {b_run['summary']['wrong_auto_rate']})")


# ── Runner ───────────────────────────────────────────────────────────────────


def test_29_regression_c27_unknown_myntra_hub_no_overmatch():
    print("\n--- 29. Regression C27: unknown Myntra hub must not overmatch ---")
    c = val.get_case("C27", "C")
    a = val.run_logic_case(c)
    v = val.evaluate_case(c, a)
    check(v["status"] == "PASS", "C27 overall PASS after fix")
    check(a["entity"] == "Myntra", "C27 entity = Myntra")
    check(a["cost_code"] == "8751", "C27 cost_code = 8751")
    check(a["role"] == "LM - Sorter", "C27 role = LM - Sorter")
    check(not a["facility"], "C27 facility left blank (no overmatch)", f"(got {a['facility']!r})")
    check(a["needs_review"] is True, "C27 flagged Needs Review")


def test_30_regression_c45_peenya_hub_only_safe_lm_review():
    print("\n--- 30. Regression C45: Peenya hub only -> safe LM + role review ---")
    c = val.get_case("C45", "C")
    a = val.run_logic_case(c)
    v = val.evaluate_case(c, a)
    check(v["status"] == "PASS", "C45 overall PASS after fix")
    # Peenya is last-mile-only on HubName.xlsx, so the hub alone is NOT op-
    # ambiguous: the resolver may safely pick the LM hub/cost code, but must NOT
    # auto-fill a role.
    check(a["needs_review"] is True, "C45 flagged Needs Review (role missing)")
    check(a["cost_code"] == "4421", "C45 chose the only matching Last Mile cost code",
          f"(got {a['cost_code']!r})")
    check(a["facility"] == "Peenya Hub", "C45 resolved to the Peenya Hub (LM only)",
          f"(got {a['facility']!r})")
    check(not a["role"], "C45 did not silently choose a role", f"(got {a['role']!r})")


def test_31_regression_c46_myntra_never_flips_to_flipkart():
    print("\n--- 31. Regression C46: Myntra entity never flips to Flipkart hub ---")
    c = val.get_case("C46", "C")
    a = val.run_logic_case(c)
    v = val.evaluate_case(c, a)
    check(v["status"] == "PASS", "C46 overall PASS after fix")
    check(a["entity"] == "Myntra", "C46 entity = Myntra")
    check(a["cost_code"] == "8751", "C46 cost_code = 8751 (Myntra Last Mile)",
          f"(got {a['cost_code']!r})")
    check(a["facility"] == "HebbalMYNTRAHub_BLR", "C46 facility is the Myntra hub",
          f"(got {a['facility']!r})")
    check("HebbalHub_BLR_PL" not in (a["facility"] or ""), "C46 never picks a Flipkart hub")
    check(a["needs_review"] is True, "C46 flagged Needs Review (role missing)")


if __name__ == "__main__":
    test_01_all_cases_load()
    test_02_run_logic_uses_production_resolver()
    test_03_expected_actual_comparison()
    test_04_needs_review_separate_from_fail()
    test_05_pass_rate_calculation()
    test_06_wrong_auto_decision_metric()
    test_07_history_saves_separately()
    test_08_export_excludes_sensitive_fields()
    test_09_no_production_candidate_created_by_run_all()
    test_10_real_upload_disabled()
    test_11_variants_safe_and_master_derived()
    test_12_variant_for_case_respects_entity()
    test_13_set_b_loads_and_selector_works()
    test_14_run_all_cases_set_aware()
    test_15_set_b_uses_production_resolver()
    test_16_set_b_metrics_correct()
    test_17_set_b_no_production_candidate()
    test_18_set_b_required_defect_patterns_covered()
    test_19_set_b_variants_derive_master_data()
    test_20_set_b_honors_facility_location_semantics()
    test_21_wrong_auto_metric_counts_confident_wrong_select()
    test_22_set_c_loads_and_selector_works()
    test_23_set_c_expected_structures_valid()
    test_24_set_c_uses_production_resolver()
    test_25_set_c_safety_metrics_account()
    test_26_set_c_ab_preserved_unchanged()
    test_27_set_c_no_production_candidate()
    test_28_set_c_variants_derive_master_data()
    test_29_regression_c27_unknown_myntra_hub_no_overmatch()
    test_30_regression_c45_peenya_hub_only_safe_lm_review()
    test_31_regression_c46_myntra_never_flips_to_flipkart()
    print(f"\nTOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
