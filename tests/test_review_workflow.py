"""Focused regression tests for the New Onboarding review workflow.

Run with:
    python tests/test_review_workflow.py

Covers:
  1. Salary normalization across string/int/float/'k'/currency inputs.
  2. Salary persistence uses the canonical 'salary' key (manual review wins).
  3. PIN Code exact-6-digit validation + persistence (never truncated/date).
  4. Aadhaar full-value persistence + 12-digit normalization.
  5. Manual reviewed values override OCR (salary / aadhaar / pin).
  6. Facility / Location regression: master row wins over client hints.
  7. Blind test pack (75+ automatically generated scenarios).
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import database as db
from app import master_data
from app import rules
from app.main import _salary_value, app

from fastapi.testclient import TestClient

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


def fresh_db():
    tmp = tempfile.mkdtemp()
    db.DB_DIR = Path(tmp) / "database"
    db.DB_PATH = db.DB_DIR / "teamhr.db"
    db.init_db()
    os.environ["TEAMHR_CONFIG_FILE"] = str(Path(tmp) / "config.json")
    return tmp


# ── 1. Salary normalization ──────────────────────────────────────────────────

def test_salary_normalization():
    print("\n--- 1. Salary normalization ---")
    valid = [
        ("15000", 15000), ("18k", 18000), ("18 k", 18000), ("18 K", 18000),
        ("salary 18 k", 18000), ("18.5k", 18500), ("18.5 k", 18500),
        ("₹18,000", 18000), ("Rs 18,000", 18000), ("INR 18000", 18000),
        ("16000", 16000), (" 15500 ", 15500), ("15000.0", 15000),
        ("18,000", 18000), ("1000", 1000),
    ]
    for raw, want in valid:
        val, err = rules.normalize_salary(raw)
        check(err is None and val == want,
              f"normalize_salary({raw!r}) = {want} (got {val!r}, {err!r})")

    # Numeric payloads the client may have already converted.
    for raw, want in [(18000, 18000), (18.5, None), (18500.0, 18500), (15000.9, 15000)]:
        val, err = rules.normalize_salary(raw)
        expect_ok = want is not None
        check((err is None) == expect_ok and (val == want),
              f"normalize_salary({raw!r}) -> {want} ok={expect_ok} (got {val!r}, {err!r})")

    invalid = [
        None, "", "   ", True, False, "15", "999", "0", "9:30 am", "10:30",
        "abc", "150", "10:30 am 12:45", -500, 999, "12:45:23", "-18000",
        "-15k", "Rs -15 k", "salary -18k",
    ]
    for raw in invalid:
        _, err = rules.normalize_salary(raw)
        check(err is not None, f"normalize_salary({raw!r}) rejected (got err={err!r})")


# ── 2. Canonical salary key (manual review wins) ─────────────────────────────

def test_salary_canonical_key():
    print("\n--- 2. Canonical salary key resolution ---")
    check(_salary_value({"salary": "15500", "salary_normalized": "99999"}) == "15500",
          "'salary' wins over salary_normalized")
    check(_salary_value({"salary_normalized": "18000"}) == "18000",
          "falls back to salary_normalized")
    check(_salary_value({"salary_display": "18 k"}) == "18 k",
          "falls back to salary_display")
    check(_salary_value({"salary_display": "500", "salary_normalized": ""}) == "500",
          "empty salary_normalized still falls through")
    check(_salary_value({"salary": "", "salary_display": "19000"}) == "19000",
          "empty salary falls through to salary_display")
    check(_salary_value({"salary": " ", "salary_normalized": "18000"}) == "18000",
          "whitespace salary falls through to salary_normalized")
    check(_salary_value({}) == "", "no keys -> empty")

    # validate_candidate must honour the manual 'salary' value over stale OCR keys.
    base = _valid_payload()
    stale = dict(base)
    stale["salary"] = "18500"
    stale["salary_normalized"] = "9999"          # bogus/stale OCR value
    stale["salary_display"] = "9,999"
    errs = rules.validate_candidate(stale)
    check(not errs.get("salary"), "manual salary 18500 accepted over stale OCR 9999")

    stale["salary"] = "12"                        # bare timestamp digit
    errs = rules.validate_candidate(stale)
    check(errs.get("salary"), "manual bare digit salary is rejected")


# ── 3. PIN Code validation ───────────────────────────────────────────────────

def test_pin_code_validation():
    print("\n--- 3. PIN Code exact-6 validation ---")
    ok_cases = [
        ("560038", "560038"), ("560 038", "560038"), ("PIN: 560038", "560038"),
        ("Pincode 560-038", "560038"), ("560038", "560038"),
    ]
    for raw, want in ok_cases:
        val, err = rules.normalize_pin_code(raw)
        check(val == want and not err,
              f"normalize_pin_code({raw!r}) = {want} (got {val!r}, {err!r})")

    bad_cases = ["56003", "5600389", "012345", "0", "56003", "1234567", 12345, 1234567]
    for raw in bad_cases:
        val, err = rules.normalize_pin_code(raw)
        check(val == "" and err is not None,
              f"normalize_pin_code({raw!r}) rejected (got {val!r}, {err!r})")

    # No digits at all is treated as blank (optional field), never an error.
    for raw in ["abc", "PIN", "--", "  ", ""]:
        val, err = rules.normalize_pin_code(raw)
        check(val == "" and err is None,
              f"normalize_pin_code({raw!r}) optional/blank (got {val!r}, {err!r})")

    val, err = rules.normalize_pin_code(None)
    check(val == "" and err is None, "None pin is optional (no error)")
    val, err = rules.normalize_pin_code(560038)
    check(val == "560038" and not err, "int pin 560038 accepted")


# ── 4. Aadhaar normalization ─────────────────────────────────────────────────

def test_aadhaar_normalization():
    print("\n--- 4. Aadhaar 12-digit normalization ---")
    ok = [
        ("123456789012", "123456789012"),
        ("1234 5678 9012", "123456789012"),
        ("1234-5678-9012", "123456789012"),
        ("aadhaar 1234 5678 9012", "123456789012"),
    ]
    for raw, want in ok:
        got = rules.normalize_aadhaar(raw)
        check(got == want, f"normalize_aadhaar({raw!r}) = {want} (got {got!r})")
    bad = ["", None, "1234567890", "1234567890123", "1234 5678", "abcd", 1234]
    for raw in bad:
        got = rules.normalize_aadhaar(raw)
        check(got == "", f"normalize_aadhaar({raw!r}) cleared (got {got!r})")
    got = rules.normalize_aadhaar(123456789012)
    check(got == "123456789012", "int 12-digit aadhaar passthrough (got {got!r})")


# 6. Facility / Location regression helpers ──────────────────────────────────

def _valid_payload():
    return {
        "name": "RAHUL SHARMA",
        "mobile": "9876543210",
        "cost_code": "8751",
        "role": "LM - Delivery Executive",
        "facility_type": "Delivery Hub",
        "facility": "KumvempunagarMYNTRAHub_MYS",
        "salary": "18500",
        "dob": "01/01/1992",
        "address": "12 MG Road, Mysuru",
    }


def test_facility_location_resolution():
    print("\n--- 6. Facility / Location regression ---")
    # Master row for the 8751 hub carries the canonical location + ref.
    resolved = master_data.resolve_facility_selection(
        "KumvempunagarMYNTRAHub_MYS", "FAKE/000", "BOGUS-REF")
    check(resolved["location"] == "CJB/KMN",
          f"master location wins over bogus hint (got {resolved['location']!r})")
    check(resolved["facility_ref"].startswith("CJB/KMN"),
          f"master facility_ref wins (got {resolved['facility_ref']!r})")

    # Wrong-cost-code hub is rejected by validation.
    p = _valid_payload()
    p["cost_code"] = "4421"
    p["facility"] = "KumvempunagarMYNTRAHub_MYS"     # a Myntra/8751 hub
    errs = rules.validate_candidate(p)
    check(errs.get("facility"), "8751 hub rejected for 4421 cost code")

    # Wrong-prefix role rejected for a cost code.
    p = _valid_payload()
    p["role"] = "FM - Sorter"                         # FM role, 8751 is LM
    errs = rules.validate_candidate(p)
    check(errs.get("role"), "FM role rejected for LM cost code 8751")


# ── 5 + 7. End-to-end confirm (manual override + persistence) ────────────────

def test_confirm_manual_override_persistence():
    print("\n--- 5+7. End-to-end confirm: manual values persist, master wins ---")
    fresh_db()
    client = TestClient(app)

    payload = _valid_payload()
    payload["salary"] = "18500"                        # manual review value
    payload["salary_display"] = "18500"
    payload["salary_normalized"] = "99999"             # stale OCR value
    payload["aadhaar_number"] = "1234 5678 9011"       # manual full display/edit
    payload["pin_code"] = "sector 560038"              # free text with digits
    payload["location_code"] = "FAKE/000000"           # bogus client hint
    payload["facility_ref"] = "BOGUS-REF"

    r = client.post("/api/confirm-candidate", json=payload)
    check(r.status_code == 200, f"confirm-candidate 200 (got {r.status_code}, {r.text[:200]})")
    cid = r.json().get("id")
    check(cid is not None, "candidate id returned")

    c = db.get_candidate(cid)
    check(c is not None, "candidate read back from DB")
    if c is not None:
        check(int(c["salary"]) == 18500,
              f"manual salary persisted 18500 (got {c['salary']!r})")
        check(c["aadhaar_number"] == "123456789011",
              f"aadhaar persisted as 12 digits (got {c['aadhaar_number']!r})")
        check(c["pin_code"] == "560038",
              f"pin persisted normalized (got {c['pin_code']!r})")
        check(c["location_code"] == "CJB/KMN",
              f"master location wins (got {c['location_code']!r})")
        check(c["facility_ref"].startswith("CJB/KMN"),
              f"master facility_ref wins (got {c['facility_ref']!r})")
        check(c["designation"] == "LM - Delivery Executive",
              f"designation resolved official (got {c['designation']!r})")
        check(c["facility_name"] == "KumvempunagarMYNTRAHub_MYS",
              f"facility name persisted (got {c['facility_name']!r})")

    # Rejected: bare-digit salary through the live API.
    bad = _valid_payload()
    bad["salary"] = "12"
    r2 = client.post("/api/confirm-candidate", json=bad)
    check(r2.status_code == 422 and "salary" in (r2.json().get("errors") or {}),
          "bare-digit salary rejected by API (422 + salary error)")

    # Rejected: wrong-cost-code hub through the live API.
    bad2 = _valid_payload()
    bad2["cost_code"] = "4421"
    bad2["facility"] = "KumvempunagarMYNTRAHub_MYS"
    r3 = client.post("/api/confirm-candidate", json=bad2)
    check(r3.status_code == 422 and (r3.json().get("errors") or {}).get("facility"),
          "wrong-cost-code hub rejected by API")


# ── 8. Blind test pack: 75+ auto-generated scenarios ─────────────────────────

def test_blind_test_pack():
    print("\n--- 8. Blind test pack (auto-generated scenarios) ---")
    count = 0

    # 8a. Salary variants (valid + invalid) — ~46 scenarios.
    salary_valid = [
        "1000", "15000", "15000 ", " 15000", "15,000", "₹15,000", "Rs.15,000",
        "INR 15000", "15k", "15 k", "15K", "salary 15k", "15.5k", "15.5 k",
        "1.5k", "15000.00", "18000", "18,000", "salary: 18000", "18000 /-",
    ]
    for raw in salary_valid:
        val, err = rules.normalize_salary(raw)
        count += 1
        check(err is None and val and val >= 1000,
              f"[{count:02d}] salary {raw!r} -> {val} (err={err!r})")
    salary_invalid = [
        "", None, "   ", "15", "0", "00", "9", "975", "999.99",
        "10:30", "10:30 am", "12:45:22", "-18000", "-15k", "Rs -15 k",
        "salary -18k", "abc", "one twenty", "s", "5 5", "0 0", True, False,
        999, "₹999", "Rs 0",
    ]
    for raw in salary_invalid:
        _, err = rules.normalize_salary(raw)
        count += 1
        check(err is not None, f"[{count:02d}] invalid salary {raw!r} rejected (err={err!r})")

    # 8b. PIN variants — ~20 scenarios.
    pin_ok = [f"{i}00000" for i in range(1, 7)]
    for raw in pin_ok:
        val, err = rules.normalize_pin_code(raw)
        count += 1
        check(val == raw and not err, f"[{count:02d}] pin {raw!r} accepted")
    pin_bad = ["56003", "5600389", "0123450", "0", "1", "12345x6789",
            "56003812", 56003, 56003890, "000000", "9999999"]
    for raw in pin_bad:
        _, err = rules.normalize_pin_code(raw)
        count += 1
        check(err is not None, f"[{count:02d}] invalid pin {raw!r} rejected")

    # 8c. Aadhaar variants — ~14 scenarios.
    for prefix in ["", "Aadhaar ", "UID ", "  ", "No: "]:
        raw = prefix + "1234 5678 9012"
        got = rules.normalize_aadhaar(raw)
        count += 1
        check(got == "123456789012", f"[{count:02d}] aadhaar {raw!r} -> 12 digits")
    for raw in ["123", "123456789", "1234567890123", "1234 5678", "xxxx", None, ""]:
        got = rules.normalize_aadhaar(raw)
        count += 1
        check(got == "", f"[{count:02d}] bad aadhaar {raw!r} cleared")
    count += 1
    check(rules.normalize_aadhaar(123456789012) == "123456789012",
          f"[{count:02d}] int aadhaar normalized")

    # 8d. Candidate validation matrix over a valid base — ~16 scenarios.
    base = _valid_payload()
    variants = [
        ("valid base", dict(base), [], ["name", "mobile", "salary", "role", "facility"]),
        ("empty name", {**base, "name": ""}, ["name"], []),
        ("name MALE", {**base, "name": "MALE"}, ["name"], []),
        ("bad mobile", {**base, "mobile": "12345"}, ["mobile"], []),
        ("bad mobile letters", {**base, "mobile": "9876abc210"}, ["mobile"], []),
        ("no cost code", {**base, "cost_code": ""}, ["cost_code"], []),
        ("bogus cost code", {**base, "cost_code": "9999"}, ["cost_code"], []),
        ("empty role", {**base, "role": ""}, ["role"], []),
        ("wrong prefix role", {**base, "role": "FM - Sorter"}, ["role"], []),
        ("non-hub facility", {**base, "facility": "SomeRandomHub_X"}, ["facility"], []),
        ("empty salary", {**base, "salary": ""}, ["salary"], []),
        ("salary too small", {**base, "salary": "750"}, ["salary"], []),
        ("salary timestamp", {**base, "salary": "10:30 am"}, ["salary"], []),
        ("salary via display key", {**base, "salary": "", "salary_display": "19000"}, [], ["salary"]),
        ("salary via normalized key", {**base, "salary": "", "salary_normalized": "20000"}, [], ["salary"]),
    ]
    for label, payload, must_err, must_not_err in variants:
        errs = rules.validate_candidate(payload)
        count += 1
        ok = all(errs.get(f) for f in must_err) and all(not errs.get(f) for f in must_not_err)
        check(ok, f"[{count:02d}] validation: {label} (errs={list(errs.keys())})")

    print(f"  scenarios: {count}")


if __name__ == "__main__":
    test_salary_normalization()
    test_salary_canonical_key()
    test_pin_code_validation()
    test_aadhaar_normalization()
    test_facility_location_resolution()
    test_confirm_manual_override_persistence()
    test_blind_test_pack()

    print("\n" + "=" * 40)
    print(f"TOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
    if FAIL:
        print("SOME TESTS FAILED")
        sys.exit(1)
    print("ALL TESTS PASSED")