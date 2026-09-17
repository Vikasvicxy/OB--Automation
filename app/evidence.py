"""Evidence scoring and candidate ranking for OCR field extraction.

Generates multiple candidates per field, scores them using spatial/semantic
evidence signals, and applies a confidence margin before auto-selecting.
Never replaces business rules (rules.py); integrates with them as hard filters.

Privacy:
  - No full Aadhaar or full addresses are stored in candidate reasons.
  - Bounding-box references are internal only, never exposed in UI payloads.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

from app.ocr_models import OCRLine

# ── Thresholds (configurable) ────────────────────────────────────────────────

# Minimum score for a name candidate to be considered at all
NAME_MIN_SCORE = 40
# Minimum score for auto-selection
NAME_AUTO_SELECT_MIN = 70
# Minimum lead over second-best candidate to auto-select
NAME_MIN_MARGIN = 20

# Address
ADDRESS_MIN_SCORE = 30
ADDRESS_AUTO_SELECT_MIN = 60
ADDRESS_MIN_MARGIN = 15

# Salary
SALARY_MIN_SCORE = 50
SALARY_AUTO_SELECT_MIN = 65
SALARY_MIN_MARGIN = 15

# Facility
FACILITY_MIN_SCORE = 50
FACILITY_AUTO_SELECT_MIN = 70
FACILITY_MIN_MARGIN = 15

# Role
ROLE_MIN_SCORE = 50
ROLE_AUTO_SELECT_MIN = 70
ROLE_MIN_MARGIN = 15

# DOB
DOB_AUTO_SELECT_MIN = 80
DOB_MIN_MARGIN = 20

# Aadhaar
AADHAAR_AUTO_SELECT_MIN = 90
AADHAAR_MIN_MARGIN = 15


# ── Confidence levels ────────────────────────────────────────────────────────


def confidence_level(top_score: float, runner_up_score: float,
                     auto_min: float, min_margin: float) -> str:
    """Determine High / Review / Missing / Conflict from scores.

    High:     top >= auto_min AND margin >= min_margin
    Review:   top >= auto_min but margin too narrow, or valid but weak
    Missing:  no acceptable candidate
    Conflict: hard business-rule conflict
    """
    if top_score < 1.0:
        return "Missing"
    margin = top_score - runner_up_score
    if top_score >= auto_min and margin >= min_margin:
        return "High"
    if top_score >= auto_min * 0.7:
        return "Review"
    return "Review"


def conflict_level() -> str:
    """Hard business-rule conflict — can never be auto-selected."""
    return "Conflict"


# ── FieldCandidate model ─────────────────────────────────────────────────────


@dataclass
class FieldCandidate:
    """A scored candidate for a single extraction field.

    ``reasons`` contains safe, human-readable evidence strings.
    ``box_refs`` stores bounding-box tuples (internal only, never exposed).
    """
    field: str
    value: str
    score: float = 0.0
    confidence: str = "Missing"  # High / Review / Missing / Conflict
    source: str = "OCR"
    reasons: list[str] = field(default_factory=list)
    rejected_reason: str = ""
    box_refs: list[tuple[float, float, float, float]] = field(default_factory=list)
    normalized_value: str = ""
    is_rejected: bool = False


@dataclass
class FieldResult:
    """Final scoring result for a field: selected candidate + runner-up + reasons."""
    field: str
    selected: Optional[FieldCandidate] = None
    runner_up: Optional[FieldCandidate] = None
    all_candidates: list[FieldCandidate] = field(default_factory=list)
    confidence: str = "Missing"
    margin: float = 0.0
    needs_review: bool = True


# ── Rejected name tokens (shared with ocr.py) ────────────────────────────────

_REJECTED_NAME_TOKENS: set[str] = {
    "male", "female",
    "dob", "date of birth", "year of birth",
    "government of india",
    "unique identification authority of india",
    "uidai",
    "aadhaar", "aadhar",
    "vid", "virtual id",
    "address", "address:",
    "pin", "pincode", "pin code",
    "india",
    "republic of india",
    "religion", "blood group",
    "father", "mother", "husband",
    "i humbly declare", "i hereby declare",
    "signature", "sign",
    "enrolment", "enrollment", "enrolment id",
}

_REJECTED_NAME_FRAGMENTS: list[str] = [
    "governmentofindia",
    "uniqueidentificationauthority",
    "uidai",
    "aadhaar",
    "aadhar",
    "virtualid",
    "yearofbirth",
    "dateofbirth",
    "dob",
    "address",
    "enrolment",
    "enrollment",
    "republicofindia",
    "pincode",
    "signature",
]

_RE_GENDER_LINE = re.compile(r"^\s*(male|female)\s*$", re.IGNORECASE)


def _contains_rejected_fragment(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    compact = re.sub(r"[^a-z0-9]", "", compact)
    for frag in _REJECTED_NAME_FRAGMENTS:
        if frag in compact:
            return True
    return False


def _is_valid_name_token(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if _RE_GENDER_LINE.match(stripped):
        return False
    if _contains_rejected_fragment(stripped):
        return False
    if lower in _REJECTED_NAME_TOKENS:
        return False
    words = stripped.split()
    for label in _REJECTED_NAME_TOKENS:
        if " " in label:
            if lower == label:
                return False
            continue
        if label in words:
            return False
    if all(t.isdigit() for t in words):
        return False
    digit_count = sum(1 for c in stripped if c.isdigit())
    if digit_count > len(stripped) * 0.4:
        return False
    if not any(c.isalpha() for c in stripped):
        return False
    if len(words) > 6:
        return False
    return True


def _looks_like_person_name(text: str) -> bool:
    stripped = text.strip()
    if not stripped or not _is_valid_name_token(stripped):
        return False
    tokens = stripped.split()
    alpha_ratio = sum(1 for c in stripped if c.isalpha()) / max(len(stripped), 1)
    return alpha_ratio >= 0.7 and 1 <= len(tokens) <= 5


def _repair_run_together(name: str) -> str:
    name = name.strip()
    if " " in name:
        return re.sub(r"\s+", " ", name).strip()
    if not name or name == name.upper():
        return name
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]", name)
    joined = " ".join(words).strip()
    return joined if joined else name


# ── NAME candidate scoring ───────────────────────────────────────────────────


def score_name_candidates(lines: list[OCRLine]) -> FieldResult:
    """Generate and score name candidates from OCR lines.

    Uses spatial proximity to DOB/gender, person-name heuristics,
    rejected-token filtering, and OCR confidence.
    """
    result = FieldResult(field="name")
    if not lines:
        return result

    doc_height = max((l.y2 for l in lines), default=1.0) or 1.0
    doc_width = max((l.x2 for l in lines), default=1.0) or 1.0

    # Locate DOB/gender reference lines
    dob_line: Optional[OCRLine] = None
    gender_line: Optional[OCRLine] = None
    for line in lines:
        low = line.text.strip().lower()
        if dob_line is None and ("dob" in low or "date of birth" in low or "birth" in low):
            dob_line = line
        if dob_line is None and re.search(r"\d{2}[/\-]\d{2}[/\-]\d{4}", line.text):
            dob_line = line
        if gender_line is None and _RE_GENDER_LINE.match(line.text.strip()):
            gender_line = line

    # Upper region: top 40% of document
    upper_threshold = doc_height * 0.4

    candidates: list[FieldCandidate] = []

    for i, line in enumerate(lines):
        text = line.text.strip()
        if not text or len(text) < 2:
            continue

        score = 0.0
        reasons: list[str] = []
        rejected = False
        rejected_reason = ""

        # ── Rejected token check ─────────────────────────────────────────
        low = text.lower()
        if low in _REJECTED_NAME_TOKENS or _contains_rejected_fragment(text):
            rejected = True
            rejected_reason = "Document label / rejected token"
        elif _RE_GENDER_LINE.match(text):
            rejected = True
            rejected_reason = "Gender identifier"
        elif not _is_valid_name_token(text):
            rejected = True
            rejected_reason = "Not a valid name pattern"
        elif all(c.isdigit() or c in " -/" for c in text):
            rejected = True
            rejected_reason = "Numeric-only line"

        if rejected:
            candidates.append(FieldCandidate(
                field="name", value=text, score=-100.0,
                confidence="Missing", source="OCR",
                reasons=[rejected_reason],
                rejected_reason=rejected_reason,
                box_refs=[(line.x1, line.y1, line.x2, line.y2)],
                is_rejected=True,
            ))
            continue

        # ── Positive signals ─────────────────────────────────────────────

        # Person-like text pattern
        if _looks_like_person_name(text):
            score += 25
            reasons.append("Person-like text pattern")

        # 2-5 alphabetic tokens (typical Aadhaar name)
        tokens = text.split()
        if 2 <= len(tokens) <= 5:
            score += 10
            reasons.append(f"{len(tokens)} tokens (typical name length)")

        # Immediately above DOB line
        if dob_line is not None:
            gap = abs(line.center_y - dob_line.y1)
            relative_gap = gap / doc_height
            if relative_gap < 0.05 and line.center_y < dob_line.center_y:
                score += 30
                reasons.append("Immediately above DOB")
            elif relative_gap < 0.15 and line.center_y < dob_line.center_y:
                score += 15
                reasons.append("Near DOB line")

        # Near gender line
        if gender_line is not None:
            gap = abs(line.center_y - gender_line.center_y)
            relative_gap = gap / doc_height
            if relative_gap < 0.05:
                score += 15
                reasons.append("Near gender line")

        # In identity region (upper part of document, roughly centered)
        if line.y1 < upper_threshold:
            score += 10
            reasons.append("Upper document region")
        if 0.1 * doc_width < line.center_x < 0.9 * doc_width:
            score += 5
            reasons.append("Centered horizontal position")

        # OCR confidence
        if line.confidence >= 0.9:
            score += 10
            reasons.append(f"OCR confidence {line.confidence:.0%}")
        elif line.confidence >= 0.7:
            score += 5
            reasons.append(f"OCR confidence {line.confidence:.0%}")
        elif line.confidence < 0.5:
            score -= 10
            reasons.append(f"Low OCR confidence {line.confidence:.0%}")

        # ── Negative signals ─────────────────────────────────────────────
        # "To" only
        if low.strip(":.,!") == "to":
            score -= 100
            rejected = True
            rejected_reason = "Document label 'To'"
            reasons.append(rejected_reason)

        # Punctuation-heavy heading
        alpha_count = sum(1 for c in text if c.isalpha())
        if alpha_count < len(text) * 0.5 and len(text) > 5:
            score -= 15
            reasons.append("Punctuation/special-char heavy")

        # Too short (single char)
        if len(text) <= 2:
            score -= 20
            reasons.append("Too short")

        # ── Normalized value ─────────────────────────────────────────────
        normalized = _repair_run_together(text)

        candidates.append(FieldCandidate(
            field="name", value=normalized, score=score,
            source="OCR", reasons=reasons,
            box_refs=[(line.x1, line.y1, line.x2, line.y2)],
            normalized_value=normalized,
            is_rejected=rejected,
            rejected_reason=rejected_reason,
        ))

    return _select_best(candidates, "name",
                        NAME_AUTO_SELECT_MIN, NAME_MIN_MARGIN)


# ── DOB candidate scoring ────────────────────────────────────────────────────


_DOB_ISSUE_TOKENS = ("issue", "issued", "enrol", "enroll", "update", "signature")


def score_dob_candidates(lines: list[OCRLine]) -> FieldResult:
    """Score DOB candidates from OCR lines.

    Explicit DOB / Date of Birth labels are strongly preferred; dates on
    issue/enrolment/update/signature lines are rejected so an Aadhaar issue date
    ("14/10/2013") is NEVER reported as the candidate's DOB.
    """
    result = FieldResult(field="dob")
    if not lines:
        return result

    doc_height = max((l.y2 for l in lines), default=1.0) or 1.0
    candidates: list[FieldCandidate] = []

    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        low = text.lower()
        if any(tok in low for tok in _DOB_ISSUE_TOKENS):
            continue

        # DD/MM/YYYY or DD-MM-YYYY pattern
        m = re.search(r"(\d{2})[/\-](\d{2})[/\-](\d{4})", text)
        if m:
            dob_val = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
            score = 80.0
            reasons = ["Date pattern DD/MM/YYYY found"]

            # Near DOB label
            if "dob" in low or "date of birth" in low:
                score += 20
                reasons.append("DOB label present")
            if "birth" in low:
                score += 10
                reasons.append("Birth-related label present")

            if line.confidence >= 0.8:
                score += 5
                reasons.append(f"OCR confidence {line.confidence:.0%}")

            candidates.append(FieldCandidate(
                field="dob", value=dob_val, score=score,
                source="OCR", reasons=reasons,
                box_refs=[(line.x1, line.y1, line.x2, line.y2)],
                normalized_value=dob_val,
            ))

    return _select_best(candidates, "dob",
                        DOB_AUTO_SELECT_MIN, DOB_MIN_MARGIN)


# ── AADHAAR candidate scoring ────────────────────────────────────────────────


def score_aadhaar_candidates(lines: list[OCRLine]) -> FieldResult:
    """Score Aadhaar number candidates from OCR lines."""
    result = FieldResult(field="aadhaar_number")
    if not lines:
        return result

    candidates: list[FieldCandidate] = []

    for line in lines:
        text = line.text.strip()
        if not text:
            continue

        digits = re.sub(r"\D", "", text)
        m = re.search(r"\d{4}[\s]?\d{4}[\s]?\d{4}", text)
        if m and len(digits) >= 12:
            core = re.sub(r"\D", "", m.group(0))
            if len(core) == 12:
                score = 95.0
                reasons = ["12-digit Aadhaar pattern found"]
                if line.confidence >= 0.8:
                    score += 3
                    reasons.append(f"OCR confidence {line.confidence:.0%}")
                candidates.append(FieldCandidate(
                    field="aadhaar_number", value=core, score=score,
                    source="OCR", reasons=reasons,
                    box_refs=[(line.x1, line.y1, line.x2, line.y2)],
                    normalized_value=core,
                ))

    return _select_best(candidates, "aadhaar_number",
                        AADHAAR_AUTO_SELECT_MIN, AADHAAR_MIN_MARGIN)


# ── ADDRESS candidate scoring ────────────────────────────────────────────────

_ADDRESS_ANCHORS = {
    "c/o", "s/o", "d/o", "w/o", "h/o",
    "vtc", "po", "post",
    "dist", "district", "dist.",
    "state", "pin", "pincode", "pin code",
    "taluk", "tq", "mandal",
    "village", "hno", "house no", "street", "road", "colony", "nagar",
}

_ADDRESS_TERMINATORS = {
    "government", "uidai", "unique identification", "aadhaar", "vid",
    "virtual id", "dob", "date of birth", "year of birth", "gender",
    "male", "female", "enrolment", "enrollment", "signature",
    "i humbly declare", "i hereby declare",
    "issue", "issued", "issued date", "update", "updated",
}

# Contact/identity line prefixes (e.g. "Mobile: 9398969253") that belong to a
# screen footer, never the postal address.
_ADDRESS_TRAILING_CONTACT = re.compile(r"(mobile|mob|phone|tel|whatsapp|whatapp)"
                                       r"[\s:.\-]*(?:91[-\s]?)?\d{5,}", re.IGNORECASE)

# A line that is ENTIRELY the Aadhaar number (4-4-4) or ENTIRELY a standalone
# date/issue-date. These always terminate the postal address block.
_ADDRESS_ENDING_NUMERIC = re.compile(
    r"^\s*(\d{4}[\s]?\d{4}[\s]?\d{4}|\d{2}[/\-]\d{2}[/\-]\d{4})\s*$")


def _line_has_anchor(text: str) -> bool:
    low = text.strip().lower()
    for t in re.split(r"[\s:/,.\-_]+", low):
        t = t.strip(".,:;-")
        if t in _ADDRESS_ANCHORS:
            return True
        if t in {"c", "s", "d", "w"} and "/o" in low:
            return True
    return any(a in low for a in _ADDRESS_ANCHORS if " " in a)


def _is_pin_code(text: str) -> bool:
    return bool(re.search(r"\b\d{6}\b", text))


def _is_state_line(text: str) -> bool:
    low = text.lower()
    states = ["karnataka", "maharashtra", "tamil nadu", "andhra pradesh",
              "telangana", "kerala", "delhi", "uttar pradesh", "rajasthan",
              "madhya pradesh", "west bengal", "gujarat", "punjab", "haryana",
              "odisha", "bihar", "jharkhand", "chhattisgarh", "assam"]
    return any(s in low for s in states)


def score_address_candidates(lines: list[OCRLine]) -> FieldResult:
    """Score address region candidates from OCR lines.

    Generates address blocks from anchor points and scores them by
    continuity, anchor density, PIN/state presence, and line count.
    """
    result = FieldResult(field="address")
    if not lines:
        return result

    doc_height = max((l.y2 for l in lines), default=1.0) or 1.0
    avg_height = sum(l.height for l in lines) / max(len(lines), 1)

    candidates: list[FieldCandidate] = []

    # Find address start candidates
    start_indices: list[int] = []
    for i, line in enumerate(lines):
        low = line.text.strip().lower()
        if low.startswith("address"):
            start_indices.append(i)
            continue
        if _line_has_anchor(line.text):
            start_indices.append(i)

    for start_idx in start_indices:
        # Collect contiguous lines
        block_lines: list[OCRLine] = []
        score = 0.0
        reasons: list[str] = []

        # Address label bonus
        low_start = lines[start_idx].text.strip().lower()
        if low_start.startswith("address"):
            score += 15
            reasons.append("Address label found")

        # Anchor detection on start line
        if _line_has_anchor(lines[start_idx].text):
            score += 10
            reasons.append("Address anchor on start line")

        # C/O / S/O / D/O relation marker
        if re.search(r"\b[csdwh]/o\b", lines[start_idx].text.lower()):
            score += 15
            reasons.append("Relation marker (C/O, S/O, etc.)")

        for j in range(start_idx, min(start_idx + 10, len(lines))):
            line = lines[j]
            low = line.text.strip().lower()

            # Stop at terminators
            if any(t in low for t in _ADDRESS_TERMINATORS):
                break
            if _ADDRESS_ENDING_NUMERIC.match(line.text.strip()):
                break
            if not line.text.strip():
                if block_lines:
                    break
                continue

            block_lines.append(line)

            # PIN code bonus
            if _is_pin_code(line.text):
                score += 20
                reasons.append("PIN code found")

            # State/district line
            if _is_state_line(line.text):
                score += 15
                reasons.append("State name found")

            # District/taluk keywords
            if re.search(r"\b(dist|taluk|tq|mandal)\b", low):
                score += 10
                reasons.append("District/taluk keyword found")

            # Address anchor in middle lines
            if _line_has_anchor(line.text) and j > start_idx:
                score += 5
                reasons.append("Address anchor in continuation")

        # Block quality scoring
        if len(block_lines) >= 2:
            score += 10
            reasons.append(f"{len(block_lines)}-line address block")
        if len(block_lines) >= 4:
            score += 5
            reasons.append("Multi-line address (4+)")

        # Continuity: average vertical gap between lines
        if len(block_lines) > 1:
            gaps = [block_lines[i + 1].y1 - block_lines[i].y2
                    for i in range(len(block_lines) - 1)]
            avg_gap = sum(gaps) / len(gaps)
            if avg_gap < avg_height * 0.5:
                score += 10
                reasons.append("Tight vertical continuity")
            elif avg_gap > avg_height * 2.5:
                score -= 10
                reasons.append("Large gap in address block")

        if not block_lines:
            continue

        # Rejected: only label, no content
        if len(block_lines) == 1 and len(block_lines[0].text.strip()) <= 8:
            candidates.append(FieldCandidate(
                field="address", value=block_lines[0].text.strip(),
                score=-100.0, confidence="Missing", source="OCR",
                reasons=["Single label line, no address content"],
                rejected_reason="Address-label only",
                box_refs=[(l.x1, l.y1, l.x2, l.y2) for l in block_lines],
                is_rejected=True,
            ))
            continue

        # Build address text (dropping trailing contact/mobile footer lines)
        block_lines = block_lines[:]
        while block_lines and _ADDRESS_TRAILING_CONTACT.search(block_lines[-1].text):
            block_lines.pop()
        if not block_lines:
            continue
        addr_text = " ".join(l.text.strip() for l in block_lines)
        box_refs = [(l.x1, l.y1, l.x2, l.y2) for l in block_lines]

        candidates.append(FieldCandidate(
            field="address", value=addr_text, score=score,
            source="OCR", reasons=reasons,
            box_refs=box_refs,
            normalized_value=addr_text,
        ))

    return _select_best(candidates, "address",
                        ADDRESS_AUTO_SELECT_MIN, ADDRESS_MIN_MARGIN)


# ── FACILITY candidate scoring ───────────────────────────────────────────────


def score_facility_candidates(
    hub_text: str,
    cost_code: str,
    all_hubs: list[str],
    hubs_for_code: list[str],
) -> FieldResult:
    """Score facility candidates from OCR hub text against master list.

    Hard filter: only compatible hubs for the given cost code appear
    in the candidate list. Incompatible hubs are never scored and are
    HARD-REJECTED (never selected).
    """
    from app.rules import get_cost_code_info
    result = FieldResult(field="facility")
    if not hub_text or not hub_text.strip():
        return result

    query = hub_text.strip()
    q_low = query.lower()
    q_tokens = [t for t in re.split(r"[\s_\-]+", q_low) if t]

    candidates: list[FieldCandidate] = []

    for hub in hubs_for_code:
        h_low = hub.lower()
        score = 0.0
        reasons: list[str] = []

        # Exact substring match
        if q_low in h_low:
            score = 95.0
            reasons.append("Exact substring match")
        else:
            # Token overlap
            hits = sum(1 for t in q_tokens if len(t) >= 3 and t in h_low)
            token_ratio = hits / max(len(q_tokens), 1)

            # Fuzzy similarity
            hub_clean = re.sub(r"[\s_\-]+", "", h_low).replace("hub", "").replace("blr", "")
            q_clean = q_low.replace(" ", "")
            if len(hub_clean) >= 4:
                sim = difflib.SequenceMatcher(None, q_clean, hub_clean).ratio()
            else:
                sim = difflib.SequenceMatcher(None, q_low, h_low).ratio()

            score = max(sim * 80, token_ratio * 70)

            if token_ratio > 0.5:
                reasons.append(f"Token overlap: {hits}/{len(q_tokens)} tokens")
            if sim > 0.6:
                reasons.append(f"Fuzzy similarity: {sim:.0%}")

        # Screenshot text detected
        reasons.append(f'Facility text "{hub_text.strip()}" detected')

        # Compatible cost code -> best compatible facility in the constrained master
        if hub in hubs_for_code:
            score += 15
            reasons.append(f"Best compatible facility for cost code {cost_code}")
        else:
            # This should not happen since we filter, but guard
            score -= 100
            reasons.append(f"Incompatible with cost code {cost_code}")

        # Master exactness: check if it's a canonical hub name
        if hub in all_hubs:
            score += 5
            reasons.append("Official master entry")

        # Entity/operation master scope
        cc_info = get_cost_code_info(cost_code)
        if cc_info:
            reasons.append(f"{cc_info.get('entity')} {cc_info.get('operation')} master only")

        # Alias strength: contains location keyword
        location_tokens = {"hub", "blr", "pl", "myntra"}
        for tok in q_tokens:
            if tok in location_tokens and tok in h_low:
                score += 3
                reasons.append(f"Location keyword '{tok}' matched")

        candidates.append(FieldCandidate(
            field="facility", value=hub, score=score,
            source="Master", reasons=reasons,
            normalized_value=hub,
        ))

    # Also add rejected incompatible candidates with -100 score (HARD REJECT).
    # These are surfaced so the UI shows WHY an incompatible hub (e.g. an FM/_PL
    # hub for an LM cost code) is not selected.
    incompatible = [h for h in all_hubs if h not in hubs_for_code and q_low in h.lower()]
    for hub in incompatible[:3]:
        candidates.append(FieldCandidate(
            field="facility", value=hub, score=-100.0,
            confidence="Conflict", source="Master",
            reasons=[
                f'Incompatible facility for cost code {cost_code}',
                f'HARD-REJECTED: {hub} is not valid for {cost_code}',
            ],
            rejected_reason=f"Incompatible with {cost_code}",
            is_rejected=True,
        ))

    return _select_best(candidates, "facility",
                        FACILITY_AUTO_SELECT_MIN, FACILITY_MIN_MARGIN)


# ── ROLE candidate scoring ───────────────────────────────────────────────────


def score_role_candidates(
    role_text: str,
    cost_code: str,
    allowed_roles: list[str],
    role_aliases: dict[str, str],
) -> FieldResult:
    """Score role candidates from OCR role text against designation master.

    Hard filter: only roles valid for the cost code appear as candidates.
    """
    result = FieldResult(field="role")
    if not role_text or not role_text.strip():
        return result

    from app.rules import parse_role_text, get_cost_code_info
    prefix_hint, base_keyword = parse_role_text(role_text)

    # Entity / operation context for the Why? reasons.
    cc_info = get_cost_code_info(cost_code)
    entity_desc = (cc_info.get("entity") if cc_info else "") + (
        f" {cc_info.get('operation')}" if cc_info else "")
    entity_tok = ""
    if cc_info and cc_info.get("entity"):
        entity_tok = cc_info["entity"]

    q_low = role_text.strip().lower()
    # Whether the recruiter screenshot explicitly said the role keyword.
    explicit_keyword = base_keyword is not None
    explicit_prefix = prefix_hint is not None

    candidates: list[FieldCandidate] = []

    for role in allowed_roles:
        r_low = role.lower()
        score = 0.0
        reasons: list[str] = []

        # Exact match
        if q_low == r_low:
            score = 95.0
            reasons.append("Exact match in recruiter screenshot")
        # Alias-based match (explicit recruiter keyword wins)
        elif base_keyword:
            if base_keyword.lower() in r_low:
                score = 80.0
                reasons.append(f'"{base_keyword}" detected explicitly in recruiter screenshot')
            # Prefix match
            if prefix_hint and r_low.startswith(prefix_hint.lower()):
                score += 10
                reasons.append(f'"{prefix_hint}" detected in recruiter screenshot')
            if cc_info and entity_tok and entity_tok.lower() in role.lower():
                score += 5
        # Fuzzy (only considered when no explicit keyword)
        else:
            sim = difflib.SequenceMatcher(None, q_low, r_low).ratio()
            score = sim * 70
            if sim > 0.6:
                reasons.append(f"Fuzzy similarity: {sim:.0%}")

        if score > 0:
            # Compatibility + master provenance reasons
            if cc_info:
                reasons.append(f"Role compatible with cost code {cost_code}")
            reasons.append("Official designation master match")
            candidates.append(FieldCandidate(
                field="role", value=role, score=score,
                source="Master", reasons=reasons,
                normalized_value=role,
            ))

    return _select_best(candidates, "role",
                        ROLE_AUTO_SELECT_MIN, ROLE_MIN_MARGIN, explicit=explicit_keyword)


# ── SALARY candidate scoring ─────────────────────────────────────────────────

# A WhatsApp/chat timestamp ("11:25 am", "9:30", "12:05 pm") that must never be
# read as a salary. Salary values are always >= 1000, so times fit in 00:00-23:59.
_TIME_ONLY_PATTERN = re.compile(r"^\s*\d{1,2}:\d{2}\s*(am|pm|a\.m\.|p\.m\.)?\s*$", re.IGNORECASE)
# Inline chat timestamp within a line ("18k salary 11:25 am").
_INLINE_TIME_PATTERN = re.compile(r"\s*\d{1,2}:\d{2}\s*(am|pm|a\.m\.|p\.m\.)?\s*", re.IGNORECASE)


def score_salary_candidates(lines: list[OCRLine]) -> FieldResult:
    """Score salary candidates from OCR lines.

    Recognizes: 18k, 18 k, salary 18k, 18000, 18,000, ₹18,000.
    Excludes timestamps: 9:30 am, 10:15, 18:45 (both standalone lines and
    timestamp tokens inline, so "18k salary 11:25 am" still yields 18000).
    """
    result = FieldResult(field="salary")
    if not lines:
        return result

    candidates: list[FieldCandidate] = []

    for line in lines:
        text = line.text.strip()
        if not text:
            continue

        # Skip lines that are ONLY a timestamp (e.g. "9:30 am", "11:25").
        if _TIME_ONLY_PATTERN.match(text):
            continue

        # Strip inline timestamp tokens so salary extraction ignores them.
        text_clean = _INLINE_TIME_PATTERN.sub(" ", text).strip()
        if not text_clean:
            continue

        low = text_clean.lower().replace(",", "")

        score = 0.0
        reasons: list[str] = []
        amount = 0

        # 18k / 18.5k / 18 k / 18.5 k pattern (optional space before 'k').
        # Timestamps are already stripped above, so '18 k salary 10:30 am'
        # yields 18000 and '9:30 am' alone is never a salary.
        m = re.search(r"\b(\d+(?:\.\d+)?)\s*k\b", low)
        if m:
            amount = int(float(m.group(1)) * 1000)
            score = 70.0
            reasons.append(f"Salary pattern '{m.group(0)}' detected")
            if ":" in text_clean:
                reasons.append("Timestamp ignored")
        else:
            # Plain numeric: 18000, 15000
            m2 = re.search(r"\b(\d{4,6})\b", low)
            if m2:
                amount = int(m2.group(1))
                if 4000 <= amount <= 500000:
                    score = 60.0
                    reasons.append(f"Numeric value {amount} detected")
                else:
                    continue

        if amount <= 0:
            continue

        # Context boost: "salary" / "ctc" / "pay" nearby
        if re.search(r"\b(salary|ctc|pay|per month|pm|p\.m)\b", low):
            score += 20
            reasons.append("Salary keyword nearby")

        # ₹ symbol
        if "₹" in text_clean or "rs" in low or "inr" in low:
            score += 5
            reasons.append("Currency symbol detected")

        if line.confidence >= 0.8:
            score += 5
            reasons.append(f"OCR confidence {line.confidence:.0%}")

        candidates.append(FieldCandidate(
            field="salary", value=str(amount), score=score,
            source="OCR", reasons=reasons,
            box_refs=[(line.x1, line.y1, line.x2, line.y2)],
            normalized_value=str(amount),
        ))

    return _select_best(candidates, "salary",
                        SALARY_AUTO_SELECT_MIN, SALARY_MIN_MARGIN)


# ── Selection logic with margin ──────────────────────────────────────────────


def _select_best(
    candidates: list[FieldCandidate],
    field_name: str,
    auto_min: float,
    min_margin: float,
    explicit: bool = False,
) -> FieldResult:
    """Select the best non-rejected candidate and apply confidence margin.

    When ``explicit`` is True (an explicit recruiter keyword like "sorter" was
    detected), the top-scoring explicit candidate is locked in and fuzzy
    candidates cannot override it — enforcing the "explicit role text wins"
    rule.
    """
    result = FieldResult(field=field_name, all_candidates=candidates)

    valid = [c for c in candidates if not c.is_rejected and c.score > 0]
    if not valid:
        result.confidence = "Missing"
        result.needs_review = True
        return result

    valid.sort(key=lambda c: c.score, reverse=True)
    result.selected = valid[0]
    result.runner_up = valid[1] if len(valid) > 1 else None

    runner_up_score = valid[1].score if len(valid) > 1 else 0.0
    result.margin = valid[0].score - runner_up_score
    result.confidence = confidence_level(
        valid[0].score, runner_up_score, auto_min, min_margin
    )

    # Apply confidence to the selected candidate
    valid[0].confidence = result.confidence
    result.needs_review = result.confidence != "High"

    return result


# ── Composite extraction with evidence scoring ───────────────────────────────


def evidence_score_extraction(
    ocr_lines: list[OCRLine],
    flat_lines: list[str],
    hub_text: Optional[str],
    role_text: Optional[str],
    cost_code: str,
    all_hubs: list[str],
    hubs_for_code: list[str],
    allowed_roles: list[str],
    role_aliases: dict[str, str],
) -> dict:
    """Run full evidence scoring across all fields.

    Returns a dict of field -> FieldResult for integration into the
    extraction pipeline. This does NOT replace rules.py; it augments
    the extraction with multi-candidate scoring.
    """
    name_result = score_name_candidates(ocr_lines)
    dob_result = score_dob_candidates(ocr_lines)
    aadhaar_result = score_aadhaar_candidates(ocr_lines)
    address_result = score_address_candidates(ocr_lines)
    salary_result = score_salary_candidates(ocr_lines)
    facility_result = score_facility_candidates(
        hub_text or "", cost_code, all_hubs, hubs_for_code
    )
    role_result = score_role_candidates(
        role_text or "", cost_code, allowed_roles, role_aliases
    )

    return {
        "name": name_result,
        "dob": dob_result,
        "aadhaar_number": aadhaar_result,
        "address": address_result,
        "salary": salary_result,
        "facility": facility_result,
        "role": role_result,
    }


def field_result_to_safe_dict(fr: FieldResult) -> dict:
    """Convert a FieldResult to a JSON-safe dict for the UI.

    Exposes only safe reasoning metadata — no raw OCR text, no bounding boxes.
    """
    selected = fr.selected
    runner_up = fr.runner_up
    return {
        "field": fr.field,
        "confidence": fr.confidence,
        "margin": round(fr.margin, 1),
        "needs_review": fr.needs_review,
        "selected_value": selected.value if selected else "",
        "selected_score": round(selected.score, 1) if selected else 0,
        "selected_reasons": selected.reasons if selected else [],
        "runner_up_value": runner_up.value if runner_up else "",
        "runner_up_score": round(runner_up.score, 1) if runner_up else 0,
        "runner_up_reasons": runner_up.reasons if runner_up else [],
        "candidate_count": len([c for c in fr.all_candidates if not c.is_rejected]),
        "rejected_count": sum(1 for c in fr.all_candidates if c.is_rejected),
    }
