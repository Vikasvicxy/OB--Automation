"""Unit tests for the layout-aware Aadhaar parser and OCR models.

Run with:
    python tests/test_layout_parser.py

Tests the new layout parsing architecture using synthetic OCRLine objects
to verify spatial extraction without requiring real images.

Covers:
  - OCRLine spatial relationships
  - Name extraction via layout (label, spatial, uppercase, best-effort)
  - "To" false-name case (back side of Aadhaar)
  - "/INFORMATION" false-name case
  - Address extraction with reading order
  - Aadhaar number extraction
  - DOB extraction
  - Gender extraction
  - End-to-end Aadhaar parsing
  - Flat-text parser (parse_aadhaar_from_text)
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ocr_models import OCRLine, FieldEvidence
from app.document_parsers.aadhaar import (
    AadhaarLayoutParser, parse_aadhaar, parse_aadhaar_from_text,
    _is_valid_name_token, _looks_like_name, _repair_run_together_name,
)

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


# ── Helper: create synthetic OCRLine with simulated bounding boxes ────────


def _line(text, y, x1=10, x2=400, conf=0.95, page=0, order=0):
    """Create an OCRLine at a given vertical position."""
    return OCRLine(
        text=text, confidence=conf,
        x1=x1, y1=y, x2=x2, y2=y + 25,
        page=page, order=order,
    )


# ── 1. OCRLine spatial relationships ────────────────────────────────────────

def test_ocrline_spatial():
    print("Test: OCRLine spatial relationships")
    a = _line("Name", 100)
    b = _line("Pallavi K V", 130)
    c = _line("DOB: 22/07/2004", 160)

    check(a.is_above(b), "a is above b")
    check(b.is_above(c), "b is above c")
    check(a.is_above(c), "a is above c (transitive)")
    check(not b.is_above(a), "b is NOT above a")
    check(a.vertical_gap(b) > 0, "positive gap a->b")
    check(b.vertical_gap(c) > 0, "positive gap b->c")

    d = _line("Same Level", 105)
    check(a.is_same_line(d, tolerance=15), "a and d are on same line (tolerance=15)")
    check(not a.is_same_line(c), "a and c are NOT on same line")


# ── 2. Name extraction: explicit "Name:" label ──────────────────────────────

def test_name_from_label():
    print("Test: name from explicit 'Name:' label")
    lines = [
        _line("Government of India", 20),
        _line("Unique Identification Authority of India", 50),
        _line("Aadhaar", 80),
        _line("Name", 110),
        _line("Pavan K N", 140),
        _line("DOB: 31/07/2006", 170),
        _line("Male", 200),
    ]
    result = parse_aadhaar(lines)
    check(result.name.value == "Pavan K N", f"name = 'Pavan K N' (got '{result.name.value}')")
    check(result.name.confidence == "High", f"confidence = High (got '{result.name.confidence}')")
    check("label" in result.name.reason.lower() or "after" in result.name.reason.lower(),
          f"reason mentions label ({result.name.reason})")


# ── 3. Name extraction: line above DOB (spatial proximity) ──────────────────

def test_name_above_dob():
    print("Test: name via spatial proximity to DOB")
    lines = [
        _line("Government of India", 20),
        _line("Aadhaar", 50),
        _line("Pavan K N", 80),
        _line("DOB: 31/07/2006", 110),
        _line("Male", 140),
    ]
    result = parse_aadhaar(lines)
    check(result.name.value == "Pavan K N", f"name = 'Pavan K N' (got '{result.name.value}')")
    check(result.name.confidence == "Review", f"confidence = Review (got '{result.name.confidence}')")
    check("dob" in result.name.reason.lower() or "above" in result.name.reason.lower(),
          f"reason mentions DOB/spatial ({result.name.reason})")


# ── 4. Name extraction: line above gender ───────────────────────────────────

def test_name_above_gender():
    print("Test: name via spatial proximity to gender")
    lines = [
        _line("Government of India", 20),
        _line("Aadhaar", 50),
        _line("PRIYA NAIK", 80),
        _line("Female", 110),
        _line("DOB: 15/03/1995", 140),
    ]
    result = parse_aadhaar(lines)
    check(result.name.value == "PRIYA NAIK", f"name = 'PRIYA NAIK' (got '{result.name.value}')")
    check("female" in result.name.reason.lower() or "gender" in result.name.reason.lower(),
          f"reason mentions gender ({result.name.reason})")


# ── 5. "To" false-name case (back side of Aadhaar) ─────────────────────────

def test_to_not_as_name():
    print("Test: 'To' is never treated as a name")
    lines = [
        _line("To", 20),
        _line("Pallavi K V", 50),
        _line("C/O Ravindra", 80),
        _line("123 Main Road", 110),
        _line("Peenya", 140),
        _line("Bangalore - 560058", 170),
        _line("Karnataka", 200),
    ]
    result = parse_aadhaar(lines)
    check(result.name.value != "To", f"'To' is not name (got '{result.name.value}')")
    # The parser should not extract 'To' as a name even though it appears first
    check(result.name.value.lower() not in ("to",), f"name is not 'To'")


# ── 6. "/INFORMATION" false-name case ───────────────────────────────────────

def test_information_not_as_name():
    print("Test: '/INFORMATION' is never treated as a name")
    lines = [
        _line("Government of India", 20),
        _line("/INFORMATION", 50),
        _line("Aadhaar", 80),
        _line("RAHUL KUMAR", 110),
        _line("Male", 140),
        _line("DOB: 01/01/1990", 170),
    ]
    result = parse_aadhaar(lines)
    check(result.name.value != "/INFORMATION",
          f"'INFORMATION' is not name (got '{result.name.value}')")
    check("RAHUL" in result.name.value or result.name.value == "RAHUL KUMAR",
          f"real name extracted (got '{result.name.value}')")


# ── 7. Address-only label: "Address:" alone is NOT an address ───────────────

def test_address_label_only():
    print("Test: 'Address:' alone is not treated as a full address")
    lines = [
        _line("Government of India", 20),
        _line("Aadhaar", 50),
        _line("RAHUL KUMAR", 80),
        _line("Male", 110),
        _line("DOB: 01/01/1990", 140),
        _line("Address:", 170),
    ]
    result = parse_aadhaar(lines)
    check(result.address.value == "" or len(result.address.value) < 8,
          f"short address rejected (got '{result.address.value}')")


# ── 8. Multiline Aadhaar address with reading order ────────────────────────

def test_multiline_address():
    print("Test: multiline address extraction with reading order")
    lines = [
        _line("Government of India", 20),
        _line("Unique Identification Authority of India", 40),
        _line("Aadhaar", 60),
        _line("Pallavi K V", 90),
        _line("Female", 120),
        _line("DOB: 22/07/2004", 150),
        _line("Address:", 200),
        _line("C/O Ravindra", 230),
        _line("123 Main Road", 260),
        _line("Peenya", 290),
        _line("Bangalore - 560058", 320),
        _line("Karnataka", 350),
    ]
    result = parse_aadhaar(lines)
    addr = result.address.value
    check(len(addr) > 10, f"address is substantial (got '{addr[:50]}...')")
    check("Ravindra" in addr, f"address contains C/O name")
    check("Main Road" in addr or "123" in addr, f"address contains street")
    check("Karnataka" in addr, f"address contains state")
    check(result.address.confidence == "Review", f"address confidence = Review")


# ── 9. Address with relational anchor (no explicit "Address:" label) ────────

def test_address_from_relational_anchor():
    print("Test: address starts from C/O anchor (no 'Address:' label)")
    lines = [
        _line("Government of India", 20),
        _line("Aadhaar", 50),
        _line("SOME NAME", 80),
        _line("Male", 110),
        _line("DOB: 01/01/1990", 140),
        _line("C/O Suresh Kumar", 200),
        _line("456 Nehru Nagar", 230),
        _line("Bangalore - 560001", 260),
        _line("Karnataka", 290),
    ]
    result = parse_aadhaar(lines)
    addr = result.address.value
    check("Suresh" in addr, f"address from C/O anchor (got '{addr[:50]}')")
    check("Nehru Nagar" in addr, f"address contains locality")


# ── 10. Address stops at Aadhaar number ─────────────────────────────────────

def test_address_stops_at_aadhaar_number():
    print("Test: address stops before Aadhaar number line")
    lines = [
        _line("Address:", 200),
        _line("C/O Ramesh", 230),
        _line("MG Road, Bangalore", 260),
        _line("560001", 290),
        _line("1234 5678 9012", 350),  # Aadhaar number - should terminate
    ]
    result = parse_aadhaar(lines)
    addr = result.address.value
    check("1234" not in addr, f"Aadhaar number not in address")
    check("Ramesh" in addr, f"address contains C/O name")


# ── 11. Aadhaar number extraction ───────────────────────────────────────────

def test_aadhaar_number():
    print("Test: 12-digit Aadhaar number extraction")
    lines = [
        _line("Government of India", 20),
        _line("Aadhaar", 50),
        _line("1234 5678 9012", 80),
        _line("RAHUL KUMAR", 110),
        _line("Male", 140),
    ]
    result = parse_aadhaar(lines)
    check(result.aadhaar_number.value == "123456789012",
          f"aadhaar = 123456789012 (got '{result.aadhaar_number.value}')")
    check(result.aadhaar_number.confidence == "High",
          f"aadhaar confidence = High")


# ── 12. DOB extraction ──────────────────────────────────────────────────────

def test_dob_extraction():
    print("Test: DOB extraction (DD/MM/YYYY)")
    lines = [
        _line("RAHUL KUMAR", 80),
        _line("DOB: 01/01/1990", 110),
        _line("Male", 140),
    ]
    result = parse_aadhaar(lines)
    check(result.dob.value == "01/01/1990", f"dob = 01/01/1990 (got '{result.dob.value}')")
    check(result.dob.confidence == "High", f"dob confidence = High")


# ── 13. Gender extraction ───────────────────────────────────────────────────

def test_gender_extraction():
    print("Test: gender extraction")
    lines_m = [_line("RAHUL", 80), _line("Male", 110)]
    r_m = parse_aadhaar(lines_m)
    check(r_m.gender.value == "Male", f"gender = Male (got '{r_m.gender.value}')")

    lines_f = [_line("PRIYA", 80), _line("Female", 110)]
    r_f = parse_aadhaar(lines_f)
    check(r_f.gender.value == "Female", f"gender = Female (got '{r_f.gender.value}')")


# ── 14. End-to-end Aadhaar parsing ──────────────────────────────────────────

def test_end_to_end_front():
    print("Test: end-to-end Aadhaar front side parsing")
    lines = [
        _line("Government of India", 20),
        _line("Unique Identification Authority of India", 45),
        _line("VID 1234 5678 9012", 70),
        _line("Aadhaar", 95),
        _line("Pallavi K V", 130),
        _line("Female", 160),
        _line("DOB: 22/07/2004", 190),
        _line("Address:", 240),
        _line("C/O Ravindra", 270),
        _line("123 Main Road", 300),
        _line("Peenya", 330),
        _line("Bangalore - 560058", 360),
        _line("Karnataka", 390),
    ]
    result = parse_aadhaar(lines)
    check(result.name.value == "Pallavi K V",
          f"name = 'Pallavi K V' (got '{result.name.value}')")
    check(result.dob.value == "22/07/2004",
          f"dob = 22/07/2004 (got '{result.dob.value}')")
    check(result.gender.value == "Female",
          f"gender = Female (got '{result.gender.value}')")
    check("Ravindra" in result.address.value,
          f"address contains C/O (got '{result.address.value[:60]}')")


def test_end_to_end_back():
    print("Test: end-to-end Aadhaar back side parsing")
    lines = [
        _line("To", 20),
        _line("Pavan K N", 50),
        _line("S/O Narayana", 80),
        _line("Village: Doddabommasandra", 110),
        _line("PO: Doddaballapur", 140),
        _line("District: Bangalore Rural", 170),
        _line("State: Karnataka", 200),
        _line("PIN: 560058", 230),
    ]
    result = parse_aadhaar(lines)
    # 'To' must not be the name
    check(result.name.value != "To", f"'To' not treated as name")
    check("Narayana" in result.address.value or "S/O" in result.address.value,
          f"address has relational anchor (got '{result.address.value[:60]}')")
    check("Karnataka" in result.address.value,
          f"address contains state")


# ── 15. Flat-text parser (backward compat) ──────────────────────────────────

def test_flat_text_parser():
    print("Test: parse_aadhaar_from_text (flat text, no coordinates)")
    lines = [
        "Government of India",
        "Aadhaar",
        "Pallavi K V",
        "Female",
        "DOB: 22/07/2004",
        "Address: C/O Ravindra, 123 Main Road, Peenya, Bangalore - 560058, Karnataka",
    ]
    result = parse_aadhaar_from_text(lines)
    check(result.name.value == "Pallavi K V",
          f"flat-text name = 'Pallavi K V' (got '{result.name.value}')")
    check(result.dob.value == "22/07/2004",
          f"flat-text dob = 22/07/2004 (got '{result.dob.value}')")
    check(result.gender.value == "Female",
          f"flat-text gender = Female (got '{result.gender.value}')")


# ── 16. Name validation helpers ─────────────────────────────────────────────

def test_name_validation():
    print("Test: name validation helpers")
    check(_is_valid_name_token("Pallavi K V"), "'Pallavi K V' is valid name")
    check(not _is_valid_name_token("MALE"), "'MALE' is not valid name")
    check(not _is_valid_name_token("Female"), "'Female' is not valid name")
    check(not _is_valid_name_token("DOB"), "'DOB' is not valid name")
    check(not _is_valid_name_token("Government of India"), "'Government of India' is not valid name")
    check(not _is_valid_name_token("1234 5678 9012"), "Aadhaar number is not valid name")
    check(not _is_valid_name_token("Aadhaar"), "'Aadhaar' is not valid name")
    check(not _is_valid_name_token("VID"), "'VID' is not valid name")
    check(not _is_valid_name_token("To"), "'To' is not valid name")
    check(_looks_like_name("Pallavi K V"), "'Pallavi K V' looks like name")
    check(_looks_like_name("RAHUL KUMAR"), "'RAHUL KUMAR' looks like name")
    check(not _looks_like_name("Male"), "'Male' does not look like name")
    check(not _looks_like_name("DOB: 01/01/1990"), "DOB line does not look like name")


# ── 17. Run-together name repair ───────────────────────────────────────────

def test_name_repair():
    print("Test: run-together name repair")
    check(_repair_run_together_name("PallaviKV") == "Pallavi K V",
          "PallaviKV -> Pallavi K V")
    check(_repair_run_together_name("JohnDoeSharma") == "John Doe Sharma",
          "JohnDoeSharma -> John Doe Sharma")
    check(_repair_run_together_name("RAHUL KUMAR") == "RAHUL KUMAR",
          "RAHUL KUMAR unchanged (uppercase)")
    check(_repair_run_together_name("Pallavi K V") == "Pallavi K V",
          "Pallavi K V unchanged (already spaced)")


# ── 18. Empty / minimal input ───────────────────────────────────────────────

def test_empty_input():
    print("Test: empty input handling")
    result = parse_aadhaar([])
    check(result.name.value == "", "empty -> no name")
    check(result.aadhaar_number.value == "", "empty -> no aadhaar")
    check(result.dob.value == "", "empty -> no dob")
    check(result.address.value == "", "empty -> no address")


# ── 19. FieldEvidence model ─────────────────────────────────────────────────

def test_field_evidence():
    print("Test: FieldEvidence model")
    ev = FieldEvidence(value="Pallavi K V", confidence="High", source="Aadhaar",
                       reason="label test")
    d = ev.to_dict()
    check(d["value"] == "Pallavi K V", "to_dict value")
    check(d["confidence"] == "High", "to_dict confidence")
    check(d["source"] == "Aadhaar", "to_dict source")
    check(d["error"] is None, "to_dict error is None")
    check(not ev.is_missing, "not missing when value present")
    ev_empty = FieldEvidence()
    check(ev_empty.is_missing, "empty is missing")


# ── Runner ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_ocrline_spatial()
    test_name_from_label()
    test_name_above_dob()
    test_name_above_gender()
    test_to_not_as_name()
    test_information_not_as_name()
    test_address_label_only()
    test_multiline_address()
    test_address_from_relational_anchor()
    test_address_stops_at_aadhaar_number()
    test_aadhaar_number()
    test_dob_extraction()
    test_gender_extraction()
    test_end_to_end_front()
    test_end_to_end_back()
    test_flat_text_parser()
    test_name_validation()
    test_name_repair()
    test_empty_input()
    test_field_evidence()

    print("\n" + "=" * 50)
    print(f"TOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
    if FAIL:
        print("SOME TESTS FAILED")
        sys.exit(1)
    print("ALL TESTS PASSED")
