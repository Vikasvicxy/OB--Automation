"""Layout-aware Aadhaar document parser.

Extracts structured fields from Aadhaar card OCR results using:
  - Bounding-box spatial relationships (reading order, vertical positioning)
  - Field anchors (DOB, Gender, Address labels)
  - Text content validation
  - Existing TeamHR business rules

This module does NOT depend on any specific OCR library.  It consumes
normalised :class:`OCRLine` objects from ``app.ocr_models``.

License: Apache 2.0.  Original code; no third-party source copied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.ocr_models import OCRLine, FieldEvidence

# ── Constants ────────────────────────────────────────────────────────────────

# Tokens that must NEVER be treated as a candidate name on Aadhaar documents.
REJECTED_NAME_TOKENS: set[str] = {
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
    "to",
}

# Space-insensitive fragments that catch OCR-broken labels like
# "Governme nt of India".
REJECTED_NAME_FRAGMENTS: list[str] = [
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
    "sign",
    "signatu",
    "signatur",
]

_RE_GENDER_LINE = re.compile(r"^\s*(male|female)\s*$", re.IGNORECASE)

# ── Address anchors & terminators ───────────────────────────────────────────

_ADDRESS_ANCHORS = {
    "c/o", "s/o", "d/o", "w/o", "h/o",
    "vtc", "po", "post",
    "dist", "district", "dist.",
    "state", "pin", "pincode", "pin code", "pincode:",
    "taluk", "tq", "mandal",
    "village", "hno", "house no", "street", "road", "colony", "nagar",
    "near", "hobli", "post office",
}

_ADDRESS_TERMINATORS = {
    "government", "uidai", "unique identification", "aadhaar", "vid",
    "virtual id", "dob", "date of birth", "year of birth", "gender",
    "male", "female", "enrolment", "enrollment", "signature",
    "i humbly declare", "i hereby declare",
    "aadhaar number", "aadhaar no",
}

# Words on the back side that indicate the address region
_BACK_ADDRESS_INDICATORS = {
    "c/o", "s/o", "d/o", "w/o", "h/o",
    "address", "to",
}

# ── Reused Aadhaar-pattern helpers ──────────────────────────────────────────

_AADHAAR_12_RE = re.compile(r"\d{4}[\s]?\d{4}[\s]?\d{4}")
_DOB_RE = re.compile(r"(\d{2})[/\-](\d{2})[/\-](\d{4})")
_YEAR_OF_BIRTH_RE = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


# ── Data class for parser output ────────────────────────────────────────────


@dataclass
class AadhaarResult:
    """Structured output from Aadhaar layout parsing."""

    name: FieldEvidence = field(default_factory=FieldEvidence)
    dob: FieldEvidence = field(default_factory=FieldEvidence)
    aadhaar_number: FieldEvidence = field(default_factory=FieldEvidence)
    address: FieldEvidence = field(default_factory=FieldEvidence)
    gender: FieldEvidence = field(default_factory=FieldEvidence)


# ── Internal helpers ────────────────────────────────────────────────────────


def _contains_rejected_fragment(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    compact = re.sub(r"[^a-z0-9]", "", compact)
    for frag in REJECTED_NAME_FRAGMENTS:
        if frag in compact:
            return True
    return False


def _is_valid_name_token(text: str) -> bool:
    """Return True if *text* could be a real person name (not a label)."""
    stripped = text.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if _RE_GENDER_LINE.match(stripped):
        return False
    if _contains_rejected_fragment(stripped):
        return False
    if lower in REJECTED_NAME_TOKENS:
        return False
    words = stripped.split()
    for label in REJECTED_NAME_TOKENS:
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


def _looks_like_name(text: str) -> bool:
    stripped = text.strip()
    if not stripped or not _is_valid_name_token(stripped):
        return False
    alpha_ratio = sum(1 for c in stripped if c.isalpha()) / max(len(stripped), 1)
    return alpha_ratio >= 0.7


def _repair_run_together_name(name: str) -> str:
    """Split run-together names like 'PallaviKV' -> 'Pallavi K V'."""
    name = name.strip()
    if " " in name:
        return re.sub(r"\s+", " ", name).strip()
    if not name or name == name.upper():
        return name
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]", name)
    joined = " ".join(words).strip()
    return joined if joined else name


def _has_address_anchor(text: str) -> bool:
    low = text.strip().lower()
    for t in re.split(r"[\s:/,.\-_]+", low):
        t = t.strip(".,:;-")
        if not t:
            continue
        if t in _ADDRESS_ANCHORS:
            return True
        if t in {"c", "s", "d", "w", "h"} and "/o" in low:
            return True
    return any(a in low for a in _ADDRESS_ANCHORS if " " in a)


def _is_address_terminator(text: str) -> bool:
    low = text.strip().lower()
    return any(t in low for t in _ADDRESS_TERMINATORS)


def _line_height(line: OCRLine) -> float:
    return line.height


def _line_is_near_top(line: OCRLine, doc_height: float) -> bool:
    """True if the line is in the upper 40% of the document."""
    if doc_height <= 0:
        return True
    return line.center_y < doc_height * 0.4


# ── Layout-aware Aadhaar parser ─────────────────────────────────────────────


class AadhaarLayoutParser:
    """Parse Aadhaar fields using bounding-box layout relationships.

    Input: list of :class:`OCRLine` objects with coordinates.
    Output: :class:`AadhaarResult` with structured field evidence.
    """

    def __init__(self, lines: list[OCRLine]):
        self.lines = lines
        self.text_lines = [l.text.strip() for l in lines]
        # Compute document dimensions for relative positioning
        if lines:
            self.doc_height = max(l.y2 for l in lines)
            self.doc_width = max(l.x2 for l in lines)
        else:
            self.doc_height = 0
            self.doc_width = 0

    def parse(self) -> AadhaarResult:
        """Run all field extractions and return structured result."""
        result = AadhaarResult()
        result.aadhaar_number = self._extract_aadhaar_number()
        result.dob = self._extract_dob()
        result.gender = self._extract_gender()
        result.name = self._extract_name()
        result.address = self._extract_address()
        return result

    # ── Aadhaar number ──────────────────────────────────────────────────

    def _extract_aadhaar_number(self) -> FieldEvidence:
        """Extract 12-digit Aadhaar number using text + layout."""
        for line in self.lines:
            m = _AADHAAR_12_RE.search(line.text)
            if m:
                core = re.sub(r"\D", "", m.group(0))
                if len(core) == 12:
                    return FieldEvidence(
                        value=core,
                        confidence="High",
                        source="Aadhaar",
                        reason="12-digit Aadhaar pattern detected",
                        source_lines=[line.text],
                        box_refs=[(line.x1, line.y1, line.x2, line.y2)],
                    )
        return FieldEvidence(source="Aadhaar", reason="Not found")

    # ── DOB ─────────────────────────────────────────────────────────────

    def _extract_dob(self) -> FieldEvidence:
        """Extract DOB in DD/MM/YYYY or 'Year of Birth' + year."""
        # Pass 1: explicit DD/MM/YYYY pattern
        for line in self.lines:
            m = _DOB_RE.search(line.text)
            if m:
                dob_str = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
                return FieldEvidence(
                    value=dob_str,
                    confidence="High",
                    source="Aadhaar",
                    reason="DOB pattern detected in OCR line",
                    source_lines=[line.text],
                    box_refs=[(line.x1, line.y1, line.x2, line.y2)],
                )
        # Pass 2: Year of birth near 'birth' label
        for i, line in enumerate(self.lines):
            if "birth" in line.label_lower:
                for cand in self.lines[i:i + 3]:
                    m = _YEAR_OF_BIRTH_RE.search(cand.text)
                    if m:
                        return FieldEvidence(
                            value=m.group(1),
                            confidence="Review",
                            source="Aadhaar",
                            reason="Year of birth detected near 'birth' label",
                            source_lines=[cand.text],
                            box_refs=[(cand.x1, cand.y1, cand.x2, cand.y2)],
                        )
        return FieldEvidence(source="Aadhaar", reason="Not found")

    # ── Gender ──────────────────────────────────────────────────────────

    def _extract_gender(self) -> FieldEvidence:
        """Extract gender from a standalone Male/Female line."""
        for line in self.lines:
            if _RE_GENDER_LINE.match(line.text.strip()):
                return FieldEvidence(
                    value=line.text.strip().capitalize(),
                    confidence="High",
                    source="Aadhaar",
                    reason="Gender label detected",
                    source_lines=[line.text],
                    box_refs=[(line.x1, line.y1, line.x2, line.y2)],
                )
        return FieldEvidence(source="Aadhaar", reason="Not found")

    # ── Name ────────────────────────────────────────────────────────────

    def _extract_name(self) -> FieldEvidence:
        """Extract name using layout relationships (multi-pass).

        Priority:
          1. Explicit "Name:" label -> next line
          2. Line immediately above DOB/gender (vertical proximity)
          3. Line immediately above DOB date value (spatial proximity)
          4. Uppercase alpha-only token near top of doc
          5. Best-effort heuristic (first name-like line in upper half)
        """
        # Pass 1: explicit "Name:" label
        result = self._name_from_label()
        if result and result.confidence == "High":
            return result

        # Pass 2-3: line above DOB/gender using spatial layout
        result_spatial = self._name_from_spatial()
        if result_spatial:
            return result_spatial

        # Pass 1 fallback (if label found but low confidence)
        if result:
            return result

        # Pass 4: uppercase alpha near top
        result_upper = self._name_from_uppercase_top()
        if result_upper:
            return result_upper

        # Pass 5: best-effort
        result_best = self._name_from_best_effort()
        if result_best:
            return result_best

        return FieldEvidence(
            source="Aadhaar", reason="No valid name found",
            confidence="Missing",
        )

    def _name_from_label(self) -> Optional[FieldEvidence]:
        """Find name from explicit 'Name:' label -> next line."""
        for i, line in enumerate(self.lines):
            low = line.label_lower
            if low in {"name", "name :", "name:"}:
                if i + 1 < len(self.lines):
                    candidate = self.lines[i + 1]
                    if _is_valid_name_token(candidate.text):
                        repaired = _repair_run_together_name(candidate.text)
                        return FieldEvidence(
                            value=repaired,
                            confidence="High",
                            source="Aadhaar",
                            reason="Line immediately after 'Name' label",
                            source_lines=[line.text, candidate.text],
                            box_refs=[
                                (line.x1, line.y1, line.x2, line.y2),
                                (candidate.x1, candidate.y1, candidate.x2, candidate.y2),
                            ],
                        )
        return None

    def _name_from_spatial(self) -> Optional[FieldEvidence]:
        """Find name by locating DOB/gender and looking at the line above."""
        # Find reference lines (DOB text, gender text, or DOB date value)
        ref_lines: list[OCRLine] = []
        for line in self.lines:
            low = line.label_lower
            if "dob" in low or "date of birth" in low or "birth" in low:
                ref_lines.append(line)
            elif _RE_GENDER_LINE.match(line.text.strip()):
                ref_lines.append(line)
            elif _DOB_RE.search(line.text):
                ref_lines.append(line)

        if not ref_lines:
            return None

        # Sort by vertical position (top-most first)
        ref_lines.sort(key=lambda l: l.y1)

        # For each reference, find the line immediately above it in
        # reading order (same column, closest above).
        for ref in ref_lines:
            best: Optional[OCRLine] = None
            best_gap = float("inf")
            for candidate in self.lines:
                if candidate is ref:
                    continue
                if candidate.center_y >= ref.center_y:
                    continue  # below or same line
                # Must be in roughly the same horizontal column
                h_overlap = (
                    candidate.x1 < ref.x2 + 50 and
                    candidate.x2 > ref.x1 - 50
                )
                if not h_overlap:
                    continue
                gap = ref.center_y - candidate.center_y
                if gap < best_gap and gap < self.doc_height * 0.3:
                    best_gap = gap
                    best = candidate

            if best and _is_valid_name_token(best.text) and _looks_like_name(best.text):
                repaired = _repair_run_together_name(best.text)
                return FieldEvidence(
                    value=repaired,
                    confidence="Review",
                    source="Aadhaar",
                    reason=f"Line immediately above {ref.label_lower} (spatial proximity)",
                    source_lines=[best.text, ref.text],
                    box_refs=[
                        (best.x1, best.y1, best.x2, best.y2),
                        (ref.x1, ref.y1, ref.x2, ref.y2),
                    ],
                )
        return None

    def _name_from_uppercase_top(self) -> Optional[FieldEvidence]:
        """Find name from uppercase alpha token near top of doc."""
        for line in self.lines:
            if not _line_is_near_top(line, self.doc_height):
                continue
            stripped = line.text.strip()
            if not stripped:
                continue
            if re.fullmatch(r"[A-Z][A-Z\s]{2,35}", stripped):
                lower = stripped.lower()
                if lower not in REJECTED_NAME_TOKENS and _is_valid_name_token(stripped):
                    return FieldEvidence(
                        value=stripped,
                        confidence="Review",
                        source="Aadhaar",
                        reason="Uppercase alpha token near top of document",
                        source_lines=[stripped],
                        box_refs=[(line.x1, line.y1, line.x2, line.y2)],
                    )
        return None

    def _name_from_best_effort(self) -> Optional[FieldEvidence]:
        """Last-resort: first name-like line in upper half."""
        for line in self.lines:
            if not _line_is_near_top(line, self.doc_height * 1.5):
                continue
            if _looks_like_name(line.text):
                lower = line.label_lower
                if lower not in REJECTED_NAME_TOKENS:
                    return FieldEvidence(
                        value=line.text.strip(),
                        confidence="Review",
                        source="Aadhaar",
                        reason="Best-effort name detection (upper region)",
                        source_lines=[line.text],
                        box_refs=[(line.x1, line.y1, line.x2, line.y2)],
                    )
        return None

    # ── Address ─────────────────────────────────────────────────────────

    def _extract_address(self) -> FieldEvidence:
        """Extract multi-line address block using layout and reading order.

        Strategy:
          1. Find address start via explicit label or relational anchor
             (C/O, S/O, etc.) on the back side of the Aadhaar.
          2. Collect contiguous address lines in reading order.
          3. Stop at large vertical gap, Aadhaar number area, QR region,
             or another identity section.
        """
        if not self.lines:
            return FieldEvidence(source="Aadhaar", reason="No OCR lines")

        # Detect if this is a back-side document (has address indicators)
        has_back = any(
            l.label_lower in {"c/o", "s/o", "d/o", "w/o", "h/o", "to"}
            or "address" in l.label_lower
            for l in self.lines
        )

        # Find start of address block
        start_line: Optional[OCRLine] = None

        # Priority 1: explicit "Address" label
        for line in self.lines:
            low = line.label_lower
            if low.startswith("address"):
                start_line = line
                break

        # Priority 2: relational anchors (C/O, S/O, D/O, W/O, H/O)
        if start_line is None:
            for line in self.lines:
                low = line.label_lower
                if low in {"c/o", "s/o", "d/o", "w/o", "h/o"}:
                    start_line = line
                    break
                # Also check for "C/O Name" patterns
                if re.match(r"^(c/o|s/o|d/o|w/o|h/o)\s+\w", low):
                    start_line = line
                    break

        # Priority 3: "To" label on back side followed by person-name-like line
        if start_line is None and has_back:
            for i, line in enumerate(self.lines):
                if line.label_lower == "to" and i + 1 < len(self.lines):
                    next_line = self.lines[i + 1]
                    if (_looks_like_name(next_line.text) and
                            _line_height(next_line) > 10):
                        # The address block starts with the line after the name
                        # Look for C/O or similar in subsequent lines
                        for later in self.lines[i + 2:i + 8]:
                            if _has_address_anchor(later.text):
                                start_line = later
                                break
                        if start_line is None:
                            # Use the line after the name as the address start
                            start_line = next_line
                        break

        # Priority 4: first line with an address anchor
        if start_line is None:
            for line in self.lines:
                if _has_address_anchor(line.text):
                    start_line = line
                    break

        if start_line is None:
            return FieldEvidence(source="Aadhaar", reason="No address block found")

        # Collect address lines from start_line forward
        addr_parts: list[str] = []
        addr_boxes: list[tuple[float, float, float, float]] = []
        prev_y = start_line.y2
        found_start = False

        for line in self.lines:
            if not found_start:
                if line is start_line or line.center_y >= start_line.center_y - 5:
                    found_start = True
                else:
                    continue

            low = line.label_lower

            # Stop conditions
            if _is_address_terminator(line.text):
                break
            # Aadhaar number area (12+ digits on one line)
            digits = re.sub(r"\D", "", line.text)
            if len(digits) >= 12:
                break
            # Large vertical gap (> 2x average line height) suggests new section
            if addr_parts and self.doc_height > 0:
                gap = line.y1 - prev_y
                avg_height = max(l.height for l in self.lines) if self.lines else 20
                if gap > avg_height * 2.5:
                    break
            # Empty line
            if not line.text.strip():
                if addr_parts:
                    break
                continue

            addr_parts.append(line.text.strip())
            addr_boxes.append((line.x1, line.y1, line.x2, line.y2))
            prev_y = line.y2

        if not addr_parts:
            return FieldEvidence(source="Aadhaar", reason="Address block empty")

        address_text = " ".join(addr_parts).strip()
        if len(address_text) < 8:
            return FieldEvidence(
                value="", source="Aadhaar",
                reason="Address too short to be valid",
            )

        # Reject if the collected text is just a label with no real content
        _ADDR_LABELS_ONLY = {
            "address", "address:", "address :", "address -",
            "c/o", "s/o", "d/o", "w/o", "h/o",
        }
        if address_text.lower().strip(".,:;-") in _ADDR_LABELS_ONLY:
            return FieldEvidence(
                value="", source="Aadhaar",
                reason="Only address label found, no address content",
            )

        return FieldEvidence(
            value=address_text,
            confidence="Review",
            source="Aadhaar",
            reason="Layout-based address extraction (reading order)",
            source_lines=addr_parts,
            box_refs=addr_boxes,
        )


# ── Public convenience function ─────────────────────────────────────────────


def parse_aadhaar(lines: list[OCRLine]) -> AadhaarResult:
    """Parse an Aadhaar document from normalised OCR lines.

    This is the main entry point for Aadhaar field extraction.
    """
    parser = AadhaarLayoutParser(lines)
    return parser.parse()


def parse_aadhaar_from_text(text_lines: list[str]) -> AadhaarResult:
    """Parse Aadhaar from plain text lines (no coordinates).

    Creates synthetic OCRLine objects with zero coordinates and
    delegates to the layout parser.  This allows the new parser to
    work with the old flat-text extraction path during migration.
    """
    ocr_lines = []
    for i, text in enumerate(text_lines):
        ocr_lines.append(OCRLine(text=text, order=i))
    return parse_aadhaar(ocr_lines)
