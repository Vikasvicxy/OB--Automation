"""Ramesh real-fixture regression tests.

Run with:
    python tests/test_ramesh_fixture.py

Covers the real recruiter WhatsApp + Aadhaar pair that was mis-reported:
  1. Full run_extraction produces Ramesh/9398969253/01-01-2003/Male/Gangu/502221/18000
     and NEVER the Aadhaar issue date (14/10/2013) as DOB.
  2. DOB flat-text extraction: issue-date labels rejected, DOB label priority,
     earliest-year fallback.
  3. DOB layout-parser extraction: same issue-date rejection.
  4. DOB evidence scoring: issue/enrolment lines rejected, DOB label boosted.
  5. Salary: '18k'/variants -> 18000; timestamps rejected.
  6. Hub text: label lines skipped, 'location: Nelamangala' -> 'Nelamangala'.
  7. Nelamangala F/M ambiguity -> requires user selection (no silent pick).
  8. Address: stops at postal boundary (no Aadhaar number, issue date, mobile).
  9. Father / PIN normalization helpers.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import ocr
from app import rules
from app.evidence import score_dob_candidates, score_address_candidates, FieldResult
from app.ocr_models import OCRLine

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


def _make_line(text, y=0, x1=0, x2=200, confidence=0.9, order=0):
    return OCRLine(
        text=text, confidence=confidence,
        x1=x1, y1=y, x2=x2, y2=y + 20,
        order=order,
    )


RAMESH_AADHAAR_TEXT = [
    "Government of India",
    "Unique Identification Authority of India",
    "RAMESH",
    "Date of Birth : 01/01/2003",
    "Male",
    "S/O: Gangu",
    "Address: H No 12-34, Nelamangala Road",
    "Village: Nelamangala",
    "Taluk: Nelamangala",
    "District: Bangalore Rural",
    "Karnataka",
    "PIN: 502221",
    "8750 3483 9137",
    "14/10/2013",
    "Signature",
]

RAMESH_WHATSAPP = ["Name: Ramesh", "Mobile: 9398969253", "F/M",
                   "location: Nelamangala", "18k salary"]


def _run_ramesh():
    """Drive run_extraction with the Ramesh pair via flat-text mocks."""
    orig_text, orig_ocr = ocr.extract_text, ocr.extract_ocr_lines

    def fake_text(b, name):
        return RAMESH_WHATSAPP if name.endswith(".txt") else RAMESH_AADHAAR_TEXT

    def fake_ocr(b, name):
        raise RuntimeError("no layout data (mock)")

    ocr.extract_text, ocr.extract_ocr_lines = fake_text, fake_ocr
    try:
        return ocr.run_extraction([
            {"filename": "whatsapp.txt", "file_type": "text", "data": b"x"},
            {"filename": "aadhaar.jpg", "file_type": "image", "data": b"x"},
        ])
    finally:
        ocr.extract_text, ocr.extract_ocr_lines = orig_text, orig_ocr


def test_full_ramesh_pipeline():
    print("\n--- 1. Full Ramesh pipeline ---")
    r = _run_ramesh()

    check(r["name"]["value"] == "RAMESH", "name = RAMESH")
    check(r["mobile"]["value"] == "9398969253", "mobile = 9398969253")
    check(r["dob"]["value"] == "01/01/2003",
          f"DOB = 01/01/2003 (got {r['dob']['value']!r})")
    check(r["dob"]["value"] != "14/10/2013",
          "issue date 14/10/2013 NEVER used as DOB")
    check(r["gender"]["value"] == "Male", "gender = Male")
    check(r["father_name"]["value"] == "Gangu", "father = Gangu")
    check(r["pin_code"]["value"] == "502221", "PIN = 502221")
    check(r["salary"]["value"] == 18000, "salary = 18000")
    check(r["aadhaar_number"] == "875034839137", "aadhaar = 875034839137")

    addr = r["address"]["value"]
    check("8750 3483 9137" not in addr,
          "address does not contain Aadhaar number")
    check("14/10/2013" not in addr, "address does not contain issue date")
    check("9398969253" not in addr, "address does not contain mobile")
    check("PIN: 502221" in addr, "address ends at the PIN (postal boundary)")

    # Ambiguity: Nelamangala F/M must require user selection, never auto-pick.
    check(r["facility"]["value"] == "",
          "Nelamangala F/M -> facility left empty (user selection required)")
    check(r["cost_code"]["value"] == "",
          "Nelamangala F/M -> cost code left empty (user selection required)")
    check(r["needs_attention"],
          "Nelamangala F/M -> needs_attention set")


def test_extract_dob():
    print("\n--- 2. DOB flat-text extraction ---")

    # Issue-date line must never win over a DOB-labeled date.
    lines = ["Date of Birth : 01/01/2003", "14/10/2013", "Signature"]
    check(ocr.extract_dob(lines) == "01/01/2003",
          "DOB label wins over bare issue date")

    # No DOB label: earliest reasonable date is selected (birth < issue date).
    lines = ["Name: X", "14/10/2013", "01/01/2003"]
    check(ocr.extract_dob(lines) == "01/01/2003",
          "earliest-year fallback (issue date 2013 vs birth 2003)")

    # Issue-date LABEL line is never selected even if it is the only date.
    lines = ["Issue date 14/10/2013", "date: 01/01/2003", "Signature"]
    got = ocr.extract_dob(lines)
    check(got != "14/10/2013",
          f"issue-date line not selected (got {got!r})")

    # Year of birth fallback.
    lines = ["Year of Birth: 2003"]
    check(ocr.extract_dob(lines) == "2003", "Year of Birth fallback")

    check(ocr.extract_dob([]) is None, "no lines -> None")


def test_layout_dob():
    print("\n--- 3. DOB layout parser ---")
    from app.document_parsers.aadhaar import parse_aadhaar

    # Issue date must not be picked over a DOB-labeled date.
    lines = [
        _make_line("RAMESH", 0),
        _make_line("Date of Birth : 01/01/2003", 40),
        _make_line("14/10/2013", 80),
        _make_line("Signature", 100),
    ]
    res = parse_aadhaar(lines)
    check(res.dob.value == "01/01/2003",
          f"layout DOB = 01/01/2003 (got {res.dob.value!r})")

    # No DOB label: earliest date wins over issue date.
    lines = [
        _make_line("14/10/2013", 0),
        _make_line("01/01/2003", 40),
    ]
    res = parse_aadhaar(lines)
    check(res.dob.value == "01/01/2003",
          f"layout earliest-year fallback (got {res.dob.value!r})")


def _val(res):
    return (res.selected.value if res.selected else "")


def test_evidence_dob():
    print("\n--- 4. DOB evidence scoring ---")
    from app.evidence import score_dob_candidates

    # DOB-labeled date wins over an issue date 10 years later.
    lines = [
        _make_line("Date of Birth : 01/01/2003", 0, confidence=0.9),
        _make_line("Issue Date 14/10/2013", 40, confidence=0.9),
    ]
    res = score_dob_candidates(lines)
    check(isinstance(res, FieldResult), "score_dob_candidates returns FieldResult")
    check(_val(res) == "01/01/2003",
          f"evidence DOB = 01/01/2003 (got {_val(res)!r})")

    # Only an issue-date line -> nothing selected (never surfaced as DOB).
    lines = [_make_line("Enrolment date 14/10/2013", 0)]
    res = score_dob_candidates(lines)
    check(not _val(res), f"issue/enrolment-only lines yield no DOB (got {_val(res)!r})")

    # Indian date form DD-MM-YYYY accepted.
    lines = [_make_line("DOB : 01-01-2003", 0)]
    res = score_dob_candidates(lines)
    check(_val(res) == "01/01/2003",
          f"DD-MM-YYYY under DOB label (got {_val(res)!r})")


def test_salary_18k():
    print("\n--- 5. Salary '18k' variants ---")
    whatsapp_variants = [
        ["18k salary"], ["18 k salary"], ["salary 18k"],
        ["Take home 18k"], ["18k"], ["salary: 18k"],
        ["18,000 /-"], ["18000"],
    ]
    for lines in whatsapp_variants:
        got = ocr._extract_salary_candidates(lines)
        ok = any(val == 18000 for _d, val in got)
        check(ok, f"_extract_salary_candidates({lines!r}) -> 18000 (got {got})")

    # Timestamps are NOT salaries.
    lines = ["9:30 am", "10:30"]
    got = ocr._extract_salary_candidates(lines)
    check(not got, f"timestamp-only lines produce no salary (got {got})")


def test_extract_hub_text():
    print("\n--- 6. Hub text extraction ---")
    check(ocr.extract_hub_text(RAMESH_WHATSAPP) == "Nelamangala",
          f"'location: Nelamangala' -> Nelamangala (got {ocr.extract_hub_text(RAMESH_WHATSAPP)!r})")
    check(ocr.extract_hub_text(["Name: Ramesh", "Mobile: 9398969253"]) is None,
          "'Name:/Mobile:' label lines spawn no hub")
    check(ocr.extract_hub_text(["LM sorter", "Peenya Hub"]) == "Peenya Hub",
          "hub line still detected after role line")
    check(ocr.extract_hub_text(["18k", "F/M"]) is None,
          "'18k' and 'F/M' spawn no hub")


def test_ambiguity_no_silent_pick():
    print("\n--- 7. Nelamangala F/M ambiguity requires selection ---")
    res = rules.resolve_smart_onboarding(None, "Nelamangala")
    check(res["facility"] == "", "facility left empty")
    check(res["cost_code"] == "", "cost code left empty")
    check(any("ambiguous" in a for a in res["needs_attention"]),
          "needs_attention flags ambiguity")

    # An explicit LM operation still resolves cleanly to the LM master.
    res = rules.resolve_smart_onboarding("LM sorter", "Nelamangala")
    check(res["facility"] in rules.get_hubs_for_cost_code("4421"),
          f"explicit LM -> 4421 master hub (got {res['facility']!r} code {res['cost_code']!r})")


def test_address_no_contact_no_aadhaar():
    print("\n--- 8. Address boundary ---")
    addr = ocr.extract_address(RAMESH_AADHAAR_TEXT)
    check("8750 3483 9137" not in addr, "no Aadhaar number in address")
    check("14/10/2013" not in addr, "no issue date in address")
    check("Mobile" not in addr, "no mobile footer in address")
    check("PIN: 502221" in addr, "PIN retained at postal boundary")

    # Evidence scorer too.
    lines = [_make_line(t, i * 30) for i, t in enumerate(RAMESH_AADHAAR_TEXT)]
    res = score_address_candidates(lines)
    addr_val = _val(res)
    check("8750 3483 9137" not in addr_val, "evidence address excludes Aadhaar")
    check("14/10/2013" not in addr_val, "evidence address excludes issue date")


def test_normalize_helpers():
    print("\n--- 9. Normalization helpers ---")
    check(rules.normalize_father_name("S/O: Gangu")[0] == "Gangu",
          "father 'S/O: Gangu' -> Gangu")
    check(rules.normalize_father_name("s/o Gangu")[0] == "Gangu",
          "father 's/o Gangu' -> Gangu")
    check(rules.normalize_father_name("")[0] == "",
          "empty father is optional (no error)")
    check(rules.normalize_pin_code("PIN: 502221")[0] == "502221",
          "pin 'PIN: 502221' -> 502221")
    check(rules.normalize_gender("MALE")[0] == "Male",
          "gender 'MALE' -> Male")
    check(rules.normalize_gender("female")[0] == "Female",
          "gender 'female' -> Female")


if __name__ == "__main__":
    test_full_ramesh_pipeline()
    test_extract_dob()
    test_layout_dob()
    test_evidence_dob()
    test_salary_18k()
    test_extract_hub_text()
    test_ambiguity_no_silent_pick()
    test_address_no_contact_no_aadhaar()
    test_normalize_helpers()

    print("\n" + "=" * 40)
    print(f"TOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
    if FAIL:
        print("SOME TESTS FAILED")
        sys.exit(1)