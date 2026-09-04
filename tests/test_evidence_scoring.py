"""Evidence scoring tests: cases A through I.

Tests the multi-candidate scoring, confidence margin logic, and
business-constraint filtering for the evidence scoring framework.

Run with:
    python tests/test_evidence_scoring.py

Covers:
  A. To rejected, real name selected
  B. Two plausible names close scores -> Needs Review
  C. Strong name with large margin -> High
  D. Address: only -> Missing
  E. Multiline address with PIN/state/district -> High/Review
  F. LM + Peenya -> PeenyaHub_BLR only (no _PL)
  G. FM + Peenya -> PeenyaHub_BLR_PL if compatible
  H. 18k salary + 9:30 am -> 18000 (timestamp excluded)
  I. Two plausible salary values -> Review if ambiguous
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ocr_models import OCRLine
from app.evidence import (
    FieldCandidate,
    FieldResult,
    score_name_candidates,
    score_dob_candidates,
    score_aadhaar_candidates,
    score_address_candidates,
    score_facility_candidates,
    score_role_candidates,
    score_salary_candidates,
    confidence_level,
    NAME_AUTO_SELECT_MIN,
    NAME_MIN_MARGIN,
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


def _make_line(text, y=0, x1=0, x2=100, confidence=0.9, order=0):
    return OCRLine(
        text=text, confidence=confidence,
        x1=x1, y1=y, x2=x2, y2=y + 20,
        order=order,
    )


# ── Test A: To rejected, real name selected ──────────────────────────────────


def test_a_to_rejected_real_name_selected():
    """'To' is a document label and must be rejected. 'Pavan K N' above DOB
    must be selected as the name candidate."""
    print("\nTest A: To rejected, real name selected")
    lines = [
        _make_line("To", y=10),
        _make_line("Pavan K N", y=40),
        _make_line("DOB: 31/07/2006", y=70),
        _make_line("Male", y=100),
    ]
    result = score_name_candidates(lines)
    check(result.selected is not None, "Name selected")
    if result.selected:
        check(result.selected.value == "Pavan K N",
              f"Selected 'Pavan K N' (got '{result.selected.value}')")
        check("To" not in result.selected.value,
              "Selected value is not 'To'")
        check(result.confidence in ("High", "Review"),
              f"Confidence is High or Review (got '{result.confidence}')")

    # Check that 'To' was rejected
    rejected = [c for c in result.all_candidates if c.is_rejected]
    to_found = any("To" in c.value for c in rejected)
    check(to_found, "'To' was rejected as a candidate")


# ── Test B: Two plausible names close scores -> Needs Review ─────────────────


def test_b_two_plausible_names_close_scores():
    """Two equally strong name candidates should trigger Needs Review."""
    print("\nTest B: Two plausible names, close scores -> Needs Review")
    # Two lines that are both plausible names, both near DOB
    lines = [
        _make_line("RAHUL KUMAR", y=40, confidence=0.92),
        _make_line("PRIYA DEVI", y=42, confidence=0.91),
        _make_line("DOB: 15/08/1995", y=70),
    ]
    result = score_name_candidates(lines)
    check(result.needs_review is True,
          f"Needs Review when scores are close (got needs_review={result.needs_review})")
    check(result.confidence != "High" or result.margin < NAME_MIN_MARGIN,
          f"Confidence is not High when margin is narrow (margin={result.margin:.1f})")


# ── Test C: Strong name with large margin -> High ────────────────────────────


def test_c_strong_name_large_margin():
    """A single clear name far above all others should be High confidence."""
    print("\nTest C: Strong name with large margin -> High")
    lines = [
        _make_line("Pavan K N", y=40, confidence=0.95),
        _make_line("DOB: 31/07/2006", y=62),
        _make_line("Male", y=84),
    ]
    result = score_name_candidates(lines)
    check(result.selected is not None, "Name selected")
    if result.selected:
        check(result.selected.value == "Pavan K N",
              f"Selected 'Pavan K N' (got '{result.selected.value}')")
    check(result.confidence == "High",
          f"Confidence is High (got '{result.confidence}', margin={result.margin:.1f}, score={result.selected.score if result.selected else 0})")
    check(result.needs_review is False,
          f"Does not need review (got needs_review={result.needs_review})")


# ── Test D: Address: only -> Missing ─────────────────────────────────────────


def test_d_address_label_only():
    """A single 'Address:' label line with no content must be Missing."""
    print("\nTest D: Address label only -> Missing")
    lines = [
        _make_line("Address:", y=50),
    ]
    result = score_address_candidates(lines)
    check(result.confidence == "Missing",
          f"Address confidence is Missing (got '{result.confidence}')")
    check(result.needs_review is True,
          "Needs Review when address is missing")
    check(result.selected is None or result.selected.is_rejected,
          "No valid address candidate selected")


# ── Test E: Multiline address with PIN/state/district -> High/Review ─────────


def test_e_multiline_address_with_pin_state():
    """A multi-line address with PIN code and state should score well."""
    print("\nTest E: Multiline address with PIN/state -> High/Review")
    lines = [
        _make_line("Address:", y=50),
        _make_line("C/O Ravindra", y=80),
        _make_line("123 Main Road", y=110),
        _make_line("Peenya Bangalore", y=140),
        _make_line("Karnataka 560058", y=170),
    ]
    result = score_address_candidates(lines)
    check(result.selected is not None, "Address selected")
    if result.selected:
        check("Karnataka" in result.selected.value or "560058" in result.selected.value,
              f"Selected address contains state or PIN (got '{result.selected.value[:60]}...')")
        check(result.selected.score > 50,
              f"Address score > 50 (got {result.selected.score:.1f})")
    check(result.confidence in ("High", "Review"),
          f"Confidence is High or Review (got '{result.confidence}')")


# ── Test F: LM + Peenya -> PeenyaHub_BLR only ───────────────────────────────


def test_f_lm_peenya_no_pl():
    """LM + Peenya must match PeenyaHub_BLR, NOT PeenyaHub_BLR_PL."""
    print("\nTest F: LM + Peenya -> PeenyaHub_BLR only (no _PL)")
    all_hubs = [
        "PeenyaHub_BLR", "PeenyaHub_BLR_PL",
        "WhitefieldHub_BLR", "WhitefieldHub_BLR_PL",
        "NelamangalaHub_BLR", "NelamangalaHub_BLR_PL",
    ]
    # 4421 = Flipkart LM -> only non-_PL hubs
    hubs_for_4421 = [h for h in all_hubs if not h.endswith("_PL")]

    result = score_facility_candidates(
        hub_text="Peenya hub",
        cost_code="4421",
        all_hubs=all_hubs,
        hubs_for_code=hubs_for_4421,
    )
    check(result.selected is not None, "Facility selected")
    if result.selected:
        check(result.selected.value == "PeenyaHub_BLR",
              f"Selected PeenyaHub_BLR (got '{result.selected.value}')")
        check(not result.selected.value.endswith("_PL"),
              "Selected facility is NOT _PL")

    # Check no _PL candidate scored positively
    pl_candidates = [c for c in result.all_candidates
                     if c.value.endswith("_PL") and not c.is_rejected]
    check(len(pl_candidates) == 0,
          "No _PL facility in positive candidates")


# ── Test G: FM + Peenya -> PeenyaHub_BLR_PL ─────────────────────────────────


def test_g_fm_peenya_pl():
    """FM + Peenya must match PeenyaHub_BLR_PL."""
    print("\nTest G: FM + Peenya -> PeenyaHub_BLR_PL")
    all_hubs = [
        "PeenyaHub_BLR", "PeenyaHub_BLR_PL",
        "WhitefieldHub_BLR", "WhitefieldHub_BLR_PL",
    ]
    # 4441 = Flipkart FM -> only _PL hubs
    hubs_for_4441 = [h for h in all_hubs if h.endswith("_PL")]

    result = score_facility_candidates(
        hub_text="Peenya hub",
        cost_code="4441",
        all_hubs=all_hubs,
        hubs_for_code=hubs_for_4441,
    )
    check(result.selected is not None, "Facility selected")
    if result.selected:
        check(result.selected.value == "PeenyaHub_BLR_PL",
              f"Selected PeenyaHub_BLR_PL (got '{result.selected.value}')")


# ── Test H: 18k salary + 9:30 am -> 18000 ───────────────────────────────────


def test_h_salary_18k_excludes_timestamp():
    """18k must be detected as salary; 9:30 am must be excluded."""
    print("\nTest H: 18k salary + 9:30 am -> 18000")
    lines = [
        _make_line("9:30 am", y=10),
        _make_line("18k salary", y=40),
        _make_line("Peenya hub", y=70),
    ]
    result = score_salary_candidates(lines)
    check(result.selected is not None, "Salary selected")
    if result.selected:
        check(result.selected.value == "18000",
              f"Selected 18000 (got '{result.selected.value}')")
    # 9:30 am should not be a candidate
    am_candidates = [c for c in result.all_candidates if "9:30" in c.value]
    check(len(am_candidates) == 0,
          "9:30 am was not a salary candidate")


# ── Test: Decimal-k salary parsing (18.5k -> 18500) ─────────────────────────


def test_salary_decimal_k_parsing():
    """Decimal-k values must parse to exact integer rupees.

    Regression guard for: 18k -> 18000, 18.5k -> 18500, 15.5k -> 15500,
    17.25k -> 17250.  (The decimal part scales the k prefix correctly.)
    """
    print("\nTest: decimal-k salary parsing")
    cases = [
        ("18k", 18000),
        ("18.5k", 18500),
        ("15.5k", 15500),
        ("17.25k", 17250),
        ("salary 17k", 17000),
    ]
    for raw, expected in cases:
        result = score_salary_candidates([_make_line(raw, y=40)])
        check(result.selected is not None, f"salary selected for {raw!r}")
        if result.selected:
            check(result.selected.value == str(expected),
                  f"{raw!r} -> {expected} (got {result.selected.value!r})")


def test_salary_decimal_k_flat_text():
    """Flat-text extraction path must parse decimal-k identically to evidence."""
    print("\nTest: decimal-k salary parsing (flat-text path)")
    from app import ocr
    cases = [
        ("18k", 18000),
        ("18.5k", 18500),
        ("15.5k", 15500),
        ("17.25k", 17250),
    ]
    for raw, expected in cases:
        out = ocr._extract_salary_candidates([raw])
        ok = bool(out) and out[0][1] == expected
        check(ok, f"flat-text {raw!r} -> {expected} (got {out!r})")


def test_salary_space_before_k_parsing():
    """Regression: '18 k' (space before 'k') and 'salary 18 k' parse to 18000,
    on BOTH the evidence path and the flat-text path, while timestamps are
    never treated as salary."""
    print("\nTest: salary with space before 'k'")
    cases = [
        ("18k", 18000),
        ("18 k", 18000),
        ("18 K", 18000),
        ("18.5k", 18500),
        ("18.5 k", 18500),
        ("salary 18 k", 18000),
        ("18 k salary", 18000),
        ("18 k salary 10:30 am", 18000),
        ("16.5k", 16500),
    ]
    for raw, expected in cases:
        result = score_salary_candidates([_make_line(raw, y=40)])
        check(result.selected is not None, f"salary selected for {raw!r}")
        if result.selected:
            check(result.selected.value == str(expected),
                  f"evidence: {raw!r} -> {expected} (got {result.selected.value!r})")
    # Timestamps are never salaries.
    for ts in ("9:30 am", "18:45", "11:25 am"):
        result = score_salary_candidates([_make_line(ts, y=40)])
        check(result.selected is None, f"timestamp {ts!r} is not a salary")


def test_salary_space_before_k_flat_text():
    """Flat-text path must also handle '18 k' (space)."""
    print("\nTest: salary space-before-k (flat-text path)")
    from app import ocr
    for raw, expected in [
        ("18k", 18000), ("18 k", 18000), ("18.5 k", 18500), ("salary 18 k", 18000),
    ]:
        out = ocr._extract_salary_candidates([raw])
        ok = bool(out) and out[0][1] == expected
        check(ok, f"flat-text {raw!r} -> {expected} (got {out!r})")
    # Timestamps excluded.
    out = ocr._extract_salary_candidates(["9:30 am"])
    check(not out, "flat-text '9:30 am' is not a salary")


# ── Test I: Two plausible salary values -> Review ────────────────────────────


def test_i_two_salary_values_review():
    """Two plausible salary values should trigger Review."""
    print("\nTest I: Two plausible salary values -> Review")
    lines = [
        _make_line("salary 18k", y=10),
        _make_line("15.5k ctc", y=40),
    ]
    result = score_salary_candidates(lines)
    check(result.needs_review is True,
          f"Needs Review with two salary values (got needs_review={result.needs_review})")
    check(result.confidence != "High",
          f"Confidence is not High (got '{result.confidence}')")


# ── Test: Confidence level logic ─────────────────────────────────────────────


def test_confidence_level_logic():
    """Verify the confidence_level function works correctly."""
    print("\nTest: Confidence level logic")
    check(confidence_level(88, 42, 70, 20) == "High",
          "88 vs 42 -> High (large margin)")
    check(confidence_level(72, 69, 70, 20) == "Review",
          "72 vs 69 -> Review (narrow margin)")
    check(confidence_level(0, 0, 70, 20) == "Missing",
          "0 vs 0 -> Missing")
    check(confidence_level(50, 10, 70, 20) == "Review",
          "50 vs 10 -> Review (below auto_min)")


# ── Test: DOB extraction ────────────────────────────────────────────────────


def test_dob_extraction():
    """Verify DOB extraction from OCR lines."""
    print("\nTest: DOB extraction")
    lines = [
        _make_line("Name: Pavan", y=10),
        _make_line("DOB: 31/07/2006", y=40),
    ]
    result = score_dob_candidates(lines)
    check(result.selected is not None, "DOB selected")
    if result.selected:
        check(result.selected.value == "31/07/2006",
              f"DOB value correct (got '{result.selected.value}')")


# ── Test: Aadhaar number extraction ──────────────────────────────────────────


def test_aadhaar_extraction():
    """Verify Aadhaar number extraction from OCR lines."""
    print("\nTest: Aadhaar number extraction")
    lines = [
        _make_line("1234 5678 9012", y=10),
    ]
    result = score_aadhaar_candidates(lines)
    check(result.selected is not None, "Aadhaar selected")
    if result.selected:
        check(result.selected.value == "123456789012",
              f"Aadhaar value correct (got '{result.selected.value}')")
        check(len(result.selected.value) == 12,
              f"Aadhaar is 12 digits (got {len(result.selected.value)} digits)")


# ── Test: Role candidate ranking ─────────────────────────────────────────────


def test_role_ranking():
    """Verify role candidates are filtered by cost code."""
    print("\nTest: Role candidate ranking")
    allowed_roles = [
        "LM - Delivery Executive",
        "LM - Sorter",
        "LM - Team Leader",
    ]
    result = score_role_candidates(
        role_text="LM sorter",
        cost_code="4421",
        allowed_roles=allowed_roles,
        role_aliases={"sorter": "Sorter", "sort": "Sorter"},
    )
    check(result.selected is not None, "Role selected")
    if result.selected:
        check(result.selected.value == "LM - Sorter",
              f"Selected LM - Sorter (got '{result.selected.value}')")
        check("FM" not in result.selected.value,
              "Selected role is not FM")


# ── Main runner ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("=" * 60)
    print("EVIDENCE SCORING TESTS")
    print("=" * 60)

    test_a_to_rejected_real_name_selected()
    test_b_two_plausible_names_close_scores()
    test_c_strong_name_large_margin()
    test_d_address_label_only()
    test_e_multiline_address_with_pin_state()
    test_f_lm_peenya_no_pl()
    test_g_fm_peenya_pl()
    test_h_salary_18k_excludes_timestamp()
    test_salary_decimal_k_parsing()
    test_salary_decimal_k_flat_text()
    test_salary_space_before_k_parsing()
    test_salary_space_before_k_flat_text()
    test_i_two_salary_values_review()
    test_confidence_level_logic()
    test_dob_extraction()
    test_aadhaar_extraction()
    test_role_ranking()

    print("\n" + "=" * 60)
    print(f"TOTAL: {PASS + FAIL}  PASS: {PASS}  FAIL: {FAIL}")
    print("=" * 60)
    if FAIL:
        sys.exit(1)
    print("ALL TESTS PASSED")
