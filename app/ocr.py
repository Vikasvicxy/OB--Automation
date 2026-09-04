"""Document intelligence for TeamHR Automation Smart Upload.

Uses pluggable OCR backends (RapidOCR default, optional PaddleOCR) to read
candidate source material: Aadhaar photos/PDFs and pasted/uploaded
screenshots.  When bounding-box data is available, a layout-aware Aadhaar
parser extracts fields via spatial relationships; otherwise the flat-text
extraction path (proven regex + heuristics) is used.

Security / data handling:
  - OCR runs locally; documents never leave the machine.
  - Aadhaar numbers / addresses are never logged to console.
  - Full Aadhaar is only surfaced in the returned extraction payload (needed to
    seed the master record on approval); the normal review UI shows it masked.
"""

import io
import logging
import re
from typing import Optional

from PIL import Image

from app import rules

# Safe debug logger — never logs sensitive plaintext (Aadhaar numbers,
# full addresses).  Only OCR-stage success/failure, rule-match info, and
# confidence categories are emitted.
logger = logging.getLogger("teamhr.ocr")


def _safe_log(msg: str, **kw) -> None:
    """Emit an INFO-level log line with optional structured extras.

    Sensitive fields (aadhaar, address) are explicitly excluded from kw.
    """
    logger.info(msg, extra=kw)


# RapidOCR is imported lazily so the app can boot even if the heavy native
# libs are briefly unavailable.
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


# ── Low-level read helpers ───────────────────────────────────────────────────
# These return flat text lists for backward compatibility with tests and the
# existing pipeline.  The new layout-aware path uses ocr_backends directly.


def _ocr_image_bytes(img_bytes: bytes) -> list[str]:
    """Run OCR on raw image bytes and return a list of detected text lines."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    import numpy as np

    arr = np.asarray(img)
    result, _elapsed = _get_engine()(arr)
    lines = []
    if result:
        for item in result:
            # item[1] is the recognized text
            txt = str(item[1]).strip()
            if txt:
                lines.append(txt)
    return lines


def _ocr_pdf_bytes(pdf_bytes: bytes) -> list[str]:
    """Render a PDF's pages to images and OCR each page."""
    import pymupdf

    lines: list[str] = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            lines.extend(_ocr_image_bytes(img_bytes))
    finally:
        doc.close()
    return lines


def extract_text(file_bytes: bytes, filename: str) -> list[str]:
    """Dispatch to the right reader based on file type.

    Returns a flat list of raw OCR text lines.
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return _ocr_pdf_bytes(file_bytes)
    return _ocr_image_bytes(file_bytes)


def extract_ocr_lines(file_bytes: bytes, filename: str) -> list:
    """Run OCR and return normalised OCRLine objects (with bounding boxes).

    Falls back to the flat-text path if the backends module is unavailable.
    """
    try:
        from app.ocr_backends import ocr_file_fallback
        return ocr_file_fallback(file_bytes, filename)
    except ImportError:
        # Fallback: create synthetic OCRLines from flat text
        from app.ocr_models import OCRLine
        text_lines = extract_text(file_bytes, filename)
        return [OCRLine(text=t, order=i) for i, t in enumerate(text_lines)]


# ── Field extractors (raw, unvalidated) ──────────────────────────────────────


def _mask_aadhaar(digits: str) -> str:
    if len(digits) == 12:
        return f"XXXX XXXX {digits[-4:]}"
    if len(digits) >= 4:
        return f"XXXX XXXX {digits[-4:]}"
    return "XXXX XXXX ?"


def extract_aadhaar_num(lines: list[str]) -> Optional[str]:
    """Return full 12-digit Aadhaar number or None."""
    for line in lines:
        digits = re.sub(r"\D", "", line)
        m = re.search(r"\d{4}[\s]?\d{4}[\s]?\d{4}", line)
        if m and len(digits) >= 12:
            core = re.sub(r"\D", "", m.group(0))
            if len(core) == 12:
                return core
    return None


def _find_line_after_label(lines: list[str], labels: list[str]) -> Optional[str]:
    """Return the first line that follows a recognized label line."""
    lower = [l.lower() for l in lines]
    for i, lab in enumerate(lower):
        for label in labels:
            # label present and likely not part of a bigger word
            if lab == label or lab.startswith(label + " ") or lab.startswith(label + ":") or lab.startswith(label + " -"):
                if i + 1 < len(lines):
                    return lines[i + 1].strip()
                return None
    return None


_NAME_LABELS = ["name", "name :", "name:"]

# Tokens that must NEVER be treated as a candidate name on Aadhaar documents.
# This covers gender, labels, government text, and other non-name lines that
# OCR sometimes returns above or near the identity block.
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

# Regex that matches a "gender-only" line such as "Male", "Female", "MALE / FEMALE"
_RE_GENDER_LINE = re.compile(r"^\s*(male|female)\s*$", re.IGNORECASE)

# Space-insensitive (normalized) forms of rejected labels, to catch OCR
# noise such as "Governme nt of India" or "GOVT OF INDIA".
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


def _contains_rejected_fragment(text: str) -> bool:
    """Return True if the text contains a known Aadhaar label fragment,
    ignoring spaces/spacing noise from OCR ("Governme nt of India")."""
    compact = re.sub(r"\s+", "", text).lower()
    compact = re.sub(r"[^a-z0-9]", "", compact)
    for frag in _REJECTED_NAME_FRAGMENTS:
        if frag in compact:
            return True
    return False


def _is_valid_name_candidate(text: str) -> bool:
    """Return True if *text* looks like it could be a real person name.

    Rejects lines that are purely numeric, contain Aadhaar labels, are
    gender identifiers, or are otherwise not plausible name text.
    """
    stripped = text.strip()
    if not stripped:
        return False

    lower = stripped.lower()

    # Exact gender match
    if _RE_GENDER_LINE.match(stripped):
        return False

    # Space-insensitive label detection (catches OCR noise / broken spacing
    # such as "Governme nt of India").
    if _contains_rejected_fragment(stripped):
        return False

    # Known label / rejected token (exact match on the whole trimmed line,
    # or as a standalone word for single-word labels).
    if lower in _REJECTED_NAME_TOKENS:
        return False
    words = stripped.split()
    for label in _REJECTED_NAME_TOKENS:
        # Multi-word labels must match the whole line (they are not a name).
        if " " in label:
            if lower == label:
                return False
            continue
        # Single-word labels: reject only when present as a standalone word,
        # never as a prefix of a longer word (so "Vidya" is allowed while
        # a lone "VID" token is rejected).
        if label in words:
            return False

    # Reject if every token is purely numeric
    tokens = stripped.split()
    if all(t.isdigit() for t in tokens):
        return False

    # Reject lines that are mostly digits (e.g. "1234 5678 9012")
    digit_count = sum(1 for c in stripped if c.isdigit())
    if digit_count > len(stripped) * 0.4:
        return False

    # Must contain at least one alphabetic character
    if not any(c.isalpha() for c in stripped):
        return False

    # Name should not be too long (Aadhaar names are typically 2-4 words)
    if len(tokens) > 6:
        return False

    return True


def _looks_like_name(text: str) -> bool:
    """Heuristic: does the text look like a proper name (alphabetic tokens)?"""
    stripped = text.strip()
    if not stripped:
        return False
    if not _is_valid_name_candidate(stripped):
        return False
    tokens = stripped.split()
    alpha_ratio = sum(1 for c in stripped if c.isalpha()) / max(len(stripped), 1)
    return alpha_ratio >= 0.7 and len(tokens) >= 1


def _repair_run_together_name(name: str, lines: list[str]) -> str:
    """Best-effort re-insert lost spaces into a run-together Aadhaar name.

    When OCR merges words (e.g. "PallaviKV" for "Pallavi K V", or
    "JohnDoeSharma" for "John Doe Sharma"), split on CamelCase words and on
    single-letter initials. Already-spaced names are returned unchanged, and
    all-uppercase tokens are left alone (they cannot be split reliably).
    """
    name = name.strip()
    if " " in name:
        return re.sub(r"\s+", " ", name).strip()
    if not name or name == name.upper():
        return name
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]", name)
    joined = " ".join(words).strip()
    return joined if joined else name


def extract_aadhaar_name(lines: list[str]) -> tuple[str, str]:
    """Extract the candidate name from an Aadhaar document.

    Returns (name, confidence) where confidence is one of
    'High', 'Review', or 'Missing'.
    """
    _safe_log("extract_aadhaar_name: starting extraction",
              line_count=len(lines))

    # ── Pass 1: look for an explicit "Name:" label ────────────────────────
    name = _find_line_after_label(lines, _NAME_LABELS)
    if name and _is_valid_name_candidate(name):
        result = _repair_run_together_name(name.strip(), lines)
        _safe_log("extract_aadhaar_name: found via name label",
                  rule="name_label", confidence="High", has_value=bool(result))
        return result, "High"

    # ── Pass 2: scan lines near DOB / gender for a name ───────────────────
    # On many Aadhaar cards the name appears on the line immediately above
    # the DOB or gender line.
    dob_idx = None
    gender_idx = None
    for i, line in enumerate(lines):
        low = line.strip().lower()
        if dob_idx is None and ("dob" in low or "date of birth" in low or "birth" in low):
            dob_idx = i
        if gender_idx is None and _RE_GENDER_LINE.match(line.strip()):
            gender_idx = i
        # Also check for DOB pattern like "01/01/1990"
        if dob_idx is None and re.search(r"\d{2}[/\-]\d{2}[/\-]\d{4}", line):
            dob_idx = i

    for ref_idx in [dob_idx, gender_idx]:
        if ref_idx is not None and ref_idx > 0:
            candidate = lines[ref_idx - 1].strip()
            if _is_valid_name_candidate(candidate) and _looks_like_name(candidate):
                result = _repair_run_together_name(candidate, lines)
                _safe_log("extract_aadhaar_name: found near DOB/gender line",
                          rule="above_dob_gender", confidence="Review",
                          ref_index=ref_idx, has_value=bool(result))
                # Position-based heuristic (line above DOB/gender). This is a
                # strong but not guaranteed signal, so we do NOT claim High.
                return result, "Review"

    # ── Pass 3: heuristic — uppercase alpha-only tokens near top of doc ────
    # Aadhaar cards typically put the name in the first few lines.
    for idx, line in enumerate(lines[:8]):
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"[A-Z][A-Z\s]{2,35}", stripped):
            lower = stripped.lower()
            if lower not in _REJECTED_NAME_TOKENS and _is_valid_name_candidate(stripped):
                # Additional check: not a known label line
                if lower not in {"name", "government of india",
                                 "unique identification authority of india",
                                 "aadhaar", "uidai"}:
                    _safe_log("extract_aadhaar_name: heuristic match at line",
                              rule="uppercase_top", confidence="Review", index=idx)
                    return stripped, "Review"

    # ── Pass 4: any line that looks like a name (best effort) ──────────────
    for idx, line in enumerate(lines[:12]):
        stripped = line.strip()
        if _looks_like_name(stripped):
            lower = stripped.lower()
            if lower not in _REJECTED_NAME_TOKENS:
                _safe_log("extract_aadhaar_name: best-effort match",
                          rule="best_effort", confidence="Review", index=idx)
                return stripped, "Review"

    _safe_log("extract_aadhaar_name: no valid name found",
              rule="none", confidence="Missing")
    return "", "Missing"


def extract_dob(lines: list[str]) -> Optional[str]:
    """Extract DOB in DD/MM/YYYY or 'Year of Birth' + year."""
    for line in lines:
        m = re.search(r"(\d{2})[/\-](\d{2})[/\-](\d{4})", line)
        if m:
            return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    # Year of birth: capture a 18xx/19xx/20xx year near 'birth'
    for i, line in enumerate(lines):
        if "birth" in line.lower():
            for cand in lines[i:i + 3]:
                m = re.search(r"\b(1[89]\d\d|20\d\d)\b", cand)
                if m:
                    return m.group(1)
    return None


# Anchors that mark the start of an Aadhaar address block. OCR often drops the
# explicit "Address:" label, so we also detect these relational/administrative
# markers to locate the start of the address.
_ADDRESS_ANCHORS = {
    "c/o", "s/o", "d/o", "w/o",
    "vtc", "po", "post",
    "dist", "district", "dist.",
    "state", "pin", "pincode", "pin code", "pincode:",
    "taluk", "tq", "mandal",
    "village", "hno", "house no", "street", "road", "colony", "nagar",
}

# Lines/header tokens that terminate the address block (identity header, labels).
_ADDRESS_TERMINATORS = {
    "government", "uidai", "unique identification", "aadhaar", "vid",
    "virtual id", "dob", "date of birth", "year of birth", "gender",
    "male", "female", "enrolment", "enrollment", "signature",
    "i humbly declare", "i hereby declare",
}


def _has_address_anchor(line: str) -> bool:
    """True if *line* contains an address-block anchor as a standalone word."""
    low = line.strip().lower()
    for t in re.split(r"[\s:/,.\-_]+", low):
        t = t.strip(".,:;-")
        if not t:
            continue
        if t in _ADDRESS_ANCHORS:
            return True
        if t in {"c", "s", "d", "w"} and "/o" in low:
            return True
    return any(a in low for a in _ADDRESS_ANCHORS if " " in a)


def extract_address(lines: list[str]) -> str:
    """Extract a multi-line address block from an Aadhaar document.

    Locates the address via an explicit "Address" label, or failing that via
    relational/administrative anchors (C/O, S/O, VTC, PO, District, State, PIN
    Code, ...), then collects the contiguous address lines. Returns "" if no
    address block is found.
    """
    start = None
    for i, line in enumerate(lines):
        low = line.strip().lower()
        if low.startswith("address"):
            start = i
            break
    if start is None:
        for i, line in enumerate(lines):
            if _has_address_anchor(line):
                start = i
                break
    if start is None:
        return ""

    parts: list[str] = []
    for line in lines[start:]:
        low = line.strip().lower()
        # A recognisable non-address header ends the block.
        if any(t in low for t in _ADDRESS_TERMINATORS):
            break
        if not line.strip():
            if parts:
                break
            continue
        parts.append(line.strip())

    text = " ".join(parts).strip()
    if len(text) < 8:
        return ""
    return text


def _extract_mobile_candidates(lines: list[str]) -> list[str]:
    candidates = []
    for line in lines:
        # +91 89046 70707 variants
        m = re.search(r"(\+?\d{1,2}[\s-]?)?(\d{5}[\s-]?\d{5})", line)
        if m:
            raw = m.group(0)
            candidates.append(raw)
    return candidates


def extract_mobile(lines: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Return (normalized_mobile, error_or_None)."""
    raw_candidates = _extract_mobile_candidates(lines)
    for raw in raw_candidates:
        norm, err = rules.normalize_mobile(raw)
        if not err:
            return norm, None
    if raw_candidates:
        return None, "Mobile number invalid (Needs Attention): " + raw_candidates[0].strip()
    return None, None


# WhatsApp/chat timestamps (e.g. "11:25 am", "9:30", "12:05 pm") that must
# NEVER be mistaken for a salary. Salary values are always >= 1000, while
# times fall in 00:00-23:59.
_TIME_ONLY_PATTERN = re.compile(r"^\s*\d{1,2}:\d{2}\s*(am|pm|a\.m\.|p\.m\.)?\s*$", re.IGNORECASE)

# A timestamp token appearing inline (e.g. "18k salary 11:25 am"). We only
# strip it from the display, the numeric salary remains.
_INLINE_TIME_PATTERN = re.compile(r"\s*\d{1,2}:\d{2}\s*(am|pm|a\.m\.|p\.m\.)?\s*", re.IGNORECASE)


def _clean_salary_display(line: str, amount: int) -> str:
    """Return a clean, timestamp-free numeric display for a salary value.

    Ignores WhatsApp timestamps so "18k salary 11:25 am" -> "18000".
    """
    cleaned = _INLINE_TIME_PATTERN.sub(" ", line).strip()
    return str(amount)


def _extract_salary_candidates(lines: list[str]) -> list[tuple[str, int]]:
    """Return [(display, amount)] for detected salary values.

    Handles "18k", "18.5k", "18k salary", "18000", "18,000". Uses word-boundary
    searches so a 10-digit mobile number or a DOB is not mistaken for a salary,
    and ignores WhatsApp timestamps ("11:25 am", "9:30 am"). The display is the
    clean numeric final value (never the raw line with a timestamp).
    """
    out = []
    for line in lines:
        stripped = line.strip()
        if _TIME_ONLY_PATTERN.match(stripped):
            continue
        low = stripped.lower().replace(",", "")
        # Prefer the 'k' form (with optional space): 18k / 18 k / 18.5k / 18.5 k
        m = re.search(r"\b(\d+(?:\.\d+)?)\s*k\b", low)
        if m:
            amount = int(float(m.group(1)) * 1000)
            out.append((_clean_salary_display(stripped, amount), amount))
            continue
        m = re.search(r"\b(\d{4,6})\b", low)
        if m:
            amount = int(m.group(1))
            if 4000 <= amount <= 500000:
                out.append((_clean_salary_display(stripped, amount), amount))
    return out


def extract_role_text(lines: list[str]) -> Optional[str]:
    """Return a role-bearing line, or None."""
    for line in lines:
        hint, base = rules.parse_role_text(line)
        if base:
            return line.strip()
    return None


def extract_hub_text(lines: list[str]) -> Optional[str]:
    """Return a short candidate hub text (non-number, non-label, non-role line)."""
    for line in lines:
        line = line.strip()
        if not line or re.search(r"\d", line):
            continue
        if line.lower() in {
            "name", "address", "government of india",
            "unique identification authority of india", "aadhaar", "dob",
            "date of birth", "lm", "fm",
        }:
            continue
        # Skip role-bearing lines (e.g. "LM sorter" is a role, not a hub).
        hint, base = rules.parse_role_text(line)
        if base:
            continue
        if len(line) <= 40:
            hub, _ = rules.match_hub_from_text(line)
            if hub:
                return line.strip()
    return None


def _combine_lines(lines: list[str]) -> str:
    return " ".join(lines)


# ── Layout-aware extraction helpers ──────────────────────────────────────────


def _extract_aadhaar_with_layout(aadhaar_ocr_lines: list) -> dict:
    """Extract Aadhaar fields using the layout-aware parser.

    Input: list of OCRLine objects (with bounding boxes).
    Returns: dict with name, dob, aadhaar_number, address, gender
             (all as FieldEvidence instances).
    """
    from app.document_parsers.aadhaar import parse_aadhaar, AadhaarResult
    result: AadhaarResult = parse_aadhaar(aadhaar_ocr_lines)
    return {
        "name": result.name,
        "dob": result.dob,
        "aadhaar_number": result.aadhaar_number,
        "address": result.address,
        "gender": result.gender,
    }


def _looks_like_aadhaar_from_ocr_lines(lines: list) -> bool:
    """Check if OCR lines look like Aadhaar (using OCRLine or str)."""
    for line in lines:
        text = line.text if hasattr(line, "text") else str(line)
        low = text.lower()
        if any(kw in low for kw in ("aadhaar", "uidai", "unique identification", "government of india")):
            return True
    return False


# ── Orchestration: full document intelligence result ────────────────────────


def run_extraction(files: list[dict]) -> dict:
    """Run OCR + rule passes over a group of uploads for one candidate.

    files: [ {filename, file_type, data(bytes)} ... ]

    Files are classified as either Aadhaar-like or screenshot-like. Aadhaar
    fields come from Aadhaar lines; onboarding fields (mobile/role/hub/salary)
    come from screenshot lines, matching the Smart Upload spec.

    When bounding-box data is available (real file upload), the layout-aware
    Aadhaar parser is used for spatial extraction.  When OCRLine objects are
    unavailable (test mocks providing flat text via extract_text), the proven
    flat-text extraction path is used.

    Returns a structured, review-ready payload. Never auto-finalizes.
    """
    aadhaar_lines: list[str] = []
    screenshot_lines: list[str] = []
    aadhaar_ocr_lines: list = []  # OCRLine objects for layout parser
    per_file = []
    has_layout_data = False

    for f in files:
        ftype = (f.get("file_type") or "").lower()
        name = (f.get("filename") or "")
        fdata = f.get("data")
        if not fdata:
            per_file.append({"filename": name, "extracted": False, "reason": "No data"})
            continue

        # Try the layout-aware OCR path first
        ocr_lines = None
        try:
            ocr_lines = extract_ocr_lines(fdata, name)
            has_layout_data = True
        except Exception:
            pass

        # Always get flat text for screenshot fields and fallback
        try:
            lines = extract_text(fdata, name)
        except Exception as exc:  # noqa: BLE001 - fallback for OCR failures
            per_file.append({"filename": name, "extracted": False,
                             "reason": "Could not confidently read this document."})
            continue

        is_aadhaar = _looks_like_aadhaar(lines)
        if not lines:
            per_file.append({"filename": name, "extracted": False,
                             "reason": "Could not confidently read this document.",
                             "kind": "Unknown"})
            continue
        per_file.append({"filename": name, "extracted": bool(lines), "reason": None,
                         "kind": "Aadhaar" if is_aadhaar else "Screenshot"})
        if is_aadhaar:
            aadhaar_lines.extend(lines)
            if ocr_lines:
                aadhaar_ocr_lines.extend(ocr_lines)
        else:
            screenshot_lines.extend(lines)

    if not aadhaar_lines and not screenshot_lines:
        return _empty_result(files, per_file, fallback=True)

    # -- Aadhaar fields: prefer layout-aware parser when OCRLine data available
    aadhaar_num: Optional[str] = None
    name = ""
    name_extraction_conf = "Missing"
    dob: Optional[str] = None
    address = ""

    if aadhaar_ocr_lines and has_layout_data:
        try:
            layout = _extract_aadhaar_with_layout(aadhaar_ocr_lines)
            # Use layout results, falling back to flat-text if layout is weak
            name_ev = layout["name"]
            dob_ev = layout["dob"]
            addr_ev = layout["address"]
            num_ev = layout["aadhaar_number"]

            aadhaar_num = num_ev.value if num_ev.value else None
            name = name_ev.value or ""
            name_extraction_conf = name_ev.confidence if name else "Missing"
            dob = dob_ev.value if dob_ev.value else None
            address = addr_ev.value or ""

            _safe_log("run_extraction: layout parser used",
                      name_conf=name_extraction_conf,
                      has_aadhaar=bool(aadhaar_num),
                      has_dob=bool(dob),
                      has_address=bool(address))
        except Exception as exc:  # noqa: BLE001
            _safe_log("run_extraction: layout parser failed, using flat-text",
                      error=str(exc))
            # Fall through to flat-text extraction below

    # Flat-text extraction for Aadhaar (always run as fallback or for mock data)
    if not aadhaar_num:
        aadhaar_num = extract_aadhaar_num(aadhaar_lines) if aadhaar_lines else None
    if not name:
        name, name_extraction_conf = (extract_aadhaar_name(aadhaar_lines)
                                      if aadhaar_lines else ("", "Missing"))
    if not dob:
        dob = extract_dob(aadhaar_lines) if aadhaar_lines else None
    if not address:
        address = extract_address(aadhaar_lines) if aadhaar_lines else ""

    # -- Screenshot / document onboarding fields
    mobile, mobile_err = extract_mobile(screenshot_lines)
    salary = _extract_salary_candidates(screenshot_lines)
    role_text = extract_role_text(screenshot_lines)
    hub_text = extract_hub_text(screenshot_lines)

    aadhaar_seen = bool(aadhaar_lines)
    screenshot_seen = bool(screenshot_lines)

    _safe_log("run_extraction: classification complete",
              aadhaar_lines=len(aadhaar_lines),
              screenshot_lines=len(screenshot_lines),
              aadhaar_detected=aadhaar_seen,
              layout_data=has_layout_data)

    # -- Evidence scoring (multi-candidate ranking with confidence margin)
    evidence_data = {}
    try:
        from app.evidence import evidence_score_extraction, field_result_to_safe_dict
        from app.master_data import get_facility_names, get_hubs_for_cost_code, get_roles_for_cost_code
        all_hub_names = get_facility_names()
        hubs_for_cc = get_hubs_for_cost_code(resolved_cost_code(aadhaar_num, role_text, hub_text))
        allowed_roles = get_roles_for_cost_code(resolved_cost_code(aadhaar_num, role_text, hub_text))
        evidence_results = evidence_score_extraction(
            ocr_lines=aadhaar_ocr_lines if aadhaar_ocr_lines else [],
            flat_lines=aadhaar_lines + screenshot_lines,
            hub_text=hub_text,
            role_text=role_text,
            cost_code=resolved_cost_code(aadhaar_num, role_text, hub_text),
            all_hubs=all_hub_names,
            hubs_for_code=hubs_for_cc,
            allowed_roles=allowed_roles,
            role_aliases=rules.ROLE_ALIASES,
        )
        evidence_data = {k: field_result_to_safe_dict(v) for k, v in evidence_results.items()}
    except Exception:  # noqa: BLE001
        pass

    result = _build_result(
        name=name,
        name_extraction_conf=name_extraction_conf,
        mobile=mobile,
        mobile_err=mobile_err,
        aadhaar_num=aadhaar_num,
        dob=dob,
        address=address,
        salary=salary,
        role_text=role_text,
        hub_text=hub_text,
        aadhaar_seen=aadhaar_seen,
        screenshot_seen=screenshot_seen,
        files=files,
        per_file=per_file,
        evidence=evidence_data,
    )
    return result


def _looks_like_aadhaar(lines: list[str]) -> bool:
    joined = _combine_lines(lines).lower()
    return "aadhaar" in joined or "uidai" in joined or "unique identification" in joined or "government of india" in joined


def resolved_cost_code(aadhaar_num: Optional[str], role_text: Optional[str], hub_text: Optional[str]) -> str:
    """Quick cost-code resolution from available evidence (for evidence scoring)."""
    resolved = rules.resolve_smart_onboarding(role_text, hub_text)
    return resolved.get("cost_code", "")


def _build_result(
    name: str,
    name_extraction_conf: str,
    mobile: Optional[str],
    mobile_err: Optional[str],
    aadhaar_num: Optional[str],
    dob: Optional[str],
    address: str,
    salary: list[tuple[str, int]],
    role_text: Optional[str],
    hub_text: Optional[str],
    aadhaar_seen: bool,
    screenshot_seen: bool,
    files: list[dict],
    per_file: list[dict],
    evidence: Optional[dict] = None,
) -> dict:

    # --- Name: use the confidence from the extraction pass; never claim
    #     "High" if the extractor itself flagged uncertainty.
    name_val = name or ""
    name_conf = name_extraction_conf if name_val else "Missing"
    # Defensive: if someone managed to sneak through a rejected token, never
    # show High confidence.
    if name_val and name_val.strip().lower() in {"male", "female"}:
        _safe_log("_build_result: rejected gender-as-name", rejected=name_val)
        name_val = ""
        name_conf = "Missing"

    # --- Mobile
    if mobile:
        mobile_conf = "High"
        mobile_source = "Screenshot" if screenshot_seen else ("Aadhaar" if aadhaar_seen else "Manual")
    elif mobile_err:
        mobile_conf = "Review"
        mobile_source = "Screenshot"
    else:
        mobile_conf = "Missing"
        mobile_source = "Manual"

    # --- Aadhaar
    aadhaar_masked = _mask_aadhaar(aadhaar_num) if aadhaar_num else "XXXX XXXX ?"
    aadhaar_conf = "High" if aadhaar_num else "Missing"

    # --- DOB / address
    dob_val = dob or ""
    dob_conf = "High" if dob else ("Missing" if not aadhaar_seen else "Review")
    addr_val = address or ""
    addr_conf = "Review" if address else "Missing"

    # --- Hub + role + cost code resolution via required precedence
    resolved = rules.resolve_smart_onboarding(role_text, hub_text)
    facility_val = resolved["facility"]
    facility_source = resolved["facility_source"]
    facility_conf = "High" if facility_val else "Missing"
    location_code = rules.get_location_for_facility(facility_val) if facility_val else ""
    cost_code = resolved["cost_code"]
    cost_reason = resolved["cost_source"]
    cost_conf = "High" if cost_code else "Missing"
    role_val = resolved["role"]
    role_source = resolved["role_source"]
    role_conf = "Review" if role_val else "Missing"
    needs_attention = list(resolved["needs_attention"])
    resolved_entity = resolved.get("entity", "")
    resolved_myntra = resolved.get("myntra", False)

    # --- Salary
    salary_val: Optional[int] = None
    salary_display = ""
    salary_source = "Manual"
    salary_conf = "Missing"
    if salary:
        salary_val = salary[0][1]
        salary_display = salary[0][0]
        salary_source = "Screenshot" if screenshot_seen else "Aadhaar"
        salary_conf = "High"

    # --- Derived entity/operation/team/facility type
    info = rules.get_cost_code_info(cost_code) if cost_code else None
    # Entity from the recruiter text (Myntra detection) takes precedence; the
    # cost-code mapping is used as an authoritative fallback.
    entity = resolved_entity or (info["entity"] if info else "")
    operation = info["operation"] if info else ""
    team = info["team"] if info else ""
    facility_type = rules.COST_CODE_FACILITY_TYPE.get(cost_code, "Delivery Hub") if cost_code else ""

    # -- Build a functional payload for the review form
    safe_files = [
        {
            "filename": f.get("filename"),
            "file_type": f.get("file_type"),
            "file_size": f.get("file_size", 0),
        }
        for f in files
    ]

    _safe_log("_build_result: extraction summary — field rules matched & confidence",
              name_conf=name_conf,
              mobile_conf=mobile_conf,
              aadhaar_high=bool(aadhaar_num),
              dob_conf=dob_conf,
              addr_conf=addr_conf,
              role_conf=role_conf,
              hub_conf=facility_conf,
              cost_conf=cost_conf,
              salary_conf=salary_conf,
              cost_code=bool(cost_code))

    return {
        "name": {"value": name_val, "source": "Aadhaar" if aadhaar_seen else "Manual", "confidence": name_conf},
        "mobile": {"value": mobile or "", "source": mobile_source, "confidence": mobile_conf, "error": mobile_err},
        "cost_code": {"value": cost_code, "source": cost_reason, "confidence": cost_conf},
        "entity": entity,
        "operation": operation,
        "team": team,
        "facility_type": facility_type,
        "role": {"value": role_val, "source": role_source, "confidence": role_conf},
        "facility": {"value": facility_val, "source": facility_source, "confidence": facility_conf},
        "location_code": location_code,
        "salary": {
            "value": salary_val,
            "display": salary_display,
            "source": salary_source,
            "confidence": salary_conf,
        },
        "migrant": "No",
        "dob": {"value": dob_val, "source": "Aadhaar" if aadhaar_seen else "Manual", "confidence": dob_conf},
        "aadhaar_masked": aadhaar_masked,
        # Full Aadhaar stays in the data model only (payload keeps it private to
        # the app screen state, used to seed the master record on approval).
        "aadhaar_number": aadhaar_num or "",
        "address": {"value": addr_val, "source": "Aadhaar" if aadhaar_seen else "Manual", "confidence": addr_conf},
        "needs_attention": needs_attention,
        "source_files": safe_files,
        "per_file": per_file,
        "fallback": False,
        "raw_role_text": role_text or "",
        "raw_hub_text": hub_text or "",
        "evidence": evidence or {},
    }


def _empty_result(files: list[dict], per_file: list[dict], fallback: bool = True) -> dict:
    safe_files = [
        {
            "filename": f.get("filename"),
            "file_type": f.get("file_type"),
            "file_size": f.get("file_size", 0),
        }
        for f in files
    ]
    return {
        "name": {"value": "", "source": "Manual", "confidence": "Missing"},
        "mobile": {"value": "", "source": "Manual", "confidence": "Missing"},
        "cost_code": {"value": "", "source": "Manual", "confidence": "Missing"},
        "entity": "", "operation": "", "team": "", "facility_type": "",
        "role": {"value": "", "source": "Manual", "confidence": "Missing"},
        "facility": {"value": "", "source": "Manual", "confidence": "Missing"},
        "location_code": "",
        "salary": {"value": None, "display": "", "source": "Manual", "confidence": "Missing"},
        "migrant": "No",
        "dob": {"value": "", "source": "Manual", "confidence": "Missing"},
        "aadhaar_masked": "XXXX XXXX ?",
        "aadhaar_number": "",
        "address": {"value": "", "source": "Manual", "confidence": "Missing"},
        "needs_attention": [],
        "source_files": safe_files,
        "per_file": per_file,
        "fallback": True,
        "raw_role_text": "",
        "raw_hub_text": "",
        "evidence": {},
    }
