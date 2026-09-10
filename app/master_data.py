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
    "facility": "Facility_Master.xlsx",
    "self_onboarding": "Self_Onboarding_Template.xlsx",
}

DESIGNATION_FILE = MASTERS_DIR / MASTER_FILES["designation"]
FACILITY_FILE = MASTERS_DIR / MASTER_FILES["facility"]

_COST_CODE_DESIGNATIONS: dict[str, list[str]] = {}
_HUB_MASTER: list[str] = []
_LOCATION_BY_FACILITY: dict[str, str] = {}
_FACILITY_ROWS: list[dict] = []

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


def _extract_facilities(records: list) -> list[dict]:
    """Parse facility rows into effective-style facility dicts WITHOUT touching
    module state or the database. Shared by :func:`load_facilities` and the safe
    dry-run :func:`preview_masters`."""
    facilities = []
    seen = set()
    for r in records:
        facility = _find_col(r, ["FACILITY"])
        location = _find_col(r, ["LOCATION", "LOCATION CODE"])
        facility_name = _find_col(r, ["FACILITY NAME", "FACILITYNAME"]) or facility
        if not facility_name or facility_name.upper() in ("FACILITY NAME", "FACILITY", "N/A", "NA"):
            continue
        if facility_name in seen:
            continue
        seen.add(facility_name)
        is_myntra = "MYNTRA" in facility_name.upper()
        is_fm = facility_name.endswith("_PL")
        # Cost code is derived once from classification rules so the effective
        # master can filter uniformly (Excel facilities emulate admin records).
        cost_code = "8751" if is_myntra else ("4441" if is_fm else "4421")
        entity = "Myntra" if is_myntra else "Flipkart"
        operation = "First Mile" if is_fm else "Last Mile"
        facilities.append({
            "facility_name": facility_name,
            "location": location,
            "facility": facility or facility_name,
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

    Expected columns: FACILITY | LOCATION | FACILITY NAME.
    FACILITY NAME is the main searchable hub value; LOCATION is the location code.
    """
    global _HUB_MASTER, _LOCATION_BY_FACILITY, _FACILITY_ROWS
    fpath = path or FACILITY_FILE
    if not fpath.exists():
        database.record_master_load("facility", fpath.name, 0, "not_configured")
        _HUB_MASTER, _LOCATION_BY_FACILITY, _FACILITY_ROWS = [], {}, []
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
    _HUB_MASTER = [f["facility_name"] for f in facilities]
    _LOCATION_BY_FACILITY = {f["facility_name"]: f["location"] for f in facilities}
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
            "facility_name": a["facility_name"],
            "location": a.get("location_code", ""),
            "facility": a["facility_name"],
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


def get_location_for_facility(facility_name: str) -> str:
    for f in _effective_facilities():
        if f["facility_name"] == facility_name:
            return f["location"]
    return ""


def get_effective_facility(facility_name: str) -> Optional[dict]:
    """Return the effective facility record for an exact facility name (or None)."""
    for f in _effective_facilities():
        if f["facility_name"] == facility_name:
            return dict(f)
    return None


def classify_hub(hub_name: str) -> str:
    upper = (hub_name or "").upper()
    if "MYNTRA" in upper:
        return "Myntra"
    if hub_name.endswith("_PL"):
        return "FM"
    return "LM"


def get_hubs_for_cost_code(cost_code: str) -> list[str]:
    """Exact official facility values valid for a cost code (effective view).

    4421 -> Flipkart LM  |  4441 -> Flipkart FM
    8751 -> Myntra LM    |  8752 -> only when an admin explicitly adds one
    """
    eff = _effective_facilities()
    if not eff:
        return []
    return [f["facility_name"] for f in eff if f["cost_code"] == cost_code]




def fuzzy_search_facilities(query: str, cost_code: str = "", top_n: int = 5) -> list[dict]:
    """Fuzzy search facility master rows (filtered by cost code if given).

    Returns up to top_n rows as {facility_name, location, facility}. Supports
    typing, dropdown, fuzzy matching, and minor spelling mistakes. Final values
    always come from the master.
    """
    hubs = get_hubs_for_cost_code(cost_code) if cost_code else get_facility_names()
    if not hubs:
        return []
    q = (query or "").strip().lower()
    by_name = {f["facility_name"]: f for f in _effective_facilities()}
    scored = []
    for hub in hubs:
        hl = hub.lower()
        if not q:
            score = 0.0
        elif q in hl:
            score = 100.0
        else:
            hub_clean = re.sub(r"[\s_\-]+", "", hl).replace("hub", "").replace("blr", "").replace("pl", "")
            score = max(
                difflib.SequenceMatcher(None, q.replace(" ", ""), hub_clean).ratio() * 100,
                difflib.SequenceMatcher(None, q, hl).ratio() * 100,
            )
        scored.append((score, hub))
    if q:
        scored.sort(key=lambda x: x[0], reverse=True)
    else:
        scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, hub in scored:
        if q and score < 30:
            break
        out.append(by_name[hub])
        if len(out) >= top_n:
            break
    return out


def fuzzy_find_hub(query: str, hubs: list[str], top_n: int = 5) -> list[str]:
    """Return up to top_n closest official facility names to ``query``."""
    return [r["facility_name"] for r in fuzzy_search_facilities(query, "")] if not hubs \
        else _fuzzy_find_hub_in(query, hubs, top_n)


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


# Expose module-level authoritative snapshots for rules / frontend.
COST_CODE_DESIGNATIONS = _COST_CODE_DESIGNATIONS
HUB_MASTER = _HUB_MASTER
LOCATION_BY_FACILITY = _LOCATION_BY_FACILITY

# Auto-load on import so the app is usable without an explicit init call.
ensure_masters_loaded()
