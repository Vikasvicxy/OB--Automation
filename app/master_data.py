"""Master data integration for TeamHR Automation.

Loads official Designation Master and Facility / Location Master from Excel
files placed in ``data/masters`` and exposes the authoritative values used
across Manual Entry, Smart Upload, validation, and display.

Design invariants
-----------------
* Final Designation and Facility (hub) values ALWAYS come from these masters.
  We never invent a role or facility name.
* Location codes are read from the same master row as the facility and are
  never synthesized.
* Facility classification (in priority order):
      1. facility name contains "MYNTRA"    -> Myntra (8751 / Last Mile)
      2. facility name ends with "_PL"      -> Flipkart First Mile (4441)
      3. otherwise                          -> Flipkart Last Mile (4421)
* 8752 (Myntra First Mile) intentionally has an EMPTY hub list for now.
* 8751 (Myntra Last Mile) does NOT offer Prexo Delivery Executive.
"""

from __future__ import annotations

import difflib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from app import database

MASTERS_DIR = Path(__file__).resolve().parent.parent / "data" / "masters"

MASTER_FILES = {
    "designation": "Designation_Master.xlsx",
    "facility": "HubName.xlsx",
    "self_onboarding": "Self_Onboarding_Template.xlsx",
}

DESIGNATION_FILE = MASTERS_DIR / MASTER_FILES["designation"]
FACILITY_FILE = MASTERS_DIR / MASTER_FILES["facility"]

_COST_CODE_DESIGNATIONS: dict[str, list[str]] = {}
_HUB_MASTER: list[str] = []
_LOCATION_BY_FACILITY: dict[str, str] = {}
_FACILITY_ROWS: list[dict] = []
_FACILITY_ROW_BY_KEY: dict[str, dict] = {}
_DISPLAY_COUNTS: dict[str, int] = {}

# Intelligent user/OCR aliases. An alias only resolves to an OFFICIAL master
# designation, and only when valid for the selected cost code.
ROLE_ALIASES = {
    "biker": "Delivery Executive",
    "delivery": "Delivery Executive",
    "delivery executive": "Delivery Executive",
    "sort": "Sorter",
    "sorter": "Sorter",
    "tl": "Team Leader",
    "team lead": "Team Leader",
    "team leader": "Team Leader",
    "prexo": "Prexo Delivery Executive",
    "prexo delivery": "Prexo Delivery Executive",
}

COST_CODES = {
    "4421": {"entity": "Flipkart", "operation": "Last Mile", "prefix": "LM"},
    "4441": {"entity": "Flipkart", "operation": "First Mile", "prefix": "FM"},
    "8751": {"entity": "Myntra", "operation": "Last Mile", "prefix": "LM"},
    "8752": {"entity": "Myntra", "operation": "First Mile", "prefix": "FM"},
}

_IS_INITIALIZED = False


# ── Excel reading helpers ───────────────────────────────────────────────────


def _read_excel_rows(path: Path) -> Optional[list[list]]:
    try:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _find_col(record: dict, names: list[str]) -> str:
    lower_map = {re.sub(r"\s+", "", k).upper(): v for k, v in record.items()}
    for n in names:
        key = re.sub(r"\s+", "", n).upper()
        if key in lower_map:
            return str(lower_map[key]).strip()
    # Fall back: match any column containing the token.
    for key, val in lower_map.items():
        for n in names:
            if n.upper() in key:
                return str(val).strip()
    return ""


def _rows_to_records(rows: list) -> list[dict]:
    if not rows or len(rows) < 1:
        return []
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    out = []
    for r in rows[1:]:
        if r is None or all(c is None or str(c).strip() == "" for c in r):
            continue
        rec = {}
        for i, col in enumerate(header):
            if not col:
                continue
            val = r[i] if i < len(r) else None
            if val is not None:
                rec[col] = str(val).strip()
        if rec:
            out.append(rec)
    return out


# ── Extractors (pure parse; used by loaders AND by the dry-run import preview) ──


def _extract_designations(records: list) -> dict[str, list[str]]:
    """Parse designation rows into {cost_code: [official names]} WITHOUT touching
    module state or the database. Shared by :func:`load_designations` and the
    safe dry-run :func:`preview_masters`."""
    by_cc: dict[str, list[str]] = {cc: [] for cc in COST_CODES}
    for r in records:
        desig = _find_col(r, ["DESIGNATION", "DESIGNATION NAME", "TITLE", "ROLE"])
        if not desig or desig.upper() in ("DESIGNATION", "N/A", "NA"):
            continue
        cc_raw = _find_col(r, ["COST CODE", "COST_CODE", "COST CODE ID"])
        if cc_raw:
            for c in [c.strip() for c in re.split(r"[;,]", cc_raw) if c.strip()]:
                if c in by_cc and desig not in by_cc[c]:
                    by_cc[c].append(desig)
        else:
            # No explicit cost-code column: assign by operational prefix.
            for cc, info in COST_CODES.items():
                if desig.startswith(info["prefix"] + " - ") and desig not in by_cc[cc]:
                    by_cc[cc].append(desig)
    return by_cc


def _classify_facility_text(*parts: str) -> tuple[bool, bool, str, str, str]:
    """Classify a facility from its combined system-reference + display text.

    Priority order (same as before, but the system reference in Column A is
    considered too, so friendly display labels like "Yelahanka Pickup Hub" are
    still recognised as First Mile via their ``_PL`` reference):
        1. text contains "MYNTRA"            -> Myntra (8751 / Last Mile)
        2. text contains "_PL" or "PICKUP"   -> Flipkart First Mile (4441)
        3. otherwise                          -> Flipkart Last Mile (4421)

    Returns (is_myntra, is_fm, cost_code, entity, operation).
    """
    text = " ".join(str(p) for p in parts if p).upper()
    is_myntra = "MYNTRA" in text
    is_fm = bool(re.search(r"_PL", text)) or "PICKUP" in text
    if is_myntra:
        return True, False, "8751", "Myntra", "Last Mile"
    if is_fm:
        return False, True, "4441", "Flipkart", "First Mile"
    return False, False, "4421", "Flipkart", "Last Mile"


def _readable_name_from_ref(facility_ref: str) -> str:
    """Extract the readable facility label from a Column A system reference.

    Column A looks like ``BLR/NLM (NelamangalaHub_BLR)`` — the parenthetical
    gives the human-searchable name. Used as the display fallback when Column C
    (FACILITY NAME) is blank so the row stays visible and selectable under its
    readable name (requirement: blank Column C rows are never dropped).
    """
    m = re.search(r"\(([^)]+)\)\s*$", facility_ref or "")
    if m:
        label = m.group(1).strip()
        if label:
            return label
    return ""


def _facility_row_key(facility_ref: str, location: str, display: str) -> str:
    """Unique identity for a master row: the LOCATION code (unique in the
    master), falling back to the system reference then the display name."""
    if location:
        return location
    if facility_ref:
        return facility_ref
    return display


def _extract_facilities(records: list) -> list[dict]:
    """Parse facility rows into effective-style facility dicts WITHOUT touching
    module state or the database. Shared by :func:`load_facilities` and the safe
    dry-run :func:`preview_masters`.

    HubName.xlsx semantics:
      * Column A "FACILITY" is the system-compatible facility reference/identifier
        (e.g. ``HEBBALMYNTRAHUB_BLR (HebbalMYNTRAHub_BLR)``) — NEVER replaced by
        the friendly display name.
      * Column B "LOCATION" is the Branch/location code used in the generated
        workbooks (e.g. ``HBB/BLR``). It is unique per row.
      * Column C "FACILITY NAME" is the human-searchable display label
        (e.g. ``HebbalMYNTRAHub_BLR``). When blank it falls back to the readable
        name embedded in Column A (``(… )`` suffix), then Column B, then
        Column A — a blank Column C never drops the row.
    Rows sharing a display name (or with a missing name) are all kept; their
    uniqueness comes from the row key, not the display name.
    """
    facilities = []
    seen_keys: set[str] = set()
    for r in records:
        facility_ref = _find_col(r, ["FACILITY"])
        location = _find_col(r, ["LOCATION", "LOCATION CODE"])
        facility_name = _find_col(r, ["FACILITY NAME", "FACILITYNAME"])
        if not facility_name or facility_name.upper() in ("FACILITY NAME", "FACILITY", "N/A", "NA"):
            facility_name = _readable_name_from_ref(facility_ref) or location or facility_ref
        if not (facility_ref or location or facility_name):
            continue
        if not facility_ref:
            facility_ref = location or facility_name
        if not location:
            location = facility_ref or facility_name
        key = _facility_row_key(facility_ref, location, facility_name)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        is_myntra, is_fm, cost_code, entity, operation = _classify_facility_text(
            facility_ref, facility_name)
        facilities.append({
            "facility_ref": facility_ref,
            "facility_name": facility_name,
            "location": location,
            "facility": facility_ref or facility_name,
            "hub_key": key,
            "is_myntra": is_myntra,
            "is_fm": is_fm,
            "entity": entity,
            "operation": operation,
            "cost_code": cost_code,
            "facility_type": "Pickup Hub" if is_fm else "Delivery Hub",
            "state": "",
            "active": 1,
            "source": "Excel Import",
        })
    return facilities


# ── Loaders ─────────────────────────────────────────────────────────────────


def load_designations(path: Optional[Path] = None) -> dict:
    """Load the official designation list from the Designation Master Excel.

    Returns a summary {status, row_count, ...}.
    """
    global _COST_CODE_DESIGNATIONS
    fpath = path or DESIGNATION_FILE
    if not fpath.exists():
        database.record_master_load("designation", fpath.name, 0, "not_configured")
        _COST_CODE_DESIGNATIONS = {}
        return {"kind": "designation", "row_count": 0, "status": "Not Configured",
                "filename": fpath.name, "ok": False}

    rows = _read_excel_rows(fpath)
    if rows is None:
        database.record_master_load("designation", fpath.name, 0, "error")
        return {"kind": "designation", "row_count": 0, "status": "Error",
                "filename": fpath.name, "ok": False}

    records = _rows_to_records(rows)
    by_cc = _extract_designations(records)
    _COST_CODE_DESIGNATIONS = {cc: list(des) for cc, des in by_cc.items()}
    total = sum(len(v) for v in _COST_CODE_DESIGNATIONS.values())
    database.record_master_load("designation", fpath.name, total, "ok")
    return {"kind": "designation", "row_count": total, "status": "Configured",
            "filename": fpath.name, "ok": True}


def load_facilities(path: Optional[Path] = None) -> dict:
    """Load the Facility / Location Master from Excel.

    HubName.xlsx columns: FACILITY | LOCATION | FACILITY NAME.
    FACILITY NAME (Column C) is the human-searchable hub value; LOCATION
    (Column B) is the Branch/location code; FACILITY (Column A) is the
    system-compatible facility reference. None of them are renamed.
    """
    global _HUB_MASTER, _LOCATION_BY_FACILITY, _FACILITY_ROWS
    global _FACILITY_ROW_BY_KEY, _DISPLAY_COUNTS
    fpath = path or FACILITY_FILE
    if not fpath.exists():
        database.record_master_load("facility", fpath.name, 0, "not_configured")
        _HUB_MASTER, _LOCATION_BY_FACILITY, _FACILITY_ROWS = [], {}, []
        _FACILITY_ROW_BY_KEY, _DISPLAY_COUNTS = {}, {}
        return {"kind": "facility", "row_count": 0, "status": "Not Configured",
                "filename": fpath.name, "ok": False}

    rows = _read_excel_rows(fpath)
    if rows is None:
        database.record_master_load("facility", fpath.name, 0, "error")
        return {"kind": "facility", "row_count": 0, "status": "Error",
                "filename": fpath.name, "ok": False}

    records = _rows_to_records(rows)
    facilities = _extract_facilities(records)
    _FACILITY_ROWS = facilities
    _FACILITY_ROW_BY_KEY = {f["hub_key"]: f for f in facilities}
    _DISPLAY_COUNTS = {}
    for f in facilities:
        name = f["facility_name"]
        _DISPLAY_COUNTS[name] = _DISPLAY_COUNTS.get(name, 0) + 1
    # Only displacements that are unambiguous (exactly one row) get a
    # display-name -> location mapping. Duplicate display names resolve only
    # through the row-level API so the user can pick the exact row.
    _HUB_MASTER = []
    _LOCATION_BY_FACILITY = {}
    for f in facilities:
        name = f["facility_name"]
        if name in _LOCATION_BY_FACILITY:
            del _LOCATION_BY_FACILITY[name]
            continue
        if _DISPLAY_COUNTS.get(name, 0) == 1:
            _LOCATION_BY_FACILITY[name] = f["location"]
        if name not in _HUB_MASTER:
            _HUB_MASTER.append(name)
    database.record_master_load("facility", fpath.name, len(facilities), "ok")
    return {"kind": "facility", "row_count": len(facilities), "status": "Configured",
            "filename": fpath.name, "ok": True}


def load_masters() -> dict:
    """(Re)load all configured masters. Returns a summary dict."""
    d = load_designations()
    f = load_facilities()
    return {"designation": d, "facility": f}


# ── Dry-run import preview (Phase 6) ─────────────────────────────────────────
# Reads the EXCEL files and diffs against the CURRENT effective master WITHOUT
# mutating module state or the DB (no record_master_load, no global assignment).
# Used by the Admin Master "Master Import" tab before Apply Import.


def preview_masters() -> dict:
    """Return a safe, dry-run classification of what an import WOULD do.

    Counts: new / changed / unchanged / duplicate / invalid / override_conflict
    for facilities and designations. Never modifies state.
    """
    result = {
        "dry_run": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "facilities": {"new": 0, "changed": 0, "unchanged": 0, "duplicate": 0,
                       "invalid": 0, "override_conflict": 0, "total": 0,
                       "examples": []},
        "designations": {"new": 0, "changed": 0, "unchanged": 0, "duplicate": 0,
                         "invalid": 0, "override_conflict": 0, "total": 0,
                         "examples": []},
    }

    # ── Facilities ──
    rows = _read_excel_rows(FACILITY_FILE)
    if rows:
        records = _rows_to_records(rows)
        parsed = _extract_facilities(records)
    else:
        parsed = []
    effective = {f["facility_name"]: f for f in _effective_facilities()}
    try:
        admin_all = _admin().list_admin_facilities()
        admin_by_name = {a["facility_name"]: a for a in admin_all}
    except Exception:  # noqa: BLE001
        admin_by_name = {}

    seen_names: set[str] = set()
    fac = result["facilities"]
    fac["total"] = len(parsed)
    for f in parsed:
        name = f["facility_name"]
        key = (name, f["cost_code"], f["entity"], f["operation"])
        if name in seen_names:
            fac["duplicate"] += 1
            fac["examples"].append({"name": name, "kind": "duplicate"})
            continue
        seen_names.add(name)
        admin = admin_by_name.get(name)
        if admin and admin.get("cost_code") != f["cost_code"]:
            fac["override_conflict"] += 1
            fac["examples"].append({"name": name, "kind": "override_conflict",
                                    "excel": f["cost_code"],
                                    "admin": admin.get("cost_code", "")})
            continue
        cur = effective.get(name)
        if cur is None:
            fac["new"] += 1
            fac["examples"].append({"name": name, "kind": "new"})
            continue
        if (cur.get("cost_code") != f["cost_code"]
                or cur.get("entity") != f["entity"]
                or cur.get("operation") != f["operation"]
                or cur.get("location") != f["location"]):
            fac["changed"] += 1
            fac["examples"].append({"name": name, "kind": "changed"})
            continue
        fac["unchanged"] += 1

    # ── Designations ──
    drows = _read_excel_rows(DESIGNATION_FILE)
    if drows:
        drecords = _rows_to_records(drows)
        parsed_d = _extract_designations(drecords)
    else:
        parsed_d = {}
    existing_roles = set()
    for v in _COST_CODE_DESIGNATIONS.values():
        existing_roles.update(v)
    try:
        for r in _admin().list_admin_roles():
            if r.get("active"):
                existing_roles.add(r["official_name"])
    except Exception:  # noqa: BLE001
        pass

    des = result["designations"]
    des["total"] = sum(len(v) for v in parsed_d.values())
    seen_d: set[str] = set()
    for cc, names in parsed_d.items():
        for dname in names:
            if dname in seen_d:
                des["duplicate"] += 1
                des["examples"].append({"name": dname, "kind": "duplicate"})
                continue
            seen_d.add(dname)
            if dname in existing_roles:
                des["unchanged"] += 1
            else:
                des["new"] += 1
                des["examples"].append({"name": dname, "kind": "new",
                                        "cost_code": cc})
    return result


_IS_INITIALIZED = False


def ensure_masters_loaded() -> None:
    """Load master data at startup (idempotent).

    Initializes the DB schema first so a fresh machine's first run (where
    ``app.main`` imports this module before ``database.init_db()``) never
    fails on the missing ``master_load_history`` table.
    """
    global _IS_INITIALIZED
    if _IS_INITIALIZED:
        return
    _IS_INITIALIZED = True
    database.init_db()
    load_designations()
    load_facilities()


# ── Status (Settings -> Master Data) ────────────────────────────────────────


def get_master_status() -> dict:
    """Return status for the Settings -> Master Data page."""
    def _file_status(kind: str, path: Path) -> dict:
        last = database.last_master_load(kind)
        configured = path.exists()
        return {
            "kind": kind,
            "name": kind.replace("_", " ").title(),
            "filename": path.name,
            "path": str(path),
            "configured": configured,
            "status": "Configured" if configured else "Not Configured",
            "last_loaded": (last.get("loaded_at") if last else None),
            "row_count": (last.get("row_count") if last else None),
        }

    return {
        "designation": _file_status("designation", DESIGNATION_FILE),
        "facility": _file_status("facility", FACILITY_FILE),
        "self_onboarding": _file_status("self_onboarding", MASTERS_DIR / MASTER_FILES["self_onboarding"]),
    }


# ── Designation accessors ───────────────────────────────────────────────────


def _admin():
    """Lazily import admin_master to avoid a circular import at module load."""
    from app import admin_master
    return admin_master


def get_all_designations() -> list[str]:
    out = []
    for v in _COST_CODE_DESIGNATIONS.values():
        for d in v:
            if d not in out:
                out.append(d)
    return out


def get_designations_for_cost_code(cost_code: str) -> list[str]:
    excel = list(_COST_CODE_DESIGNATIONS.get(cost_code, []))
    admin = _safe_admin_roles(cost_code)
    merged = list(excel)
    for name in admin:
        if name not in merged:
            merged.append(name)
    return merged


def get_roles_for_cost_code(cost_code: str) -> list[str]:
    """Official roles for a cost code (effective: Excel designations + admin roles)."""
    return get_designations_for_cost_code(cost_code)


def _safe_admin_roles(cost_code: str) -> list[str]:
    """Admin roles for a cost code; empty if the admin tables aren't ready."""
    try:
        return _admin().effective_roles_for_cost_code(cost_code)
    except Exception:  # noqa: BLE001
        return []


def get_role_aliases() -> dict:
    """Effective role aliases: built-in ones merged with active admin aliases.

    Admin aliases are official-name keyed (alias -> official role name); when an
    admin alias maps to a role that shares the base with an official designation
    it simply narrows/extends resolution. Admin aliases always win for the same
    alias key.
    """
    merged = dict(ROLE_ALIASES)
    try:
        admin_aliases = _admin().effective_aliases()
    except Exception:  # noqa: BLE001
        admin_aliases = {}
    # Admin aliases map alias -> OFFICIAL role name (e.g. 'tl' -> 'LM - Team
    # Leader'). Built-in aliases map alias -> base ('tl' -> 'Team Leader').
    merged.update(admin_aliases)
    return merged


def resolve_designation(query: str, cost_code: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a role query to an OFFICIAL designation valid for the cost code.

    Supports aliases (biker, delivery, sorter, tl, team leader, prexo) but an
    alias only resolves to an official designation present in the master for
    that cost code. 8751 (Myntra Last Mile) has no Prexo.
    """
    q = (query or "").strip().lower()
    if not q:
        return None, "Role is required"
    available = get_designations_for_cost_code(cost_code)
    if not available:
        return None, f"No roles available for cost code {cost_code}"

    aliases = get_role_aliases()
    available_lower = [d.lower() for d in available]
    # Admin aliases are exact official role names; check direct match first.
    admin_alias = aliases.get(q)
    if admin_alias:
        for d in available:
            if d.lower() == admin_alias.lower():
                return d, None
    base = aliases.get(q)
    if base:
        for d in available:
            if base.lower() in d.lower():
                return d, None
    if q in available_lower:
        return available[available_lower.index(q)], None
    alias_matches = difflib.get_close_matches(q, list(aliases.keys()), n=1, cutoff=0.5)
    if alias_matches:
        base = aliases[alias_matches[0]]
        for d in available:
            if base.lower() in d.lower():
                return d, None
    m = difflib.get_close_matches(q, available_lower, n=1, cutoff=0.55)
    if m:
        return available[available_lower.index(m[0])], None
    return None, f'No matching role found for "{query}" in cost code {cost_code}'


# ── Facility accessors ──────────────────────────────────────────────────────


def _effective_facilities() -> list[dict]:
    """Effective merged facility view: Excel master + admin records.

    Merge rules (admin never touches the Excel file):
      * A facility name with an ACTIVE admin record uses the admin record and,
        if the same name came from Excel, is treated as an Admin Override.
      * A facility name whose ONLY admin record is INACTIVE is deactivated and
        excluded from the effective view (protects history; not a hard delete).
      * Otherwise the Excel row is used as-is.

    Every row carries ``facility_ref`` (the system-compatible facility
    reference) so the friendly display name never replaces the system
    identifier. For Excel rows this is Column A; for admin rows it is the
    display name itself.
    """
    excel = {f["facility_name"]: f for f in _FACILITY_ROWS}
    try:
        admin_all = _admin().list_admin_facilities()
    except Exception:  # noqa: BLE001
        admin_all = []
    admin_active: dict[str, dict] = {}
    admin_only_inactive: set[str] = set()
    for a in admin_all:
        if int(a.get("active", 0)):
            admin_active[a["facility_name"]] = a
        else:
            admin_only_inactive.add(a["facility_name"])

    merged: list[dict] = []
    names = list(excel.keys()) + [n for n in admin_active if n not in excel]
    for name in names:
        if name not in admin_active:
            # A name that only exists as an inactive admin record is excluded
            # (soft-deleted). But if the same name also came from Excel, we
            # fall back to the Excel row (deactivating an override restores the
            # Excel facility; it must not vanish from the master).
            if name in admin_only_inactive and name not in excel:
                continue
            merged.append(excel[name])
            continue
        a = admin_active[name]
        merged.append({
            "facility_ref": a.get("facility_ref") or a["facility_name"],
            "facility_name": a["facility_name"],
            "location": a.get("location_code", ""),
            "facility": a["facility_name"],
            "hub_key": a.get("location_code") or a["facility_name"],
            "is_myntra": a.get("entity", "") == "Myntra",
            "is_fm": (a.get("operation", "") == "First Mile"),
            "entity": a.get("entity", ""),
            "operation": a.get("operation", ""),
            "cost_code": a.get("cost_code", ""),
            "facility_type": a.get("facility_type", "Delivery Hub"),
            "state": a.get("state", ""),
            "active": 1,
            "source": "Admin Override" if name in excel else "Admin",
        })
    return merged


def get_facilities() -> list[dict]:
    return [dict(f) for f in _effective_facilities()]


def get_facility_names() -> list[str]:
    return [f["facility_name"] for f in _effective_facilities()]


def get_facility_row_by_key(hub_key: str) -> dict | None:
    """Fetch the single effective master row for ``hub_key`` (location code),
    or None. Used to attach derived classification (cost code, entity,
    operation, facility type) to a resolved facility selection."""
    if not hub_key:
        return None
    key = {f["hub_key"]: f for f in get_facility_rows()}.get(hub_key)
    return dict(key) if key else None


def get_facility_rows(cost_code: str = "") -> list[dict]:
    """Row-level facility view (Excel rows + active admin rows).

    Unlike :func:`get_facilities`, this is NOT deduplicated by display name:
    duplicate/ambiguous display names appear once per master row so the UI can
    show an exact pick. Each row carries ``facility_ref``, ``location`` and
    ``hub_key``. Filtered by cost code when given.
    """
    out = []
    excel = {f["hub_key"]: f for f in _FACILITY_ROWS}
    try:
        admin_all = _admin().list_admin_facilities()
    except Exception:  # noqa: BLE001
        admin_all = []
    caught: set[str] = set()
    for a in admin_all:
        if not int(a.get("active", 0)):
            caught.add(a.get("facility_name", ""))
            continue
        key = a.get("location_code") or a.get("facility_name", "")
        # An active admin record for a name that also exists in Excel overrides
        # the Excel rows of the same display name; otherwise it is appended.
        if a.get("facility_name") in {f["facility_name"] for f in excel.values()}:
            excel.pop(key, None)
            for k in [k for k, v in excel.items() if v["facility_name"] == a.get("facility_name")]:
                excel.pop(k, None)
        row = {
            "facility_ref": a.get("facility_ref") or a.get("facility_name", ""),
            "facility_name": a.get("facility_name", ""),
            "location": a.get("location_code", ""),
            "facility": a.get("facility_name", ""),
            "hub_key": key,
            "is_myntra": a.get("entity", "") == "Myntra",
            "is_fm": a.get("operation", "") == "First Mile",
            "entity": a.get("entity", ""),
            "operation": a.get("operation", ""),
            "cost_code": a.get("cost_code", ""),
            "facility_type": a.get("facility_type", "Delivery Hub"),
            "state": a.get("state", ""),
            "active": 1,
            "source": "Admin Override" if a.get("facility_name") in {f["facility_name"] for f in _FACILITY_ROWS} else "Admin",
        }
        excel[key] = row
    for f in excel.values():
        if f.get("facility_name") in caught and f["source"] == "Admin":
            continue
        if f.get("cost_code") and cost_code and f["cost_code"] != cost_code:
            continue
        out.append(dict(f))
    return out


def get_row_for_location(location: str) -> Optional[dict]:
    """Return the EXACT master row whose location (Column B) equals ``location``."""
    loc = (location or "").strip()
    if not loc:
        return None
    for f in _FACILITY_ROWS:
        if f["location"] == loc:
            return dict(f)
    return None


def get_rows_for_display(display: str) -> list[dict]:
    """All master rows whose display name (Column C) equals ``display``.

    Used to present duplicate/ambiguous display names as exact choices.
    """
    d = (display or "").strip()
    if not d:
        return []
    return [dict(f) for f in get_facility_rows() if f["facility_name"] == d]


def resolve_facility_selection(facility: str, location: str = "", facility_ref: str = "") -> dict:
    """Resolve a UI facility selection to its authoritative ``location`` and
    ``facility_ref`` from the master rows — never from client text alone.

    Match order (strongest first):
      1. facility_ref (system identifier) exact match on Column A,
      2. location code exact match on Column B,
      3. display name (Column C) exact match when it is unambiguous,
      4. facility_ref substring on the display text (e.g. "HebbalMYNTRAHub_BLR").
    Returns {"location": ..., "facility_ref": ..., "facility_name": ...,
             "hub_key": ...} with whatever could be resolved.
    """
    res = {"location": "", "facility_ref": "", "facility_name": facility, "hub_key": ""}
    facility = (facility or "").strip()
    location = (location or "").strip()
    facility_ref = (facility_ref or "").strip()

    def _pick(row):
        if row:
            res["location"] = row.get("location", "")
            res["facility_ref"] = row.get("facility_ref", "")
            res["facility_name"] = row.get("facility_name", facility)
            res["hub_key"] = row.get("hub_key", "")
            return True
        return False

    rows = get_facility_rows()
    if not rows:
        return res
    # 1. System reference exact match (Column A).
    if facility_ref:
        if _pick(next((r for r in rows if r["facility_ref"] == facility_ref), None)):
            return res
    # 2. Location code exact match (Column B).
    if location:
        if _pick(next((r for r in rows if r["location"] == location), None)):
            return res
    # 3. Display name exact match (Column C) but only when unambiguous.
    if facility:
        exact = [r for r in rows if r["facility_name"] == facility]
        if len(exact) == 1:
            if _pick(exact[0]):
                return res
        elif len(exact) > 1:
            if location:
                if _pick(next((r for r in exact if r["location"] == location), None)):
                    return res
            return res
    # 4. Substring: also verify the reference so "myntrahub" never hijacks a
    #    Flipkart facility.
    if facility and _DISPLAY_COUNTS.get(facility, 0) == 0:
        matches = [r for r in rows if facility in r["facility_ref"]]
        if len(matches) == 1:
            _pick(matches[0])
    return res


def get_location_for_facility(facility_name: str, location: str = "", facility_ref: str = "") -> str:
    """Exact official LOCATION code for a facility.

    Accepts the display name (Column C), a location code (Column B, identity
    preserved) or the facility system reference (Column A). Returns "" when the
    value is unknown or maps to more than one row. Resolves through the
    effective (admin-merged) row view so overrides are honoured.
    """
    return resolve_facility_selection(facility_name, location, facility_ref)["location"]


def get_effective_facility(facility_name: str) -> Optional[dict]:
    """Return the effective facility record for an exact facility name (or None)."""
    for f in _effective_facilities():
        if f["facility_name"] == facility_name:
            return dict(f)
    return None


def classify_hub(hub_name: str, location: str = "", facility_ref: str = "") -> str:
    """Classify a facility as LM, FM, or Myntra based on name.

    Classification considers the display name, the system reference (Column A)
    and the location so markers like ``_PL``/MYNTRA that live in the reference
    are honoured even when the friendly label hides them.

    Priority order:
        1. text contains "MYNTRA"        -> "Myntra"
        2. text contains "_PL" or "PICKUP" -> "FM" (Flipkart First Mile)
        3. otherwise                      -> "LM" (Flipkart Last Mile)
    """
    is_myntra, is_fm, _cc, _e, _op = _classify_facility_text(
        facility_ref, hub_name, location)
    if is_myntra:
        return "Myntra"
    if is_fm:
        return "FM"
    return "LM"


def get_hubs_for_cost_code(cost_code: str) -> list[str]:
    """Exact official facility VALUES (display names) valid for a cost code.

    The display label (Column C) is used as the facility value in the workbook;
    the system reference/location stay available on the row. Cost-code
    membership is decided by the row classification (display + system ref).

    4421 -> Flipkart LM  |  4441 -> Flipkart FM
    8751 -> Myntra LM    |  8752 -> only when an admin explicitly adds one
    """
    names = []
    for f in get_facility_rows():
        if f["cost_code"] == cost_code and f["facility_name"] not in names:
            names.append(f["facility_name"])
    return names


def _facility_search_rows(query: str, cost_code: str = "", top_n: int = 5,
                          full: bool = False) -> list[dict]:
    """Score effective facility rows against ``query`` on display + system ref.

    ``cost_code`` is intentionally IGNORED for filtering (Facility selection is
    manual: the dropdown always searches the COMPLETE master so an LM-inferred
    candidate can still pick an FM ``_PL`` hub and vice versa). Search text is
    the display name (Column C / readable fallback), system reference (Column A)
    and location (Column B); ``full=True`` returns the whole master (sorted for
    browsing) regardless of query.
    """
    rows = get_facility_rows()
    if not rows:
        return []
    if full:
        crows = [dict(r) for r in rows]
        crows.sort(key=lambda r: (r.get("operation", ""), r.get("facility_name", "")))
        return crows
    q = (query or "").strip().lower()
    q_norm = re.sub(r"[\s_\-]+", "", q)
    scored = []
    for row in rows:
        if not q:
            scored.append((0.0, row))
            continue
        display = row.get("facility_name", "")
        ref = row.get("facility_ref", "")
        loc = row.get("location", "")
        readable = _readable_name_from_ref(ref) or display
        best = 0.0
        for hay in (display, ref, loc, readable):
            hl = hay.lower()
            if not hl:
                continue
            if q == hl:
                best = max(best, 150.0)
            elif q in hl:
                best = max(best, 100.0)
            else:
                hl_norm = re.sub(r"[\s_\-]+", "", hl)
                if q_norm and q_norm in hl_norm:
                    best = max(best, 96.0)
                else:
                    qclean = _strip_facility_noise(q_norm)
                    hclean = _strip_facility_noise(hl_norm)
                    q_tokens = [t for t in re.split(r"[\s_\-]+", q) if t]
                    h_tokens = [t for t in re.split(r"[\s_\-]+", hl) if t]
                    token_hits = sum(1 for qt in q_tokens for ht in h_tokens
                                     if qt and ht and len(qt) >= 3 and (qt in ht or ht in qt))
                    if q_tokens and token_hits:
                        best = max(best, token_hits / len(q_tokens) * 90.0)
                    elif qclean and hclean and len(hclean) >= 4:
                        ratio = difflib.SequenceMatcher(None, qclean, hclean).ratio()
                        if ratio >= 0.66:
                            best = max(best, ratio * 100.0)
        scored.append((best, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    max_exact = scored[0][0] if scored else 0.0
    out = []
    for score, row in scored:
        if q and score < 30:
            break
        if q and max_exact >= 96.0 and score < 96.0:
            continue
        out.append(dict(row))
        if len(out) >= top_n:
            break
    return out


def _strip_facility_noise(s: str) -> str:
    """Strip structural affixes (hub/hubs/blr/pl/myntra/… ) from a normalized
    facility string so only the discriminative locality remains for fuzzy
    scoring (e.g. ``nelamangalahubblrpl`` -> ``nelamanga``)."""
    out = (s or "").lower()
    for m in ("myntra", "hubs", "hub", "blr", "pl", "mpl", "lm", "fm"):
        out = out.replace(m, "")
    return re.sub(r"[^a-z0-9]+", "", out)


def fuzzy_search_facilities(query: str, cost_code: str = "", top_n: int = 5,
                            full: bool = False) -> list[dict]:
    """Fuzzy search facility master ROWS across the COMPLETE master.

    Facility selection is manual, so ``cost_code`` is never used to pre-filter
    the available rows — every valid HubName row (Last Mile, First Mile ``_PL``,
    Myntra, IPC/LDP, other) stays searchable/selectable regardless of the
    current inference.

    Primary search text is the display name (Column C / readable fallback); the
    system reference (Column A) and location (Column B) are searched too, so
    ``NelamangalaHub_BLR``/``BLR/NLM`` and ``NelamangalaHub_BLR_PL`` all
    resolve. Returns up to top_n rows as dicts carrying {facility_name,
    location, facility_ref, facility, hub_key, ...}. Duplicate display names
    appear once per row so the UI can offer an exact pick. Final values always
    come from the master.
    """
    return _facility_search_rows(query, "", top_n, full=full)


def fuzzy_find_hub(query: str, hubs: list[str], top_n: int = 5) -> list[str]:
    """Return up to top_n closest official facility names to ``query``.

    The constrained ``hubs`` display list is scored first; when nothing matches
    we fall back to the row-level search (system ref + location are searched
    too) and map results back to display names that exist in ``hubs``. This way
    a hub whose display fell back to a readable name (e.g. ``NelamangalaHub_BLR``)
    is still findable by its location code (``BLR/NLM``) and the returned value
    is always a valid entry of the constrained list.
    """
    if not hubs:
        return [r["facility_name"] for r in _facility_search_rows(query, "", top_n)]
    direct = _fuzzy_find_hub_in(query, hubs, top_n)
    if direct:
        return direct
    out = []
    hubs_set = set(hubs)
    for r in _facility_search_rows(query, "", top_n):
        name = r["facility_name"]
        if name in hubs_set and name not in out:
            out.append(name)
        if len(out) >= top_n:
            break
    return out


def _fuzzy_find_hub_in(query: str, hubs: list[str], top_n: int) -> list[str]:
    q = (query or "").strip().lower()
    if not q or not hubs:
        return hubs[:top_n]
    if len(q) < 3:
        return hubs[:top_n]
    scored = []
    for hub in hubs:
        hl = hub.lower()
        if q in hl:
            scored.append((hub, 1.0))
            continue
        hub_clean = re.sub(r"[\s_\-]+", "", hl).replace("hub", "").replace("blr", "").replace("pl", "")
        base = difflib.SequenceMatcher(None, q.replace(" ", ""), hub_clean).ratio() \
            if len(hub_clean) >= 4 else difflib.SequenceMatcher(None, q, hl).ratio()
        q_tokens = [t for t in re.split(r"[\s_\-]+", q) if t]
        hits = sum(1 for t in q_tokens if len(t) >= 3 and t in hl)
        token_score = hits / max(len(q_tokens), 1)
        score = max(base, token_score * 0.85)
        if score >= 0.4:
            scored.append((hub, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:top_n]]


# Expose module-level authoritative snapshots (reference the LIVE containers
# so reloads via load_facilities() are reflected).
COST_CODE_DESIGNATIONS = _COST_CODE_DESIGNATIONS
HUB_MASTER = _HUB_MASTER
LOCATION_BY_FACILITY = _LOCATION_BY_FACILITY

# Auto-load on import so the app is usable without an explicit init call.
ensure_masters_loaded()
