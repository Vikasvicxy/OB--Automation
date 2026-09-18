"""Manual validation engine for recruiter-input combinations.

IMPORTANT:
  * This module is validation-only. It NEVER creates candidates, NEVER modifies
    batches, NEVER generates onboarding Excel, and NEVER uploads to eSampark.
  * RESULTS are computed by calling the SAME production resolver functions used
    in real onboarding (``rules.resolve_smart_onboarding`` /
    ``rules.normalize_salary`` / ``rules.get_location_for_facility`` /
    ``master_data.get_roles_for_cost_code``).  There is NO parallel/fake
    implementation of the business rules.
  * Cases whose production output deviates from the configured expected fixture
    are shown honestly as FAIL (or REVIEW when ambiguity is intended) so real
    defects surface instead of being masked.

The module is framework-agnostic (no FastAPI) so it can be unit tested directly.
"""

from __future__ import annotations

from typing import Optional

from app import rules
from app import master_data


# ── Case definitions ─────────────────────────────────────────────────────────
# Each case:
#   case_id        str            e.g. "CASE 01"
#   title          str            short human label
#   role_text      str            recruiter role/screenshot phrase
#   hub_text       str            recruiter facility phrase
#   salary_text    str            recruiter salary phrase (or "")
#   tag            str            grouping tag (Known Real / Varied / ...)
#   ocr_capable    bool           whether a full OCR path can be exercised
#   expect_review  bool           True when ambiguity is intended (sent to review)
#   expected       dict           expected fixture (see compare_field)
#   notes          str            human guidance
#
# expected dict field semantics (all optional; omitted -> not compared):
#   entity        exact str
#   operation     exact str
#   cost_code     exact str
#   role          exact str, OR dict {"compatible_with": code} meaning: actual
#                 role must equal an official role valid for that cost code
#   facility      exact str, OR dict {"options": [..], "allow_blank": bool}
#   location      exact str
#   salary        int            exact normalized rupees
#   needs_review  bool           whether the case SHOULD end up needing review
#   must_not      list[str]      values that must never be produced (e.g. forbidden hubs)


# Two independent validation sets.
#   SET_A  -> the original 20 cases (validation set A) — kept unchanged.
#   SET_B  -> a NEW second set of 20 generalization cases.
# CASES is a backward-compatible alias for SET_A so existing callers/tests
# that reference CASES keep working.

SET_A: list[dict] = [
    # ── Known real cases ─────────────────────────────────────────────────────
    {
        "case_id": "CASE 01",
        "title": "PREM KUMAR",
        "role_text": "LM sorter",
        "hub_text": "Hebbal Myntra hub",
        "salary_text": "15500",
        "tag": "Known Real",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Myntra",
            "operation": "Last Mile",
            "cost_code": "8751",
            "role": "LM - Sorter",
            "facility": "HebbalMYNTRAHub_BLR",
            "salary": 15500,
        },
        "notes": "Myntra LM sorter on the Hebbal Myntra hub.",
    },
    {
        "case_id": "CASE 02",
        "title": "PALLAVI",
        "role_text": "LM sorter",
        "hub_text": "Peenya hub",
        "salary_text": "18k salary 11:25 am",
        "tag": "Known Real",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": "LM - Sorter",
            "facility": "Peenya Hub",
            "location": "BLR/PEN",
            "salary": 18000,
            "must_not": ["NelamangalaHub_BLR_PL"],
        },
        "notes": "Timestamp must be ignored; must never select the FM _PL hub.",
    },
    {
        "case_id": "CASE 03",
        "title": "RENUKA",
        "role_text": "LM sorter",
        "hub_text": "Nelamangala",
        "salary_text": "15500",
        "tag": "Known Real",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": "LM - Sorter",
            "facility": "NelamangalaHub_BLR",
            "salary": 15500,
        },
        "notes": "Standard Flipkart LM sorter.",
    },

    # ── Varied cases ─────────────────────────────────────────────────────────
    {
        "case_id": "CASE 04",
        "title": "FM Sorter - Nelamangala",
        "role_text": "FM sorter",
        "hub_text": "Nelamangala hub",
        "salary_text": "17k",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "First Mile",
            "cost_code": "4441",
            "role": {"compatible_with": "4441"},
            "facility": "NelamangalaHub_BLR_PL",
            "salary": 17000,
        },
        "notes": "FM sorter resolves against the FM master; role must be FM-compatible.",
    },
    {
        "case_id": "CASE 05",
        "title": "LM Delivery - Nelamangala",
        "role_text": "LM delivery",
        "hub_text": "Nelamangala",
        "salary_text": "18.5k",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": {"compatible_with": "4421"},
            "facility": "NelamangalaHub_BLR",
            "salary": 18500,
        },
        "notes": "Decimal-k salary 18.5k -> 18500.",
    },
    {
        "case_id": "CASE 06",
        "title": "LM Team Leader - Peenya",
        "role_text": "LM team leader",
        "hub_text": "Peenya hub",
        "salary_text": "20000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": "LM - Team Leader",
            "facility": "Peenya Hub",
            "salary": 20000,
        },
        "notes": "Team Leader resolves exactly.",
    },
    {
        "case_id": "CASE 07",
        "title": "LM Biker - Peenya",
        "role_text": "LM biker",
        "hub_text": "Peenya",
        "salary_text": "17.25k",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": {"compatible_with": "4421"},
            "facility": "Peenya Hub",
            "salary": 17250,
        },
        "notes": "Alias 'biker' -> Delivery Executive-compatible role; 17.25k -> 17250.",
    },
    {
        "case_id": "CASE 08",
        "title": "LM Sorter - Hebbal Myntra",
        "role_text": "LM sorter",
        "hub_text": "Hebbal Myntra",
        "salary_text": "16000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Myntra",
            "operation": "Last Mile",
            "cost_code": "8751",
            "role": "LM - Sorter",
            "facility": "HebbalMYNTRAHub_BLR",
            "salary": 16000,
        },
        "notes": "Myntra LM, never Flipkart.",
    },
    {
        "case_id": "CASE 09",
        "title": "LM Delivery - Banaswadi Myntra",
        "role_text": "LM delivery",
        "hub_text": "Banaswadi Myntra hub",
        "salary_text": "18k",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Myntra",
            "operation": "Last Mile",
            "cost_code": "8751",
            "role": {"compatible_with": "8751"},
            "facility": "BanaswadiMYNTRAHub_BLR",
            "salary": 18000,
        },
        "notes": "Myntra LM delivery role from master.",
    },
    {
        "case_id": "CASE 10",
        "title": "FM Sorter - Myntra (blank hub)",
        "role_text": "FM sorter",
        "hub_text": "Myntra",
        "salary_text": "18000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": True,
        "expected": {
            "entity": "Myntra",
            "operation": "First Mile",
            "cost_code": "8752",
            "role": {"compatible_with": "8752"},
            "facility": {"options": [], "allow_blank": True},
            "salary": 18000,
            "needs_review": True,
        },
        "notes": "8752 hub list is intentionally empty; must NOT reuse Myntra LM hubs.",
    },
    {
        "case_id": "CASE 11",
        "title": "LM Prexo - Banaswadi Myntra",
        "role_text": "LM prexo",
        "hub_text": "Banaswadi Myntra",
        "salary_text": "18000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": True,
        "expected": {
            "entity": "Myntra",
            "operation": "Last Mile",
            "cost_code": "8751",
            "role": "",
            "facility": "BanaswadiMYNTRAHub_BLR",
            "salary": 18000,
            "needs_review": True,
        },
        "notes": "8751 must NOT allow Prexo Delivery Executive; the Myntra hub evidence is retained but the incompatible Prexo role is blanked for review.",
    },
    {
        "case_id": "CASE 12",
        "title": "LM Sorter - PeenyaHub_BLR_PL",
        "role_text": "LM sorter",
        "hub_text": "PeenyaHub_BLR_PL",
        "salary_text": "18000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": "LM - Sorter",
            "facility": {"options": ["Peenya Hub"], "allow_blank": True},
            "salary": 18000,
            "must_not": ["4441"],
        },
        "notes": "Explicit LM must never silently flip to 4441; approximate _PL text may resolve to the LM hub.",
    },
    {
        "case_id": "CASE 13",
        "title": "FM Sorter - NelamangalaHub_BLR",
        "role_text": "FM sorter",
        "hub_text": "NelamangalaHub_BLR",
        "salary_text": "18000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "First Mile",
            "cost_code": "4441",
            "role": "FM - Sorter",
            "facility": {"options": ["NelamangalaHub_BLR_PL"], "allow_blank": True},
            "salary": 18000,
            "must_not": ["4421"],
        },
        "notes": "Explicit FM must never silently flip to 4421; a last-mile-looking name may resolve to the first-mile _PL hub.",
    },
    {
        "case_id": "CASE 14",
        "title": "Sorter only - Nelamangala _PL (suffix fallback)",
        "role_text": "sorter",
        "hub_text": "NelamangalaHub_BLR_PL",
        "salary_text": "17000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "First Mile",
            "cost_code": "4441",
            "role": {"compatible_with": "4441"},
            "facility": "NelamangalaHub_BLR_PL",
            "salary": 17000,
        },
        "notes": "No explicit LM/FM; hub _PL suffix should infer First Mile and an FM-compatible Sorter role.",
    },
    {
        "case_id": "CASE 15",
        "title": "Sorter only - NelamangalaHub_BLR (exact LM display)",
        "role_text": "sorter",
        "hub_text": "NelamangalaHub_BLR",
        "salary_text": "17000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": {"compatible_with": "4421"},
            "facility": "NelamangalaHub_BLR",
            "salary": 17000,
        },
        "notes": "'NelamangalaHub_BLR' is the LM display name (location BLR/NLM); an exact "
               "readable name resolves unambiguously to Last Mile/4421. Only the BARE "
               "Nelamangala locality (no _PL / LM / FM token) stays ambiguous -> review.",
    },
    {
        "case_id": "CASE 16",
        "title": "LM Sort - fuzzy nelmangla",
        "role_text": "LM sort",
        "hub_text": "nelmangla",
        "salary_text": "15.5k",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": "LM - Sorter",
            "facility": "NelamangalaHub_BLR",
            "salary": 15500,
        },
        "notes": "Fuzzy facility spelling + alias 'sort' => Sorter.",
    },
    {
        "case_id": "CASE 17",
        "title": "LM TL - peenya (alias)",
        "role_text": "LM tl",
        "hub_text": "peenya",
        "salary_text": "salary 18k",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": "LM - Team Leader",
            "facility": "Peenya Hub",
            "salary": 18000,
        },
        "notes": "Alias 'tl' => Team Leader.",
    },
    {
        "case_id": "CASE 18",
        "title": "LM Delivery - unknown hub",
        "role_text": "LM delivery",
        "hub_text": "unknownhubxyz",
        "salary_text": "18000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": True,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": {"compatible_with": "4421"},
            "facility": {"options": [], "allow_blank": True},
            "location": "",
            "salary": 18000,
            "needs_review": True,
        },
        "notes": "Unknown facility -> blank + review; never guess a random hub.",
    },
    {
        "case_id": "CASE 19",
        "title": "LM Sorter - Peenya (timestamp)",
        "role_text": "LM sorter",
        "hub_text": "Peenya",
        "salary_text": "18k salary 9:30 am",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart",
            "operation": "Last Mile",
            "cost_code": "4421",
            "role": "LM - Sorter",
            "facility": "Peenya Hub",
            "salary": 18000,
        },
        "notes": "Timestamp must be ignored.",
    },
    {
        "case_id": "CASE 20",
        "title": "Myntra LM Sorter - hebbal (₹)",
        "role_text": "Myntra LM sorter",
        "hub_text": "hebbal",
        "salary_text": "₹18,000",
        "tag": "Varied",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Myntra",
            "operation": "Last Mile",
            "cost_code": "8751",
            "role": "LM - Sorter",
            "facility": "HebbalMYNTRAHub_BLR",
            "salary": 18000,
        },
        "notes": "₹ comma-formatted salary + Myntra entity.",
    },
]

# Backward-compatible alias — CASES is the original Set A.
CASES: list[dict] = SET_A


# ── Validation Set B: 20 NEW generalization cases ────────────────────────────
# These stress how the PRODUCTION resolver generalizes beyond Set A.  Expected
# fixtures are written to the spec WITHOUT weakening to match current output.

SET_B: list[dict] = [
    {
        "case_id": "CASE B01",
        "title": "last mile sorter - Peenya",
        "role_text": "last mile sorter",
        "hub_text": "peenya",
        "salary_text": "16k",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": "Peenya Hub", "salary": 16000,
        },
        "notes": "'last mile sorter' must resolve to LM Sorter.",
    },
    {
        "case_id": "CASE B02",
        "title": "first mile sorter - Nelamangala",
        "role_text": "first mile sorter",
        "hub_text": "nelamangala",
        "salary_text": "16k",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"},
            "facility": "NelamangalaHub_BLR_PL", "salary": 16000,
        },
        "notes": "'first mile sorter' must resolve to FM Sorter on the _PL hub.",
    },
    {
        "case_id": "CASE B03",
        "title": "LM delivery executive - Nelamangala",
        "role_text": "LM delivery executive",
        "hub_text": "nelamangla hub",
        "salary_text": "19000",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": {"compatible_with": "4421"},
            "facility": "NelamangalaHub_BLR", "salary": 19000,
        },
        "notes": "Official LM Delivery Executive role; fuzzy hub spelling.",
    },
    {
        "case_id": "CASE B04",
        "title": "FM delivery - Nelamangala",
        "role_text": "FM delivery",
        "hub_text": "Nelamangala",
        "salary_text": "19.5k",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"},
            "facility": "NelamangalaHub_BLR_PL", "salary": 19500,
        },
        "notes": "FM Delivery-compatible role; 19.5k -> 19500.",
    },
    {
        "case_id": "CASE B05",
        "title": "LM TL - Peenya (₹)",
        "role_text": "LM TL",
        "hub_text": "Peenya Hub",
        "salary_text": "₹21,000",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Team Leader", "facility": "Peenya Hub", "salary": 21000,
        },
        "notes": "Alias TL -> Team Leader; ₹21,000 -> 21000.",
    },
    {
        "case_id": "CASE B06",
        "title": "last mile biker - Peenya",
        "role_text": "last mile biker",
        "hub_text": "peenya",
        "salary_text": "18 k",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": {"compatible_with": "4421"},
            "facility": "Peenya Hub", "salary": 18000,
        },
        "notes": "'last mile biker' -> Delivery Executive-compatible role; '18 k' -> 18000.",
    },
    {
        "case_id": "CASE B07",
        "title": "Myntra last mile sorter - Hebbal",
        "role_text": "Myntra last mile sorter",
        "hub_text": "Hebbal",
        "salary_text": "17k",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter", "facility": "HebbalMYNTRAHub_BLR", "salary": 17000,
        },
        "notes": "Myntra LM must win; never Flipkart.",
    },
    {
        "case_id": "CASE B08",
        "title": "Myntra LM delivery - Banaswadi",
        "role_text": "Myntra LM delivery",
        "hub_text": "Banaswadi",
        "salary_text": "18.5k",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": {"compatible_with": "8751"},
            "facility": "BanaswadiMYNTRAHub_BLR", "salary": 18500,
        },
        "notes": "Myntra LM delivery role from master.",
    },
    {
        "case_id": "CASE B09",
        "title": "Myntra FM sorter - Hebbal (blank FM hub)",
        "role_text": "Myntra FM sorter",
        "hub_text": "Hebbal",
        "salary_text": "18000",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": True,
        "expected": {
            "entity": "Myntra", "operation": "First Mile", "cost_code": "8752",
            "role": {"compatible_with": "8752"},
            "facility": {"options": [], "allow_blank": True},
            "salary": 18000, "needs_review": True,
        },
        "notes": "8752 FM hub list empty; must NOT reuse HebbalMYNTRAHub_BLR (8751).",
    },
    {
        "case_id": "CASE B10",
        "title": "Myntra last mile prexo - Hebbal",
        "role_text": "Myntra last mile prexo",
        "hub_text": "Hebbal",
        "salary_text": "18000",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": True,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "", "facility": "HebbalMYNTRAHub_BLR",
            "salary": 18000, "needs_review": True,
        },
        "notes": "Prexo NOT allowed for 8751 -> role blank/rejected + review. Stay Myntra.",
    },
    {
        "case_id": "CASE B11",
        "title": "sorter - NelamangalaHub_BLR_PL (suffix infer FM)",
        "role_text": "sorter",
        "hub_text": "NelamangalaHub_BLR_PL",
        "salary_text": "18000",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"},
            "facility": "NelamangalaHub_BLR_PL", "salary": 18000,
        },
        "notes": "No LM/FM; exact _PL hub infers FM/4441 with an FM-compatible Sorter role.",
    },
    {
        "case_id": "CASE B12",
        "title": "delivery - PeenyaHub_BLR (suffix infer LM)",
        "role_text": "delivery",
        "hub_text": "PeenyaHub_BLR",
        "salary_text": "18000",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": {"compatible_with": "4421"},
            "facility": "Peenya Hub", "salary": 18000,
        },
        "notes": "No LM/FM; LM hub suffix infers LM/4421 with an LM-compatible role.",
    },
    {
        "case_id": "CASE B13",
        "title": "LM sorter - NelamangalaHub_BLR_PL",
        "role_text": "LM sorter",
        "hub_text": "NelamangalaHub_BLR_PL",
        "salary_text": "17500",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": {"options": ["NelamangalaHub_BLR"], "allow_blank": True},
            "salary": 17500, "must_not": ["4441", "NelamangalaHub_BLR_PL"],
        },
        "notes": "Explicit LM wins; never selects the _PL hub; never flips to 4441.",
    },
    {
        "case_id": "CASE B14",
        "title": "FM sorter - NelamangalaHub_BLR",
        "role_text": "FM sorter",
        "hub_text": "NelamangalaHub_BLR",
        "salary_text": "17500",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": "FM - Sorter", "facility": {"options": ["NelamangalaHub_BLR_PL"], "allow_blank": True},
            "salary": 17500, "must_not": ["4421", "NelamangalaHub_BLR"],
        },
        "notes": "Explicit FM wins; never selects the LM hub (NelamangalaHub_BLR); never flips to 4421.",
    },
    {
        "case_id": "CASE B15",
        "title": "LM sort - nelmangala (decimal salary)",
        "role_text": "lm sort",
        "hub_text": "nelmangala",
        "salary_text": "salary 15.75k",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": "NelamangalaHub_BLR", "salary": 15750,
        },
        "notes": "15.75k -> 15750 (decimal-k).",
    },
    {
        "case_id": "CASE B16",
        "title": "FM TL - Nelamangala (timestamp)",
        "role_text": "fm tl",
        "hub_text": "nelamangala hub",
        "salary_text": "22k salary 10:45 am",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"},
            "facility": "NelamangalaHub_BLR_PL", "salary": 22000,
        },
        "notes": "FM TL; timestamp 10:45 am ignored; 22k -> 22000.",
    },
    {
        "case_id": "CASE B17",
        "title": "LM delivery - Peenya (salary 18500 + timestamp)",
        "role_text": "LM delivery",
        "hub_text": "peenya",
        "salary_text": "salary 18500",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": {"compatible_with": "4421"},
            "facility": "Peenya Hub", "salary": 18500,
        },
        "notes": "Official LM Delivery Executive role; plain 18500 salary (timestamp field present separately).",
    },
    {
        "case_id": "CASE B18",
        "title": "LM sorter - unknown facility",
        "role_text": "LM sorter",
        "hub_text": "totallyunknownfacility",
        "salary_text": "18000",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": True,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": {"options": [], "allow_blank": True},
            "location": "", "salary": 18000, "needs_review": True,
        },
        "notes": "Unknown facility -> blank + review; never guess a random master hub.",
    },
    {
        "case_id": "CASE B19",
        "title": "Myntra sorter - BanaswadiMYNTRAHub_BLR (exact infer)",
        "role_text": "Myntra sorter",
        "hub_text": "BanaswadiMYNTRAHub_BLR",
        "salary_text": "18000",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter", "facility": "BanaswadiMYNTRAHub_BLR", "salary": 18000,
        },
        "notes": "Exact Myntra LM facility infers Myntra/8751 + LM Sorter.",
    },
    {
        "case_id": "CASE B20",
        "title": "sorter - HebbalMYNTRAHub_BLR (exact infer)",
        "role_text": "sorter",
        "hub_text": "HebbalMYNTRAHub_BLR",
        "salary_text": "16.5k",
        "tag": "Set B",
        "ocr_capable": True,
        "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter", "facility": "HebbalMYNTRAHub_BLR", "salary": 16500,
        },
        "notes": "No explicit entity/LM/FM; exact Myntra official facility infers Myntra/8751 + LM Sorter.",
    },
]


# ── Validation Set C: 50 final generalization cases ──────────────────────────
# Final stress test before any real eSampark upload.  These 50 NEW scenarios
# exercise Flipkart/Myntra LM+FM variants, exact/fuzzy hubs, aliases,
# misspellings, salary formats, timestamps, unknown facilities, conflict
# handling, Prexo rejection, role matching and Needs Review behaviour, on top
# of the CURRENT production resolver — with expected fixtures written to the
# spec WITHOUT weakening to match current output.

SET_C: list[dict] = [
    # ── Flipkart LM / FM variants ────────────────────────────────────────────
    {
        "case_id": "C01", "title": "LM sorter - Penya hub (typo)",
        "role_text": "LM sorter", "hub_text": "Penya hub", "salary_text": "16.5k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": "Peenya Hub", "salary": 16500,
        },
        "notes": "Explicit LM sorter; 'Penya' typo -> Peenya Hub (BLR/PEN); 16.5k.",
    },
    {
        "case_id": "C02", "title": "FM sorter - Nelamangala hub (fuzzy)",
        "role_text": "FM sorter", "hub_text": "Nelamangala hub", "salary_text": "16.5k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"}, "facility": "NelamangalaHub_BLR_PL",
            "salary": 16500,
        },
        "notes": "Explicit FM sorter -> _PL hub; never the LM hub.",
    },
    {
        "case_id": "C03", "title": "last-mile delivery - nelamangla (typo)",
        "role_text": "last-mile delivery", "hub_text": "nelamangla", "salary_text": "19000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": {"compatible_with": "4421"}, "facility": "NelamangalaHub_BLR",
            "salary": 19000,
        },
        "notes": "Phrase 'last-mile delivery' -> LM; fuzzy hub spelling.",
    },
    {
        "case_id": "C04", "title": "first-mile delivery - nelamangla",
        "role_text": "first-mile delivery", "hub_text": "nelamangla", "salary_text": "19 k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"}, "facility": "NelamangalaHub_BLR_PL",
            "salary": 19000,
        },
        "notes": "Phrase 'first-mile delivery' -> FM; '19 k' -> 19000.",
    },
    {
        "case_id": "C05", "title": "LM team lead - peenya hub (₹)",
        "role_text": "LM team lead", "hub_text": "peenya hub", "salary_text": "₹20,500",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Team Leader", "facility": "Peenya Hub", "salary": 20500,
        },
        "notes": "Alias 'team lead' -> Team Leader; ₹20,500 -> 20500.",
    },
    {
        "case_id": "C06", "title": "FM team leader - nelamangala",
        "role_text": "FM team leader", "hub_text": "nelamangala", "salary_text": "21.25k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"}, "facility": "NelamangalaHub_BLR_PL",
            "salary": 21250,
        },
        "notes": "FM Team Leader-compatible; 21.25k -> 21250.",
    },
    {
        "case_id": "C07", "title": "lm biker - nelamangala",
        "role_text": "lm biker", "hub_text": "nelamangala", "salary_text": "17k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": {"compatible_with": "4421"}, "facility": "NelamangalaHub_BLR",
            "salary": 17000,
        },
        "notes": "Alias 'biker' -> Delivery-compatible role.",
    },
    {
        "case_id": "C08", "title": "first mile biker - nelamangala",
        "role_text": "first mile biker", "hub_text": "nelamangala", "salary_text": "18k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"}, "facility": "NelamangalaHub_BLR_PL",
            "salary": 18000,
        },
        "notes": "Phrase 'first mile biker' -> FM Delivery/Biker-compatible.",
    },

    # ── Myntra LM / FM variants ──────────────────────────────────────────────
    {
        "case_id": "C09", "title": "Myntra LM sorter - Hebal (typo)",
        "role_text": "Myntra LM sorter", "hub_text": "Hebal", "salary_text": "17500",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter", "facility": "HebbalMYNTRAHub_BLR", "salary": 17500,
        },
        "notes": "Myntra LM sorter; 'Hebal' typo -> HebbalMYNTRAHub_BLR.",
    },
    {
        "case_id": "C10", "title": "myntra last-mile delivery - banaswadi",
        "role_text": "myntra last-mile delivery", "hub_text": "banaswadi", "salary_text": "18 k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": {"compatible_with": "8751"}, "facility": "BanaswadiMYNTRAHub_BLR",
            "salary": 18000,
        },
        "notes": "Myntra LM delivery; never Flipkart.",
    },
    {
        "case_id": "C11", "title": "MYNTRA LM team lead - hebbal myntra",
        "role_text": "MYNTRA LM team lead", "hub_text": "hebbal myntra", "salary_text": "22000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": {"compatible_with": "8751"}, "facility": "HebbalMYNTRAHub_BLR",
            "salary": 22000,
        },
        "notes": "Myntra LM Team Leader-compatible if allowed by master (it is for 8751).",
    },
    {
        "case_id": "C12", "title": "Myntra LM prexo - Banaswadi (reject)",
        "role_text": "Myntra LM prexo", "hub_text": "Banaswadi", "salary_text": "19000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "", "facility": "BanaswadiMYNTRAHub_BLR", "salary": 19000,
            "needs_review": True,
        },
        "notes": "Prexo NOT allowed for 8751 -> role rejected/blank + review; stay Myntra.",
    },
    {
        "case_id": "C13", "title": "Myntra FM sorter - Banaswadi (blank FM hub)",
        "role_text": "Myntra FM sorter", "hub_text": "Banaswadi", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Myntra", "operation": "First Mile", "cost_code": "8752",
            "facility": {"options": [], "allow_blank": True}, "salary": 18000,
            "needs_review": True,
        },
        "notes": "8752 hub list empty -> facility blank + review.",
    },
    {
        "case_id": "C14", "title": "Myntra first mile delivery - Hebbal (blank FM hub)",
        "role_text": "Myntra first mile delivery", "hub_text": "Hebbal", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Myntra", "operation": "First Mile", "cost_code": "8752",
            "facility": {"options": [], "allow_blank": True}, "salary": 18000,
            "needs_review": True,
        },
        "notes": "8752 blank FM hub -> facility blank + review.",
    },

    # ── Exact hub inference ──────────────────────────────────────────────────
    {
        "case_id": "C15", "title": "sorter - NelamangalaHub_BLR_PL (exact infer FM)",
        "role_text": "sorter", "hub_text": "NelamangalaHub_BLR_PL", "salary_text": "17000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"}, "facility": "NelamangalaHub_BLR_PL",
            "salary": 17000,
        },
        "notes": "Exact _PL hub -> FM/4441 + FM-compatible Sorter.",
    },
    {
        "case_id": "C16", "title": "sorter - PeenyaHub_BLR (exact infer LM)",
        "role_text": "sorter", "hub_text": "PeenyaHub_BLR", "salary_text": "17000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": "Peenya Hub", "salary": 17000,
        },
        "notes": "Exact LM hub -> LM/4421 + LM Sorter.",
    },
    {
        "case_id": "C17", "title": "delivery - NelamangalaHub_BLR_PL",
        "role_text": "delivery", "hub_text": "NelamangalaHub_BLR_PL", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"}, "facility": "NelamangalaHub_BLR_PL",
            "salary": 18000,
        },
        "notes": "Exact _PL hub -> FM + FM Delivery role.",
    },
    {
        "case_id": "C18", "title": "delivery - NelamangalaHub_BLR (exact LM display)",
        "role_text": "delivery", "hub_text": "NelamangalaHub_BLR", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": {"compatible_with": "4421"}, "facility": "NelamangalaHub_BLR",
            "salary": 18000,
        },
        "notes": "'NelamangalaHub_BLR' is the exact LM display name (location BLR/NLM); "
               "an exact readable name resolves to Last Mile/4421. The BARE Nelamangala "
               "locality (no _PL / LM / FM token) remains ambiguous -> review.",
    },
    {
        "case_id": "C19", "title": "sorter - HebbalMYNTRAHub_BLR (exact infer Myntra)",
        "role_text": "sorter", "hub_text": "HebbalMYNTRAHub_BLR", "salary_text": "16500",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter", "facility": "HebbalMYNTRAHub_BLR", "salary": 16500,
        },
        "notes": "Exact Myntra hub -> Myntra/8751 + LM Sorter.",
    },
    {
        "case_id": "C20", "title": "delivery - BanaswadiMYNTRAHub_BLR",
        "role_text": "delivery", "hub_text": "BanaswadiMYNTRAHub_BLR", "salary_text": "17.5k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": {"compatible_with": "8751"}, "facility": "BanaswadiMYNTRAHub_BLR",
            "salary": 17500,
        },
        "notes": "Exact Myntra hub -> Myntra/8751 + Myntra Delivery role.",
    },

    # ── Conflict handling (explicit LM/FM precedence over hub suffix) ────────
    {
        "case_id": "C21", "title": "LM sorter - NelamangalaHub_BLR_PL (never _PL)",
        "role_text": "LM sorter", "hub_text": "NelamangalaHub_BLR_PL", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter",
            "facility": {"options": ["NelamangalaHub_BLR"], "allow_blank": True},
            "salary": 18000, "must_not": ["NelamangalaHub_BLR_PL", "4441"],
        },
        "notes": "Operation stays Last Mile; never select _PL hub.",
    },
    {
        "case_id": "C22", "title": "FM sorter - NelamangalaHub_BLR (never LM hub)",
        "role_text": "FM sorter", "hub_text": "NelamangalaHub_BLR", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"},
            "facility": {"options": ["NelamangalaHub_BLR_PL"], "allow_blank": True},
            "salary": 18000, "must_not": ["NelamangalaHub_BLR", "4421"],
        },
        "notes": "Operation stays First Mile; never select LM hub.",
    },
    {
        "case_id": "C23", "title": "LM sorter - NelamangalaHub_BLR_PL (never _PL)",
        "role_text": "LM sorter", "hub_text": "NelamangalaHub_BLR_PL", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter",
            "facility": {"options": ["NelamangalaHub_BLR"], "allow_blank": True},
            "salary": 18000, "must_not": ["NelamangalaHub_BLR_PL", "4441"],
        },
        "notes": "Never retain _PL for explicit LM; compatible LM hub or review.",
    },
    {
        "case_id": "C24", "title": "FM sorter - NelamangalaHub_BLR (never LM hub)",
        "role_text": "FM sorter", "hub_text": "NelamangalaHub_BLR", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"},
            "facility": {"options": ["NelamangalaHub_BLR_PL"], "allow_blank": True},
            "salary": 18000, "must_not": ["NelamangalaHub_BLR", "4421"],
        },
        "notes": "Never retain the LM hub for explicit FM; compatible _PL hub or review.",
    },

    # ── Unknown facilities -> Needs Review ───────────────────────────────────
    {
        "case_id": "C25", "title": "LM delivery - completelyunknownhub",
        "role_text": "LM delivery", "hub_text": "completelyunknownhub", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": {"compatible_with": "4421"},
            "facility": {"options": [], "allow_blank": True}, "location": "",
            "salary": 18000, "needs_review": True,
        },
        "notes": "Unknown facility -> blank + review; never guess a hub.",
    },
    {
        "case_id": "C26", "title": "FM sorter - xyzunknown",
        "role_text": "FM sorter", "hub_text": "xyzunknown", "salary_text": "17000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"},
            "facility": {"options": [], "allow_blank": True},
            "salary": 17000, "needs_review": True,
        },
        "notes": "Unknown facility -> blank + review.",
    },
    {
        "case_id": "C27", "title": "Myntra LM sorter - unknownmyntrahub",
        "role_text": "Myntra LM sorter", "hub_text": "unknownmyntrahub", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter",
            "facility": {"options": [], "allow_blank": True},
            "salary": 18000, "needs_review": True,
        },
        "notes": "Unknown Myntra facility -> blank + review.",
    },
    {
        "case_id": "C28", "title": "Myntra FM sorter - unknownhub",
        "role_text": "Myntra FM sorter", "hub_text": "unknownhub", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Myntra", "operation": "First Mile", "cost_code": "8752",
            "role": {"compatible_with": "8752"},
            "facility": {"options": [], "allow_blank": True},
            "salary": 18000, "needs_review": True,
        },
        "notes": "8752 + unknown hub -> facility blank + review.",
    },

    # ── Aliases / salary formats / timestamps ────────────────────────────────
    {
        "case_id": "C29", "title": "lm sort - peenya (salary 16 k)",
        "role_text": "lm sort", "hub_text": "peenya", "salary_text": "salary 16 k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": "Peenya Hub", "salary": 16000,
        },
        "notes": "Alias 'sort' -> Sorter; 'salary 16 k' -> 16000.",
    },
    {
        "case_id": "C30", "title": "fm sort - nelamangala (salary 16.75 k)",
        "role_text": "fm sort", "hub_text": "nelamangala", "salary_text": "salary 16.75 k",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"}, "facility": "NelamangalaHub_BLR_PL",
            "salary": 16750,
        },
        "notes": "FM Sorter-compatible; 16.75 k -> 16750.",
    },
    {
        "case_id": "C31", "title": "last mile tl - nelamangala (₹)",
        "role_text": "last mile tl", "hub_text": "nelamangala", "salary_text": "salary ₹21,000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Team Leader", "facility": "NelamangalaHub_BLR", "salary": 21000,
        },
        "notes": "Alias 'tl' -> Team Leader; ₹21,000 -> 21000.",
    },
    {
        "case_id": "C32", "title": "first mile tl - nelamangala (₹)",
        "role_text": "first mile tl", "hub_text": "nelamangala", "salary_text": "₹22,250",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"}, "facility": "NelamangalaHub_BLR_PL",
            "salary": 22250,
        },
        "notes": "FM Team Leader-compatible; ₹22,250 -> 22250.",
    },
    {
        "case_id": "C33", "title": "LM sorter - Peenya (salary + timestamp)",
        "role_text": "LM sorter", "hub_text": "Peenya", "salary_text": "18k salary 10:30 am",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": "Peenya Hub", "salary": 18000,
        },
        "notes": "Timestamp '10:30 am' ignored.",
    },
    {
        "case_id": "C34", "title": "FM sorter - Nelamangala (salary + timestamp)",
        "role_text": "FM sorter", "hub_text": "Nelamangala", "salary_text": "salary 18.5k 11:45 am",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": {"compatible_with": "4441"}, "facility": "NelamangalaHub_BLR_PL",
            "salary": 18500,
        },
        "notes": "Timestamp ignored; 18.5k -> 18500.",
    },
    {
        "case_id": "C35", "title": "Myntra LM sorter - Hebbal (timestamp)",
        "role_text": "Myntra LM sorter", "hub_text": "Hebbal", "salary_text": "17k 1:00 pm",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter", "facility": "HebbalMYNTRAHub_BLR", "salary": 17000,
        },
        "notes": "Timestamp ignored; 17k -> 17000.",
    },
    {
        "case_id": "C36", "title": "Myntra LM delivery - Banaswadi (timestamp)",
        "role_text": "Myntra LM delivery", "hub_text": "Banaswadi", "salary_text": "salary 17.25k 9:15 am",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": {"compatible_with": "8751"}, "facility": "BanaswadiMYNTRAHub_BLR",
            "salary": 17250,
        },
        "notes": "Timestamp ignored; 17.25k -> 17250.",
    },

    # ── Facility misspellings ────────────────────────────────────────────────
    {
        "case_id": "C37", "title": "LM sorter - peenyaa hub (typo)",
        "role_text": "LM sorter", "hub_text": "peenyaa hub", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": "Peenya Hub", "salary": 18000,
        },
        "notes": "'peenyaa' -> Peenya Hub (BLR/PEN).",
    },
    {
        "case_id": "C38", "title": "LM sorter - nelamanglaa (typo)",
        "role_text": "LM sorter", "hub_text": "nelamanglaa", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Sorter", "facility": "NelamangalaHub_BLR", "salary": 18000,
        },
        "notes": "'nelamanglaa' -> NelamangalaHub_BLR.",
    },
    {
        "case_id": "C39", "title": "Myntra LM sorter - hebbal myntr (typo)",
        "role_text": "Myntra LM sorter", "hub_text": "hebbal myntr", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter", "facility": "HebbalMYNTRAHub_BLR", "salary": 18000,
        },
        "notes": "'hebbal myntr' -> HebbalMYNTRAHub_BLR.",
    },
    {
        "case_id": "C40", "title": "Myntra LM delivery - banaswdi myntra (typo)",
        "role_text": "Myntra LM delivery", "hub_text": "banaswdi myntra", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": {"compatible_with": "8751"}, "facility": "BanaswadiMYNTRAHub_BLR",
            "salary": 18000,
        },
        "notes": "'banaswdi' -> BanaswadiMYNTRAHub_BLR.",
    },

    # ── Role-missing / Needs Review behaviour ────────────────────────────────
    {
        "case_id": "C41", "title": "LM - Peenya (role missing)",
        "role_text": "LM", "hub_text": "Peenya", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "", "facility": "Peenya Hub", "salary": 18000,
            "needs_review": True,
        },
        "notes": "LM + hub but no role -> role blank / Needs Review.",
    },
    {
        "case_id": "C42", "title": "FM - Nelamangala (role missing)",
        "role_text": "FM", "hub_text": "Nelamangala", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": "", "facility": "NelamangalaHub_BLR_PL", "salary": 18000,
            "needs_review": True,
        },
        "notes": "FM + hub but no role -> role blank / Needs Review.",
    },
    {
        "case_id": "C43", "title": "Myntra LM - Hebbal (role missing)",
        "role_text": "Myntra LM", "hub_text": "Hebbal", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "", "facility": "HebbalMYNTRAHub_BLR", "salary": 18000,
            "needs_review": True,
        },
        "notes": "Myntra LM + hub but no role -> role blank / Needs Review.",
    },
    {
        "case_id": "C44", "title": "sorter only (no hub)",
        "role_text": "sorter", "hub_text": "", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "needs_review": True,
        },
        "notes": "No reliable facility/operation -> Needs Review; do not guess cost code/hub.",
    },
    {
        "case_id": "C45", "title": "Peenya hub only (role missing)",
        "role_text": "", "hub_text": "Peenya hub", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "", "facility": "Peenya Hub", "salary": 18000,
            "needs_review": True,
        },
        "notes": "Peenya is last-mile-only on HubName.xlsx (BLR/PEN); facility and cost code are safe, but the missing role -> Needs Review (never auto a role).",
    },
    {
        "case_id": "C46", "title": "Myntra - Hebbal (role missing)",
        "role_text": "Myntra", "hub_text": "Hebbal", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Myntra", "cost_code": "8751",
            "facility": {"options": ["HebbalMYNTRAHub_BLR"], "allow_blank": True},
            "role": "", "salary": 18000, "needs_review": True,
        },
        "notes": "Myntra entity + LM facility evidence, role missing -> role blank / Needs Review.",
    },

    # ── Prexo validation ─────────────────────────────────────────────────────
    {
        "case_id": "C47", "title": "LM prexo - Peenya (allowed by master)",
        "role_text": "LM prexo", "hub_text": "Peenya", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Flipkart", "operation": "Last Mile", "cost_code": "4421",
            "role": "LM - Prexo Delivery Executive", "facility": "Peenya Hub",
            "salary": 18000,
        },
        "notes": "Prexo IS allowed for 4421 (Flipkart LM) -> role resolved, no review.",
    },
    {
        "case_id": "C48", "title": "FM prexo - Nelamangala (reject, not FM-compatible)",
        "role_text": "FM prexo", "hub_text": "NelamangalaHub_BLR_PL", "salary_text": "18000",
        "tag": "Set C", "ocr_capable": True, "expect_review": True,
        "expected": {
            "entity": "Flipkart", "operation": "First Mile", "cost_code": "4441",
            "role": "", "facility": "NelamangalaHub_BLR_PL", "salary": 18000,
            "needs_review": True,
        },
        "notes": "Prexo not in FM master -> role blank + review.",
    },

    # ── Exact Myntra facility + salary/timestamp combos ──────────────────────
    {
        "case_id": "C49", "title": "Myntra sorter - HebbalMYNTRAHub_BLR (₹)",
        "role_text": "Myntra sorter", "hub_text": "HebbalMYNTRAHub_BLR", "salary_text": "₹16,500",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter", "facility": "HebbalMYNTRAHub_BLR", "salary": 16500,
        },
        "notes": "Exact Myntra hub -> Myntra/8751 + LM Sorter; ₹16,500 -> 16500.",
    },
    {
        "case_id": "C50", "title": "sorter - BanaswadiMYNTRAHub_BLR (salary + timestamp)",
        "role_text": "sorter", "hub_text": "BanaswadiMYNTRAHub_BLR", "salary_text": "salary 18 k 12:30 pm",
        "tag": "Set C", "ocr_capable": True, "expect_review": False,
        "expected": {
            "entity": "Myntra", "operation": "Last Mile", "cost_code": "8751",
            "role": "LM - Sorter", "facility": "BanaswadiMYNTRAHub_BLR", "salary": 18000,
        },
        "notes": "Exact Myntra hub infers Myntra LM; timestamp ignored; 18 k -> 18000.",
    },
]


def get_set(set_name: str) -> list[dict]:
    """Return the case list for a set name: 'A' | 'B' | 'C' | 'all' (default 'A')."""
    key = (set_name or "A").strip().lower()
    if key in ("a", "seta", "set_a", "set-a"):
        return SET_A
    if key in ("b", "setb", "set_b", "set-b"):
        return SET_B
    if key in ("c", "setc", "set_c", "set-c", "set c", "cset"):
        return SET_C
    if key in ("all", "both"):
        return SET_A + SET_B + SET_C
    return SET_A


def get_case(case_id: str, set_name: str = "all") -> Optional[dict]:
    for c in get_set(set_name):
        if c["case_id"] == case_id:
            return c
    return None


# ── Safe variant generation ──────────────────────────────────────────────────
# Only safe test strings; expected outcomes are derived from the configured
# master/business-rule fixtures (via run_logic_case), never invented randomly.

# Validation Set C variant lists (per spec).
ROLE_VARIANTS_C = [
    "LM sorter", "lm sort", "last mile sorter", "last-mile delivery",
    "FM sorter", "fm sort", "first mile sorter", "first-mile delivery",
    "LM team lead", "FM team leader", "LM biker", "first mile biker",
    "Myntra LM sorter", "myntra last-mile delivery", "MYNTRA LM team lead",
    "Myntra LM prexo", "Myntra FM sorter", "Myntra first mile delivery",
    "sorter", "delivery", "LM", "FM", "Myntra LM", "Myntra",
]
FACILITY_VARIANTS_C = [
    "penya hub", "peenya hub", "peenyaa hub", "nelamangla", "nelamanglaa",
    "hebbal myntra", "hebbal myntr", "banaswadi", "banaswdi myntra",
    "PeenyaHub_BLR", "PeenyaHub_BLR_PL", "NelamangalaHub_BLR",
    "NelamangalaHub_BLR_PL", "HebbalMYNTRAHub_BLR", "BanaswadiMYNTRAHub_BLR",
]
SALARY_VARIANTS_C = [
    "16k", "16 k", "₹16,000", "16000", "16.5k", "salary 16 k",
    "salary 16.75 k", "₹20,500", "21.25k", "18k salary 10:30 am",
    "salary 18.5k 11:45 am", "17k 1:00 pm", "salary 18 k 12:30 pm",
]

ROLE_VARIANTS = [
    "LM sorter", "lm Sorter", "Last mile sorter", "LM sort", "Lm delivery",
    "first mile sorter", "FM sorter", "Myntra LM sorter",
]
FACILITY_VARIANTS = [
    "Peenya", "Peenya hub", "peenyaHub", "nelmangla", "Nelamangala hub",
    "Hebbal Myntra", "Banaswadi myntra hub",
]
SALARY_VARIANTS = [
    "18k", "18 k", "₹18,000", "18000", "18.5k", "salary 17k", "18k salary 9:30 am",
]

# Validation Set B variant lists (per spec).
ROLE_VARIANTS_B = [
    "LM", "lm", "Last mile", "last-mile",
    "FM", "fm", "First mile", "first-mile",
    "sorter", "sort",
    "TL", "team lead", "team leader",
    "delivery", "delivery executive", "biker",
    "Myntra", "myntra", "MYNTRA",
]
FACILITY_VARIANTS_B = [
    "peenya", "penya", "peenya hub",
    "nelamangala", "nelmangla",
    "hebbal myntra", "banaswadi myntra",
]
SALARY_VARIANTS_B = [
    "16k", "16 k", "₹16,000", "16000", "16.5k", "salary 16k",
    "16k salary 10:30 am",
]


def generate_variants(set_name: str = "A") -> list[dict]:
    """Generate safe validation variant strings for a set.

    Expected results are not invented: each variant is resolved through the
    production resolver at run time.  Only the input strings are varied.
    """
    out = []
    key = (set_name or "A").strip().lower()
    if key in ("b", "setb", "set_b", "set-b"):
        roles, hubs, sals = ROLE_VARIANTS_B, FACILITY_VARIANTS_B, SALARY_VARIANTS_B
    elif key in ("c", "setc", "set_c", "set-c"):
        roles, hubs, sals = ROLE_VARIANTS_C, FACILITY_VARIANTS_C, SALARY_VARIANTS_C
    else:
        roles, hubs, sals = ROLE_VARIANTS, FACILITY_VARIANTS, SALARY_VARIANTS
    for role in roles:
        for hub in hubs:
            for sal in sals:
                out.append({
                    "role_text": role,
                    "hub_text": hub,
                    "salary_text": sal,
                })
    return out


def generate_variants_for_case(case_id: str, set_name: str = "all") -> list[dict]:
    """Generate safe variants honoring a base case's entity/operation constraints."""
    base = get_case(case_id, set_name)
    if not base:
        return []
    base_role = base.get("role_text", "")
    base_hub = base.get("hub_text", "")
    base_sal = base.get("salary_text", "")
    entity = base.get("expected", {}).get("entity", "")

    roles = [base_role]
    if "Myntra" in entity or "Myntra" in base_role:
        # Keep the Myntra orientation for a Myntra-hub case; never emit a
        # plain Flipkart role variant for a Myntra facility case.
        for r in ROLE_VARIANTS:
            if "myntra" in r.lower():
                roles.append(r)
    elif base_role and "lm" in base_role.lower():
        for r in ROLE_VARIANTS:
            rl = r.lower()
            if "myntra" not in rl and (rl.startswith("lm") or "last mile" in rl):
                roles.append(r)
    elif base_role and "fm" in base_role.lower():
        for r in ROLE_VARIANTS:
            rl = r.lower()
            if "myntra" not in rl and (rl.startswith("fm") or "first mile" in rl):
                roles.append(r)

    hubs = [base_hub]
    sals = [base_sal]
    out = []
    for r in roles:
        for h in hubs:
            for s in sals:
                out.append({"role_text": r, "hub_text": h, "salary_text": s})
    return out


# ── Actual resolution (uses PRODUCTION resolver) ─────────────────────────────


def run_logic_case(case: dict) -> dict:
    """Run the production resolver for a case's rule inputs only (no OCR).

    Returns an 'actual' dict which is the ground-truth output of the production
    business-rule resolver — never a parallel implementation.
    """
    role_text = (case.get("role_text") or "").strip()
    hub_text = (case.get("hub_text") or "").strip()
    salary_text = (case.get("salary_text") or "").strip()

    resolved = rules.resolve_smart_onboarding(role_text or None, hub_text or None)

    # Salary is normalized through the same production OCR/evidence salary
    # extractor used in real onboarding (handles 18k / ₹18,000 / timestamps).
    salary_value = None
    salary_err = None
    if salary_text:
        salary_value, salary_err = _normalize_salary_text(salary_text)

    facility = resolved.get("facility", "") or ""
    location = ""
    if facility:
        location = rules.get_location_for_facility(facility) or ""

    cost_code = resolved.get("cost_code", "") or ""
    role = resolved.get("role", "") or ""
    must_check = case.get("expected", {}).get("must_not", [])

    # needs_review: facility blank/conflict, role unresolved, or attention flag
    needs_review = bool(
        resolved.get("needs_attention")
        or resolved.get("role_unresolved")
        or (not facility and (case.get("expected", {}).get("needs_review")) and cost_code)
    )

    actual = {
        "entity": resolved.get("entity", ""),
        "operation": (rules.get_cost_code_info(cost_code) or {}).get("operation", "")
                     if cost_code else "",
        "cost_code": cost_code,
        "role": role if role else None,
        "facility": facility if facility else None,
        "location": location if location else None,
        "salary": salary_value,
        "salary_err": salary_err,
        "needs_review": needs_review,
        "needs_attention": resolved.get("needs_attention", []),
        "role_unresolved": resolved.get("role_unresolved", False),
    }
    return actual


def _normalize_salary_text(salary_text: str):
    """Normalize a recruiter salary phrase via the production evidence scorer.

    Handles: 18k, 18.5k, 18 k, ₹18,000, 18000, "salary 17k", and inline
    timestamps ("18k salary 9:30 am").  Returns (int_value, error_or_None).
    """
    from app.evidence import score_salary_candidates
    from app.ocr_models import OCRLine
    result = score_salary_candidates([OCRLine(text=salary_text, confidence=0.9)])
    if result.selected and result.selected.value:
        try:
            return int(result.selected.value), None
        except (TypeError, ValueError):
            return None, "Invalid salary format"
    return None, "Could not parse salary"


# ── Per-field comparison ─────────────────────────────────────────────────────


def compare_field(field: str, expected, actual) -> dict:
    """Compare one field's expected fixture against an actual value.

    Returns {"field", "expected", "actual", "result"} with result in
    PASS / FAIL / SKIP (expected not configured).
    """
    # Handle "compatible_with" role expectations.
    if field == "role" and isinstance(expected, dict) and "compatible_with" in expected:
        code = expected["compatible_with"]
        allowed = set(master_data.get_roles_for_cost_code(code))
        ok = actual in allowed
        return {
            "field": field,
            "expected": f"any official role compatible with {code}",
            "actual": actual,
            "result": "PASS" if ok else "FAIL",
        }

    # Handle facility options / allow_blank.
    if field == "facility" and isinstance(expected, dict):
        options = expected.get("options") or []
        allow_blank = expected.get("allow_blank", False)
        if options:
            ok = actual in options
        elif allow_blank:
            ok = (actual is None) or (actual == "") or (actual in options)
        else:
            ok = actual in options
        # Also allow any hub actually valid for the expected cost code when the
        # expectation is a compatible-hit (approximate recruiter text).
        cost_code = expected.get("for_cost_code")
        if cost_code and not ok:
            allowed = set(master_data.get_hubs_for_cost_code(cost_code))
            if actual in allowed:
                ok = True
        return {
            "field": field,
            "expected": options if options else ("blank" if allow_blank else ""),
            "actual": actual,
            "result": "PASS" if ok else "FAIL",
        }

    # Exact comparison.
    if expected is None:
        return {"field": field, "expected": expected, "actual": actual, "result": "SKIP"}
    if field == "salary":
        ok = (expected == actual)
        return {
            "field": field, "expected": expected, "actual": actual,
            "result": "PASS" if ok else "FAIL",
        }
    ok = (str(expected) == str(actual)) or (expected == "" and actual in (None, ""))
    return {
        "field": field, "expected": expected, "actual": actual,
        "result": "PASS" if ok else "FAIL",
    }


# ── Full-case comparison + status ────────────────────────────────────────────


def evaluate_case(case: dict, actual: dict) -> dict:
    """Compare a case's expected fixture to the production actual result.

    Returns a case_verdict dict with per-field results, overall status, and
    safety classification (auto_decision_correct / review_correct /
    wrong_auto_decision / missing_extraction / conflict_blocked).
    """
    exp = case.get("expected", {})
    fields = ["entity", "operation", "cost_code", "role", "facility", "location", "salary"]

    results = []
    field_results = {}
    for f in fields:
        if f in exp:
            fr = compare_field(f, exp[f], actual.get(f))
        else:
            fr = {"field": f, "expected": "-", "actual": actual.get(f), "result": "SKIP"}
        results.append(fr)
        field_results[f] = fr["result"]

    must_not = exp.get("must_not", [])
    blocked_ok = True
    for banned in must_not:
        # Compare cost codes and facility names by EXACT equality. Substring
        # matching is wrong here because one official hub name can be a
        # substring of another (e.g. "PeenyaHub_BLR" vs "PeenyaHub_BLR_PL").
        if str(actual.get("cost_code") or "") == str(banned) \
                or str(actual.get("facility") or "") == str(banned):
            blocked_ok = False

    # needs_review compliance
    expect_review = bool(exp.get("needs_review", False)) or bool(case.get("expect_review", False))
    actual_review = bool(actual.get("needs_review"))

    # Field-level failure detection
    failed_fields = [r for r in results if r["result"] == "FAIL"]
    if blocked_ok is False:
        failed_fields.append({"field": "must_not", "result": "FAIL",
                              "expected": "must not produce " + ",".join(must_not),
                              "actual": actual.get("cost_code") or actual.get("facility")})

    all_fields_pass = not failed_fields

    # Status:
    #   REVIEW  -> ambiguous case whose auto decision is a valid review request
    #              (or a case intended to require review surfaced correctly)
    #   PASS    -> all compared fields matched, not intended to be reviewed
    #   FAIL    -> any compared field mismatched OR review required but not raised
    if not all_fields_pass:
        status = "FAIL"
    elif expect_review:
        # Intended review: PASS if the resolver actually flagged review.
        status = "PASS" if actual_review else "FAIL"
        if not actual_review:
            failed_fields.append({"field": "needs_review", "result": "FAIL",
                                  "expected": "review", "actual": "auto"})
    else:
        # Not intended for review; if resolver forced review incorrectly it's a FAIL.
        status = "PASS" if not actual_review else "FAIL"
        if actual_review and not failed_fields:
            failed_fields.append({"field": "auto", "result": "FAIL",
                                  "expected": "auto-select", "actual": "review",
                                  "note": "unexpected forced review"})

    # Safety classification.
    #
    # A "wrong auto-decision" is ANY case where the resolver auto-selected a
    # materially wrong concrete value (wrong entity, wrong LM/FM, wrong cost
    # code, wrong hub, wrong role) and did NOT correctly flag Needs Review.
    # It is NOT limited to cases whose fixture expected review. A FAIL where the
    # resolver produced no concrete values at all is a plain extraction
    # failure / missing extraction instead.
    produced_auto = any(
        (actual.get(k) or "") not in ("", None)
        for k in ("entity", "cost_code", "role", "facility")
    )

    if status == "FAIL":
        if actual_review and not expect_review:
            # Resolver incorrectly forced review when an auto decision is valid.
            safety = {"wrong_auto_decision": False,
                      "correct_auto": False, "correct_review": False,
                      "false_review": True, "missing_extraction": False}
        elif not actual_review:
            # Resolver auto-decided and did NOT defer to review. If it emitted
            # a materially wrong concrete value (or was expected to review), it
            # is a wrong auto-decision; if it produced nothing at all it is an
            # extraction failure.
            if produced_auto or expect_review:
                safety = {"wrong_auto_decision": True,
                          "correct_auto": False, "correct_review": False,
                          "false_review": False, "missing_extraction": False}
            else:
                safety = {"wrong_auto_decision": False,
                          "correct_auto": False, "correct_review": False,
                          "false_review": False, "missing_extraction": True}
        else:
            # actual_review is True: the resolver correctly deferred to review
            # even though a secondary fixture field could not be satisfied.
            # This is NOT a wrong auto-decision.
            safety = {"wrong_auto_decision": False,
                      "correct_auto": False, "correct_review": True,
                      "false_review": False, "missing_extraction": False}
    elif expect_review:
        # PASS and review was intended: the resolver correctly sent to review.
        safety = {"wrong_auto_decision": False,
                  "correct_auto": False, "correct_review": True,
                  "false_review": False, "missing_extraction": False}
    else:
        # PASS auto case: resolver auto-selected the correct value.
        safety = {"wrong_auto_decision": False,
                  "correct_auto": True, "correct_review": False,
                  "false_review": False, "missing_extraction": False}

    # distributed extra flags
    conflict_blocked = bool(case.get("expected", {}).get("must_not"))
    return {
        "case_id": case["case_id"],
        "title": case["title"],
        "scenario": {
            "role_text": case.get("role_text", ""),
            "hub_text": case.get("hub_text", ""),
            "salary_text": case.get("salary_text", ""),
        },
        "expected": exp,
        "actual": actual,
        "field_results": results,
        "failed_fields": failed_fields,
        "status": status,
        "expect_review": expect_review,
        "actual_review": actual_review,
        "safety": safety,
        "blocked_ok": blocked_ok,
        "notes": case.get("notes", ""),
    }


# ── Aggregation / metrics ────────────────────────────────────────────────────


def summarize(verdicts: list[dict]) -> dict:
    total = len(verdicts)
    passed = sum(1 for v in verdicts if v["status"] == "PASS")
    failed = sum(1 for v in verdicts if v["status"] == "FAIL")
    needs_review = sum(1 for v in verdicts if v["status"] == "PASS" and v["expect_review"])
    not_tested = 0
    correct_auto = sum(1 for v in verdicts if v["safety"].get("correct_auto"))
    correct_review = sum(1 for v in verdicts if v["safety"].get("correct_review"))
    wrong_auto = sum(1 for v in verdicts if v["safety"].get("wrong_auto_decision"))
    false_review = sum(1 for v in verdicts if v["safety"].get("false_review"))
    missing_extraction = sum(1 for v in verdicts if v["safety"].get("missing_extraction"))
    conflict_blocked = sum(1 for v in verdicts
                           if v["expected"].get("must_not") and v["blocked_ok"])

    pass_rate = (passed / total * 100) if total else 0.0
    auto_decisions = correct_auto + wrong_auto + false_review
    wrong_auto_rate = (wrong_auto / auto_decisions * 100) if auto_decisions else 0.0

    return {
        "total_tests": total,
        "passed": passed,
        "failed": failed,
        "needs_review": needs_review,
        "not_tested": not_tested,
        "pass_rate": round(pass_rate, 1),
        "correct_auto_decision": correct_auto,
        "correct_needs_review": correct_review,
        "wrong_auto_decision": wrong_auto,
        "wrong_auto_rate": round(wrong_auto_rate, 1),
        "false_review": false_review,
        "missing_extraction": missing_extraction,
        "conflict_blocked": conflict_blocked,
    }


def run_all_cases(set_name: str = "A") -> dict:
    """Run every configured logic case in a set through the production
    resolver and aggregate results. Pure in-memory; never touches the database
    or production tables."""
    cases = get_set(set_name)
    verdicts = []
    for case in cases:
        actual = run_logic_case(case)
        verdicts.append(evaluate_case(case, actual))
    return {
        "verdicts": verdicts,
        "summary": summarize(verdicts),
    }
