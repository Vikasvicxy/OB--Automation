"""Self Onboarding + TeamHR Backend Mail Excel pair generation + daily master.

Stage 3: generate the official Self Onboarding Excel and the TeamHR Backend
Mail workbook TOGETHER from the SAME approved/Ready candidates, and produce a
daily operational master export (masked Aadhaar) for internal tracking.

Design invariants
-----------------
* The configured templates are NEVER modified. We open a copy, write candidate
  rows into it, and save the generated output separately.
* Both files share ONE timestamp in their names so a pair is instantly
  recognizable: ``Self_Onboarding_<ts>.xlsx`` + ``TeamHR_OB_<ts>.xlsx``.
  Generation is transactional: if either workbook cannot be built or verified,
  NO files are left behind and the pair is recorded as failed.
* Only candidates with status ``ready`` are included. Any candidate that is
  Draft / Needs Attention (or fails validation) is excluded and reported.
* Backend generation is blocked when any Ready candidate is missing a required
  backend field (recruiter, DOJ, gender, PIN, full Aadhaar, etc.) — we never
  silently guess.
* Full Aadhaar appears ONLY inside the backend workbook. It is never written to
  filenames, folders, the daily master, or any log/audit trail.
* Location/Branch comes from the Facility / Location Master (never the facility
  name). Facility Type follows the cost-code rule: Last Mile -> Delivery Hub,
  First Mile -> Pickup Hub; 8752 vehicles remain "Needs Review".
* Configuration (output base folder + recruiter profile) is stored in
  ``data/config.json``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app import database
from app import rules
from app import master_data

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "generated"
CONFIG_FILE = BASE_DIR / "data" / "config.json"

# Optional test override: point the runtime config file at a temporary path so
# test runs never mutate the production/local ``data/config.json``. When unset,
# the production config file is used (unchanged behaviour).
CONFIG_FILE_ENV = "TEAMHR_CONFIG_FILE"


def config_file() -> Path:
    override = os.environ.get(CONFIG_FILE_ENV, "").strip()
    if override:
        return Path(override)
    return CONFIG_FILE

TEMPLATE_FILE = master_data.MASTERS_DIR / master_data.MASTER_FILES["self_onboarding"]
BACKEND_TEMPLATE_NAME = "TeamHR_OB_Template.xlsx"
TEMPLATE_BACKEND_FILE = master_data.MASTERS_DIR / BACKEND_TEMPLATE_NAME

# Authoritative single-workbook template (the ONLY template the UI uses now).
TEMPLATES_DATA_DIR = BASE_DIR / "data" / "templates"
EXCEL_TEMPLATE_NAME = "Excel Generation.xlsx"
EXCEL_GENERATION_FILE = TEMPLATES_DATA_DIR / EXCEL_TEMPLATE_NAME

# The two sheets inside the Excel Generation workbook.
OB_FORMAT_SHEET = "OB Format"
MAIL_FORMAT_SHEET = "Mail Format"

# Authoritative OB Format columns (13, in this exact order — from the template).
OB_FORMAT_COLUMNS = [
    "Sl No",
    "Name*",
    "Mobile Number*",
    "Team*",
    "Cost Code*",
    "Facility Type*",
    "Line of Business*",
    "Sub Type*",
    "Role - Designation*",
    "Fixed Net Take Home*",
    "State*",
    "Facility*",
    "Contractor*",
]

# Authoritative Mail Format columns (14, in this exact order — from the template).
MAIL_FORMAT_COLUMNS = [
    "Date of Joining",
    "Name",
    "Mobile No",
    "Designation",
    "Branch",
    "Vertical",
    "State",
    "Net Salary",
    "Aadhar No",
    "DOB",
    "Fathers Name",
    "Address",
    "Pin Code",
    "Gender",
]

# Current fixed business values (validated by the app / production workbook).
DEFAULT_MIGRANT = "No"
DEFAULT_CONTRACTOR = "TEAM HR GSA PRIVATE LIMITED"
DEFAULT_LOB = "EKART"
DEFAULT_SUB_TYPE = "EKART"

# Self-Onboarding workbook display strings (uppercase from the official file).
STATE_SO = "KARNATAKA"
MIGRANT_SO = "No"
CONTRACTOR_SO = DEFAULT_CONTRACTOR

# Location-code city -> State rule. City is the first segment of the location
# code (e.g. ``BLR/NLM`` -> ``BLR``). Extend as new cities are onboarded.
CITY_STATE = {
    "BLR": "Karnataka",
}


# ── Configuration (output base folder + recruiter profile) ──────────────────


def _load_config() -> dict:
    cf = config_file()
    try:
        if cf.exists():
            return json.loads(cf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_config(cfg: dict) -> None:
    cf = config_file()
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get_output_base_dir() -> Path:
    cfg = _load_config()
    p = cfg.get("output_base_dir")
    return Path(p) if p else DEFAULT_OUTPUT_DIR


def set_output_base_dir(path: str) -> str:
    p = str(path or "").strip()
    if not p:
        p = str(DEFAULT_OUTPUT_DIR)
    cfg = _load_config()
    cfg["output_base_dir"] = p
    _save_config(cfg)
    return p


def get_recruiter_profile() -> dict:
    """Return the configured default recruiter profile.

    ``{name, ...}``. A candidate may override recruiter_name per record; the
    profile is only the default used when a candidate has none.
    """
    cfg = _load_config()
    return dict(cfg.get("recruiter_profile") or {})


def get_default_recruiter_name() -> str:
    return (get_recruiter_profile().get("name") or "").strip()


def save_recruiter_profile(name: str) -> str:
    """Persist the default recruiter name into the runtime config file.

    Only the name is stored (no other personal data). Empty name clears the
    profile so the first-run "Set Recruiter Name" prompt reappears.
    """
    name = str(name or "").strip()
    cfg = _load_config()
    if name:
        cfg["recruiter_profile"] = {"name": name}
    else:
        cfg.pop("recruiter_profile", None)
    _save_config(cfg)
    return name


def get_output_config() -> dict:
    """Public snapshot for Settings -> Excel Output / Recruiter Profile."""
    out_dir = get_output_base_dir()
    last = _last_generated()
    return {
        "output_base_dir": str(out_dir),
        "default_output_base_dir": str(DEFAULT_OUTPUT_DIR),
        "template_configured": template_is_configured(),
        "template_file": str(TEMPLATE_FILE),
        "backend_template_configured": template_backend_is_configured(),
        "backend_template_file": str(TEMPLATE_BACKEND_FILE),
        "excel_template_configured": excel_template_is_configured(),
        "excel_template_file": str(EXCEL_GENERATION_FILE),
        "recruiter_name": get_default_recruiter_name(),
        "captured": last,
    }


# ── Template ────────────────────────────────────────────────────────────────


def template_is_configured() -> bool:
    return TEMPLATE_FILE.exists()


def excel_template_is_configured() -> bool:
    return EXCEL_GENERATION_FILE.exists()


def template_backend_is_configured() -> bool:
    return TEMPLATE_BACKEND_FILE.exists()


def _template_version(path: Optional[Path] = None) -> str:
    """Best-effort template version (file mtime) for the audit log."""
    try:
        ts = (path or EXCEL_GENERATION_FILE).stat().st_mtime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return ""


# ── State derivation ─────────────────────────────────────────────────────────


def derive_state(candidate: dict) -> str:
    """Return the State for a candidate.

    Prefer an explicit candidate ``state`` if present; otherwise derive from
    the Location Code's city via the configured city->state rule. The HubName
    Facility / Location Master is the Karnataka operations list, so a facility
    that resolves to a master row defaults to Karnataka. If no State can be
    determined the empty string is returned (caller flags Needs Attention and
    excludes the candidate — we never silently guess).
    """
    s = (candidate.get("state") or "").strip()
    if s:
        return s
    loc = (candidate.get("location_code") or "").strip()
    if loc and "/" in loc:
        city = loc.split("/", 1)[0].strip().upper()
        if city in CITY_STATE:
            return CITY_STATE[city]
    facility = (candidate.get("facility_name") or "").strip()
    if facility:
        try:
            row = master_data.get_effective_facility(facility)
        except Exception:  # noqa: BLE001
            row = None
        if row is not None:
            return "Karnataka"
    return ""


def derive_state_so(candidate: dict) -> str:
    """Self-Onboarding workbook State* value (uppercase, e.g. KARNATAKA)."""
    state = derive_state(candidate)
    return state.upper() if state else ""


# ── Folder + filename helpers ───────────────────────────────────────────────


def get_date_stamp(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d")


def build_date_folders(now: Optional[datetime] = None) -> dict:
    """Create and return the date folders: ``Y-M-D/{uploads,backend_mail,results,errors}``."""
    now = now or datetime.now()
    date_folder = now.strftime("%Y-%m-%d")
    base = get_output_base_dir() / date_folder
    uploads = base / "uploads"
    backend_mail = base / "backend_mail"
    results = base / "results"
    errors = base / "errors"
    for d in (base, uploads, backend_mail, results, errors):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "base": str(base),
        "date_folder": date_folder,
        "uploads": str(uploads),
        "backend_mail": str(backend_mail),
        "results": str(results),
        "errors": str(errors),
    }


def build_pair_timestamp(now: Optional[datetime] = None) -> str:
    """Shared timestamp token for BOTH workbooks: ``YYYY-MM-DD_HH-MM-SS``."""
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d_%H-%M-%S")


def build_filename(batch_id: int, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return "OB_BATCH-{batch:04d}_{date}_{time}.xlsx".format(
        batch=batch_id,
        date=now.strftime("%Y-%m-%d"),
        time=now.strftime("%H-%M-%S"),
    )


def self_onboarding_filename(ts: str) -> str:
    return f"Self_Onboarding_{ts}.xlsx"


def backend_filename(ts: str) -> str:
    return f"TeamHR_OB_{ts}.xlsx"


def onboarding_filename(ts: str) -> str:
    """Single-workbook filename: ONE timestamped file with both sheets."""
    return f"Onboarding_{ts}.xlsx"


def _unique_path(path: Path) -> Path:
    """Return ``path`` or a collision-avoiding ``_N``-suffixed variant."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    counter = 1
    while True:
        candidate = path.parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def result_file_for(filename: str, kind: str = "RESULT") -> str:
    """Future portal result/error filename (same stem, suffix marker)."""
    stem = Path(filename).stem
    return f"{stem}_{kind}.xlsx"


# ── Validation ──────────────────────────────────────────────────────────────

# Authoritative Self-Onboarding columns (production self-onboarding workbook).
_TEMPLATE_COLUMNS = [
    "Sl No",
    "Name*",
    "Mobile Number*",
    "Team*",
    "Cost Code*",
    "Migrant Bonus*",
    "Facility Type*",
    "Line of Business*",
    "Sub Type*",
    "Role - Designation*",
    "Fixed Net Take Home*",
    "State*",
    "Facility*",
    "Contractor*",
]

# Authoritative TeamHR Backend Mail columns (16 exact, in this order).
BACKEND_COLUMNS = [
    "Recruiter Name",
    "Date of Joining",
    "Name",
    "Mobile No",
    "Designation",
    "Branch",
    "Vertical",
    "State",
    "Net Salary",
    "Aadhar No",
    "DOB",
    "Fathers Name",
    "Address",
    "Pin Code",
    "Gender",
    "UAN NO",
]


def facility_type_display_for(candidate: dict) -> str:
    """Canonical Facility Type* value for the Self-Onboarding workbook.

    Resolved via the effective facility master first (authoritative), then the
    cost-code rule. Empty result means the candidate is flagged "Needs Review".
    """
    master_row = None
    try:
        master_row = master_data.get_effective_facility(candidate.get("facility_name") or "")
    except Exception:  # noqa: BLE001
        master_row = None
    value = rules.facility_type_output(candidate.get("cost_code") or "", master_row)
    return value or rules.FACILITY_TYPE_REVIEW


def validate_candidates(candidates: list[dict]) -> dict:
    """Validate candidates for generation.

    Returns {rows, errors, blocked} where ``rows`` is the validated list of
    row-dicts (exact values) and ``errors`` maps candidate_id -> list of messages.
    If any individual candidate fails, it is excluded (not partially written).
    """
    rows = []
    errors: dict[int, list[str]] = {}
    blocked = False

    for c in candidates:
        cid = c["candidate_id"]
        errs: list[str] = []

        name = (c.get("name") or "").strip()
        if not name:
            errs.append("Candidate name is missing.")

        mobile, mobile_err = rules.normalize_mobile(c.get("mobile", ""))
        if mobile_err:
            errs.append(f"Invalid mobile: {mobile_err}")

        cost_code = c.get("cost_code") or ""
        if cost_code not in rules.COST_CODES:
            errs.append(f"Invalid cost code: {cost_code}")

        designation = c.get("designation") or ""
        designations = rules.get_roles_for_cost_code(cost_code) if cost_code else []
        if designation not in designations:
            errs.append(
                f"Designation '{designation}' is not valid for cost code {cost_code}."
            )

        facility = c.get("facility_name") or ""
        if cost_code == "8752":
            errs.append("No Myntra First Mile facility is configured yet (Needs Review).")
        else:
            hubs = rules.get_hubs_for_cost_code(cost_code)
            if facility not in hubs:
                errs.append(f"Facility '{facility}' is not valid for cost code {cost_code}.")

        if not c.get("location_code"):
            errs.append("Location code is missing.")

        # Facility Type must match the cost-code rule (Delivery Hub / Pickup Hub).
        expected_display = rules.facility_type_display(cost_code)
        facility_type = c.get("facility_type") or ""
        if not expected_display:
            errs.append(f"Facility Type for cost code {cost_code} needs review.")
        elif facility_type and facility_type not in ("Delivery Hub", "Pickup Hub"):
            errs.append(f"Facility Type must be 'Delivery Hub' or 'Pickup Hub'.")

        migrant = c.get("migrant") or ""
        if migrant and migrant != DEFAULT_MIGRANT:
            errs.append(f"Migrant must be '{DEFAULT_MIGRANT}'.")

        # No invalid Myntra Prexo on 8751.
        if cost_code == "8751" and "PREXO" in designation.upper():
            errs.append("Prexo Delivery Executive is not allowed for cost code 8751.")

        state = derive_state(c)
        if not state:
            errs.append("State is not available (marking Needs Attention).")

        if errs:
            errors[cid] = errs
            blocked = True
            continue

        rows.append({
            "candidate_id": cid,
            "name": name,
            "mobile": mobile,
            "team": c.get("team") or "",
            "cost_code": cost_code,
            "designation": designation,
            "migrant": c.get("migrant") or DEFAULT_MIGRANT,
            "facility_type": facility_type_display_for(c),
            "lob": c.get("lob") or DEFAULT_LOB,
            "sub_type": c.get("sub_type") or DEFAULT_SUB_TYPE,
            "state": STATE_SO if derive_state(c).upper() == STATE_SO else derive_state(c).upper(),
            "salary": c.get("salary") or c.get("salary_display") or "",
            "contractor": c.get("contractor") or DEFAULT_CONTRACTOR,
            "facility_name": facility,
            "location_code": c.get("location_code") or "",
            "entity": c.get("entity") or "",
            "operation": c.get("operation") or "",
        })

    # No duplicate candidate row in the same generation.
    seen_ids = set()
    dup_ids = []
    for r in rows:
        if r["candidate_id"] in seen_ids:
            dup_ids.append(r["candidate_id"])
        seen_ids.add(r["candidate_id"])
    rows = [r for r in rows if r["candidate_id"] not in dup_ids]
    for cid in dup_ids:
        errors.setdefault(cid, []).append("Duplicate candidate in same generation.")

    return {"rows": rows, "errors": errors, "blocked": blocked}


def _find_header_row(ws, require_cost_code: bool = True) -> Optional[int]:
    """Locate the header row (row index 1-based) in the first few rows.

    Header cells may carry a ``*`` required marker (e.g. ``Name*``); markers
    are stripped for comparison. Requires 'name' + one of the mobile-column
    spellings + 'cost code' (the OB Format sheet) to avoid matching a
    Lists/reference sheet. The Mail Format sheet has no cost-code column, so
    ``require_cost_code`` may be disabled for it.
    """
    for row_idx in range(1, min(ws.max_row, 5) + 1):
        cells = []
        for c in range(1, ws.max_column + 1):
            raw = str(ws.cell(row=row_idx, column=c).value or "").strip().lower()
            cells.append(raw.rstrip("*"))
        if "name" not in cells:
            continue
        if not ("mobile" in cells or "mobile number" in cells
                or "mobile no" in cells):
            continue
        if require_cost_code and "cost code" not in cells:
            continue
        return row_idx
    return None


def _clean_header(value) -> str:
    """Header cell -> normalized key (lowercase, trailing * stripped)."""
    return str(value or "").strip().lower().rstrip("*")


def _map_row_to_columns(ws, header_row: int, row_data: dict) -> dict:
    """Map row_data onto template columns by matching header names."""
    col_map = {}
    for c in range(1, ws.max_column + 1):
        header = _clean_header(ws.cell(row=header_row, column=c).value)
        if header:
            col_map[header] = c
    # Template header (normalized) -> row_data field. Covers both the
    # authoritative 14-column spec and any legacy template spellings.
    mapping = {
        "sl no": "sl_no",
        "name": "name",
        "mobile number": "mobile",
        "mobile": "mobile",
        "mobile no": "mobile",
        "team": "team",
        "cost code": "cost_code",
        "migrant bonus": "migrant",
        "migrant": "migrant",
        "facility type": "facility_type",
        "line of business": "lob",
        "sub type": "sub_type",
        "role - designation": "designation",
        "role / designation": "designation",
        "role": "designation",
        "fixed net take home": "salary",
        "fixed net take-home": "salary",
        "net salary": "salary",
        "state": "state",
        "facility": "facility_name",
        "contractor": "contractor",
        "facility / location code": "location_code",
        "location code": "location_code",
    }
    out = {}
    for header, field in mapping.items():
        col = col_map.get(header)
        if not col:
            continue
        val = row_data.get(field, "")
        if field == "sl_no":
            val = row_data.get("sl_no", "")
        out[col] = "" if val is None else val
    return out


# ── Generation ──────────────────────────────────────────────────────────────


def build_backend_rows(rows: list[dict], candidates_by_id: dict) -> dict:
    """Build the TeamHR Backend Mail rows for the approved candidates.

    Returns {rows, problems}. ``rows`` are serializable backend row-dicts;
    ``problems`` maps candidate_id -> list of blocking messages. Backend key
    values come from the candidate DB record (full Aadhaar, DOB, address,
    recruiter, DOJ, gender, PIN, father name, UAN) and the validated row
    (name, mobile, designation, branch, vertical, state, salary) — never
    inferred here.
    """
    out_rows = []
    problems: dict[int, list[str]] = {}
    for r in rows:
        cid = r["candidate_id"]
        cand = candidates_by_id.get(cid)
        if cand is None:
            problems[cid] = ["Candidate record missing."]
            continue
        # Validate the complete backend record: static PII from the candidate
        # row plus the derived/validated values from the Self-Onboarding row.
        merged = {**cand, **r}
        problems[cid] = rules.validate_backend_candidate(merged)
        if problems[cid]:
            continue
        salary, _ = rules.normalize_salary(str(r.get("salary") or cand.get("salary") or ""))
        out_rows.append({
            "candidate_id": cid,
            "recruiter_name": (cand.get("recruiter_name") or "").strip(),
            "date_of_joining": rules.normalize_doj(cand.get("doj"))[0],
            "name": r["name"],
            "mobile": r["mobile"],
            "designation": r["designation"],
            "branch": r["location_code"],
            "vertical": r["facility_name"],
            "state": (r.get("state") or derive_state(cand)).title(),
            "salary": salary if salary is not None else "",
            "aadhaar_number": (cand.get("aadhaar_number") or "").strip(),
            "dob": rules.parse_date(cand.get("dob")),
            "father_name": rules.normalize_father_name(cand.get("father_name"))[0],
            "address": (cand.get("address") or "").strip(),
            "pin_code": rules.normalize_pin_code(cand.get("pin_code"))[0],
            "gender": rules.normalize_gender(cand.get("gender"))[0],
            "uan_no": rules.normalize_uan(cand.get("uan_no"))[0],
        })
    return {"rows": out_rows, "problems": problems}


def build_self_onboarding_workbook(rows: list[dict]) -> tuple:
    """Open a copy of the Self-Onboarding template and write candidate rows.

    Returns ``(wb, ws, header_row, written_count)`` or raises on failure.
    Column values are mapped by header name so the workbook never leaves the
    official structure intact. Mobile/Facility cells stay text-typed.
    """
    import openpyxl.styles as _s
    from openpyxl import load_workbook

    wb = load_workbook(str(TEMPLATE_FILE))
    ws = wb.worksheets[0]
    header_row = _find_header_row(ws)
    if header_row is None:
        wb.close()
        raise ValueError("Self-Onboarding template header columns not found.")
    next_row = header_row + 1
    while ws.cell(row=next_row, column=1).value not in (None, ""):
        next_row += 1

    mobile_col = None
    facility_col = None
    for c in range(1, ws.max_column + 1):
        header = _clean_header(ws.cell(row=header_row, column=c).value)
        if header == "mobile number":
            mobile_col = c
        if header == "facility":
            facility_col = c

    for idx, rdata in enumerate(rows, start=1):
        row_data = dict(rdata)
        row_data["sl_no"] = idx
        mapped = _map_row_to_columns(ws, header_row, row_data)
        for col, val in mapped.items():
            cell = ws.cell(row=next_row, column=col)
            cell.value = "" if val is None else val
        # Text-typed identifier columns (keeps numbers visible, no sci-notation).
        if mobile_col:
            ws.cell(row=next_row, column=mobile_col).number_format = "@"
        if facility_col:
            ws.cell(row=next_row, column=facility_col).number_format = "@"
        next_row += 1

    return wb, ws, header_row, len(rows)


def build_backend_workbook(rows: list[dict]) -> tuple:
    """Create the TeamHR Backend Mail workbook from the official template.

    Writes the 16 authoritative headers (bold, light fill, borders, widths)
    and one data row per candidate. Dates are real Excel dates; mobile,
    Aadhaar and UAN are explicit TEXT (no scientific notation); salary is
    numeric with a thousand separator. Returns (wb, ws, header_row, count).
    """
    import openpyxl.styles as _s
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    if TEMPLATE_BACKEND_FILE.exists():
        wb = load_workbook(str(TEMPLATE_BACKEND_FILE))
        ws = wb.worksheets[0]
    else:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "TeamHR Backend Mail"

    header_row = 1
    if ws.max_row and any(str(ws.cell(row=1, column=c).value or "").strip() for c in range(1, ws.max_column + 1)):
        header_row = 1
    else:
        header_row = 1

    # Write authoritative headers (a pre-filled template keeps identical text).
    for c in range(len(BACKEND_COLUMNS)):
        ws.cell(row=header_row, column=c + 1, value=BACKEND_COLUMNS[c])

    # Header styling.
    header_fill = _s.PatternFill("solid", fgColor="D9E1F2")
    header_font = _s.Font(bold=True)
    thin = _s.Side(style="thin")
    border = _s.Border(left=thin, right=thin, top=thin, bottom=thin)
    for c in range(1, len(BACKEND_COLUMNS) + 1):
        cell = ws.cell(row=header_row, column=c)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = _s.Alignment(horizontal="center", vertical="center")

    # Column widths (approximate visual layout of the official workbook).
    widths = {
        1: 18, 2: 16, 3: 22, 4: 13, 5: 30, 6: 16, 7: 30, 8: 14,
        9: 14, 10: 18, 11: 14, 12: 22, 13: 45, 14: 12, 15: 12, 16: 16,
    }
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w

    start = header_row + 1
    for idx, b in enumerate(rows):
        row_num = start + idx
        values = [
            b["recruiter_name"],
            _as_date(b.get("date_of_joining")),
            b["name"],
            b["mobile"],
            b["designation"],
            b["branch"],
            b["vertical"],
            b["state"],
            b["salary"],
            b["aadhaar_number"],
            _as_date(b.get("dob")),
            b["father_name"],
            b["address"],
            b["pin_code"],
            b["gender"],
            b["uan_no"],
        ]
        for col, val in enumerate(values, start=1):
            cell = ws.cell(row=row_num, column=col)
            col_letter = get_column_letter(col)
            if col_letter in ("D", "J", "P"):  # Mobile No, Aadhar No, UAN NO -> TEXT
                cell.number_format = "@"
                cell.value = str(val) if val not in (None, "") else ""
            elif col_letter in ("B", "K"):     # Date of Joining, DOB -> real dates
                cell.number_format = "DD/MM/YYYY"
                cell.value = val or None
            elif col_letter == "I":            # Net Salary -> number
                cell.number_format = "#,##0"
                cell.value = val if val not in (None, "") else None
            else:
                cell.value = "" if val is None else val
            cell.border = border
            cell.alignment = _s.Alignment(vertical="center")

    return wb, ws, header_row, len(rows)


def _as_date(value: object):
    """Convert a YYYY-MM-DD string (or datetime) into a ``datetime.date``."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    parsed = rules.parse_date(value)
    if not parsed:
        return None
    try:
        return datetime.strptime(parsed, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _save_workbook_checked(wb, path: Path, expect_rows: int, headers: list[str],
                           col_count: int, another_sheet: Optional[list[str]] = None) -> str:
    """Save ``wb`` to ``path`` (tmp+rename) then re-open and verify it.

    Verification asserts the sheet exists, headers match exactly, and the
    expected number of data rows is present. When ``another_sheet`` is given it
    is verified on the matching second worksheet too. The file is written
    atomically (tmp then rename) so a failed build never leaves a corrupt
    workbook.
    """
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        wb.save(str(tmp_path))
        tmp_path.replace(path)
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
    # Re-open and verify.
    import openpyxl
    from openpyxl import load_workbook
    wb2 = load_workbook(str(path))
    try:
        ws = wb2.worksheets[0]
        actual_headers = [_clean_header(ws.cell(row=1, column=c).value)
                          for c in range(1, col_count + 1)]
        expected = [_clean_header(h) for h in headers]
        if actual_headers != expected:
            raise ValueError(
                f"Workbook headers do not match the official template: {actual_headers}"
            )
        data_rows = ws.max_row - 1
        if data_rows < expect_rows:
            raise ValueError(
                f"Expected {expect_rows} data rows but workbook has {data_rows}."
            )
        if another_sheet is not None:
            extra_ws = None
            for sws in wb2.worksheets:
                if sws is not ws:
                    extra_ws = sws
                    break
            if extra_ws is None:
                raise ValueError("Workbook is missing the second sheet.")
            extra_headers = [_clean_header(extra_ws.cell(row=1, column=c).value)
                             for c in range(1, len(another_sheet) + 1)]
            extra_expected = [_clean_header(h) for h in another_sheet]
            if extra_headers != extra_expected:
                raise ValueError(
                    f"Second sheet headers do not match the official template: "
                    f"{extra_headers}"
                )
    finally:
        wb2.close()
    return str(path)


def _find_sheet_by_title(wb, sheet_name: str):
    """Return the worksheet whose TITLE equals ``sheet_name`` (case-insensitive)."""
    lower = sheet_name.lower()
    for ws in wb.worksheets:
        if (ws.title or "").strip().lower() == lower:
            return ws
    return None


def build_mail_rows(rows: list[dict], candidates_by_id: dict) -> dict:
    """Build the Mail Format rows for the approved candidates.

    Returns {rows, problems}. ``rows`` are serializable mail-row dicts;
    ``problems`` maps candidate_id -> list of blocking messages. Values come
    from the candidate DB record (full Aadhaar, DOB, address, DOJ, gender,
    PIN, father name) and the validated row (name, mobile, designation, branch,
    vertical, state, salary) — never inferred here.
    """
    out_rows = []
    problems: dict[int, list[str]] = {}
    for r in rows:
        cid = r["candidate_id"]
        cand = candidates_by_id.get(cid)
        if cand is None:
            problems[cid] = ["Candidate record missing."]
            continue
        merged = {**cand, **r}
        problems[cid] = rules.validate_backend_candidate(merged)
        if problems[cid]:
            continue
        salary, _ = rules.normalize_salary(str(r.get("salary") or cand.get("salary") or ""))
        out_rows.append({
            "candidate_id": cid,
            "date_of_joining": rules.normalize_doj(cand.get("doj"))[0],
            "name": r["name"],
            "mobile": r["mobile"],
            "designation": r["designation"],
            "branch": r["location_code"],
            "vertical": r["facility_name"],
            "state": (r.get("state") or derive_state(cand)).title(),
            "salary": salary if salary is not None else "",
            "aadhaar_number": (cand.get("aadhaar_number") or "").strip(),
            "dob": rules.parse_date(cand.get("dob")),
            "father_name": rules.normalize_father_name(cand.get("father_name"))[0],
            "address": (cand.get("address") or "").strip(),
            "pin_code": rules.normalize_pin_code(cand.get("pin_code"))[0],
            "gender": rules.normalize_gender(cand.get("gender"))[0],
        })
    return {"rows": out_rows, "problems": problems}


def build_onboarding_workbook(rows: list[dict], mail_rows: list[dict]) -> tuple:
    """Open a copy of the authoritative ``Excel Generation.xlsx`` template and
    write candidate rows into BOTH its sheets (OB Format + Mail Format).

    Formatting is preserved from the template (headers, widths, styles). New
    mobile / facility / Aadhaar cells are forced to TEXT (no scientific
    notation); DOB / Date of Joining are real Excel dates with DD/MM/YYYY
    format; Net Salary is a number with a thousand separator.

    Returns (wb, header_rows, written_count) or raises on failure.
    """
    from openpyxl import load_workbook

    if not EXCEL_GENERATION_FILE.exists():
        raise ValueError("Excel Generation template is not configured.")

    wb = load_workbook(str(EXCEL_GENERATION_FILE))
    ob_ws = _find_sheet_by_title(wb, OB_FORMAT_SHEET)
    mail_ws = _find_sheet_by_title(wb, MAIL_FORMAT_SHEET)
    if ob_ws is None or mail_ws is None:
        wb.close()
        raise ValueError(
            f"Excel Generation template must contain '{OB_FORMAT_SHEET}' and "
            f"'{MAIL_FORMAT_SHEET}' sheets."
        )

    ob_header = _find_header_row(ob_ws)
    if ob_header is None:
        wb.close()
        raise ValueError("OB Format sheet: header row not found.")
    mail_header = _find_header_row(mail_ws, require_cost_code=False)
    if mail_header is None:
        wb.close()
        raise ValueError("Mail Format sheet: header row not found.")

    mobile_col = None
    facility_col = None
    for c in range(1, ob_ws.max_column + 1):
        header = _clean_header(ob_ws.cell(row=ob_header, column=c).value)
        if header == "mobile number":
            mobile_col = c
        if header == "facility":
            facility_col = c

    def _next_free(ws, header_row):
        row = header_row + 1
        while ws.cell(row=row, column=1).value not in (None, ""):
            row += 1
        return row

    def _clear_sample_rows(ws, header_row):
        """Remove the template's demo/sample data rows below the header so the
        generated deliverable contains ONLY the real candidate rows while all
        header formatting (filters, widths, styles) is preserved."""
        if ws.max_row > header_row:
            ws.delete_rows(header_row + 1, ws.max_row - header_row)

    # OB Format: Facility* = the LOCATION code (Column B) from the master row,
    # exactly like the template's own sample rows (HBB/BLR, BLR/PEN, ...).
    _clear_sample_rows(ob_ws, ob_header)
    _clear_sample_rows(mail_ws, mail_header)
    next_row = _next_free(ob_ws, ob_header)
    for idx, rdata in enumerate(rows, start=1):
        row_data = dict(rdata)
        row_data["sl_no"] = idx
        row_data["facility_name"] = rdata.get("location_code") or rdata.get("facility_name") or ""
        mapped = _map_row_to_columns(ob_ws, ob_header, row_data)
        for col, val in mapped.items():
            cell = ob_ws.cell(row=next_row, column=col)
            cell.value = "" if val is None else val
            if col == mobile_col:
                cell.number_format = "@"
            elif col == facility_col:
                cell.number_format = "@"
        next_row += 1

    # Mail Format: Branch = LOCATION code (Column B); Vertical = facility name.
    for idx, m in enumerate(mail_rows):
        row_num = _next_free(mail_ws, mail_header)
        values = [
            _as_date(m.get("date_of_joining")),
            m["name"],
            m["mobile"],
            m["designation"],
            m["branch"],
            m["vertical"],
            m["state"],
            m["salary"],
            m["aadhaar_number"],
            _as_date(m.get("dob")),
            m["father_name"],
            m["address"],
            m["pin_code"],
            m["gender"],
        ]
        for col, val in enumerate(values, start=1):
            cell = mail_ws.cell(row=row_num, column=col)
            if col in (3, 9):            # Mobile No, Aadhar No -> TEXT
                cell.number_format = "@"
                cell.value = str(val) if val not in (None, "") else ""
            elif col in (1, 10):         # Date of Joining, DOB -> real dates
                cell.number_format = "DD/MM/YYYY"
                cell.value = val or None
            elif col == 8:               # Net Salary -> number
                cell.number_format = "#,##0"
                cell.value = val if val not in (None, "") else None
            else:
                cell.value = "" if val is None else val

    return wb, {"ob_header": ob_header, "mail_header": mail_header}, len(rows)


def generate_onboarding_workbook(batch_id: int, only_ready: bool = True) -> dict:
    """Generate the SINGLE onboarding workbook (OB Format + Mail Format sheets).

    Transactional: if the workbook cannot be built or verified, NO file is left
    behind. Only ``ready`` candidates are included. The file is timestamped and
    re-generation NEVER overwrites an existing workbook (a new timestamp/file).

    Returns a result dict with ``success``, ``generated_file_id`` (the single
    file), ``generation_pair_id`` (single id), ``filename``, ``file_path``.
    """
    now = datetime.now()
    if not excel_template_is_configured():
        return {"success": False, "error": "Excel Generation Template is not configured."}

    candidates = database.get_batch_candidates(batch_id)
    ready = [c for c in candidates if (c.get("status") or "").lower() == "ready"]
    others = [c for c in candidates if (c.get("status") or "").lower() != "ready"]
    excluded = {}
    for c in others:
        excluded[c["candidate_id"]] = [
            f"Candidate status is '{c.get('status')}' (only Ready candidates are generated)."
        ]

    vres = validate_candidates(ready)
    rows = vres["rows"]
    for cid, errs in vres["errors"].items():
        excluded[cid] = errs
        database.update_candidate(cid, {"status": "needs_attention"})

    if not rows:
        return {
            "success": False,
            "error": "No Ready candidates passed validation for this batch.",
            "excluded": excluded,
            "candidate_count": 0,
        }

    # Mail Format validation is blocking: never generate a workbook whose Mail
    # sheet is missing required PII (full Aadhaar, DOB, gender, PIN, ...).
    candidates_by_id = {c["candidate_id"]: c for c in ready}
    mail = build_mail_rows(rows, candidates_by_id)
    blocking = {cid: msgs for cid, msgs in mail["problems"].items() if msgs}
    if blocking:
        return {
            "success": False,
            "error": "Mail Format requirements are not met for some candidates.",
            "excluded": {cid: msgs for cid, msgs in blocking.items()},
            "candidate_count": 0,
            "mail_problems": blocking,
        }

    try:
        wb, _hdr, n_rows = build_onboarding_workbook(rows, mail["rows"])
    except Exception as e:  # noqa: BLE001
        return {"success": False,
                "error": f"Could not build onboarding workbook: {e}",
                "excluded": excluded, "candidate_count": 0}

    ts = build_pair_timestamp(now)
    while database.get_generation_pair(f"PO-{ts}"):
        now = now + timedelta(seconds=1)
        ts = build_pair_timestamp(now)
    try:
        folders = build_date_folders(now)
    except Exception as e:  # noqa: BLE001
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
        return {
            "success": False,
            "error": f"Could not create output folders: {e}",
            "excluded": excluded,
            "candidate_count": 0,
        }
    pair_id = f"PO-{ts}"
    name = onboarding_filename(ts)
    path = _unique_path(Path(folders["base"]) / name)

    try:
        saved = _save_workbook_checked(wb, path, len(rows),
                                       OB_FORMAT_COLUMNS, len(OB_FORMAT_COLUMNS),
                                       another_sheet=MAIL_FORMAT_COLUMNS)
    except Exception as e:  # noqa: BLE001
        _try_unlink(path)
        return {"success": False,
                "error": f"Could not save/verify onboarding workbook: {e}",
                "excluded": excluded, "candidate_count": 0}

    generated_at = now.strftime("%Y-%m-%d %H:%M:%S")
    gid = database.create_generated_file(
        batch_id=batch_id,
        filename=path.name,
        file_path=str(path),
        date_folder=folders["date_folder"],
        candidate_count=len(rows),
        generation_status="generated",
        portal_result_path=str(Path(folders["results"]) / result_file_for(path.name, "RESULT")),
        portal_failure_path=str(Path(folders["errors"]) / result_file_for(path.name, "FAILED")),
        kind="excel_generation",
        generation_pair_id=pair_id,
    )
    database.update_generated_file_generated_at(gid, generated_at)
    database.update_candidates_generated([r["candidate_id"] for r in rows], gid)
    database.update_batch_status(batch_id, "Generated")
    database.record_generation_audit(
        batch_id=batch_id,
        generated_file_id=gid,
        candidate_ids=[r["candidate_id"] for r in rows],
        template_name=EXCEL_TEMPLATE_NAME,
        template_version=_template_version(),
        status="ok",
    )

    try:
        daily_file = export_daily_master(rows, now, path.name)
    except Exception:  # noqa: BLE001
        daily_file = None

    return {
        "success": True,
        "generated_file_id": gid,
        "generation_pair_id": pair_id,
        "batch_id": batch_id,
        "candidate_count": len(rows),
        "filename": path.name,
        "file_path": str(path),
        "date_folder": folders["date_folder"],
        "folders": folders,
        "generated_at": generated_at,
        "excluded": excluded,
        "daily_master_file": str(daily_file) if daily_file else None,
    }


def generate_onboarding_pair(batch_id: int, only_ready: bool = True) -> dict:
    """Generate the onboarding workbook (single file, OB + Mail sheets).

    The two-file pair design was replaced by the single two-sheet workbook from
    ``Excel Generation.xlsx``; generators and callers see ONE result dict.
    """
    return generate_onboarding_workbook(batch_id, only_ready=only_ready)


def _try_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:  # noqa: BLE001
        pass


def generate_batch_excel(batch_id: int, only_ready: bool = True) -> dict:
    """Backward-compatible wrapper: generate the onboarding workbook."""
    return generate_onboarding_workbook(batch_id, only_ready=only_ready)


# ── Daily master export ─────────────────────────────────────────────────────


def _last_generated():
    files = database.list_generated_files(limit=1)
    if files:
        return {
            "filename": files[0]["filename"],
            "generated_at": files[0]["generated_at"],
            "file_path": files[0]["file_path"],
            "candidate_count": files[0]["candidate_count"],
            "batch_id": files[0]["batch_id"],
        }
    return None


def export_daily_master(rows: list[dict], now: Optional[datetime] = None,
                        generated_file_name: str = "") -> Optional[Path]:
    """Write/update the daily operational master ``TeamHR_Master_YYYY-MM-DD.xlsx``.

    Uses Candidate ID as the unique key; existing candidates are updated rather
    than duplicated. Aadhaar is always masked. Returns the written path (or
    None if there is nothing to write).
    """
    if not rows:
        return None
    now = now or datetime.now()
    date_folder = now.strftime("%Y-%m-%d")
    base = get_output_base_dir() / date_folder
    base.mkdir(parents=True, exist_ok=True)
    out_path = base / f"TeamHR_Master_{date_folder}.xlsx"

    import openpyxl
    from openpyxl import load_workbook, Workbook

    HEADERS = [
        "Candidate ID", "Batch ID", "Name", "Mobile", "DOB", "DOJ",
        "Masked Aadhaar", "Address", "Entity", "Cost Code", "Operation",
        "Team", "Designation", "Facility Type", "Facility Name",
        "Location Code", "Salary", "Migrant", "Contractor", "LOB", "Status",
        "Created At", "Generated File", "Portal Status", "Portal Remarks",
    ]

    if out_path.exists():
        wb = load_workbook(str(out_path))
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.append(HEADERS)

    # Build a name->index map from headers (order-agnostic).
    hdr = {str(c.value).strip() if c.value else "": i + 1
           for i, c in enumerate(ws[1])}

    for r in rows:
        cid = r["candidate_id"]
        existing = database.get_candidate(cid)
        created_at = ""
        if existing:
            created_at = f"{existing.get('created_date') or ''} {existing.get('created_time') or ''}".strip()
        masked = database.mask_aadhaar(existing.get("aadhaar_number", "") if existing else "")
        row_vals = [
            cid,
            existing.get("batch_id") if existing else r.get("batch_id"),
            r["name"],
            r["mobile"],
            existing.get("dob") if existing else "",
            existing.get("doj") if existing else "",
            masked,
            existing.get("address") if existing else "",
            existing.get("entity") if existing else "",
            r["cost_code"],
            existing.get("operation") if existing else "",
            r["team"],
            r["designation"],
            r["facility_type"],
            existing.get("facility_name") if existing else r.get("facility_name"),
            r["location_code"],
            existing.get("salary") if existing else "",
            r["migrant"],
            r["contractor"],
            r["lob"],
            existing.get("status") if existing else "",
            created_at,
            generated_file_name or existing.get("generated_file") if existing else generated_file_name,
            existing.get("portal_status") if existing else "",
            existing.get("portal_remarks") if existing else "",
        ]

        # Find the row for this candidate_id (column "Candidate ID").
        row_found = None
        if hdr.get("Candidate ID") and ws.max_row > 1:
            for rr in range(2, ws.max_row + 1):
                if ws.cell(row=rr, column=hdr["Candidate ID"]).value == cid:
                    row_found = rr
                    break
        if row_found:
            for col, val in enumerate(row_vals, start=1):
                ws.cell(row=row_found, column=col, value=val)
        else:
            ws.append(row_vals)

    wb.save(str(out_path))

    # Mirror into SQLite (source of truth remains candidates; this is tracking).
    for c in rows:
        existing = database.get_candidate(c["candidate_id"])
        if existing:
            database.upsert_daily_master({
                "candidate_id": c["candidate_id"],
                "name": existing.get("name"),
                "mobile": existing.get("mobile"),
                "dob": existing.get("dob"),
                "doj": existing.get("doj"),
                "masked_aadhaar": database.mask_aadhaar(existing.get("aadhaar_number", "")),
                "address": existing.get("address"),
                "entity": existing.get("entity"),
                "cost_code": existing.get("cost_code"),
                "operation": existing.get("operation"),
                "team": existing.get("team"),
                "designation": existing.get("designation"),
                "facility_type": existing.get("facility_type"),
                "facility_name": existing.get("facility_name"),
                "location_code": existing.get("location_code"),
                "salary": existing.get("salary"),
                "migrant": existing.get("migrant"),
                "contractor": existing.get("contractor"),
                "lob": existing.get("lob"),
                "status": existing.get("status"),
                "created_at": f"{existing.get('created_date') or ''} {existing.get('created_time') or ''}".strip(),
                "generated_file": generated_file_name,
                "portal_status": existing.get("portal_status"),
                "portal_remarks": existing.get("portal_remarks"),
            })
    return out_path
