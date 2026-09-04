import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import rules
from app import ocr
from app import master_data
from app import database
from app import generation
from app import admin_master
from app.portal import service as portal_service
from app.portal import live_upload

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="TeamHR Automation")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Starlette >= 1.6 moved ``Jinja2Templates.TemplateResponse`` to the
# ``(request, name, context)`` signature. Every page route here uses the older
# ``(name, context)`` form, so wrap it for compatibility.
_original_template_response = templates.TemplateResponse


def _template_response(name_or_request, context=None, **kwargs):
    """Accept the legacy ``TemplateResponse(name, {"request": request, ...})``
    call used by every page route, forwarding to Starlette's current
    ``(request, name, context)`` signature."""
    if isinstance(name_or_request, Request):
        request, name = name_or_request, context
    else:
        name = name_or_request
        request = (context or {}).get("request")
    return _original_template_response(request, name, context, **kwargs)


templates.TemplateResponse = _template_response

database.init_db()


def _get_now():
    now = datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")


def _count_candidates():
    return database.count_candidates()


# ── Dashboard ────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    counts = _count_candidates()
    stats = database.dashboard_stats()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "candidates": counts,
            "stats": stats,
            "nav_active": "dashboard",
        },
    )


# ── Manual Entry ─────────────────────────────────────────────────────────────


@app.get("/manual-entry", response_class=HTMLResponse)
async def manual_entry_page(request: Request, edit: Optional[int] = None):
    rules_json = json.dumps(rules.get_rules_for_frontend())
    edit_candidate = None
    if edit:
        edit_candidate = database.get_candidate(edit)
    return templates.TemplateResponse(
        "manual_entry.html",
        {
            "request": request,
            "rules_json": rules_json,
            "edit_candidate": edit_candidate,
            "edit_id": edit,
        },
    )


def _attach_documents(candidate_id: int, source_files) -> None:
    """Persist document records for a saved/confirmed candidate."""
    if not source_files:
        return
    for f in source_files or []:
        if isinstance(f, dict):
            database.insert_document({
                "candidate_id": candidate_id,
                "filename": f.get("filename", ""),
                "file_type": f.get("file_type", ""),
                "file_size": f.get("file_size", 0),
                "document_type": "Aadhaar",
                "extraction_status": "extracted",
            })
        elif isinstance(f, str):
            database.insert_document({
                "candidate_id": candidate_id,
                "filename": f,
                "file_type": "",
                "file_size": 0,
                "document_type": "Aadhaar",
                "extraction_status": "extracted",
            })


@app.post("/api/save-draft")
async def save_draft(request: Request):
    data = await request.json()
    now_date, now_time = _get_now()

    edit_id = data.get("edit_id")
    if edit_id:
        updated = database.update_candidate(edit_id, {
            "name": data.get("name", ""),
            "mobile": data.get("mobile", ""),
            "entity": data.get("entity", ""),
            "cost_code": data.get("cost_code", ""),
            "operation": data.get("operation", ""),
            "team": data.get("team", ""),
            "designation": data.get("role", data.get("designation", "")),
            "facility_type": data.get("facility_type", ""),
            "facility": data.get("facility", ""),
            "facility_name": data.get("facility", data.get("facility_name", "")),
            "location_code": rules.get_location_for_facility(data.get("facility", data.get("facility_name", ""))),
            "salary": data.get("salary_normalized", data.get("salary")),
            "salary_display": data.get("salary_display", ""),
            "aadhaar_number": data.get("aadhaar_number", ""),
            "dob": data.get("dob", ""),
            "address": data.get("address", ""),
            "status": "draft",
        })
        return JSONResponse({"id": edit_id, "status": "draft"})

    cid = database.insert_candidate({
        "batch_id": data.get("batch_id", 1),
        "candidate_number": data.get("candidate_number", 1),
        "name": data.get("name", ""),
        "mobile": data.get("mobile", ""),
        "entity": data.get("entity", ""),
        "cost_code": data.get("cost_code", ""),
        "operation": data.get("operation", ""),
        "team": data.get("team", ""),
        "designation": data.get("role", data.get("designation", "")),
        "facility_type": data.get("facility_type", ""),
        "facility_name": data.get("facility", data.get("facility_name", "")),
        "location_code": rules.get_location_for_facility(data.get("facility", data.get("facility_name", ""))),
        "salary": data.get("salary_normalized", data.get("salary")),
        "salary_display": data.get("salary_display", ""),
        "aadhaar_number": data.get("aadhaar_number", ""),
        "dob": data.get("dob", ""),
        "address": data.get("address", ""),
        "status": "draft",
    })
    draft_files = data.get("source_files")
    if not draft_files and data.get("aadhaar_filename"):
        draft_files = [data.get("aadhaar_filename")]
    _attach_documents(cid, draft_files or [])
    return JSONResponse({"id": cid, "status": "draft"})


@app.post("/api/confirm-candidate")
async def confirm_candidate(request: Request):
    data = await request.json()
    errors = rules.validate_candidate(data)
    if errors:
        return JSONResponse({"errors": errors}, status_code=422)

    mobile_normalized, _ = rules.normalize_mobile(data.get("mobile", ""))
    salary_normalized, _ = rules.normalize_salary(str(data.get("salary", "")))
    cost_info = rules.get_cost_code_info(data.get("cost_code", ""))

    cost_code = data.get("cost_code", "")
    auto_ft = rules.COST_CODE_FACILITY_TYPE.get(cost_code)
    if auto_ft:
        ft_resolved = auto_ft
    else:
        ft_resolved, _ = rules.resolve_facility_type(data.get("facility_type", ""))

    role_query = data.get("role", "")
    role_resolved, _ = rules.resolve_role_for_cost_code(role_query, cost_code)

    facility = data.get("facility", "")
    location_code = rules.get_location_for_facility(facility)

    edit_id = data.get("edit_id")
    if edit_id:
        updated = database.update_candidate(edit_id, {
            "name": data.get("name", ""),
            "mobile": mobile_normalized,
            "entity": cost_info["entity"] if cost_info else "",
            "cost_code": cost_code,
            "operation": cost_info["operation"] if cost_info else "",
            "team": cost_info["team"] if cost_info else "",
            "designation": role_resolved or role_query,
            "facility_type": ft_resolved or data.get("facility_type", ""),
            "facility": facility,
            "facility_name": facility,
            "location_code": location_code,
            "salary": salary_normalized,
            "salary_display": data.get("salary_display", ""),
            "aadhaar_number": data.get("aadhaar_number", ""),
            "dob": data.get("dob", ""),
            "address": data.get("address", ""),
            "status": "ready",
        })
        if updated:
            _attach_documents(edit_id, data.get("source_files") or [])
        return JSONResponse({"id": edit_id, "status": "ready"})

    cid = database.insert_candidate({
        "batch_id": data.get("batch_id", 1),
        "candidate_number": data.get("candidate_number", 1),
        "name": data.get("name", ""),
        "mobile": mobile_normalized,
        "entity": cost_info["entity"] if cost_info else "",
        "cost_code": cost_code,
        "operation": cost_info["operation"] if cost_info else "",
        "team": cost_info["team"] if cost_info else "",
        "designation": role_resolved or role_query,
        "facility_type": ft_resolved or data.get("facility_type", ""),
        "facility_name": facility,
        "location_code": location_code,
        "salary": salary_normalized,
        "salary_display": data.get("salary_display", ""),
        "aadhaar_number": data.get("aadhaar_number", ""),
        "dob": data.get("dob", ""),
        "address": data.get("address", ""),
        "migrant": data.get("migrant", "No"),
        "status": "ready",
    })
    source_files = data.get("source_files") or (data.get("aadhaar_filename") and [data.get("aadhaar_filename")]) or []
    _attach_documents(cid, source_files)
    return JSONResponse({"id": cid, "status": "ready"})


@app.post("/api/check-duplicate")
async def check_duplicate(request: Request):
    data = await request.json()
    mobile, _ = rules.normalize_mobile(data.get("mobile", ""))
    exclude_id = data.get("exclude_id")
    if not mobile:
        return JSONResponse({"duplicate": False})
    existing = database.find_duplicate_mobile(mobile, exclude_id)
    if existing:
        return JSONResponse({
            "duplicate": True,
            "existing_id": existing["candidate_id"],
            "existing": {
                "name": existing.get("name"),
                "mobile": existing.get("mobile"),
                "facility": existing.get("facility_name"),
                "status": existing.get("status"),
                "cost_code": existing.get("cost_code"),
                "created_at": existing.get("created_date") + " " + existing.get("created_time", ""),
                "created_date": existing.get("created_date"),
            },
        })
    return JSONResponse({"duplicate": False})


# ── Batch Review ─────────────────────────────────────────────────────────────


@app.get("/batch-review", response_class=HTMLResponse)
async def batch_review_page(request: Request, batch_id: int = 1):
    batch = database.get_batch_candidates(batch_id)
    if not batch and not database.get_batch(batch_id):
        database.get_or_create_batch(batch_id=batch_id)
    batch = database.get_batch_candidates(batch_id)
    counts = {
        "total": len(batch),
        "ready": sum(1 for c in batch if c["status"] == "ready"),
        "draft": sum(1 for c in batch if c["status"] == "draft"),
        "needs_attention": sum(1 for c in batch if c["status"] == "needs_attention"),
        "generated": sum(1 for c in batch if c.get("excel_generated") == "true"),
        "other": sum(1 for c in batch if c["status"] not in ("ready", "draft", "needs_attention")),
    }
    return templates.TemplateResponse(
        "batch_review.html",
        {
            "request": request,
            "candidates": batch,
            "counts": counts,
            "batch_id": batch_id,
            "template_configured": generation.template_is_configured(),
            "output_base_dir": str(generation.get_output_base_dir()),
            "generated_files": database.list_generated_files(batch_id),
        },
    )


@app.post("/api/remove-candidate/{candidate_id}")
async def remove_candidate(candidate_id: int):
    if database.remove_candidate(candidate_id):
        return JSONResponse({"success": True})
    return JSONResponse({"error": "Not found"}, status_code=404)


@app.post("/api/edit-candidate/{candidate_id}")
async def edit_candidate(candidate_id: int, request: Request):
    data = await request.json()
    if database.update_candidate(candidate_id, data):
        return JSONResponse({"success": True})
    return JSONResponse({"error": "Not found"}, status_code=404)


# ── Candidates Page ──────────────────────────────────────────────────────────


@app.get("/candidates", response_class=HTMLResponse)
async def candidates_page(request: Request, search: str = ""):
    all_candidates = database.search_candidates(search)
    return templates.TemplateResponse(
        "candidates.html",
        {"request": request, "candidates": all_candidates, "search": search},
    )


@app.get("/api/candidates")
async def api_candidates(search: str = ""):
    return JSONResponse(database.search_candidates(search))


@app.get("/api/search")
async def api_search(q: str = ""):
    """Global search. Searches candidates (name/mobile/id/batch/facility/role)
    and generated files (filename). NEVER searches sensitive Aadhaar values.
    """
    q = (q or "").strip()
    if not q:
        return JSONResponse({"candidates": [], "files": []})
    ql = q.lower()
    candidates = database.list_candidates()
    matches = []
    for c in candidates:
        name = (c.get("name") or "").lower()
        mobile = (c.get("mobile") or "").strip()
        facility = (c.get("facility_name") or "").lower()
        role = (c.get("designation") or "").lower()
        cid = str(c.get("candidate_id") or "")
        bid = str(c.get("batch_id") or "")
        if (ql in name or ql in mobile or ql in cid or ql in bid
                or ql in facility or ql in role):
            matches.append({
                "candidate_id": c.get("candidate_id"),
                "name": c.get("name"),
                "mobile": mobile,
                "status": c.get("status") or "Draft",
                "batch_id": c.get("batch_id"),
            })
    matches = matches[:20]

    files = []
    for f in database.list_generated_files(limit=200):
        filename = (f.get("filename") or "").lower()
        if ql in filename:
            files.append({
                "filename": f.get("filename"),
                "batch_id": f.get("batch_id"),
            })
    files = files[:10]
    return JSONResponse({"candidates": matches, "files": files})


# ── Data Quality Command Center ──────────────────────────────────────────────


def _dq_categories(candidates: list[dict]) -> list[dict]:
    """Classify candidates into data-quality buckets (safe metrics only)."""
    def first_missing_required(c):
        for f in ("name", "mobile", "aadhaar_number"):
            if not (c.get(f) or "").strip():
                return f
        return None

    def low_confidence(c):
        # No evidence score stored on candidate rows; treat missing address as
        # the informational "missing address" signal instead.
        return None

    buckets = {
        "missing_name": [],
        "low_confidence": [],
        "missing_address": [],
        "duplicate_mobile": [],
        "role_conflict": [],
        "facility_conflict": [],
        "unknown_facility": [],
        "invalid_salary": [],
        "needs_review": [],
    }

    mobile_seen = {}
    for c in candidates:
        m = (c.get("mobile") or "").strip()
        mobile_seen.setdefault(m, []).append(c)

    for c in candidates:
        cid = c.get("candidate_id")
        if not (c.get("name") or "").strip():
            buckets["missing_name"].append(cid)
        if not (c.get("address") or "").strip():
            buckets["missing_address"].append(cid)
        if not (c.get("facility_name") or "").strip():
            buckets["unknown_facility"].append(cid)
        m = (c.get("mobile") or "").strip()
        if len(mobile_seen.get(m, [])) > 1:
            buckets["duplicate_mobile"].append(cid)
        salary = c.get("salary")
        try:
            sal = int(salary)
            if sal <= 0 or sal > 100000:
                buckets["invalid_salary"].append(cid)
        except (TypeError, ValueError):
            if salary not in (None, "", 0):
                buckets["invalid_salary"].append(cid)
        if (c.get("status") or "").lower() in ("needs_attention",):
            buckets["needs_review"].append(cid)

    # Severity labels exposed in the command center cards.
    def bucket(label, cids, severity):
        return {
            "key": label.replace("_", "-"),
            "label": label.replace("_", " ").title(),
            "count": len(cids),
            "severity": severity,
            "candidate_ids": cids[:20],
        }

    return [
        bucket("needs_review", buckets["needs_review"], "Critical"),
        bucket("duplicate_mobile", buckets["duplicate_mobile"], "Critical"),
        bucket("missing_name", buckets["missing_name"], "Critical"),
        bucket("unknown_facility", buckets["unknown_facility"], "Review"),
        bucket("missing_address", buckets["missing_address"], "Review"),
        bucket("invalid_salary", buckets["invalid_salary"], "Review"),
        bucket("role_conflict", buckets["role_conflict"], "Review"),
        bucket("facility_conflict", buckets["facility_conflict"], "Review"),
        bucket("low_confidence", buckets["low_confidence"], "Informational"),
    ]


@app.get("/data-quality", response_class=HTMLResponse)
async def data_quality_page(request: Request):
    candidates = database.list_candidates()
    cards = _dq_categories(candidates)
    return templates.TemplateResponse(
        "data_quality.html",
        {
            "request": request,
            "cards": cards,
            "nav_active": "data_quality",
            "total": len(candidates),
        },
    )



# ── Reports Page ─────────────────────────────────────────────────────────────


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    counts = _count_candidates()
    return templates.TemplateResponse(
        "reports.html",
        {"request": request, "counts": counts},
    )


# ── Settings / Master Data ───────────────────────────────────────────────────


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    status = master_data.get_master_status()
    history = database.get_master_load_history(20)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "master_status": status,
            "admin_master_status": admin_master.get_master_status(),
            "master_history": history,
            "output_config": generation.get_output_config(),
            "generated_history": database.list_generated_files(limit=20),
            "generation_audit": database.get_generation_history(20),
            "daily_master": database.get_daily_master_mirror(),
            "portal_status": portal_service.portal_status(),
            "portal_uploads": database.list_portal_uploads(limit=20),
            "portal_audit": database.get_portal_audit(20),
        },
    )


@app.get("/api/master-status")
async def api_master_status():
    return JSONResponse({
        **master_data.get_master_status(),
        "history": database.get_master_load_history(20),
    })


@app.post("/api/reload-masters")
async def api_reload_masters():
    status = master_data.load_masters()
    for kind in ("designation", "facility"):
        info = status.get(kind, {})
        database.record_master_load(
            master_type=kind,
            filename=info.get("filename", ""),
            row_count=info.get("row_count", 0),
            status="OK" if info.get("ok") else "ERROR",
        )
    rules.refresh_masters()
    return JSONResponse({
        "reloaded": True,
        "status": master_data.get_master_status(),
    })


# ── Admin Master Management ─────────────────────────────────────────────────
# Admin-managed hubs/facilities, roles/designations, aliases, override tracking
# and change history. Never touches Excel masters, candidates, generated files
# or eSampark. No passwords/auth in this stage (local dev; ADMIN_FEATURE_ENABLED).


def _admin_mutation_refresh():
    """Refresh resolver snapshots after an admin master mutation so the change
    takes effect immediately without an app restart."""
    from app import master_data as _md
    _md.ensure_masters_loaded()
    rules.refresh_masters()


@app.get("/admin/master-data", response_class=HTMLResponse)
async def admin_master_page(request: Request, tab: str = "facilities"):
    if not admin_master.admin_enabled():
        return JSONResponse({"ok": False, "error": "Admin feature is disabled."},
                            status_code=403)
    facilities = admin_master.effective_facilities()
    roles = admin_master.list_admin_roles()
    excel_facilities = [dict(f) for f in master_data._FACILITY_ROWS]
    excel_roles = master_data.get_all_designations()
    return templates.TemplateResponse(
        "admin_master.html",
        {
            "request": request,
            "tab": tab,
            "facilities": facilities,
            "admin_facilities": admin_master.list_admin_facilities(),
            "roles": roles,
            "excel_facilities": excel_facilities,
            "excel_roles": excel_roles,
            "combos": admin_master.valid_entity_operation(),
            "entities": admin_master.ENTITIES,
            "operations": admin_master.OPERATIONS,
            "facility_types": admin_master.FACILITY_TYPES,
            "history": admin_master.change_history(200),
            "master_status": admin_master.get_master_status(),
            "admin_enabled": admin_master.admin_enabled(),
        },
    )


@app.get("/api/admin/status")
async def api_admin_status():
    return JSONResponse(admin_master.get_master_status())


@app.get("/api/admin/facilities")
async def api_admin_facilities(q: str = "", filter: str = "All"):
    fac = admin_master.effective_facilities()
    f = filter or "All"
    out = []
    for row in fac:
        if f == "Flipkart" and row.get("entity") != "Flipkart":
            continue
        if f == "Myntra" and row.get("entity") != "Myntra":
            continue
        if f == "LM" and (row.get("operation") or "").split()[0] != "Last":
            continue
        if f == "FM" and (row.get("operation") or "").split()[0] != "First":
            continue
        if f == "Active" and not int(row.get("active", 1)):
            continue
        if f == "Inactive" and int(row.get("active", 1)):
            continue
        if q:
            ql = q.lower()
            hay = " ".join([
                str(row.get("facility_name", "")),
                str(row.get("location", "")),
                str(row.get("cost_code", "")),
                str(row.get("entity", "")),
                str(row.get("operation", "")),
            ]).lower()
            if " ".join(ql.split()) not in hay:
                continue
        out.append(row)
    return JSONResponse({"ok": True, "facilities": out})


@app.post("/api/admin/facilities")
async def api_admin_add_facility(request: Request):
    data = await request.json() or {}
    res = admin_master.add_facility(data)
    if res.get("ok"):
        _admin_mutation_refresh()
    return JSONResponse(res, status_code=200 if res.get("ok") else 422)


@app.put("/api/admin/facilities/{facility_id}")
async def api_admin_update_facility(facility_id: int, request: Request):
    data = await request.json() or {}
    res = admin_master.update_facility(facility_id, data)
    if res.get("ok"):
        _admin_mutation_refresh()
    return JSONResponse(res, status_code=200 if res.get("ok") else 422)


@app.post("/api/admin/facilities/{facility_id}/deactivate")
async def api_admin_deactivate_facility(facility_id: int):
    res = admin_master.set_facility_active(facility_id, False)
    _admin_mutation_refresh()
    return JSONResponse(res)


@app.post("/api/admin/facilities/{facility_id}/activate")
async def api_admin_activate_facility(facility_id: int):
    res = admin_master.set_facility_active(facility_id, True)
    _admin_mutation_refresh()
    return JSONResponse(res)


@app.get("/api/admin/roles")
async def api_admin_list_roles():
    return JSONResponse({"ok": True, "roles": admin_master.list_admin_roles()})


@app.get("/api/admin/roles/{role_id}")
async def api_admin_get_role(role_id: int):
    return JSONResponse(admin_master.get_role_payload(role_id))


@app.post("/api/admin/roles")
async def api_admin_add_role(request: Request):
    data = await request.json() or {}
    res = admin_master.add_role(data)
    if res.get("ok"):
        _admin_mutation_refresh()
    return JSONResponse(res, status_code=200 if res.get("ok") else 422)


@app.put("/api/admin/roles/{role_id}")
async def api_admin_update_role(role_id: int, request: Request):
    data = await request.json() or {}
    res = admin_master.update_role(role_id, data)
    if res.get("ok"):
        _admin_mutation_refresh()
    return JSONResponse(res, status_code=200 if res.get("ok") else 422)


@app.post("/api/admin/roles/{role_id}/deactivate")
async def api_admin_deactivate_role(role_id: int):
    res = admin_master.set_role_active(role_id, False)
    _admin_mutation_refresh()
    return JSONResponse(res)


@app.post("/api/admin/roles/{role_id}/activate")
async def api_admin_activate_role(role_id: int):
    res = admin_master.set_role_active(role_id, True)
    _admin_mutation_refresh()
    return JSONResponse(res)


@app.get("/api/admin/history")
async def api_admin_history():
    return JSONResponse({"history": admin_master.change_history(200)})


@app.post("/api/admin/export")
async def api_admin_export(kind: str = "facilities"):
    rows = admin_master.export_facilities() if kind == "facilities" else admin_master.export_roles()
    return JSONResponse({"kind": kind, "rows": rows})


# ── Manual Validation Mode ───────────────────────────────────────────────────
# Validation-only. Never creates candidates, never modifies batches, never
# generates onboarding Excel, never connects/uploads to eSampark.


from app import validation as validation_engine  # noqa: E402


@app.get("/validation", response_class=HTMLResponse)
async def validation_page(request: Request, set: str = "A"):
    sel = set or "A"
    cases = validation_engine.get_set(sel)
    run = validation_engine.run_all_cases(sel)
    summary = run["summary"]
    verdicts = {v["case_id"]: v for v in run["verdicts"]}
    return templates.TemplateResponse(
        "validation.html",
        {
            "request": request,
            "cases": cases,
            "verdicts": verdicts,
            "summary": summary,
            "selected_set": sel.upper(),
            "set_a_count": len(validation_engine.SET_A),
            "set_b_count": len(validation_engine.SET_B),
            "set_c_count": len(validation_engine.SET_C),
            "config_total": len(cases),
            "logic_cases": len(cases),
            "ocr_capable": sum(1 for c in cases if c.get("ocr_capable")),
            "variant_count": len(validation_engine.generate_variants(sel)),
            "history": database.list_validation_runs(60),
        },
    )


@app.post("/api/validation/run")
async def validation_run(request: Request):
    """Run one configured logic case through the PRODUCTION resolver. Manual &
    read-only: nothing is created in production tables."""
    data = await request.json() or {}
    case_id = data.get("case_id", "")
    set_name = data.get("set_name", "all")
    case = validation_engine.get_case(case_id, set_name)
    if not case:
        return JSONResponse({"error": "Unknown case"}, status_code=404)
    actual = validation_engine.run_logic_case(case)
    verdict = validation_engine.evaluate_case(case, actual)

    database.save_validation_run(
        case_id=case_id,
        test_type="logic",
        status=verdict["status"],
        expected_summary=json.dumps(case.get("expected", {}), default=str),
        actual_summary=json.dumps({
            "entity": actual.get("entity"),
            "operation": actual.get("operation"),
            "cost_code": actual.get("cost_code"),
            "role": actual.get("role"),
            "facility": actual.get("facility"),
            "location": actual.get("location"),
            "salary": actual.get("salary"),
        }, default=str),
        notes=case.get("notes", ""),
    )
    return JSONResponse(verdict)


@app.post("/api/validation/run-all")
async def validation_run_all(request: Request):
    """Run all configured logic cases. Read-only with respect to production."""
    data = await request.json() or {}
    set_name = data.get("set_name", "A")
    run = validation_engine.run_all_cases(set_name)
    for verdict in run["verdicts"]:
        database.save_validation_run(
            case_id=verdict["case_id"],
            test_type="logic",
            status=verdict["status"],
            expected_summary=json.dumps(verdict.get("expected", {}), default=str),
            actual_summary=json.dumps(verdict.get("actual", {}), default=str),
            notes=verdict.get("notes", ""),
        )
    return JSONResponse(run)


@app.get("/api/validation/variants")
async def validation_variants(case_id: str = "", set_name: str = "A"):
    """Return safe variant inputs derived from the configured master fixtures."""
    if case_id:
        gen = validation_engine.generate_variants_for_case(case_id, set_name)
    else:
        gen = validation_engine.generate_variants(set_name)
    return JSONResponse({"variants": gen, "count": len(gen)})


@app.post("/api/validation/run-variant")
async def validation_run_variant(request: Request):
    """Run a safe variant through the production resolver."""
    data = await request.json() or {}
    set_name = data.get("set_name", "A")
    base_case = validation_engine.get_case(data.get("case_id", ""), set_name) if data.get("case_id") else None
    exp = dict((base_case or {}).get("expected", {})) if base_case else {}
    case = {
        "case_id": data.get("case_id") or "VARIANT",
        "title": data.get("title") or "Variant",
        "role_text": data.get("role_text", ""),
        "hub_text": data.get("hub_text", ""),
        "salary_text": data.get("salary_text", ""),
        "tag": "Variant",
        "ocr_capable": False,
        "expect_review": bool((base_case or {}).get("expect_review")),
        "expected": exp,
        "notes": "Generated variant routed through production resolver.",
    }
    actual = validation_engine.run_logic_case(case)
    verdict = validation_engine.evaluate_case(case, actual)
    database.save_validation_run(
        case_id=case["case_id"],
        test_type="variant",
        status=verdict["status"],
        expected_summary=json.dumps(exp, default=str),
        actual_summary=json.dumps(actual, default=str),
        notes=case.get("notes", ""),
    )
    return JSONResponse(verdict)


@app.post("/api/validation/export")
async def validation_export(request: Request):
    """Export validation results to data/validation/manual_validation_YYYY-MM-DD.csv.

    EXCLUDES full Aadhaar, full address, and raw OCR output.
    """
    import csv as _csv

    data = await request.json() or {}
    run = data.get("run") or validation_engine.run_all_cases(data.get("set_name", "A"))
    verdicts = run.get("verdicts", [])

    export_dir = Path(__file__).resolve().parent.parent / "data" / "validation"
    export_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = export_dir / f"manual_validation_{date_str}.csv"

    header = ["case_id", "scenario", "expected", "actual", "status", "review_expected", "notes", "validated_at"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = _csv.writer(fh)
        writer.writerow(header)
        for v in verdicts:
            scn = v.get("scenario", {})
            scenario = f"{scn.get('role_text','')} | {scn.get('hub_text','')} | {scn.get('salary_text','')}"
            writer.writerow([
                v.get("case_id", ""),
                scenario,
                json.dumps(v.get("expected", {}), default=str),
                json.dumps(v.get("actual", {}), default=str),
                v.get("status", ""),
                "yes" if v.get("expect_review") else "no",
                v.get("notes", ""),
                now,
            ])
    return JSONResponse({"ok": True, "path": str(path), "rows": len(verdicts)})


@app.get("/api/validation/history")
async def validation_history():
    return JSONResponse({"runs": database.list_validation_runs(200)})


# ── Smart Upload Page ────────────────────────────────────────────────────────


@app.get("/smart-upload", response_class=HTMLResponse)
async def smart_upload_page(request: Request):
    return templates.TemplateResponse(
        "smart_upload.html",
        {"request": request, "rules": rules.get_rules_for_frontend()},
    )


extraction_sessions: dict[str, dict] = {}


@app.post("/api/smart-extract")
async def smart_extract(files: list[UploadFile]):
    """Run document intelligence on one candidate's uploads.

    Returns a review-ready extraction payload (fields + source + confidence),
    masks Aadhaar in the visible payload, and never automatically finalizes.
    """
    drafts = []
    session_files = []
    for up in files:
        raw = await up.read()
        if not raw:
            continue
        ext = (up.filename or "").rsplit(".", 1)[-1].lower()
        entries = {
            "filename": up.filename or "",
            "file_type": "pdf" if ext == "pdf" else "image",
            "file_size": len(raw),
            "data": raw,
        }
        drafts.append(entries)
        session_files.append({
            "filename": up.filename or "",
            "file_type": "pdf" if ext == "pdf" else "image",
            "file_size": len(raw),
        })

    if not drafts:
        return JSONResponse({"error": "No readable files uploaded", "fallback": True}, status_code=400)

    try:
        result = ocr.run_extraction(drafts)
    except Exception:  # noqa: BLE001 - fallback for OCR failures
        result = ocr._empty_result(
            session_files,
            [{"filename": f.get("filename"), "extracted": False,
              "reason": "Could not confidently read this document."} for f in session_files],
            fallback=True,
        )

    sid = secrets.token_hex(8)
    extraction_sessions[sid] = {"source_files": session_files, "created": datetime.now().isoformat()}

    payload = result
    payload["session_id"] = sid
    return JSONResponse(payload)


@app.post("/api/manual-ocr-extract")
async def manual_ocr_extract(file: UploadFile):
    """Run the shared OCR pipeline for a single Aadhaar document uploaded via
    Manual Entry. Returns the same structured payload as /api/smart-extract.
    """
    raw = await file.read()
    if not raw:
        return JSONResponse({"error": "Empty file", "fallback": True}, status_code=400)

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    drafts = [{
        "filename": file.filename or "",
        "file_type": "pdf" if ext == "pdf" else "image",
        "file_size": len(raw),
        "data": raw,
    }]
    session_files = [{
        "filename": file.filename or "",
        "file_type": "pdf" if ext == "pdf" else "image",
        "file_size": len(raw),
    }]

    try:
        result = ocr.run_extraction(drafts)
    except Exception:  # noqa: BLE001
        result = ocr._empty_result(
            session_files,
            [{"filename": file.filename, "extracted": False,
              "reason": "Could not confidently read this document."}],
            fallback=True,
        )

    return JSONResponse(result)


@app.post("/api/upload-document")
async def upload_document(request: Request):
    data = await request.json()
    doc_id = database.insert_document({
        "candidate_id": data.get("candidate_id"),
        "filename": data.get("filename", "unknown"),
        "file_type": data.get("file_type", ""),
        "file_size": data.get("file_size", 0),
        "document_type": data.get("document_type", "Other"),
        "extraction_status": data.get("extraction_status", "pending"),
    })
    return JSONResponse({"document_id": doc_id})


@app.get("/api/documents")
async def get_documents(candidate_id: Optional[int] = None):
    if candidate_id:
        return JSONResponse(database.get_documents_for_candidate(candidate_id))
    return JSONResponse([])


# ── Hub Search API ───────────────────────────────────────────────────────────


@app.get("/api/search-hubs")
async def search_hubs(cost_code: str = "", query: str = ""):
    hubs = rules.get_hubs_for_cost_code(cost_code)
    if query:
        results = rules.fuzzy_find_hub(query, hubs, top_n=5)
    else:
        results = hubs[:20]
    return JSONResponse({
        "hubs": results,
        "locations": {h: rules.get_location_for_facility(h) for h in results},
        "total": len(hubs),
    })


@app.get("/api/location")
async def location_for_facility(facility: str = ""):
    return JSONResponse({"facility": facility, "location": rules.get_location_for_facility(facility)})


# ── API: Dashboard Counts ────────────────────────────────────────────────────


@app.get("/api/counts")
async def api_counts():
    return JSONResponse(_count_candidates())


# ── API: Self Onboarding Generation ─────────────────────────────────────────


@app.get("/api/generate-preview")
async def generate_preview(batch_id: int):
    """Return a preview summary for the Generate Onboarding Excel dialog.

    Only Ready candidates are eligible; Draft / Needs Attention / invalid
    candidates are shown as excluded.
    """
    template_ok = generation.template_is_configured()
    candidates = database.get_batch_candidates(batch_id)
    ready = [c for c in candidates if (c.get("status") or "").lower() == "ready"]
    others = [c for c in candidates if (c.get("status") or "").lower() != "ready"]

    vres = generation.validate_candidates(ready)
    valid_ids = {r["candidate_id"] for r in vres["rows"]}
    invalid_ready = [c for c in ready if c["candidate_id"] not in valid_ids]

    now = datetime.now()
    return JSONResponse({
        "template_configured": template_ok,
        "batch_id": batch_id,
        "ready_count": len(ready),
        "included_count": len(vres["rows"]),
        "excluded_count": len(others) + len(invalid_ready),
        "excluded": {
            c["candidate_id"]: generation.validate_candidates([c])["errors"].get(c["candidate_id"], [])
            for c in others
        } | {c: vres["errors"][c] for c in vres["errors"]},
        "output_folder": str(generation.get_output_base_dir()),
        "filename_preview": generation.build_filename(batch_id, now),
        "message": ("" if template_ok else "Self Onboarding Template is not configured.") +
                   ("" if vres["rows"] else " No Ready candidates passed validation."),
    })


@app.post("/api/generate-excel")
async def generate_excel(request: Request):
    data = await request.json() or {}
    batch_id = int(data.get("batch_id", 1))
    only_ready = bool(data.get("only_ready", True))
    result = generation.generate_batch_excel(batch_id, only_ready=only_ready)
    status_code = 200 if result.get("success") else 422
    return JSONResponse(result, status_code=status_code)


@app.get("/api/settings-output")
async def settings_output():
    return JSONResponse(generation.get_output_config())


@app.post("/api/settings-output")
async def settings_output_set(request: Request):
    data = await request.json() or {}
    base = generation.set_output_base_dir(data.get("output_base_dir", ""))
    return JSONResponse({"output_base_dir": base})


@app.get("/api/generated")
async def generated_files(batch_id: Optional[int] = None):
    files = database.list_generated_files(batch_id)
    # keep sensitive fields out of API responses for the mirror
    mirror = database.get_daily_master_mirror()
    return JSONResponse({"files": files, "daily_master": mirror})


# ── API: eSampark Portal (Stage 4) ──────────────────────────────────────────


@app.get("/portal", response_class=HTMLResponse)
async def portal_status_page(request: Request, batch_id: Optional[int] = None):
    """Portal processing screen: batch, generated file, upload status, per-candidate
    outcomes (no full Aadhaar shown)."""
    files = database.list_generated_files(batch_id if batch_id else None)
    uploads = database.list_portal_uploads(batch_id if batch_id else None)
    audit = database.get_portal_audit(50)
    status = portal_service.portal_status()

    # Per-batch summary of candidate outcomes for the requested batch (default all).
    batch_id = batch_id or (files[0]["batch_id"] if files else 1)
    candidates = database.get_batch_candidates(batch_id)
    summary = {
        "total": len(candidates),
        "portal_uploaded": sum(1 for c in candidates
                               if (c.get("portal_status") or "") in ("Success", "Portal Uploaded", "Link Generated")),
        "failed": sum(1 for c in candidates if (c.get("portal_status") or "") == "Failed"),
        "pending": sum(1 for c in candidates
                       if not (c.get("portal_status") or "") or (c.get("portal_status") or "") == "Processing"),
        "failed_rows": [
            {"candidate_id": c["candidate_id"], "name": c.get("name"),
             "mobile": c.get("mobile"), "creation_remarks": c.get("portal_remarks")}
            for c in candidates if (c.get("portal_status") or "") == "Failed"
        ],
    }

    return templates.TemplateResponse(
        "portal_status.html",
        {
            "request": request,
            "portal_status": status,
            "files": files,
            "uploads": uploads,
            "audit": audit,
            "batch_id": batch_id,
            "summary": summary,
            "credentials_configured": portal_service.portal_status()["configured"],
        },
    )


@app.get("/api/portal/status")
async def api_portal_status():
    return JSONResponse(portal_service.portal_status())


@app.post("/api/portal/connect")
async def api_portal_connect():
    return JSONResponse(portal_service.connect())


@app.post("/api/portal/reconnect")
async def api_portal_reconnect():
    return JSONResponse(portal_service.reconnect())


@app.get("/api/portal/diagnostic")
async def api_portal_diagnostic():
    return JSONResponse(portal_service.diagnostic())


@app.get("/api/portal/uploads")
async def api_portal_uploads(batch_id: Optional[int] = None):
    return JSONResponse({"uploads": database.list_portal_uploads(batch_id)})


@app.get("/api/portal/audit")
async def api_portal_audit():
    return JSONResponse({"audit": database.get_portal_audit(100)})


@app.get("/api/portal/upload-preview")
async def api_portal_upload_preview(file_id: int):
    """Pre-upload confirmation payload for a generated file."""
    resolved = portal_service.resolve_generated_file(file_id)
    if not resolved["ok"]:
        return JSONResponse({"ok": False, "error": resolved["reason"]}, status_code=422)
    gf = resolved["file"]
    existing = portal_service.existing_submission(file_id)
    gf_updated = database.get_generated_file(file_id)
    return JSONResponse({
        "ok": True,
        "file_id": file_id,
        "batch_id": gf["batch_id"],
        "filename": gf["filename"],
        "candidate_count": gf["candidate_count"],
        "generated_at": gf["generated_at"],
        "portal_status": portal_service.portal_status(),
        "already_submitted": existing is not None,
        "existing": existing,
        "portal_fields": {
            "portal_status": gf_updated.get("portal_status"),
            "portal_uploaded_at": gf_updated.get("portal_uploaded_at"),
            "portal_reference": gf_updated.get("portal_reference"),
        },
    })


@app.post("/api/portal/upload")
async def api_portal_upload(request: Request):
    """Upload a generated file to eSampark.

    Requires explicit ``confirm`` unless the file has never been submitted.
    """
    data = await request.json() or {}
    file_id = int(data.get("file_id") or 0)
    confirm = bool(data.get("confirm", False))
    if file_id <= 0:
        return JSONResponse({"ok": False, "error": "file_id is required."}, status_code=422)
    result = portal_service.upload_generated_file(file_id, confirm=confirm)
    if "ok" in result and not result["ok"]:
        status = 422 if result.get("duplicate") else 400
        return JSONResponse(result, status_code=status)
    return JSONResponse(result)


@app.get("/api/portal/upload/{upload_id}/check")
async def api_portal_upload_check(upload_id: int):
    result = portal_service.check_upload_status(upload_id)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@app.post("/api/portal/import-result")
async def api_portal_import_result(file: UploadFile, generated_file_id: str = ""):
    """Manual fallback: import a portal-generated result/error workbook using the
    same parser as the automatic download path."""
    raw = await file.read()
    if not raw:
        return JSONResponse({"ok": False, "error": "Empty result file."}, status_code=400)
    try:
        gid = int(generated_file_id) if generated_file_id else None
    except ValueError:
        return JSONResponse({"ok": False, "error": "Invalid generated_file_id."}, status_code=422)
    result = portal_service.apply_result_file(raw, gid)
    return JSONResponse(result, status_code=200 if result.get("ok") else 422)


# ── Controlled single-candidate LIVE upload (live-test mode) ─────────────────


@app.get("/live-upload", response_class=HTMLResponse)
async def live_upload_page(request: Request, file_id: Optional[int] = None):
    """Controlled single-candidate live eSampark upload screen.

    Nothing is submitted on page load. Requires BOTH safety flags + an explicit
    manual checklist + a two-step confirmation before the real Upload click.
    """
    files = database.list_generated_files(limit=50)
    safety = live_upload.safety_status()
    current = live_upload.report(file_id) if file_id else {"found": False}
    return templates.TemplateResponse(
        "live_upload.html",
        {
            "request": request,
            "files": files,
            "safety": safety,
            "selected_file_id": file_id,
            "current": current,
            "diagnostic": portal_service.diagnostic(),
        },
    )


@app.get("/api/live-upload/prepare")
async def api_live_upload_prepare(file_id: int):
    """Step 1a: validate + return the manual review checklist payload."""
    try:
        return JSONResponse(live_upload.prepare(file_id))
    except live_upload.LiveUploadError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)


@app.get("/api/live-upload/validate")
async def api_live_upload_validate(file_id: int):
    """Pure validation of a generated file (no state change, read-only)."""
    return JSONResponse(live_upload.validate_generated_file(file_id))


@app.post("/api/live-upload/confirm")
async def api_live_upload_confirm(request: Request):
    """Step 1b: record completion of the manual review checklist."""
    data = await request.json() or {}
    res = live_upload.confirm(int(data.get("file_id") or 0),
                              data.get("checked", []) or [])
    return JSONResponse(res, status_code=200 if res.get("ok") else 422)


@app.post("/api/live-upload/submit")
async def api_live_upload_submit(request: Request):
    """Step 2 (final): real single-candidate upload after explicit confirmation."""
    data = await request.json() or {}
    res = live_upload.submit(int(data.get("file_id") or 0),
                             action=data.get("action") or live_upload.FINAL_ACTION)
    status = 200 if res.get("ok") else 400
    return JSONResponse(res, status_code=status)


@app.get("/api/live-upload/report")
async def api_live_upload_report(file_id: int = 0):
    return JSONResponse(live_upload.report(file_id))


@app.get("/api/live-upload/list")
async def api_live_upload_list():
    return JSONResponse(live_upload.list_reports())


# ── OCR Debug View (Development Only) ────────────────────────────────────────


@app.get("/debug/ocr")
async def debug_ocr_page(request: Request):
    """Development-only OCR debug view. Binds to localhost only.

    Enable with OCR_DEBUG_VIEW=true environment variable.
    Shows annotated bounding boxes for extracted fields.
    """
    from app.debug_view import DEBUG_VIEW_ENABLED
    if not DEBUG_VIEW_ENABLED:
        return JSONResponse(
            {"error": "Debug view is disabled. Set OCR_DEBUG_VIEW=true to enable."},
            status_code=403,
        )
    return templates.TemplateResponse(
        "debug_ocr.html",
        {"request": request},
    )


@app.post("/api/debug/ocr-annotate")
async def debug_ocr_annotate(file: UploadFile):
    """Upload an image and get back annotated debug overlay (base64).

    Development-only. Returns annotated image with bounding boxes.
    """
    from app.debug_view import DEBUG_VIEW_ENABLED, render_debug_overlay_base64
    if not DEBUG_VIEW_ENABLED:
        return JSONResponse(
            {"error": "Debug view is disabled."}, status_code=403,
        )

    raw = await file.read()
    if not raw:
        return JSONResponse({"error": "Empty file"}, status_code=400)

    # Run extraction to get field results
    try:
        from app.ocr import extract_ocr_lines
        from app.evidence import evidence_score_extraction, field_result_to_safe_dict
        from app.master_data import get_facility_names, get_hubs_for_cost_code, get_roles_for_cost_code

        ocr_lines = extract_ocr_lines(raw, file.filename or "")
        all_hubs = get_facility_names()
        evidence_results = evidence_score_extraction(
            ocr_lines=ocr_lines,
            flat_lines=[],
            hub_text=None,
            role_text=None,
            cost_code="",
            all_hubs=all_hubs,
            hubs_for_code=[],
            allowed_roles=[],
            role_aliases={},
        )
        overlay = render_debug_overlay_base64(raw, evidence_results)
        evidence_dict = {k: field_result_to_safe_dict(v) for k, v in evidence_results.items()}
        return JSONResponse({
            "overlay": overlay,
            "evidence": evidence_dict,
            "ocr_line_count": len(ocr_lines),
        })
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/evidence-score")
async def api_evidence_score(request: Request):
    """Return evidence scoring for a set of OCR lines (for existing extractions).

    Accepts JSON with ocr_lines (list of text strings) and returns
    multi-candidate scoring results.
    """
    data = await request.json()
    from app.ocr_models import OCRLine
    from app.evidence import evidence_score_extraction, field_result_to_safe_dict
    from app.master_data import get_facility_names, get_hubs_for_cost_code, get_roles_for_cost_code

    raw_lines = data.get("ocr_lines", [])
    ocr_lines = [
        OCRLine(text=t, order=i) for i, t in enumerate(raw_lines)
    ] if raw_lines and isinstance(raw_lines[0], str) else raw_lines

    hub_text = data.get("hub_text")
    role_text = data.get("role_text")
    cost_code = data.get("cost_code", "")

    if not cost_code:
        from app.rules import resolve_smart_onboarding
        resolved = resolve_smart_onboarding(role_text, hub_text)
        cost_code = resolved.get("cost_code", "")

    all_hubs = get_facility_names()
    hubs_for_cc = get_hubs_for_cost_code(cost_code)
    allowed_roles = get_roles_for_cost_code(cost_code)

    evidence_results = evidence_score_extraction(
        ocr_lines=ocr_lines,
        flat_lines=[],
        hub_text=hub_text,
        role_text=role_text,
        cost_code=cost_code,
        all_hubs=all_hubs,
        hubs_for_code=hubs_for_cc,
        allowed_roles=allowed_roles,
        role_aliases={},
    )

    return JSONResponse({
        k: field_result_to_safe_dict(v) for k, v in evidence_results.items()
    })


# ── Startup: record master load history ─────────────────────────────────────


@app.on_event("startup")
async def on_startup():
    from app import master_data as md
    status = md.get_master_status()
    for kind in ("designation", "facility"):
        info = status.get(kind, {})
        if info.get("configured"):
            database.record_master_load(
                master_type=kind,
                filename=info.get("filename", ""),
                row_count=info.get("row_count", 0),
                status="OK",
            )
