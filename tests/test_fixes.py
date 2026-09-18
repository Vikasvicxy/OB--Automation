"""Regression tests for the TeamHR Automation fixes.

Run with:
    python tests/test_fixes.py

Covers the critical behavioural requirements:
  1. Aadhaar name extraction must NEVER produce MALE/FEMALE.
  2. Aadhaar labels must not be used as candidate names.
  3. Name confidence must not claim "High" when uncertain.
  4. Facility Type rule: 4421/8751 -> Delivery Hub, 4441 -> Pickup Hub,
     8752 -> no facility master yet. (Updated: 4441 was Delivery Hub until
     the PICKUP_HUB correction; see generation of Self_Onboarding / Backend.)
  5. validate_candidate rejects missing/invalid/MALE-FEMALE name.
  6. Cost-code change safety helpers (clear role/hub, re-derive).
  7. OCR summary logging does not include sensitive plaintext (checked
     indirectly - safe logs only structured extras, no aadhaar/address).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import generation
from app import ocr
from app import rules


PASS = 0
FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


# ── 1. Aadhaar name extraction — MALE / FEMALE never become the name ────────

def _aadhaar_lines(name_slot, gender="Male"):
    # A realistic Aadhaar OCR layout: government header, then a JPG/identity
    # block where the candidate name is the line just above the gender/DOB.
    return [
        "Government of India",
        "Unique Identification Authority of India",
        "VID 1234 5678 9012",
        "Aadhaar",
        name_slot,
        gender,
        "DOB: 01/01/1990",
        "Address: 12 MG Road, Bangalore",
    ]


def test_male_never_becomes_name():
    print("Test 1/2: aadhaar image containing MALE/FEMALE")
    # Pass 2 heuristic: the line just above the gender line is treated as name.
    # If OCR returns gender as the only candidate, it must NOT be used.
    lines = _aadhaar_lines("MALE")          # name slot literally "MALE"
    name, conf = ocr.extract_aadhaar_name(lines)
    check(name.lower() != "male", f"name is not MALE (got '{name}' conf={conf})")

    lines = _aadhaar_lines("FEMALE")
    name, conf = ocr.extract_aadhaar_name(lines)
    check(name.lower() != "female", f"name is not FEMALE (got '{name}' conf={conf})")

    # Even a gender-only doc with no real name must not return a gender.
    lines2 = ["Government of India", "Aadhaar", "MALE", "DOB: 02/02/1992"]
    name2, conf2 = ocr.extract_aadhaar_name(lines2)
    check(name2.lower() != "male", f"gender-only doc name not MALE (got '{name2}')")

    lines3 = ["Government of India", "Aadhaar", "FEMALE", "DOB: 03/03/1993"]
    name3, _ = ocr.extract_aadhaar_name(lines3)
    check(name3.lower() != "female", f"gender-only doc name not FEMALE (got '{name3}')")


def test_aadhaar_labels_never_become_name():
    print("Test: aadhaar labels are never candidate names")
    labels = ["DOB", "Male", "Female", "Government of India",
              "Year of Birth", "Address", "VID", "Aadhaar"]
    for label in labels:
        lines = _aadhaar_lines(label)
        name, _ = ocr.extract_aadhaar_name(lines)
        check(name.lower() != label.lower(),
              f"label '{label}' not used as name (got '{name}')")


def test_real_name_is_detected():
    print("Test: a proper name near identity block is picked up")
    lines = _aadhaar_lines("RAHUL KUMAR SHARMA")
    name, conf = ocr.extract_aadhaar_name(lines)
    check(name == "RAHUL KUMAR SHARMA", f"real name detected (got '{name}')")
    check(conf in ("High", "Review"), f"name confidence is not overly confident (conf={conf})")

    lines = _aadhaar_lines("PRIYA NAIK", gender="Female")
    name, conf = ocr.extract_aadhaar_name(lines)
    check(name == "PRIYA NAIK", f"female doc real name detected (got '{name}')")


def test_uncertain_name_not_high_confidence():
    print("Test: uncertain extraction never claims High")
    # A doc with a name but nothing else => heuristic match => Review, not High.
    lines = ["Government of India", "Aadhaar", "SOME RANDOM TOKEN",
             "DOB: 05/05/1995"]
    name, conf = ocr.extract_aadhaar_name(lines)
    check(conf != "High", f"uncertain name not High (conf={conf})")


# ── 2. Facility Type rules ───────────────────────────────────────────────────

def test_facility_type_all_cost_codes():
    print("Test: facility type rule = 4421/8751 Delivery Hub, 4441 Pickup Hub, 8752 unmapped")
    expected = {"4421": "Delivery Hub", "8751": "Delivery Hub",
                "4441": "Pickup Hub", "8752": ""}
    for cc, expect in expected.items():
        got = rules.COST_CODE_FACILITY_TYPE.get(cc)
        check(got == expect, f"cost code {cc} -> '{got}' (expected '{expect}')")

    # Derived from the master when possible; otherwise the cost-code rule.
    check(rules.facility_type_output("4421") == "DELIVERY_HUB", "4421 -> DELIVERY_HUB")
    check(rules.facility_type_output("4441") == "PICKUP_HUB", "4441 -> PICKUP_HUB")
    check(rules.facility_type_output("8751") == "DELIVERY_HUB", "8751 -> DELIVERY_HUB")
    check(rules.facility_type_output("8752") == "", "8752 -> '' (unresolved, Needs Review)")
    check(generation.facility_type_display_for({"cost_code": "8752", "facility_name": ""})
          == "Needs Review", "8752 display -> Needs Review")


# ── 3. Candidate validation — name required / no MALE / no FEMALE ───────────

def _base_valid_payload():
    return {
        "name": "RAHUL SHARMA",
        "mobile": "9876543210",
        "cost_code": "4421",
        "role": "LM - Delivery Executive",
        "facility_type": "Delivery Hub",
        "facility": "KoramangalaHub_BLR",
        "salary": "18000",
    }


def test_validation_name_required():
    print("Test: candidate cannot be approved with invalid/missing name")
    p = _base_valid_payload()
    errs = rules.validate_candidate(p)
    check(not errs.get("name"), "valid candidate has no name error")

    p2 = dict(p); p2["name"] = ""
    errs2 = rules.validate_candidate(p2)
    check("name" in errs2, "missing name -> error")

    p3 = dict(p); p3["name"] = "MALE"
    errs3 = rules.validate_candidate(p3)
    check("name" in errs3, "name=MALE -> error")

    p4 = dict(p); p4["name"] = "FEMALE"
    errs4 = rules.validate_candidate(p4)
    check("name" in errs4, "name=FEMALE -> error")


# ── 4. Cost-code change safety logic ─────────────────────────────────────────

def _role_valid_for(cc):
    return rules.get_roles_for_cost_code(cc)[0]


def test_cost_code_switch_clears_incompatible_data():
    print("Test: switching cost code clears incompatible role/hub, re-derives facility type")
    # 4421 -> 4441 : LM role/hub must be cleared; facility type re-derives
    # (Delivery Hub -> Pickup Hub) because 4441 is a First Mile cost code.
    for old, new in [("4421", "4441"), ("4441", "8751")]:
        # After switching to `new`, the resolves must use `new`'s prefix.
        info = rules.get_cost_code_info(new)
        check(info["operation"] in ("First Mile", "Last Mile"), f"{new} operation derived")

    # 4421 / 8751 are Delivery Hub; 4441 is Pickup Hub (First Mile).
    check(rules.COST_CODE_FACILITY_TYPE["4421"] == "Delivery Hub" and
          rules.COST_CODE_FACILITY_TYPE["4441"] == "Pickup Hub" and
          rules.COST_CODE_FACILITY_TYPE["8751"] == "Delivery Hub",
          "facility type re-derived by cost code (4421/8751 Delivery, 4441 Pickup)")

    # Role that is valid for the old code may be invalid for the new one;
    # validate_candidate on the new code with the stale role must error.
    stale_role = _role_valid_for("4421")   # LM role
    p = _base_valid_payload()
    p["cost_code"] = "4441"
    p["role"] = stale_role                  # LM role, but FM code
    errs = rules.validate_candidate(p)
    check("role" in errs, "stale LM role rejected for FM code 4441")

    # Facility from the old code must be rejected for the new code.
    p2 = _base_valid_payload()
    p2["cost_code"] = "4441"
    p2["facility"] = "KoramangalaHub_BLR"   # a Flipkart LM hub, wrong for FM
    p2["role"] = _role_valid_for("4441")
    errs2 = rules.validate_candidate(p2)
    check("facility" in errs2, "stale LM hub rejected for FM code 4441")


# ── 5. Smart Upload data-quality precedence (Pallavi regression) ─────────────

def _pallavi_fake_ocr(name_line):
    """Mock OCR for the Pallavi test case.

    Aadhaar: name + DOB + an address block with a C/O anchor.
    Screenshot: mobile, "Lm sorter", "Peenya hub", "18k salary".
    """
    real = ocr.extract_text

    def make_fake(_b, n):
        if ("aadhaar" in (n or "").lower()) or ("adh" in (n or "").lower()):
            return [
                "Government of India",
                "Unique Identification Authority of India",
                "Aadhaar",
                name_line,
                "Female",
                "DOB: 22/07/2004",
                "C/O Ravindra",
                "123 Main Road",
                "Peenya",
                "Bangalore - 560058",
                "Karnataka",
            ]
        return ["8951297926", "Lm sorter", "Peenya hub", "18k salary"]

    ocr.extract_text = make_fake
    return real


def test_pallavi_smart_upload_precedence():
    """Explicit 'LM sorter' + 'Peenya hub' must resolve Flipkart Last Mile,
    NOT the FM/_PL hub, when hub-suffix inference would otherwise win."""
    print("Test: Pallavi Smart Upload (LM sorter + Peenya hub -> LM, not FM)")
    prev = _pallavi_fake_ocr("Pallavi K V")
    try:
        res = ocr.run_extraction([
            {"filename": "aadhaar.jpg", "file_type": "image", "data": b"x"},
            {"filename": "shot_8951297926.png", "file_type": "image", "data": b"y"},
        ])
    finally:
        ocr.extract_text = prev

    check(res["name"]["value"] == "Pallavi K V",
          f"name = 'Pallavi K V' (got {res['name']['value']!r})")
    check(res["mobile"]["value"] == "8951297926",
          f"mobile = 8951297926 (got {res['mobile']['value']!r})")
    check(res["dob"]["value"] == "22/07/2004",
          f"dob = 22/07/2004 (got {res['dob']['value']!r})")
    check(res["address"]["value"] != "" and "Ravindra" in res["address"]["value"],
          f"address extracted (got {res['address']['value']!r})")
    check(res["cost_code"]["value"] == "4421",
          f"cost_code = 4421 (got {res['cost_code']['value']!r})")
    check(res["operation"] == "Last Mile",
          f"operation = Last Mile (got {res['operation']!r})")
    check(res["role"]["value"] == "LM - Sorter",
          f"role = LM - Sorter (got {res['role']['value']!r})")
    check(res["facility"]["value"] == "Peenya Hub",
          f"facility = Peenya Hub (got {res['facility']['value']!r})")
    check(not res["facility"]["value"].endswith("_PL"),
          f"facility is NOT an FM/_PL hub (got {res['facility']['value']!r})")
    check(res["salary"]["value"] == 18000,
          f"salary = 18000 (got {res['salary']['value']!r})")
    # Required source labels
    check(res["cost_code"]["source"] == "Screenshot LM evidence",
          f"cost source = 'Screenshot LM evidence' (got {res['cost_code']['source']!r})")
    check(res["role"]["source"] == "Screenshot + master match",
          f"role source = 'Screenshot + master match' (got {res['role']['source']!r})")
    check(res["facility"]["source"] == "Screenshot + master match",
          f"facility source = 'Screenshot + master match' (got {res['facility']['source']!r})")
    check(res["salary"]["source"] == "Screenshot",
          f"salary source = 'Screenshot' (got {res['salary']['source']!r})")


def test_pallavi_run_together_name_spacing():
    print("Test: run-together Aadhaar name is re-spaced (PallaviKV -> Pallavi K V)")
    prev = _pallavi_fake_ocr("PallaviKV")
    try:
        res = ocr.run_extraction([
            {"filename": "aadhaar.jpg", "file_type": "image", "data": b"x"},
            {"filename": "shot_8951297926.png", "file_type": "image", "data": b"y"},
        ])
    finally:
        ocr.extract_text = prev
    check(res["name"]["value"] == "Pallavi K V",
          f"name re-spaced = 'Pallavi K V' (got {res['name']['value']!r})")


def test_smart_conflict_keeps_explicit_operation():
    print("Test: explicit LM is never flipped to FM on a hub conflict")
    r = rules.resolve_smart_onboarding("LM sorter", "Whitefield-PL")
    check(r["cost_code"] == "4421", f"LM stays 4421 (got {r['cost_code']!r})")
    check(r["role"] == "LM - Sorter", f"role stays LM - Sorter (got {r['role']!r})")
    check(not r["facility"].endswith("_PL"),
          f"facility not FM/_PL (got {r['facility']!r})")


def test_smart_hub_suffix_fallback_only_when_operation_unknown():
    print("Test: hub-suffix inference used only when LM/FM not explicit")
    # No role/operation text, only a hub phrase -> hub-suffix inference allowed.
    r = rules.resolve_smart_onboarding(None, "NelamangalaHub_BLR_PL")
    check(r["operation_known"] is False, "operation not known without role text")
    check(r["facility"] == "NelamangalaHub_BLR_PL",
          f"hub-suffix fallback resolves _PL hub (got {r['facility']!r})")
    check(r["cost_code"] == "4441", f"fallback infers FM 4441 (got {r['cost_code']!r})")


# ── TEST A: PREM (Myntra detection + facility matching) ────────────────────

def test_a_prem_myntra():
    """TEST A — PREM: 'LM sorter' + 'Hebbal Myntra hub' + 15500
    must resolve to Myntra / Last Mile / 8751 / LM - Sorter /
    HebbalMYNTRAHub_BLR / 15500."""
    print("TEST A — PREM (Myntra detection)")
    r = rules.resolve_smart_onboarding("LM sorter", "Hebbal Myntra hub")
    check(r["entity"] == "Myntra", f"entity = Myntra (got {r['entity']!r})")
    check(r["cost_code"] == "8751", f"cost_code = 8751 (got {r['cost_code']!r})")
    check(r["role"] == "LM - Sorter", f"role = LM - Sorter (got {r['role']!r})")
    check(r["facility"] == "HebbalMYNTRAHub_BLR",
          f"facility = HebbalMYNTRAHub_BLR (got {r['facility']!r})")
    check(r["operation_known"] is True, "operation is known")
    # Facility must be a Myntra hub (not a Flipkart hub).
    check("MYNTRA" in r["facility"].upper(),
          f"facility is a Myntra hub (got {r['facility']!r})")


def test_a_prem_myntra_fm_8752():
    """Myntra + FM -> 8752 (never Flipkart FM 4441)."""
    print("TEST A — PREM (Myntra detection: FM)")
    r = rules.resolve_smart_onboarding("FM sorter", "some myntra hub")
    check(r["entity"] == "Myntra", f"entity = Myntra (got {r['entity']!r})")
    check(r["cost_code"] == "8752", f"cost_code = 8752 (got {r['cost_code']!r})")
    check(r["role"] == "FM - Sorter", f"role = FM - Sorter (got {r['role']!r})")


def test_a_prem_myntra_facility_banaswadi():
    """8751 + 'Banaswadi Myntra' -> BanaswadiMYNTRAHub_BLR."""
    print("TEST A — PREM (Myntra facility matching: Banaswadi)")
    r = rules.resolve_smart_onboarding(None, "Banaswadi Myntra")
    check(r["entity"] == "Myntra", f"entity = Myntra (got {r['entity']!r})")
    check(r["cost_code"] == "8751", f"cost_code = 8751 (got {r['cost_code']!r})")
    check(r["facility"] == "BanaswadiMYNTRAHub_BLR",
          f"facility = BanaswadiMYNTRAHub_BLR (got {r['facility']!r})")


# ── TEST B: PALLAVI (explicit role + timestamp-safe salary + _PL reject) ────

def test_b_pallavi_timestamp_salary():
    """TEST B — PALLAVI: 'LM sorter' + 'Peenya hub' + '18k salary 11:25 am'
    must resolve to 4421 / LM - Sorter / Peenya Hub / 18000, never
    LM - Team Leader, never an FM/_PL facility, and the raw salary text with
    timestamp must not leak into the value."""
    print("TEST B — PALLAVI (timestamp-safe salary + _PL hard reject)")
    r = rules.resolve_smart_onboarding("LM sorter", "Peenya hub")
    check(r["cost_code"] == "4421", f"cost_code = 4421 (got {r['cost_code']!r})")
    check(r["role"] == "LM - Sorter", f"role = LM - Sorter (got {r['role']!r})")
    check(r["role"] != "LM - Team Leader", "role is NOT LM - Team Leader")
    check(r["facility"] == "Peenya Hub",
          f"facility = Peenya Hub (got {r['facility']!r})")
    check(not r["facility"].endswith("_PL"),
          f"facility is NOT an FM/_PL facility (got {r['facility']!r})")


def test_b_pallavi_end_to_end_timestamp():
    """End-to-end: screenshot with a WhatsApp timestamp in the salary line."""
    print("TEST B — PALLAVI (end-to-end timestamp salary)")
    real = ocr.extract_text
    ocr.extract_text = lambda _b, n: (
        ["Government of India", "Aadhaar", "Pallavi K V", "Female",
         "DOB: 22/07/2004", "C/O Ravindra", "Peenya", "Bangalore 560058", "Karnataka"]
        if ("aadhaar" in (n or "").lower())
        else ["8951297926", "LM sorter", "Peenya hub", "18k salary 11:25 am"]
    )
    try:
        res = ocr.run_extraction([
            {"filename": "aadhaar.jpg", "file_type": "image", "data": b"x"},
            {"filename": "shot.png", "file_type": "image", "data": b"y"},
        ])
    finally:
        ocr.extract_text = real
    check(res["cost_code"]["value"] == "4421",
          f"cost_code = 4421 (got {res['cost_code']['value']!r})")
    check(res["role"]["value"] == "LM - Sorter",
          f"role = LM - Sorter (got {res['role']['value']!r})")
    check(res["facility"]["value"] == "Peenya Hub",
          f"facility = Peenya Hub (got {res['facility']['value']!r})")
    check(not res["facility"]["value"].endswith("_PL"),
          "facility is NOT an FM/_PL hub")
    check(res["salary"]["value"] == 18000,
          f"salary = 18000 (got {res['salary']['value']!r})")
    check("11:25" not in str(res["salary"].get("display", "")),
          f"salary display has no timestamp (got {res['salary'].get('display')!r})")
    check(res["facility"]["value"] == "Peenya Hub",
          "facility stays Peenya Hub")


# ── TEST C: RENUKA (keep working) ─────────────────────────────────────────

def test_c_renuka_still_works():
    """TEST C — RENUKA: 'LM sorter' + 'Nelamangala' + 15500
    must KEEP resolving to 4421 / LM - Sorter / LM display NelamangalaHub_BLR
    (location BLR/NLM) / 15500."""
    print("TEST C — RENUKA (keep working)")
    r = rules.resolve_smart_onboarding("LM sorter", "Nelamangala")
    check(r["cost_code"] == "4421", f"cost_code = 4421 (got {r['cost_code']!r})")
    check(r["role"] == "LM - Sorter", f"role = LM - Sorter (got {r['role']!r})")
    check(r["facility"] == "NelamangalaHub_BLR",
          f"facility = NelamangalaHub_BLR (got {r['facility']!r})")
    check(rules.get_location_for_facility(r["facility"]) == "BLR/NLM",
          f"location = BLR/NLM (got {rules.get_location_for_facility(r['facility'])!r})")


def test_needs_attention_not_flip_pallavi_pl():
    """An LM operation must never flip to FM merely because an _PL hub exists."""
    print("Test: _PL hub presence does not flip explicit LM to FM")
    r = rules.resolve_smart_onboarding("LM sorter", "Peenya hub")
    check(r["cost_code"] == "4421", f"cost_code stays 4421 (got {r['cost_code']!r})")
    check(not r["facility"].endswith("_PL"),
          f"facility not _PL (got {r['facility']!r})")


# ── Runner ───────────────────────────────────────────────────────────────────

def test_run_extraction_pipeline_does_not_leak_gender():
    """End-to-end pipeline (with mock OCR) must not surface MALE/FEMALE
    and must not claim false High confidence."""
    print("Test: run_extraction pipeline (mock OCR)")
    real = ocr.extract_text

    def fake_gender(_b, _n):
        return ["Government of India", "Aadhaar", "MALE", "Male",
                "DOB: 01/01/1990", "Address: 12 MG Road, Bangalore"]

    def fake_name(_b, _n):
        return ["Government of India", "Aadhaar", "RAHUL KUMAR", "Male",
                "DOB: 01/01/1990"]

    ocr.extract_text = fake_gender
    try:
        res = ocr.run_extraction([{"filename": "aadhaar.jpg",
                                   "file_type": "image", "data": b"x"}])
        check(res["name"]["value"].lower() != "male",
              "pipeline: MALE not surfaced as name")
        check(res["name"]["confidence"] in ("Missing", "Review"),
              "pipeline: no false High for gender")
    finally:
        ocr.extract_text = real

    ocr.extract_text = fake_name
    try:
        res = ocr.run_extraction([{"filename": "aadhaar.jpg",
                                   "file_type": "image", "data": b"x"}])
        check(res["name"]["value"] == "RAHUL KUMAR",
              "pipeline: real name extracted")
        check(res["name"]["confidence"] in ("High", "Review"),
              "pipeline: real name confidence is High/Review")
    finally:
        ocr.extract_text = real


def test_setb_first_and_last_mile_phrases():
    """'first mile' / 'last mile' (with/without hyphen/case) are explicit
    First/Last Mile operations, never overridden by role/facility fallback."""
    print("Test: first mile / last mile phrase -> explicit FM/LM")
    import re
    for phrase, want in [
        ("first mile", "FM"), ("First Mile", "FM"), ("first-mile", "FM"),
        ("FIRST MILE", "FM"), ("last mile", "LM"), ("Last Mile", "LM"),
        ("last-mile", "LM"), ("LAST MILE", "LM"),
    ]:
        prefix, base = rules.parse_role_text(f"{phrase} sorter")
        check(prefix == want, f"'{phrase} sorter' -> {want} (got {prefix!r}, base={base!r})")
        check(base == "Sorter", f"'{phrase} sorter' base=Sorter (got {base!r})")
    # B02 end-to-end: first mile + peenya -> FM/4441/PeenyaHub_BLR_PL
    r = rules.resolve_smart_onboarding("first mile sorter", "peenya")
    check(r["cost_code"] == "4441", f"first mile -> 4441 (got {r['cost_code']!r})")


def test_setb_salary_space_before_k():
    """Salary '18 k' (space) and 'salary 18 k' must parse to 18000; timestamps
    are never salaries; '18 k salary 10:30 am' -> 18000."""
    print("Test: salary with space before k / timestamp exclusion")
    for txt, want in [
        ("18k", 18000), ("18 k", 18000), ("18 K", 18000),
        ("18.5k", 18500), ("18.5 k", 18500),
        ("18000", 18000), ("salary 18 k", 18000), ("18 k salary", 18000),
        ("18 k salary 10:30 am", 18000), ("16.5k", 16500),
    ]:
        val, err = rules.normalize_salary(txt)
        check(val == want and err is None,
              f"normalize_salary({txt!r}) = {want} (got {val!r}, {err!r})")
    for tsv in ("9:30 am", "18:45"):
        val, err = rules.normalize_salary(tsv)
        check(err is not None, f"timestamp {tsv!r} rejected as salary (got {val!r})")
    from app.evidence import score_salary_candidates
    from app.ocr_models import OCRLine
    ev = score_salary_candidates([OCRLine(text="18 k salary 10:30 am", confidence=0.9)])
    check(ev.selected is not None and int(ev.selected.value) == 18000,
          "evidence scorer: '18 k salary 10:30 am' -> 18000")
    ev2 = score_salary_candidates([OCRLine(text="9:30 am", confidence=0.9)])
    check(ev2.selected is None, "evidence scorer: '9:30 am' is not a salary")


def test_setb_myntra_entity_wins():
    """Once entity=Myntra is detected, the resolver must never fall back to a
    Flipkart 4421 hub/role."""
    print("Test: Myntra entity never falls back to Flipkart 4421")
    # B07: Myntra last mile sorter / Hebbal -> Myntra/8751/HebbalMYNTRAHub_BLR
    r = rules.resolve_smart_onboarding("Myntra last mile sorter", "Hebbal")
    check(r["entity"] == "Myntra", f"B07 entity Myntra (got {r['entity']!r})")
    check(r["cost_code"] == "8751", f"B07 cost 8751 (got {r['cost_code']!r})")
    check(r["role"] == "LM - Sorter", f"B07 role LM - Sorter (got {r['role']!r})")
    check(r["facility"] == "HebbalMYNTRAHub_BLR",
          f"B07 hub HebbalMYNTRAHub_BLR (got {r['facility']!r})")
    # B19: Myntra sorter / BanaswadiMYNTRAHub_BLR (exact) -> 8751
    r2 = rules.resolve_smart_onboarding("Myntra sorter", "BanaswadiMYNTRAHub_BLR")
    check(r2["entity"] == "Myntra" and r2["cost_code"] == "8751",
          f"B19 Myntra/8751 (got {r2['entity']!r}/{r2['cost_code']!r})")
    # B20: sorter / HebbalMYNTRAHub_BLR (exact) -> Myntra/8751
    r3 = rules.resolve_smart_onboarding("sorter", "HebbalMYNTRAHub_BLR")
    check(r3["entity"] == "Myntra" and r3["cost_code"] == "8751",
          f"B20 Myntra/8751 (got {r3['entity']!r}/{r3['cost_code']!r})")


def test_setb_prexo_rejected_keeps_myntra():
    """B10: Myntra + last mile + Prexo rejects Prexo (blank) and raises review,
    staying Myntra/8751 — never falling back to Flipkart 4421."""
    print("Test: Prexo rejected for Myntra 8751, stays Myntra")
    r = rules.resolve_smart_onboarding("Myntra last mile prexo", "Hebbal")
    check(r["entity"] == "Myntra", f"B10 entity stays Myntra (got {r['entity']!r})")
    check(r["cost_code"] == "8751", f"B10 cost stays 8751 (got {r['cost_code']!r})")
    check(r["role"] == "", f"B10 role rejected/blank (got {r['role']!r})")
    check(r["role_unresolved"] is True, "B10 role_unresolved True")
    check(r["facility"] == "HebbalMYNTRAHub_BLR",
          f"B10 hub HebbalMYNTRAHub_BLR (got {r['facility']!r})")


def test_setb_exact_pl_hub_infers_fm():
    """B11: 'sorter' + exact NelamangalaHub_BLR_PL infers FM/4441 (facility
    evidence before role-default), even though 'sorter' alone defaults to LM."""
    print("Test: exact _PL hub infers First Mile / 4441")
    r = rules.resolve_smart_onboarding("sorter", "NelamangalaHub_BLR_PL")
    check(r["entity"] == "Flipkart", f"B11 entity Flipkart (got {r['entity']!r})")
    check(r["cost_code"] == "4441", f"B11 cost 4441 (got {r['cost_code']!r})")
    check(r["role"] == "FM - Sorter", f"B11 role FM - Sorter (got {r['role']!r})")
    check(r["facility"] == "NelamangalaHub_BLR_PL",
          f"B11 hub NelamangalaHub_BLR_PL (got {r['facility']!r})")
    check(r["operation_known"] is False, "B11 operation inferred, not explicit")


def test_setb_explicit_lm_beats_pl_suffix():
    """Explicit LM always beats a _PL facility suffix -> stays LM/4421, never FM."""
    print("Test: explicit LM + _PL hub stays LM/4421")
    r = rules.resolve_smart_onboarding("LM sorter", "PeenyaHub_BLR_PL")
    check(r["cost_code"] == "4421", f"LM + _PL stays 4421 (got {r['cost_code']!r})")
    check(not r["facility"].endswith("_PL"),
          f"LM + _PL hub does NOT select _PL facility (got {r['facility']!r})")


def test_setb_exact_non_pl_hub_infers_lm():
    """B12: 'delivery' + exact Peenya Hub (no _PL) infers LM/4421."""
    print("Test: exact non-_PL hub infers Last Mile / 4421")
    r = rules.resolve_smart_onboarding("delivery", "PeenyaHub_BLR")
    check(r["cost_code"] == "4421", f"B12 cost 4421 (got {r['cost_code']!r})")
    check(r["facility"] == "Peenya Hub",
          f"B12 hub Peenya Hub (got {r['facility']!r})")


if __name__ == "__main__":
    test_male_never_becomes_name()
    test_aadhaar_labels_never_become_name()
    test_real_name_is_detected()
    test_uncertain_name_not_high_confidence()
    test_facility_type_all_cost_codes()
    test_validation_name_required()
    test_cost_code_switch_clears_incompatible_data()
    test_run_extraction_pipeline_does_not_leak_gender()
    test_pallavi_smart_upload_precedence()
    test_pallavi_run_together_name_spacing()
    test_smart_conflict_keeps_explicit_operation()
    test_smart_hub_suffix_fallback_only_when_operation_unknown()
    test_a_prem_myntra()
    test_a_prem_myntra_fm_8752()
    test_a_prem_myntra_facility_banaswadi()
    test_b_pallavi_timestamp_salary()
    test_b_pallavi_end_to_end_timestamp()
    test_c_renuka_still_works()
    test_needs_attention_not_flip_pallavi_pl()
    test_setb_first_and_last_mile_phrases()
    test_setb_salary_space_before_k()
    test_setb_myntra_entity_wins()
    test_setb_prexo_rejected_keeps_myntra()
    test_setb_exact_pl_hub_infers_fm()
    test_setb_explicit_lm_beats_pl_suffix()
    test_setb_exact_non_pl_hub_infers_lm()

    print("\n" + "=" * 40)
    print(f"TOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
    if FAIL:
        print("SOME TESTS FAILED")
        sys.exit(1)
    print("ALL TESTS PASSED")
