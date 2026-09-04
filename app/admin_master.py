"""Admin Master Management service layer.

Allows an authorized admin to manage hubs/facilities and roles/designations
directly from the TeamHR UI, layered on top of the existing Excel masters.

Design invariants
-----------------
* Excel masters remain the base. Admin records are a SEPARATE SQLite layer that
  is merged into the *effective* master view consumed by the production
  resolver. The source Excel files are NEVER modified by admin actions.
* Cost code is DERIVED from Entity + Operation and can never be entered
  manually to an incompatible value.
* Identity fields (Facility Name, Entity, Operation, Cost Code) are immutable
  after creation; corrections require deactivate + recreate (protects history).
* Nothing is hard-deleted; rows are Active/Inactive.
* Every change is recorded in master_change_history with changed_by.
* Admin master changes never touch candidates, generated Excel, or eSampark.

The effective merge (Excel + admin overrides/additions - inactive) is owned by
:mod:`app.master_data`; this module provides the raw admin records, validation,
CRUD and the derived cost code, plus display/export/count helpers.
"""

from __future__ import annotations

from typing import Optional

from app import database
from app import rules

# Cost code is derived exclusively from Entity + Operation (4 valid combos).
COST_CODE_BY_COMBO = {
    ("Flipkart", "Last Mile"): "4421",
    ("Flipkart", "First Mile"): "4441",
    ("Myntra", "Last Mile"): "8751",
    ("Myntra", "First Mile"): "8752",
}

ENTITIES = ["Flipkart", "Myntra"]
OPERATIONS = ["Last Mile", "First Mile"]
FACILITY_TYPES = ["Delivery Hub"]

# Admin feature toggle. Default on for local development. No passwords/auth in
# this stage — access control can be layered on later.
ADMIN_FEATURE_ENABLED = True

# Prexo is never allowed for Myntra Last Mile (8751) and Myntra First Mile (8752).
PREXO_RESTRICTED_CODES = {"8751", "8752"}
PREXO_MARKERS = ("prexo",)


def admin_enabled() -> bool:
    """True when the Admin Master page is available (default: local dev)."""
    return bool(ADMIN_FEATURE_ENABLED)


# ── Cost code derivation / validation helpers ───────────────────────────────


def derive_cost_code(entity: str, operation: str) -> Optional[str]:
    """Return the cost code for an entity+operation, or None if incompatible."""
    return COST_CODE_BY_COMBO.get((entity, operation))


def valid_entity_operation() -> list[dict]:
    return [
        {"entity": e, "operation": o, "cost_code": COST_CODE_BY_COMBO[(e, o)]}
        for e, o in COST_CODE_BY_COMBO
    ]


def _norm(value: Optional[str]) -> str:
    return (value or "").strip()


# ── Facility validation ──────────────────────────────────────────────────────


def validate_facility(data: dict, existing: Optional[dict] = None) -> tuple[bool, list[str], str]:
    """Validate a facility create/edit.

    Returns (ok, errors, cost_code). On create the identity fields are set and
    the cost code must match entity+operation exactly (no manual override). On
    edit only location_code / state / active are changeable (identity immutable).
    """
    errors: list[str] = []
    name = _norm(data.get("facility_name"))
    entity = _norm(data.get("entity"))
    operation = _norm(data.get("operation"))
    location = _norm(data.get("location_code"))
    cost_code = _norm(data.get("cost_code"))

    if existing is None:
        # ── Create path ──
        if not name:
            errors.append("Facility Name is required.")
        if not location:
            errors.append("Location Code is required.")
        if entity not in ENTITIES:
            errors.append(f"Entity must be one of: {', '.join(ENTITIES)}.")
        if operation not in OPERATIONS:
            errors.append(f"Operation must be one of: {', '.join(OPERATIONS)}.")

        derived = derive_cost_code(entity, operation)
        if derived is None and entity and operation:
            errors.append("Invalid Entity/Operation combination.")
        if cost_code and derived and cost_code != derived:
            errors.append(
                f"Cost code {cost_code} is not compatible with {entity} + {operation}. "
                f"It must be {derived}."
            )
        # The cost code the admin may think they see is forbidden if free-text;
        # we always force the derived value regardless of what was submitted.
        if errors:
            return False, errors, derived or ""

        # Uniqueness: an ADMIN record for the same facility must be unique. A
        # name that already exists in the Excel master is allowed here because
        # it creates an ADMIN OVERRIDE (the effective view replaces the Excel
        # row), which is an explicit, supported capability.
        dup_admin = database.get_master_facility_by_name(name)
        if dup_admin:
            errors.append(
                f"An admin facility named '{name}' already exists "
                f"(id {dup_admin['facility_id']}). Duplicate creation blocked."
            )
            return False, errors, derived
        return True, [], derived

    # ── Edit path: identity immutable ──
    identity_changed = [
        f for f in ("facility_name", "entity", "operation", "cost_code")
        if _norm(data.get(f)) and existing.get(f) != _norm(data.get(f))
    ]
    if identity_changed:
        field = identity_changed[0]
        errors.append(
            f"Facility identity field '{field}' cannot be changed on an existing record. "
            "Deactivate the old facility and create a new one instead (protects history)."
        )
    if not location:
        errors.append("Location Code is required.")
    if errors:
        return False, errors, existing.get("cost_code", "")
    return True, [], existing.get("cost_code", "")


def find_any_active_facility(name: str) -> Optional[dict]:
    """Search both admin records and the effective (Excel) view for a name."""
    admin_rec = database.get_master_facility_by_name(name)
    if admin_rec:
        return {"id": admin_rec["facility_id"], "cost_code": admin_rec["cost_code"],
                "location_code": admin_rec["location_code"], "source": "Admin"}
    from app import master_data
    if name in master_data.get_facility_names():
        loc = master_data.get_location_for_facility(name)
        return {"id": "excel", "cost_code": "", "location_code": loc, "source": "Excel Import"}
    return None


# ── Facility CRUD ────────────────────────────────────────────────────────────


def add_facility(data: dict) -> dict:
    """Create a new facility. Returns {ok, errors, facility, cost_code, history_id}."""
    ok, errors, cost_code = validate_facility(data)
    if not ok:
        return {"ok": False, "errors": errors}
    name = _norm(data.get("facility_name"))
    facility_id = database.create_master_facility({
        "facility_name": name,
        "location_code": _norm(data.get("location_code")),
        "entity": _norm(data.get("entity")),
        "operation": _norm(data.get("operation")),
        "cost_code": cost_code,
        "facility_type": "Delivery Hub",
        "state": _norm(data.get("state")),
        "active": 1,
        "source": "Admin",
    })
    summary = (f"{name} | {cost_code} | {_norm(data.get('location_code'))} | "
               f"{_norm(data.get('entity'))} {_norm(data.get('operation'))}")
    database.record_master_change("facility", facility_id, "Created", "", summary)
    return {"ok": True, "facility_id": facility_id, "cost_code": cost_code,
            "facility": database.get_master_facility(facility_id)}


def update_facility(facility_id: int, data: dict) -> dict:
    """Edit a facility's editable fields (location_code, state, active)."""
    existing = database.get_master_facility(facility_id)
    if not existing:
        return {"ok": False, "errors": ["Facility not found."]}
    ok, errors, cost_code = validate_facility(data, existing)
    if not ok:
        return {"ok": False, "errors": errors}
    old = (f"location={existing.get('location_code')} state={existing.get('state')} "
           f"active={existing.get('active')}")
    new = (f"location={_norm(data.get('location_code'))} state={_norm(data.get('state'))} "
           f"active={1 if _norm(data.get('active')) else 0}")
    database.update_master_facility_editable(facility_id, data)
    database.record_master_change("facility", facility_id, "Updated", old, new)
    return {"ok": True, "facility": database.get_master_facility(facility_id)}


def set_facility_active(facility_id: int, active: bool) -> dict:
    existing = database.get_master_facility(facility_id)
    if not existing:
        return {"ok": False, "errors": ["Facility not found."]}
    action = "Deactivated" if not active else "Reactivated"
    database.set_master_facility_active(facility_id, active)
    database.record_master_change(
        "facility", facility_id, action,
        f"active={1 if active else 0}",
        f"active={0 if active else 1}",
    )
    return {"ok": True, "facility": database.get_master_facility(facility_id)}


def list_admin_facilities() -> list[dict]:
    """All admin facility records (including inactive) — used to build the
    effective master view."""
    return database.list_master_facilities(active_only=False)


# ── Role validation ──────────────────────────────────────────────────────────


def validate_role(data: dict, existing: Optional[dict] = None) -> tuple[bool, list[str]]:
    errors: list[str] = []
    name = _norm(data.get("official_name"))
    entity_scope = _norm(data.get("entity_scope"))
    operation = _norm(data.get("operation"))
    cost_codes = [c.strip() for c in _norm(data.get("cost_codes")).replace(",", " ").split() if c.strip()]
    aliases = [a.strip().lower() for a in _norm(data.get("aliases")).replace(",", "\n").replace(";", "\n").split("\n") if a.strip()]

    if not name:
        errors.append("Official Role Name is required.")
    if entity_scope and entity_scope not in ("Flipkart", "Myntra", "Both") and entity_scope != "Both":
        errors.append("Entity scope must be Flipkart, Myntra or Both.")
    if operation not in OPERATIONS:
        errors.append(f"Operation must be one of: {', '.join(OPERATIONS)}.")

    # Allowed cost codes must exist and be compatible with the operation.
    valid_cc = {oc["cost_code"] for oc in valid_entity_operation()
                if not operation or oc["operation"] == operation}
    for cc in cost_codes:
        if cc not in rules.COST_CODES:
            errors.append(f"Cost code {cc} is invalid.")
        elif operation and cc not in valid_cc:
            errors.append(f"Cost code {cc} is not compatible with {operation} operation.")

    # Prexo restriction remains explicit.
    if any(m in name.lower() for m in PREXO_MARKERS):
        for cc in cost_codes:
            if cc in PREXO_RESTRICTED_CODES:
                errors.append(
                    f"Prexo roles must NOT allow cost code {cc} ({rules.COST_CODES[cc]['label']})."
                )

    if errors:
        return False, errors

    if existing is None:
        dup = database.get_master_role_by_name(name)
        if dup:
            errors.append(f"A role named '{name}' already exists. Duplicate blocked.")
            return False, errors
    return True, []


# ── Role CRUD ────────────────────────────────────────────────────────────────


def _split_codes_and_aliases(data: dict) -> tuple[list[str], list[str]]:
    cost_codes = [c.strip() for c in _norm(data.get("cost_codes")).replace(",", " ").split() if c.strip()]
    aliases = [a.strip().lower() for a in _norm(data.get("aliases")).replace(",", "\n").replace(";", "\n").split("\n") if a.strip()]
    return cost_codes, aliases


def add_role(data: dict) -> dict:
    ok, errors = validate_role(data)
    if not ok:
        return {"ok": False, "errors": errors}
    name = _norm(data.get("official_name"))
    cost_codes, aliases = _split_codes_and_aliases(data)
    role_id = database.create_master_role(
        name,
        _norm(data.get("entity_scope")),
        _norm(data.get("operation")),
        cost_codes,
        aliases,
        source="Admin",
    )
    summary = f"{name} | {_norm(data.get('operation'))} | allowed={','.join(sorted(cost_codes))}"
    database.record_master_change("role", role_id, "Created", "", summary)
    return {"ok": True, "role_id": role_id, "role": get_role_payload(role_id)}


def update_role(role_id: int, data: dict) -> dict:
    existing = database.get_master_role(role_id)
    if not existing:
        return {"ok": False, "errors": ["Role not found."]}
    ok, errors = validate_role(data, existing)
    if not ok:
        return {"ok": False, "errors": errors}
    cost_codes, aliases = _split_codes_and_aliases(data)
    old_cc = ",".join(current_role_cost_codes(role_id))
    database.update_master_role(
        role_id, _norm(data.get("entity_scope")), _norm(data.get("operation")),
        cost_codes, aliases,
        active=bool(_norm(data.get("active"))),
    )
    new_cc = ",".join(sorted(cost_codes))
    database.record_master_change(
        "role", role_id, "Updated",
        f"allowed={old_cc}",
        f"allowed={new_cc}",
    )
    return {"ok": True, "role": get_role_payload(role_id)}


def set_role_active(role_id: int, active: bool) -> dict:
    existing = database.get_master_role(role_id)
    if not existing:
        return {"ok": False, "errors": ["Role not found."]}
    action = "Deactivated" if not active else "Reactivated"
    database.set_master_role_active(role_id, active)
    database.record_master_change("role", role_id, action, "", "")
    return {"ok": True, "role": get_role_payload(role_id)}


def current_role_cost_codes(role_id: int) -> list[str]:
    return database.get_role_cost_codes(role_id)


def current_role_aliases(role_id: int) -> list[str]:
    return database.get_role_aliases(role_id)


def get_role_payload(role_id: int) -> dict:
    role = database.get_master_role(role_id)
    if not role:
        return {}
    return {
        "role_id": role["role_id"],
        "official_name": role["official_name"],
        "entity_scope": role["entity_scope"],
        "operation": role["operation"],
        "cost_codes": current_role_cost_codes(role_id),
        "aliases": current_role_aliases(role_id),
        "active": role["active"],
        "source": role["source"],
    }


def list_admin_roles() -> list[dict]:
    return [get_role_payload(r["role_id"]) for r in database.list_master_roles(active_only=False)]


# ── Effective view helpers (for Admin UI display / export / counts) ─────────


def effective_facilities() -> list[dict]:
    from app import master_data
    return master_data.get_facilities()


def effective_roles_for_cost_code(cost_code: str) -> list[str]:
    try:
        return database.get_roles_for_cost_code_admin(cost_code)
    except Exception:  # noqa: BLE001
        return []


def effective_aliases() -> dict:
    """admin alias -> OFFICIAL role name (only active roles)."""
    try:
        return database.get_admin_role_aliases()
    except Exception:  # noqa: BLE001
        return {}


def get_master_status() -> dict:
    """Counts for the Settings -> Master Data admin status block."""
    from app import master_data
    excel_fac = len(master_data._FACILITY_ROWS)
    admin_fac = len(database.list_master_facilities(active_only=False))
    admin_fac_active = len(database.list_master_facilities(active_only=True))
    eff_fac = len(effective_facilities())

    excel_desig = len(master_data.get_all_designations())
    admin_roles = len(database.list_master_roles(active_only=False))
    admin_roles_active = len(database.list_master_roles(active_only=True))
    eff_roles = len(set(master_data.get_all_designations()) |
                    {r["official_name"] for r in list_admin_roles() if r.get("active")})

    overrides = len([f for f in database.list_master_facilities(active_only=False)
                     if f["facility_name"] in excel_fac_names()])

    return {
        "excel_facilities": excel_fac,
        "admin_facilities": admin_fac_active,
        "effective_facilities": eff_fac,
        "excel_roles": excel_desig,
        "admin_roles": admin_roles_active,
        "effective_roles": eff_roles,
        "overrides": overrides,
        "admin_enabled": admin_enabled(),
    }


def excel_fac_names() -> set:
    from app import master_data
    return {f["facility_name"] for f in master_data._FACILITY_ROWS}


# ── Change history ───────────────────────────────────────────────────────────


def change_history(limit: int = 200) -> list[dict]:
    return database.list_master_changes(limit)


# ── Export helpers ───────────────────────────────────────────────────────────


def export_facilities() -> list[dict]:
    return [{
        "Facility": f["facility_name"],
        "Location": f.get("location", ""),
        "Entity": f.get("entity", ""),
        "Operation": f.get("operation", ""),
        "Cost Code": f.get("cost_code", ""),
        "Facility Type": f.get("facility_type", "Delivery Hub"),
        "State": f.get("state", ""),
        "Source": f.get("source", ""),
    } for f in effective_facilities()]


def export_roles() -> list[dict]:
    """Effective role list: Excel designations + active admin roles.

    Mirrors the effective view used by the production resolver so the exported
    roles match what is actually in play.
    """
    from app import master_data
    seen = set()
    out = []
    for cc in sorted(rules.COST_CODES):
        for name in master_data.get_designations_for_cost_code(cc):
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "Role": name,
                "Operation": rules.COST_CODES[cc]["operation"],
                "Allowed Cost Codes": ",".join(sorted({
                    k for k, info in rules.COST_CODES.items()
                    if name in master_data.get_designations_for_cost_code(k)
                })),
                "Aliases": "",
                "Source": "Excel Import",
                "Active": 1,
            })
    for r in list_admin_roles():
        if not r.get("active"):
            continue
        if r["official_name"] in seen:
            continue
        seen.add(r["official_name"])
        out.append({
            "Role": r["official_name"],
            "Operation": r["operation"],
            "Allowed Cost Codes": ",".join(r["cost_codes"]),
            "Aliases": ",".join(r["aliases"]),
            "Source": r["source"],
            "Active": r["active"],
        })
    return out
