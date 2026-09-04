"""Parse eSampark result / error workbooks (Stage 4).

Both the automatic download path and the ``Import eSampark Result`` manual
fallback share this parser so behaviour is identical.

Design rules
------------
* The official **Creation Remarks** column is located **by header**, never by a
  fixed column letter.
* Candidate identifier columns (Mobile, Candidate Name, or another stable portal
  identifier) are also located by header.
* A header/table row is detected rather than assumed to be row 1 (portal exports
  occasionally have title rows above the header).
* Rows are matched back to SQLite candidates using the preferred order:
    1. Mobile number
    2. another unique portal identifier (if the API exposes one)
    3. Candidate Name (fallback only)
* Sensitive/full data from the workbook is never written to logs.
"""

from __future__ import annotations

import io
from typing import Optional

# Candidate identifier columns we know how to match, in priority order.
IDENTIFIER_HEADERS = {
    # header_lower: field_key
    "mobile": "mobile",
    "mobile number": "mobile",
    "phone": "mobile",
    "phone number": "mobile",
    "contact": "mobile",
    "candidate name": "name",
    "candidate_name": "name",
    "name": "name",
    "employee name": "name",
    "portal id": "portal_id",
    "portal reference": "portal_id",
    "reference id": "portal_id",
    "reference number": "portal_id",
    "application id": "portal_id",
}

# Official column whose free-text ("Creation Remarks") we must preserve.
CREATION_REMARKS_HEADERS = (
    "creation remarks",
    "creationreason",
    "remarks",
    "failure reason",
    "error message",
    "message",
)

# Internal mapped statuses.
SUCCESS = "Success"
FAILED = "Failed"
PARTIAL_FAILURE = "Partial Failure"
UNKNOWN = "Unknown"


class ResultParseError(Exception):
    """Raised when a result workbook cannot be parsed."""


def _load_workbook(source):
    """Load an openpyxl workbook from a path or raw bytes."""
    import openpyxl

    if isinstance(source, (bytes, bytearray)):
        return openpyxl.load_workbook(io.BytesIO(bytes(source)), data_only=True)
    return openpyxl.load_workbook(str(source), data_only=True)


def _find_header_row(ws, col_count: int) -> Optional[int]:
    """Return the 1-based header row for the active sheet.

    Looks for a row with at least one identifier or Creation-Remarks header,
    scanning the first few rows to skip title/logo rows.
    """
    for row_idx in range(1, min(ws.max_row, 8) + 1):
        seen = 0
        for col in range(1, ws.max_column + 1):
            val = str(ws.cell(row=row_idx, column=col).value or "").strip().lower()
            if val in IDENTIFIER_HEADERS or val in CREATION_REMARKS_HEADERS:
                seen += 1
        if seen:
            return row_idx
    return None


def _col_map(ws, header_row: int) -> dict:
    """Build {header_lower -> column} for the detected header row."""
    if header_row is None:
        return {}
    m = {}
    for col in range(1, ws.max_column + 1):
        h = str(ws.cell(row=header_row, column=col).value or "").strip().lower()
        if h:
            # Map a "Status"/"Result" column too.
            m[h] = col
    return m


def detect_columns(ws, header_row: int) -> dict:
    """Return a dict of located columns.

    Keys: ``creation_remarks``, ``mobile``, ``name``, ``portal_id``, ``status``
    (status/result/success columns). Values are 1-based column indexes or None.
    """
    m = _col_map(ws, header_row)

    def find(*keys):
        for k in keys:
            if k in m:
                return m[k]
        return None

    remarks = None
    for h in CREATION_REMARKS_HEADERS:
        if h in m:
            remarks = m[h]
            break

    mobile_col = None
    name_col = None
    portal_id_col = None
    for h, key in IDENTIFIER_HEADERS.items():
        if h not in m:
            continue
        col = m[h]
        if key == "mobile" and mobile_col is None:
            mobile_col = col
        elif key == "name" and name_col is None:
            name_col = col
        elif key == "portal_id" and portal_id_col is None:
            portal_id_col = col

    status_col = find("status", "result", "upload status", "onboarding status",
                      "creation status", "success")
    return {
        "creation_remarks": remarks,
        "mobile": mobile_col,
        "name": name_col,
        "portal_id": portal_id_col,
        "status": status_col,
    }


def parse_workbook(source) -> list[dict]:
    """Parse a result/error workbook into per-row dicts.

    Each row dict has the parsed values:
        mobile, name, portal_id, creation_remarks, status
    (None when the column is not present in the workbook).
    """
    try:
        wb = _load_workbook(source)
    except Exception as exc:  # noqa: BLE001
        raise ResultParseError(f"Could not open result workbook: {exc}") from exc

    if not wb.sheetnames:
        raise ResultParseError("Result workbook has no sheets.")
    ws = wb[wb.sheetnames[0]]
    header_row = _find_header_row(ws, ws.max_column)
    if header_row is None:
        raise ResultParseError(
            "Could not locate a header row with Creation Remarks or a candidate identifier."
        )
    cols = detect_columns(ws, header_row)
    if cols["creation_remarks"] is None and cols["mobile"] is None and cols["name"] is None:
        raise ResultParseError(
            "Result workbook has no Creation Remarks or candidate identifier columns."
        )

    rows: list[dict] = []
    for r in range(header_row + 1, ws.max_row + 1):

        def cell(col):
            if col is None:
                return None
            v = ws.cell(row=r, column=col).value
            return None if v is None else str(v).strip()

        row = {
            "mobile": cell(cols["mobile"]),
            "name": cell(cols["name"]),
            "portal_id": cell(cols["portal_id"]),
            "creation_remarks": cell(cols["creation_remarks"]),
            "status": cell(cols["status"]),
        }
        if any(v not in (None, "") for v in row.values()):
            rows.append(row)
    return rows


# ── Candidate matching ────────────────────────────────────────────────────────


def normalize_identifier(value) -> str:
    """Best-effort normalization of a mobile/candidate identifier for matching."""
    if value is None:
        return ""
    # Keep only digits for phones; otherwise strip and collapse spaces.
    if any(ch.isdigit() for ch in str(value)) and all(
        ch.isdigit() or ch in " +-()" for ch in str(value)
    ):
        return "".join(ch for ch in str(value) if ch.isdigit())
    return " ".join(str(value).strip().lower().split())


def match_parsed_rows(parsed_rows, candidates) -> dict:
    """Match parsed result rows back to candidate records.

    Matching priority per spec:
        1. Mobile number
        2. Other unique portal identifier (portal_id)
        3. Candidate Name (fallback)

    ``candidates`` is a list of candidate dicts each with at least
    ``candidate_id``. Returns::

        {
          "matches": [ {candidate_id, row, matched_by, portal_status,
                        portal_remarks} ... ],
          "unmatched": [ row, ... ],
        }

    ``portal_status`` is ``FAILED`` when there is a Creation Remarks / failure
    marker, else ``SUCCESS`` (a result-file row without remarks is treated as a
    success, i.e. "Link Generated"). The caller decides how to persist.
    """
    # Build lookup indexes from candidates.
    by_mobile: dict[str, dict] = {}
    by_portal: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for c in candidates:
        mob = normalize_identifier(c.get("mobile"))
        if mob:
            by_mobile.setdefault(mob, c)
        pid = normalize_identifier(c.get("portal_id") or c.get("portal_reference"))
        if pid:
            by_portal.setdefault(pid, c)
        nm = normalize_identifier(c.get("name"))
        if nm:
            by_name.setdefault(nm, c)

    used = set()
    matches = []
    unmatched = []
    for row in parsed_rows:
        matched = None
        matched_by = None

        m = normalize_identifier(row.get("mobile"))
        if m and m in by_mobile and m not in used:
            matched = by_mobile[m]
            matched_by = "mobile"

        if matched is None:
            p = normalize_identifier(row.get("portal_id"))
            if p and p in by_portal:
                matched = by_portal[p]
                matched_by = "portal_id"

        if matched is None:
            n = normalize_identifier(row.get("name"))
            if n and n in by_name:
                matched = by_name[n]
                matched_by = "name"

        if matched is None:
            unmatched.append(row)
            continue

        cid = matched["candidate_id"]
        if cid in used:
            unmatched.append(row)
            continue
        used.add(cid)

        remarks = row.get("creation_remarks")
        status_raw = (row.get("status") or "").lower()
        is_failure = bool(remarks) or any(
            k in status_raw for k in ("fail", "error", "reject", "invalid")
        )
        matches.append({
            "candidate_id": cid,
            "name": matched.get("name"),
            "matched_by": matched_by,
            "portal_status": FAILED if is_failure else SUCCESS,
            "portal_remarks": remarks or "",
            "status_raw": row.get("status"),
        })

    return {"matches": matches, "unmatched": unmatched}
