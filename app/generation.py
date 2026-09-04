"""Self Onboarding Excel generation + daily operational master export.

Stage 3: generate the actual Self Onboarding Excel file from approved/Ready
candidates using the official Self Onboarding Template as the source structure,
and produce a daily operational master export (masked Aadhaar) for internal
tracking.

Design invariants
-----------------
* The configured Self Onboarding Template is NEVER modified. We open it, write
  candidate rows into a copy, and save the generated output separately.
* Only candidates with status ``ready`` are included. Any candidate that is
  Draft / Needs Attention (or fails validation) is excluded and reported.
* Location Code comes from the Facility / Location Master (never the facility
  name), e.g. ``NelamangalaHub_BLR -> BLR/NLM``.
* Sensitive data (full Aadhaar, full address) is NEVER written to the audit log;
  the daily master uses MASKED Aadhaar.
* Configuration (output base folder) is stored in ``data/config.json``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from app import database
from app import rules
from app import master_data

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "data" / "generated"
CONFIG_FILE = BASE_DIR / "data" / "config.json"

TEMPLATE_FILE = master_data.MASTERS_DIR / master_data.MASTER_FILES["self_onboarding"]

# Current fixed business values (validated by the app).
DEFAULT_MIGRANT = "No"
DEFAULT_FACILITY_TYPE = "Delivery Hub"
DEFAULT_CONTRACTOR = "TEAM HR"
DEFAULT_LOB = "Ekart"

# Location-code city -> State rule. City is the first segment of the location
# code (e.g. ``BLR/NLM`` -> ``BLR``). Extend as new cities are onboarded.
CITY_STATE = {
    "BLR": "Karnataka",
}


# ── Configuration (output base folder) ──────────────────────────────────────


def _load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


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


def get_output_config() -> dict:
    """Public snapshot for Settings -> Excel Output."""
    out_dir = get_output_base_dir()
    last = _last_generated()
    return {
        "output_base_dir": str(out_dir),
        "default_output_base_dir": str(DEFAULT_OUTPUT_DIR),
        "template_configured": template_is_configured(),
        "template_file": str(TEMPLATE_FILE),
        "captured": last,
    }


# ── Template ────────────────────────────────────────────────────────────────


def template_is_configured() -> bool:
    return TEMPLATE_FILE.exists()


def _template_version() -> str:
    """Best-effort template version (file mtime) for the audit log."""
    try:
        ts = TEMPLATE_FILE.stat().st_mtime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return ""


# ── State derivation ─────────────────────────────────────────────────────────


def derive_state(candidate: dict) -> str:
    """Return the State for a candidate.

    Prefer an explicit candidate ``state`` if present; otherwise derive from
    the Location Code's city via the configured city->state rule. If no State
    can be determined the empty string is returned (caller flags Needs
    Attention and excludes the candidate — we never silently guess).
    """
    s = (candidate.get("state") or "").strip()
    if s:
        return s
    loc = (candidate.get("location_code") or "").strip()
    if loc and "/" in loc:
        city = loc.split("/", 1)[0].strip().upper()
        if city in CITY_STATE:
            return CITY_STATE[city]
    return ""


# ── Folder + filename helpers ───────────────────────────────────────────────


def get_date_stamp(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d")


def build_date_folders(now: Optional[datetime] = None) -> dict:
    """Create and return the date folders: ``Y-M-D/{uploads,results,errors}``."""
    now = now or datetime.now()
    date_folder = now.strftime("%Y-%m-%d")
    base = get_output_base_dir() / date_folder
    uploads = base / "uploads"
    results = base / "results"
    errors = base / "errors"
    for d in (base, uploads, results, errors):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "base": str(base),
        "date_folder": date_folder,
        "uploads": str(uploads),
        "results": str(results),
        "errors": str(errors),
    }


def build_filename(batch_id: int, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return "OB_BATCH-{batch:04d}_{date}_{time}.xlsx".format(
        batch=batch_id,
        date=now.strftime("%Y-%m-%d"),
        time=now.strftime("%H-%M-%S"),
    )


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


_TEMPLATE_COLUMNS = [
    "Name",
    "Mobile",
    "Team",
    "Cost Code",
    "Role / Designation",
    "Migrant",
    "Facility Type",
    "LOB / Sub Type",
    "State",
    "Contractor",
    "Facility / Location Code",
]


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
            errs.append("Unsupported 8752 (Myntra First Mile) facility.")
        else:
            hubs = rules.get_hubs_for_cost_code(cost_code)
            if facility not in hubs:
                errs.append(f"Facility '{facility}' is not valid for cost code {cost_code}.")

        if not c.get("location_code"):
            errs.append("Location code is missing.")

        facility_type = c.get("facility_type") or ""
        if facility_type and facility_type != DEFAULT_FACILITY_TYPE:
            errs.append(f"Facility Type must be '{DEFAULT_FACILITY_TYPE}'.")

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
            "facility_type": c.get("facility_type") or DEFAULT_FACILITY_TYPE,
            "lob": c.get("lob") or DEFAULT_LOB,
            "state": state,
            "contractor": c.get("contractor") or DEFAULT_CONTRACTOR,
            "location_code": c.get("location_code") or "",
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


def _find_header_row(ws) -> Optional[int]:
    """Locate the header row (row index 1-based) matching the template columns."""
    for row_idx in range(1, min(ws.max_row, 5) + 1):
        cells = [str(ws.cell(row=row_idx, column=c).value or "").strip()
                 for c in range(1, ws.max_column + 1)]
        if "Name" in cells and "Mobile" in cells and "Cost Code" in cells:
            return row_idx
    return None


def _map_row_to_columns(ws, header_row: int, row_data: dict) -> dict:
    """Map row_data onto template columns by matching header names."""
    col_map = {}
    for c in range(1, ws.max_column + 1):
        header = str(ws.cell(row=header_row, column=c).value or "").strip().lower()
        if not header:
            continue
        col_map[header] = c
    # Template header (lowercased) -> row_data field.
    mapping = {
        "name": "name",
        "mobile": "mobile",
        "team": "team",
        "cost code": "cost_code",
        "role / designation": "designation",
        "migrant": "migrant",
        "facility type": "facility_type",
        "lob / sub type": "lob",
        "state": "state",
        "contractor": "contractor",
        "facility / location code": "location_code",
        "location code": "location_code",
        "facility": "location_code",
    }
    out = {}
    for header, field in mapping.items():
        col = col_map.get(header)
        if not col:
            continue
        out[col] = row_data.get(field, "")
    return out


# ── Generation ──────────────────────────────────────────────────────────────


def generate_batch_excel(batch_id: int, only_ready: bool = True) -> dict:
    """Generate the Self Onboarding Excel for a batch.

    Returns a result dict describing success/failure, created file(s), and
    per-candidate inclusion/exclusion. Never writes a partial corrupt workbook.
    """
    now = datetime.now()
    if not template_is_configured():
        return {"success": False, "error": "Self Onboarding Template is not configured."}

    candidates = database.get_batch_candidates(batch_id)
    ready = [c for c in candidates if (c.get("status") or "").lower() == "ready"]
    others = [c for c in candidates if (c.get("status") or "").lower() != "ready"]
    excluded = {}

    for c in others:
        excluded[c["candidate_id"]] = [
            f"Candidate status is '{c.get('status')}' (only Ready candidates are generated)."
        ]

    # Validate ready candidates. Invalid ready candidates are excluded.
    vres = validate_candidates(ready)
    rows = vres["rows"]
    for cid, errs in vres["errors"].items():
        excluded[cid] = errs
        # Mark needs-attention so the user can see why it was excluded.
        database.update_candidate(cid, {"status": "needs_attention"})

    if not rows:
        return {
            "success": False,
            "error": "No Ready candidates passed validation for this batch.",
            "excluded": excluded,
            "candidate_count": 0,
        }

    # Load template (openpyxl) and write candidate rows into a copy.
    try:
        import openpyxl
        from openpyxl import load_workbook
        wb = load_workbook(str(TEMPLATE_FILE))
        ws = wb.worksheets[0]
        header_row = _find_header_row(ws)
        if header_row is None:
            return {"success": False, "error": "Template header columns not found.",
                    "excluded": excluded, "candidate_count": 0}
        next_row = header_row + 1
        # Find first empty row after header.
        while ws.cell(row=next_row, column=1).value not in (None, ""):
            next_row += 1
        for rdata in rows:
            mapped = _map_row_to_columns(ws, header_row, rdata)
            for col, val in mapped.items():
                ws.cell(row=next_row, column=col, value=val)
            next_row += 1
    except Exception as e:  # noqa: BLE001
        return {"success": False,
                "error": f"Could not build workbook from template: {e}",
                "excluded": excluded, "candidate_count": 0}

    # Output folders + filename.
    folders = build_date_folders(now)
    filename = build_filename(batch_id, now)
    uploads_dir = Path(folders["uploads"])
    out_path = _unique_path(uploads_dir / filename)
    final_filename = out_path.name

    # Save generated workbook to a temp then move (avoid partial writes).
    tmp_path = uploads_dir / (final_filename + ".tmp")
    try:
        wb.save(str(tmp_path))
        tmp_path.replace(out_path)
    except Exception as e:  # noqa: BLE001
        if tmp_path.exists():
            tmp_path.unlink()
        return {"success": False,
                "error": f"Could not save generated workbook: {e}",
                "excluded": excluded, "candidate_count": 0}
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass

    # DB links.
    generated_file_id = database.create_generated_file(
        batch_id=batch_id,
        filename=final_filename,
        file_path=str(out_path),
        date_folder=folders["date_folder"],
        candidate_count=len(rows),
        generation_status="generated",
        portal_result_path=str(Path(folders["results"]) / result_file_for(final_filename, "RESULT")),
        portal_failure_path=str(Path(folders["errors"]) / result_file_for(final_filename, "FAILED")),
    )
    database.mark_candidates_generated([r["candidate_id"] for r in rows], generated_file_id)
    database.update_batch_status(batch_id, "Generated")
    database.record_generation_audit(
        batch_id=batch_id,
        generated_file_id=generated_file_id,
        candidate_ids=[r["candidate_id"] for r in rows],
        template_name=TEMPLATE_FILE.name,
        template_version=_template_version(),
        status="ok",
    )

    # Update the daily master export.
    daily_file = export_daily_master(rows, now, final_filename)

    return {
        "success": True,
        "generated_file_id": generated_file_id,
        "batch_id": batch_id,
        "candidate_count": len(rows),
        "filename": final_filename,
        "file_path": str(out_path),
        "date_folder": folders["date_folder"],
        "folders": folders,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "excluded": excluded,
        "daily_master_file": str(daily_file) if daily_file else None,
    }


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
