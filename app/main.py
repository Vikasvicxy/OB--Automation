import json
import os
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
from app import backup_service
from app import health as health_service
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
    pair_count = len(database.list_generation_pairs(limit=100))
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "candidates": counts,
            "stats": stats,
            "pair_count": pair_count,
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
            "recruiter_name": generation.get_default_recruiter_name(),
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


def _backend_fields(data: dict) -> dict:
    """Extract the backend-only candidate fields, normalized conservatively.

    Empty results stay empty (the review screen resolves them); nothing is
    invented here. Used by save-draft / confirm / approve payloads. The Date of
    Joining DEFAULTS to the current local date when not provided.
    """
    doj_raw = str(data.get("doj", "") or "").strip()
    if not doj_raw:
        doj_raw = _get_now()[0]
    doj, _ = rules.normalize_doj(doj_raw)
    gender, _ = rules.normalize_gender(data.get("gender", ""))
    pin, _ = rules.normalize_pin_code(data.get("pin_code", ""))
    father, _ = rules.normalize_father_name(data.get("father_name", ""))
    uan, _ = rules.normalize_uan(data.get("uan_no", ""))
    return {
        "doj": doj,
        "recruiter_name": str(data.get("recruiter_name", "") or "").strip(),
        "gender": gender,
        "pin_code": pin,
        "father_name": father,
        "uan_no": uan,
    }


def _salary_value(data: dict) -> object:
    """Canonical candidate salary value from any supported payload key.

    The canonical field is ``salary``. Older clients also send
    ``salary_normalized`` / ``salary_display``; all are normalized to the same
    integer before persistence/validation. Populated siblings always win over
    an explicitly empty ``salary``.
    """
    return rules.salary_value(data)


def _facility_for_candidate(data: dict) -> dict:
    """Resolve a candidate payload to the authoritative facility row.

    Primary identity is the facility (display) text the user typed; the selected
    row's location/facility_ref (if provided) refine the pick and are verified
    against the master. Returns {facility_name, location, facility_ref}. A stale
    client-supplied location can never be stored: the master row always wins.
    """
    facility = str(data.get("facility", data.get("facility_name", "")) or "").strip()
    loc_hint = str(data.get("location", data.get("location_code", "")) or "").strip()
    ref_hint = str(data.get("facility_ref", "") or "").strip()
    resolved = master_data.resolve_facility_selection(facility, loc_hint, ref_hint)
    return {
        "facility_name": resolved["facility_name"] or facility,
        "location": resolved["location"],
        "facility_ref": resolved["facility_ref"],
    }


@app.post("/api/save-draft")
async def save_draft(request: Request):
    data = await request.json()
    now_date, now_time = _get_now()

    edit_id = data.get("edit_id")
    if edit_id:
        fc = _facility_for_candidate(data)
        updated = database.update_candidate(edit_id, {
            "name": data.get("name", ""),
            "mobile": data.get("mobile", ""),
            "entity": data.get("entity", ""),
            "cost_code": data.get("cost_code", ""),
            "operation": data.get("operation", ""),
            "team": data.get("team", ""),
            "designation": data.get("role", data.get("designation", "")),
            "facility_type": data.get("facility_type", ""),
            "facility_name": fc["facility_name"],
            "facility_ref": fc["facility_ref"],
            "location_code": fc["location"],
            "salary": rules.normalize_salary(_salary_value(data))[0],
            "salary_display": data.get("salary_display", ""),
            "aadhaar_number": rules.normalize_aadhaar(data.get("aadhaar_number", "")),
            "dob": data.get("dob", ""),
            "address": data.get("address", ""),
            "status": "draft",
            **_backend_fields(data),
        })
        database.record_candidate_event(edit_id, "Manual Review",
                                        "Draft updated and saved.")
        return JSONResponse({"id": edit_id, "status": "draft"})

    fc = _facility_for_candidate(data)
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
        "facility_name": fc["facility_name"],
        "facility_ref": fc["facility_ref"],
        "location_code": fc["location"],
        "salary": rules.normalize_salary(_salary_value(data))[0],
        "salary_display": data.get("salary_display", ""),
        "aadhaar_number": rules.normalize_aadhaar(data.get("aadhaar_number", "")),
        "dob": data.get("dob", ""),
        "address": data.get("address", ""),
        "status": "draft",
        **_backend_fields(data),
    })
    draft_files = data.get("source_files")
    if not draft_files and data.get("aadhaar_filename"):
        draft_files = [data.get("aadhaar_filename")]
    _attach_documents(cid, draft_files or [])
    database.record_candidate_event(cid, "Manual Review",
                                    "Draft saved for manual review.")
    return JSONResponse({"id": cid, "status": "draft"})


@app.post("/api/confirm-candidate")
async def confirm_candidate(request: Request):
    data = await request.json()
    errors = rules.validate_candidate(data)
    if errors:
        return JSONResponse({"errors": errors}, status_code=422)

    mobile_normalized, _ = rules.normalize_mobile(data.get("mobile", ""))
    salary_normalized, _ = rules.normalize_salary(_salary_value(data))
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
    fc = master_data.resolve_facility_selection(
        facility,
        str(data.get("location", data.get("location_code", "")) or ""),
        str(data.get("facility_ref", "") or ""),
    )
    location_code = fc["location"]
    facility_ref = fc["facility_ref"]

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
            "facility_name": fc["facility_name"] or facility,
            "facility_ref": facility_ref,
            "location_code": location_code,
            "salary": salary_normalized,
            "salary_display": data.get("salary_display", ""),
            "aadhaar_number": rules.normalize_aadhaar(data.get("aadhaar_number", "")),
            "dob": data.get("dob", ""),
            "address": data.get("address", ""),
            "status": "ready",
            **_backend_fields(data),
        })
        if updated:
            _attach_documents(edit_id, data.get("source_files") or [])
        database.record_candidate_event(edit_id, "Approved",
                                        "Candidate confirmed and marked ready.")
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
        "facility_name": fc["facility_name"] or facility,
        "facility_ref": facility_ref,
        "location_code": location_code,
        "salary": salary_normalized,
        "salary_display": data.get("salary_display", ""),
        "aadhaar_number": rules.normalize_aadhaar(data.get("aadhaar_number", "")),
        "dob": data.get("dob", ""),
        "address": data.get("address", ""),
        "migrant": data.get("migrant", "No"),
        "status": "ready",
        **_backend_fields(data),
    })
    source_files = data.get("source_files") or (data.get("aadhaar_filename") and [data.get("aadhaar_filename")]) or []
    _attach_documents(cid, source_files)
    database.record_candidate_event(cid, "Approved",
                                    "Candidate confirmed and marked ready.")
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
    blocking_count, backend_problems = _backend_blocking_info(batch)
    return templates.TemplateResponse(
        "batch_review.html",
        {
            "request": request,
            "candidates": batch,
            "counts": counts,
            "batch_id": batch_id,
            "template_configured": generation.excel_template_is_configured(),
            "backend_template_configured": generation.excel_template_is_configured(),
            "recruiter_name": generation.get_default_recruiter_name(),
            "output_base_dir": str(generation.get_output_base_dir()),
            "generated_files": database.list_generated_files(batch_id),
            "backend_blocking": blocking_count,
            "backend_problems": backend_problems,
        },
    )


def _backend_blocking_info(batch) -> tuple[int, dict]:
    """Return (count, per-candidate problems) for Mail-sheet blocking.

    ``problems`` maps candidate_id -> list of missing-field labels.
    """
    try:
        validated = generation.validate_candidates(batch)
        enriched = validated.get("rows", [])
        by_id = {c["candidate_id"]: c for c in batch}
        result = generation.build_mail_rows(enriched, by_id)
        raw = result.get("problems", {})
        problems = {cid: msgs for cid, msgs in raw.items() if msgs}
        return len(problems), problems
    except Exception:
        return 0, {}


def _backend_blocking_count(batch) -> int:
    """Number of validated candidates that would block the backend workbook."""
    return _backend_blocking_info(batch)[0]


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
        {"request": request, "candidates": all_candidates, "search": search,
         "saved_views": database.list_saved_views("candidates"),
         "batches": database.list_batches()},
    )


@app.get("/api/candidates")
async def api_candidates(search: str = ""):
    return JSONResponse(database.search_candidates(search))


@app.get("/api/candidates/export-selected")
async def api_export_selected_candidates(ids: str = ""):
    """Export selected candidates' SAFE fields as CSV (no Aadhaar/address).

    Registered BEFORE the dynamic /api/candidates/{candidate_id} route so the
    literal "export-selected" path is never parsed as a candidate id.
    """
    ids_list = [int(i) for i in ids.split(",") if i.strip().lstrip("-").isdigit()]
    rows = database.get_candidates_safe(ids_list)
    fields = ["candidate_id", "name", "mobile", "entity", "operation", "cost_code",
              "designation", "facility_name", "location_code", "salary",
              "salary_display", "status", "batch_id", "portal_status"]
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    from fastapi.responses import Response
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=selected_candidates.csv"},
    )


# ── Candidate Detail (read-first page) ───────────────────────────────────────
# The ONLY place where the full Aadhaar number is shown (explicit detail page).
# The API and other surfaces (search/list/dashboard/data-quality/timeline) never
# expose the full Aadhaar.


def _candidate_context(candidate: dict) -> dict:
    """Safe metadata used to render the detail page (full Aadhaar is rendered
    separately and only when the detail template is shown)."""
    return candidate


# ── Phase 6: Review attention summary ────────────────────────────────────────
# Real evidence only (stored candidate fields + resolver evidence snapshot).


def _review_summary(candidate: dict) -> dict:
    """Compact, evidence-backed review status for the Candidate Review workspace."""
    reasons: list[str] = []
    status = (candidate.get("status") or "").lower()
    portal = (candidate.get("portal_status") or "").lower()
    evidence = candidate.get("evidence_snapshot") or {}
    try:
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
    except Exception:  # noqa: BLE001
        evidence = {}

    level = "ready"
    if portal in ("failed",):
        level = "portal_failed"
    elif status in ("generated", "portal_pending", "portal_success"):
        level = "ready"
    elif status in ("needs_attention", "needs_review"):
        level = "needs_review"

    if not (candidate.get("designation") or "").strip():
        reasons.append("Missing role")
    if not (candidate.get("facility_name") or "").strip():
        reasons.append("Ambiguous facility")
    else:
        cost_code = candidate.get("cost_code") or ""
        if cost_code and candidate["facility_name"] not in rules.get_hubs_for_cost_code(cost_code):
            reasons.append("Facility incompatible with cost code")

    name_conflict = False
    for field_name in ("name",):
        info = evidence.get(field_name) if isinstance(evidence, dict) else None
        if isinstance(info, dict) and (info.get("confidence") or "") in ("Conflict", "Low"):
            name_conflict = True
    facility_conflict = False
    finfo = evidence.get("facility") if isinstance(evidence, dict) else None
    if isinstance(finfo, dict) and (finfo.get("confidence") or "") in ("Conflict", "Low"):
        facility_conflict = True

    if name_conflict:
        reasons.append("Low confidence name")
    if facility_conflict:
        reasons.append("Ambiguous facility")
    if not (candidate.get("mobile") or "").strip():
        reasons.append("Missing mobile")

    dup = database.find_duplicate_mobile(candidate.get("mobile", ""),
                                         exclude_id=candidate.get("candidate_id"))
    if dup:
        reasons.append("Duplicate mobile")

    salary = candidate.get("salary")
    try:
        if salary and int(salary) <= 0:
            reasons.append("Invalid salary")
    except (TypeError, ValueError):
        if salary not in (None, "", 0):
            reasons.append("Invalid salary")

    if level == "ready" and reasons:
        level = "needs_review"
    return {
        "level": level,
        "label": {
            "ready": "Ready", "needs_review": "Needs Review",
            "conflict": "Conflict", "portal_failed": "Portal Failed",
        }[level if level in ("ready", "needs_review", "conflict", "portal_failed") else "needs_review"],
        "reasons": reasons,
    }


def _quick_actions(candidate: dict) -> dict:
    """Which quick actions are valid for the CURRENT status and backend.
    Never offers portal submit / restore / delete."""
    status = (candidate.get("status") or "").lower()
    in_flow = status in ("generated", "portal_pending", "portal_success", "portal_failed")
    batch_id = candidate.get("batch_id")
    return {
        "edit": True,
        "approve": not in_flow,
        "mark_review": not in_flow,
        "add_to_batch": not (batch_id is not None and batch_id) or True,
        "view_generated": bool(batch_id),
        "prepare_live_upload": status in ("ready", "generated"),
    }


@app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
async def candidate_detail_page(request: Request, candidate_id: int):
    candidate = database.get_candidate(candidate_id)
    if candidate is None:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "message": "Candidate not found."},
            status_code=404,
        )
    events = database.list_candidate_events(candidate_id)
    edit_history = database.list_edit_history(candidate_id)
    documents = database.get_documents_for_candidate(candidate_id)
    generated_files = database.get_generated_files_for_candidate(candidate_id)
    portal = database.get_portal_history_for_candidate(candidate_id)
    neighbors = database.get_neighbor_candidates(candidate_id)

    # Evidence / Why (safe reason strings only from the resolver snapshots).
    evidence = None
    candidate_evidence = candidate.get("evidence_snapshot") or {}
    try:
        if isinstance(candidate_evidence, str):
            candidate_evidence = json.loads(candidate_evidence)
        if candidate_evidence:
            evidence = candidate_evidence
    except Exception:  # noqa: BLE001
        evidence = None

    review = _review_summary(dict(candidate))
    qa = _quick_actions(dict(candidate))

    return templates.TemplateResponse(
        "candidate_detail.html",
        {
            "request": request,
            "candidate": candidate,
            "events": events,
            "edit_history": edit_history,
            "documents": documents,
            "generated_files": generated_files,
            "portal": portal,
            "evidence": evidence,
            "review_summary": review,
            "quick_actions": qa,
            "batches": database.list_batches(),
            "prev_id": neighbors["prev_id"],
            "next_id": neighbors["next_id"],
            "nav_active": "candidates",
        },
    )


@app.get("/api/candidates/{candidate_id}")
async def api_candidate_detail(candidate_id: int):
    """Detail API. Returns full Aadhaar ONLY for this explicit detail endpoint;
    the page uses it to render the detail view. Other APIs stay masked."""
    candidate = database.get_candidate(candidate_id)
    if candidate is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    candidate["aadhaar_masked"] = database.mask_aadhaar(candidate.get("aadhaar_number", ""))
    return JSONResponse(candidate)


@app.get("/api/generated-file/{file_id}/result")
async def api_generated_file_result(file_id: int):
    """Serve a generated file's portal result workbook for preview/open.

    Only serves files that already exist on disk; never performs uploads.
    """
    gf = database.get_generated_file(file_id)
    if gf is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    rel = gf.get("portal_result_path") or gf.get("file_path") or ""
    if not rel:
        return JSONResponse({"error": "No result file"}, status_code=404)
    base = generation.get_output_base_dir()
    candidate_path = Path(rel)
    full = candidate_path if candidate_path.is_absolute() else base / rel
    if not full.exists():
        return JSONResponse({"error": "Result file not found on disk"}, status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(str(full), filename=Path(full).name)


@app.get("/api/generated-file/{file_id}/download")
async def api_generated_file_download(file_id: int):
    """Download a generated workbook (the single Onboarding file)."""
    gf = database.get_generated_file(file_id)
    if gf is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    rel = gf.get("file_path") or ""
    if not rel:
        return JSONResponse({"error": "No file path"}, status_code=404)
    base = generation.get_output_base_dir()
    candidate_path = Path(rel)
    full = candidate_path if candidate_path.is_absolute() else base / rel
    if not full.exists():
        return JSONResponse({"error": "File not found on disk"}, status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(str(full), filename=Path(full).name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── Batch Detail (read-first page) ───────────────────────────────────────────


@app.get("/batches/{batch_id}", response_class=HTMLResponse)
async def batch_detail_page(request: Request, batch_id: int):
    batch = database.get_batch(batch_id)
    if batch is None:
        return templates.TemplateResponse(
            "404.html",
            {"request": request, "message": "Batch not found."},
            status_code=404,
        )
    summary = database.get_batch_summary(batch_id)
    candidates = database.get_batch_candidates(batch_id)
    generated_files = database.get_generated_files_with_portal(batch_id)
    events = database.list_batch_events(batch_id)
    portal = database.list_portal_uploads(batch_id)

    # "Excluded / Needs Review" = candidates flagged needs review/attention.
    review = [c for c in candidates
              if (c.get("status") or "").lower() in
              ("needs_attention", "needs_review")]
    ready_rows = [c for c in candidates
                  if (c.get("status") or "").lower() == "ready"]

    return templates.TemplateResponse(
        "batch_detail.html",
        {
            "request": request,
            "batch_id": batch_id,
            "batch": batch,
            "summary": summary,
            "candidates": candidates,
            "ready_candidates": ready_rows,
            "review_candidates": review,
            "generated_files": generated_files,
            "events": events,
            "portal": portal,
            "nav_active": "batches",
        },
    )


# ── Candidate Pipeline / Kanban (read-only) ──────────────────────────────────
# Uses REAL candidate status + portal status. Never drags cards into
# portal-derived states; this phase is read-only for safety.


PIPELINE_COLUMNS = [
    "draft", "needs_review", "ready", "generated",
    "portal_pending", "portal_success", "portal_failed",
]


def _pipeline_bucket(candidate: dict) -> str:
    """Map a candidate into ONE pipeline column using status + portal status.

    Portal success/failed are derived from the system portal_status and are
    NOT user-movable. Never silently discards a candidate: unmappable statuses
    fall through to an explicit 'Other/Review' column so nothing is lost.
    """
    status = (candidate.get("status") or "").lower().strip()
    portal = (candidate.get("portal_status") or "").lower().strip()

    if portal in ("success",):
        return "portal_success"
    if portal == "failed":
        return "portal_failed"
    if portal in ("processing", "portal uploaded", "link generated", "pending"):
        return "portal_pending"

    if status in ("needs_attention", "needs_review"):
        return "needs_review"
    if status == "ready":
        return "ready"
    if status == "generated":
        return "generated"
    if status == "draft":
        return "draft"
    # Unmappable -> explicit other bucket (never discarded).
    return "other"


def _pipeline_safe(candidate: dict) -> dict:
    """Safe compact card payload (no Aadhaar/address)."""
    return {
        "candidate_id": candidate.get("candidate_id"),
        "name": candidate.get("name") or "",
        "mobile": candidate.get("mobile") or "",
        "role": candidate.get("designation") or "",
        "facility": candidate.get("facility_name") or "",
        "batch_id": candidate.get("batch_id"),
        "status": candidate.get("status") or "",
        "cost_code": candidate.get("cost_code") or "",
        "attention": (candidate.get("status") or "").lower() in
                     ("needs_attention", "needs_review"),
    }


def _build_pipeline(candidates: list[dict]) -> tuple[dict, list[str]]:
    """Return dict of column -> list[safe cards] and the ordered column names.

    Includes an 'other' column only when there are unmappable candidates so we
    never silently drop anyone.
    """
    columns = {c: [] for c in PIPELINE_COLUMNS}
    columns["other"] = []
    for c in candidates:
        bucket = _pipeline_bucket(c)
        columns.setdefault(bucket, []).append(_pipeline_safe(c))
    order = [c for c in PIPELINE_COLUMNS if columns[c]]
    if columns.get("other"):
        order.append("other")
    return columns, order


def filter_pipeline_candidates(
    candidates: list[dict],
    search: str = "",
    today: bool = False,
    entity: str = "",
    operation: str = "",
    cost_code: str = "",
    role: str = "",
    facility: str = "",
    batch_id: Optional[str] = "",
    status: str = "",
) -> list[dict]:
    """Apply pipeline filters (name/mobile search + entity/operation/etc.).

    NEVER searches Aadhaar or address.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    results = []
    for c in candidates:
        if today and (c.get("created_date") or "") != today_str:
            continue
        if entity and (c.get("entity") or "").strip().lower() != entity.strip().lower():
            continue
        if operation and (c.get("operation") or "").strip().lower() != operation.strip().lower():
            continue
        if cost_code and (c.get("cost_code") or "").strip() != cost_code.strip():
            continue
        if role and (c.get("designation") or "").strip().lower() != role.strip().lower():
            continue
        if facility and (c.get("facility_name") or "").strip().lower() != facility.strip().lower():
            continue
        if batch_id and str(c.get("batch_id") or "").strip() != str(batch_id).strip():
            continue
        if status and _pipeline_bucket(c) != status and status != "all":
            continue
        if search:
            q = search.lower()
            name = (c.get("name") or "").lower()
            mobile = (c.get("mobile") or "").lower()
            if q not in name and q not in mobile:
                continue
        results.append(c)
    return results


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline_page(request: Request, search: str = "", today: bool = False,
                        entity: str = "", operation: str = "", cost_code: str = "",
                        role: str = "", facility: str = "", batch_id: str = "",
                        status: str = ""):
    all_candidates = database.list_candidates()
    filtered = filter_pipeline_candidates(
        all_candidates, search=search, today=today, entity=entity,
        operation=operation, cost_code=cost_code, role=role, facility=facility,
        batch_id=batch_id, status=status,
    )
    columns, order = _build_pipeline(filtered)
    total = len(all_candidates)
    filtered_count = len(filtered)

    # Distinct values for the filter dropdowns (safe fields only).
    entities = sorted({(c.get("entity") or "").strip() for c in all_candidates
                       if (c.get("entity") or "").strip()})
    operations = sorted({(c.get("operation") or "").strip() for c in all_candidates
                         if (c.get("operation") or "").strip()})
    cost_codes = sorted({(c.get("cost_code") or "").strip() for c in all_candidates
                         if (c.get("cost_code") or "").strip()})
    roles = sorted({(c.get("designation") or "").strip() for c in all_candidates
                    if (c.get("designation") or "").strip()})
    facilities = sorted({(c.get("facility_name") or "").strip() for c in all_candidates
                         if (c.get("facility_name") or "").strip()})
    batches = sorted({c.get("batch_id") for c in all_candidates
                      if c.get("batch_id") is not None})

    return templates.TemplateResponse(
        "pipeline.html",
        {
            "request": request,
            "columns": columns,
            "column_order": order,
            "total": total,
            "filtered_count": filtered_count,
            "filters": {
                "search": search, "today": today, "entity": entity,
                "operation": operation, "cost_code": cost_code, "role": role,
                "facility": facility, "batch_id": batch_id, "status": status,
            },
            "entities": entities, "operations": operations,
            "cost_codes": cost_codes, "roles": roles, "facilities": facilities,
            "batches": batches,
            "column_labels": {
                "draft": "Draft", "needs_review": "Needs Review", "ready": "Ready",
                "generated": "Generated", "portal_pending": "Portal Pending",
                "portal_success": "Portal Success", "portal_failed": "Portal Failed",
                "other": "Other / Review",
            },
            "saved_views": database.list_saved_views("pipeline"),
            "nav_active": "pipeline",
        },
    )


@app.get("/api/search")
async def api_search(q: str = ""):
    """Global search. Uses parameterized SQL against safe fields only.

    Candidates match on name/mobile/id/batch/facility/role. Generated files
    match on filename/batch_id. Returns at most 10 of each. NEVER searches or
    exposes sensitive Aadhaar values, addresses, or raw OCR text.
    """
    q = (q or "").strip()
    return JSONResponse(database.search_global(q, limit=10))


# ── Data Quality Command Center ──────────────────────────────────────────────


def _dq_categories(candidates: list[dict]) -> list[dict]:
    """Classify candidates into data-quality buckets (safe metrics only).

    Each bucket carries the affected candidate IDs plus a safe detail map
    (no Aadhaar/address) so the UI can render a rich drill-down table.
    """
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

    # Build a safe candidate detail map for the drill-down table (NO Aadhaar/address).
    def _safe(c):
        return {
            "candidate_id": c.get("candidate_id"),
            "name": c.get("name") or "",
            "mobile": (c.get("mobile") or "").strip(),
            "status": c.get("status") or "",
            "designation": c.get("designation") or "",
            "facility_name": c.get("facility_name") or "",
            "cost_code": c.get("cost_code") or "",
            "batch_id": c.get("batch_id"),
        }

    # Severity labels exposed in the command center cards.
    def bucket(label, cids, severity):
        return {
            "key": label.replace("_", "-"),
            "label": label.replace("_", " ").title(),
            "count": len(cids),
            "severity": severity,
            "candidate_ids": cids[:20],
            "candidates": [_safe(c) for c in candidates if c.get("candidate_id") in set(cids)][:20],
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
async def data_quality_page(request: Request, issue: str = ""):
    candidates = database.list_candidates()
    cards = _dq_categories(candidates)
    return templates.TemplateResponse(
        "data_quality.html",
        {
            "request": request,
            "cards": cards,
            "nav_active": "data_quality",
            "total": len(candidates),
            "active_issue": issue,
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
            "health_summary": health_service.summary(),
        },
    )


@app.get("/api/master-status")
async def api_master_status():
    return JSONResponse({
        **master_data.get_master_status(),
        "history": database.get_master_load_history(20),
    })


# ── Backups (create / verify / restore) ──────────────────────────────────────


@app.get("/backups", response_class=HTMLResponse)
async def backups_page(request: Request):
    records = backup_service.list_backups()
    audit = database.list_system_events(50)
    audit_display = [
        {"event_type": e["event_type"], "summary": e.get("summary", ""),
         "created_at": e.get("created_at", "")} for e in audit
        if e["event_type"].startswith(("Backup", "Restore", "Pre-Restore"))
    ]
    return templates.TemplateResponse(
        "backups.html",
        {
            "request": request,
            "backups": records,
            "backup_dir": str(backup_service.backup_dir()),
            "backup_audit": audit_display,
            "nav_active": "backups",
        },
    )


@app.post("/api/backups/create")
async def api_backup_create(request: Request):
    body = await request.json()
    include_generated = bool(body.get("include_generated", False))
    try:
        result = backup_service.create_backup(include_generated=include_generated)
    except Exception as e:  # pragma: no cover - defensive
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse(result)


@app.post("/api/backups/verify")
async def api_backup_verify(request: Request):
    body = await request.json()
    filename = (body.get("filename") or "").strip()
    if not filename:
        return JSONResponse({"ok": False, "error": "filename required"}, status_code=400)
    result = backup_service.verify_backup_by_name(filename)
    safe = {
        "ok": result.get("ok"),
        "valid": result.get("valid"),
        "status": result.get("status"),
        "error": result.get("error"),
        "warnings": result.get("warnings", []),
    }
    m = result.get("manifest")
    if m:
        safe["manifest"] = {
            "created_at": m.get("created_at"),
            "git_commit": m.get("git_commit"),
            "database_size": m.get("database_size"),
            "include_generated_files": m.get("include_generated_files"),
            "files": list(m.get("files", {}).keys()),
        }
    return JSONResponse(safe)


@app.post("/api/backups/restore")
async def api_backup_restore(request: Request):
    body = await request.json()
    filename = (body.get("filename") or "").strip()
    if not filename:
        return JSONResponse({"ok": False, "error": "filename required"}, status_code=400)
    result = backup_service.restore_backup(filename)
    return JSONResponse(result)


@app.post("/api/backups/open-folder")
async def api_backup_open_folder():
    return JSONResponse(backup_service.open_backup_folder())


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/health", response_class=HTMLResponse)
async def health_page(request: Request):
    report = health_service.build_health_report()
    return templates.TemplateResponse(
        "health.html",
        {
            "request": request,
            "cards": report["cards"],
            "overall": report["overall"],
            "nav_active": "health",
        },
    )


@app.get("/api/health")
async def api_health():
    return JSONResponse(health_service.json_health())


# ── Release Readiness ────────────────────────────────────────────────────────


@app.get("/release", response_class=HTMLResponse)
async def release_page(request: Request):
    from app import release_readiness
    data = release_readiness.release_status()
    return templates.TemplateResponse("release.html", {
        "request": request,
        "data": data,
        "nav_active": "release",
    })


@app.get("/api/release/readiness")
async def api_release_readiness():
    from app import release_readiness
    return JSONResponse(release_readiness.release_status())


# ── Candidate drafts (Smart Upload / Manual Entry recovery) ─────────────────


@app.post("/api/drafts")
async def api_save_draft(request: Request):
    """Save (create or update) a candidate draft. Only safe fields are stored."""
    data = await request.json()
    draft_type = (data.get("draft_type") or "manual_entry").strip()
    if draft_type not in ("manual_entry", "smart_upload"):
        draft_type = "manual_entry"
    draft_id = data.get("draft_id")
    payload = data.get("safe_payload") or {}
    candidate_id = data.get("candidate_id")
    if draft_id:
        database.update_candidate_draft(int(draft_id), payload)
        return JSONResponse({"ok": True, "draft_id": int(draft_id),
                             "draft_type": draft_type})
    new_id = database.save_candidate_draft(draft_type, payload, candidate_id)
    return JSONResponse({"ok": True, "draft_id": new_id, "draft_type": draft_type})


@app.get("/api/drafts")
async def api_list_drafts(draft_type: str = ""):
    drafts = database.list_candidate_drafts(
        draft_type=draft_type if draft_type in ("manual_entry", "smart_upload")
        else None, limit=10)
    # Return latest per type for resume (never includes sensitive payload).
    items = []
    for d in drafts:
        payload = d.get("safe_payload") or "{}"
        try:
            payload = json.loads(payload) if isinstance(payload, str) else payload
        except (TypeError, ValueError):
            payload = {}
        items.append({
            "draft_id": d["draft_id"],
            "draft_type": d["draft_type"],
            "candidate_id": d.get("candidate_id"),
            "updated_at": d.get("updated_at"),
            "safe_payload": payload,
        })
    return JSONResponse({"drafts": items})


@app.post("/api/drafts/discard")
async def api_discard_draft(request: Request):
    body = await request.json()
    draft_id = body.get("draft_id")
    if not draft_id:
        return JSONResponse({"ok": False, "error": "draft_id required"}, status_code=400)
    database.record_system_event("Draft Discarded", "Recovery draft discarded.")
    return JSONResponse({"ok": database.delete_candidate_draft(int(draft_id))})


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


# ── Phase 6: Master Import Preview (dry-run, no mutation) ────────────────────
# The preview reads the Excel masters and diffs against the CURRENT effective
# master. It never record_master_loads, never assigns globals, and never touches
# source Excel files. Apply Import only re-runs the loader (same as Reload).


@app.post("/api/admin/import-preview")
async def api_admin_import_preview():
    preview = master_data.preview_masters()
    return JSONResponse(preview)


@app.post("/api/admin/import-apply")
async def api_admin_import_apply():
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


# ── Phase 6: Saved views / filters ───────────────────────────────────────────
# Views store ONLY safe filter definitions; sensitive fields are rejected.

_SAVED_VIEW_SAFE_KEYS = {"search", "status", "entity", "operation", "cost_code",
                         "role", "facility", "batch_id", "today", "view_name"}


def _sanitized_view_def(raw: dict) -> dict:
    raw = raw or {}
    return {k: raw[k] for k in raw if k in _SAVED_VIEW_SAFE_KEYS}


@app.get("/api/saved-views")
async def api_saved_views(page: str = "candidates"):
    views = database.list_saved_views(page or "candidates")
    return JSONResponse({"ok": True, "views": views})


@app.get("/api/saved-views/{view_id}")
async def api_saved_view_get(view_id: int):
    """Fetch one saved view (view_def) so the UI can apply the stored filter."""
    view = database.get_saved_view(view_id)
    if view is None:
        return JSONResponse({"ok": False, "error": "View not found."}, status_code=404)
    return JSONResponse({"ok": True, **view})


@app.post("/api/saved-views")
async def api_create_saved_view(request: Request):
    data = await request.json() or {}
    name = (data.get("name") or "").strip()
    page = (data.get("page") or "candidates").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "View name is required."},
                            status_code=422)
    if page not in ("candidates", "pipeline"):
        return JSONResponse({"ok": False, "error": "Unsupported page."},
                            status_code=422)
    view_id = database.create_saved_view(name, page, _sanitized_view_def(data.get("def") or {}))
    return JSONResponse({"ok": True, "view_id": view_id})


@app.put("/api/saved-views/{view_id}")
async def api_update_saved_view(view_id: int, request: Request):
    data = await request.json() or {}
    name = (data.get("name") or "").strip()
    view_def = _sanitized_view_def(data.get("def") or {})
    ok = database.update_saved_view(view_id,
                                    view_name=name or None,
                                    view_def=view_def or None)
    if not ok:
        return JSONResponse({"ok": False, "error": "View not found."}, status_code=404)
    return JSONResponse({"ok": True})


@app.delete("/api/saved-views/{view_id}")
async def api_delete_saved_view(view_id: int):
    ok = database.delete_saved_view(view_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "View not found."}, status_code=404)
    return JSONResponse({"ok": True})


@app.post("/api/saved-views/{view_id}/default")
async def api_set_default_saved_view(view_id: int, request: Request):
    data = await request.json() or {}
    page = (data.get("page") or "candidates").strip()
    ok = database.set_default_saved_view(view_id, page)
    if not ok:
        return JSONResponse({"ok": False, "error": "View not found."}, status_code=404)
    return JSONResponse({"ok": True})


# ── Phase 6: Safe bulk actions + export selected ─────────────────────────────
# Allowed: Mark Needs Review · Add Ready to batch · Export safe fields.
# Never: bulk approve, portal status changes, uploads, deletes, Aadhaar/address.

_BULK_ACTIONS = {"mark_needs_review", "add_to_batch"}


@app.post("/api/candidates/bulk-action")
async def api_candidates_bulk_action(request: Request):
    data = await request.json() or {}
    action = (data.get("action") or "").strip().lower()
    ids = [int(i) for i in (data.get("ids") or [])
           if str(i).strip().lstrip("-").isdigit()]
    if action not in _BULK_ACTIONS:
        return JSONResponse(
            {"ok": False, "error": f"Bulk action '{action}' is not supported."},
            status_code=422)
    if not ids:
        return JSONResponse({"ok": False, "error": "No candidates selected."},
                            status_code=422)
    ids = ids[:500]
    if action == "mark_needs_review":
        updated, errors = database.bulk_mark_needs_review(ids)
    else:  # add_to_batch
        try:
            batch_id = int(data.get("batch_id") or 0)
        except (TypeError, ValueError):
            batch_id = 0
        if batch_id <= 0:
            return JSONResponse({"ok": False, "error": "A valid batch is required."},
                                status_code=422)
        updated, errors = database.bulk_add_to_batch(ids, batch_id)
    return JSONResponse({"ok": True, "action": action, "updated": updated,
                         "errors": errors})


# ── Phase 6: Candidate review quick actions ──────────────────────────────────
# Approve reuses the SAME backend validation the form does (never bypasses it).
# Mark Review / Add to Batch only flip safe status/batch fields.


@app.post("/api/candidates/{candidate_id}/approve")
async def api_approve_candidate(candidate_id: int, request: Request):
    candidate = database.get_candidate(candidate_id)
    if candidate is None:
        return JSONResponse({"ok": False, "error": "Candidate not found."},
                            status_code=404)
    status = (candidate.get("status") or "").lower()
    if status in ("generated", "portal_pending", "portal_success", "portal_failed"):
        return JSONResponse(
            {"ok": False,
             "error": "Cannot approve a candidate already in generation/portal flow."},
            status_code=422)
    payload = {
        "name": candidate.get("name", ""),
        "mobile": candidate.get("mobile", ""),
        "cost_code": candidate.get("cost_code", ""),
        "role": candidate.get("designation", ""),
        "facility": candidate.get("facility_name", ""),
        "facility_type": candidate.get("facility_type", ""),
    }
    errors = rules.validate_candidate(payload)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=422)

    # Persist optional backend-field overrides sent by the review workspace
    # (recruiter, DOJ, gender, PIN, father name, UAN). Values that are present
    # replace the candidate's stored values; absent values are left untouched.
    overrides = {}
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    if data:
        fields = _backend_fields(data)
        for key in ("doj", "recruiter_name", "gender", "pin_code", "father_name", "uan_no"):
            if key in data and str(data.get(key, "")).strip():
                overrides[key] = fields.get(key, data.get(key, ""))
    if overrides:
        database.update_candidate(candidate_id, overrides)

    database.update_candidate(candidate_id, {"status": "ready"})
    database.record_candidate_event(candidate_id, "Approved",
                                    "Candidate approved via review workspace.")
    return JSONResponse({"ok": True, "status": "ready"})


@app.post("/api/candidates/{candidate_id}/mark-review")
async def api_mark_candidate_review(candidate_id: int):
    candidate = database.get_candidate(candidate_id)
    if candidate is None:
        return JSONResponse({"ok": False, "error": "Candidate not found."},
                            status_code=404)
    status = (candidate.get("status") or "").lower()
    if status in ("generated", "portal_pending", "portal_success", "portal_failed"):
        return JSONResponse(
            {"ok": False,
             "error": "Cannot flag a candidate in generation/portal flow for review."},
            status_code=422)
    database.update_candidate(candidate_id, {"status": "needs_review"})
    database.record_candidate_event(candidate_id, "Marked for Review",
                                    "Candidate flagged for review from review workspace.")
    return JSONResponse({"ok": True, "status": "needs_review"})


@app.post("/api/candidates/{candidate_id}/batch")
async def api_add_candidate_to_batch(candidate_id: int, request: Request):
    data = await request.json() or {}
    try:
        batch_id = int(data.get("batch_id") or 0)
    except (TypeError, ValueError):
        batch_id = 0
    if batch_id <= 0:
        return JSONResponse({"ok": False, "error": "A valid batch is required."},
                            status_code=422)
    candidate = database.get_candidate(candidate_id)
    if candidate is None:
        return JSONResponse({"ok": False, "error": "Candidate not found."},
                            status_code=404)
    updated, errors = database.bulk_add_to_batch([candidate_id], batch_id)
    if not updated:
        return JSONResponse({"ok": False, "errors": errors},
                            status_code=422)
    return JSONResponse({"ok": True, "batch_id": batch_id})


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
    rules_json = json.dumps(rules.get_rules_for_frontend())
    return templates.TemplateResponse(
        "smart_upload.html",
        {
            "request": request,
            "rules": rules.get_rules_for_frontend(),
            "rules_json": rules_json,
            "recruiter_name": generation.get_default_recruiter_name(),
        },
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
    """Facility typeahead: primary search text is the display name (Column C);
    the system reference (Column A) and location (Column B) are also searched.
    Returns EXACT master rows (never invented values) — including the row's
    location and facility_ref — so the UI can bind a row exactly and duplicate
    display names can be offered as explicit choices.
    """
    if query:
        rows = master_data.fuzzy_search_facilities(query, cost_code, top_n=5)
    else:
        rows = master_data.get_facility_rows(cost_code)[:20]
    hubs = []
    locations: dict[str, str] = {}
    for r in rows:
        hubs.append({
            "facility_name": r["facility_name"],
            "location": r.get("location", ""),
            "facility_ref": r.get("facility_ref", ""),
            "facility": r.get("facility_name", ""),
            "hub_key": r.get("hub_key", ""),
            "cost_code": r.get("cost_code", ""),
            "entity": r.get("entity", ""),
            "operation": r.get("operation", ""),
            "facility_type": r.get("facility_type", ""),
        })
        locations[r["facility_name"]] = r.get("location", "")
    return JSONResponse({
        "hubs": hubs,
        "locations": locations,
        "total": len(master_data.get_hubs_for_cost_code(cost_code)) if cost_code else len(master_data.get_facility_names()),
    })


@app.get("/api/location")
async def location_for_facility(facility: str = "", location: str = "", facility_ref: str = ""):
    resolved = master_data.resolve_facility_selection(facility, location, facility_ref)
    return JSONResponse({
        "facility": facility,
        "facility_name": resolved["facility_name"],
        "location": resolved["location"],
        "facility_ref": resolved["facility_ref"],
    })


# ── API: Dashboard Counts ────────────────────────────────────────────────────


@app.get("/api/counts")
async def api_counts():
    return JSONResponse(_count_candidates())


# ── API: Self Onboarding Generation ─────────────────────────────────────────


@app.get("/api/generate-preview")
async def generate_preview(batch_id: int):
    """Return a preview summary for the Generate Onboarding Files dialog.

    Only Ready candidates are eligible; Draft / Needs Attention / invalid
    candidates are shown as excluded. A single two-sheet workbook (OB Format
    + Mail Format) is generated from the Excel Generation template.
    """
    template_ok = generation.excel_template_is_configured()
    recruiter_name = generation.get_default_recruiter_name()
    candidates = database.get_batch_candidates(batch_id)
    ready = [c for c in candidates if (c.get("status") or "").lower() == "ready"]
    others = [c for c in candidates if (c.get("status") or "").lower() != "ready"]

    vres = generation.validate_candidates(ready)
    valid_ids = {r["candidate_id"] for r in vres["rows"]}
    invalid_ready = [c for c in ready if c["candidate_id"] not in valid_ids]

    # Mail-sheet readiness of the valid candidates (blocks generation when missing).
    candidates_by_id = {c["candidate_id"]: c for c in ready}
    mail = generation.build_mail_rows(vres["rows"], candidates_by_id)
    mail_blocking = {cid: msgs for cid, msgs in mail["problems"].items() if msgs}

    now = datetime.now()
    ts = generation.build_pair_timestamp(now)
    _parts = []
    if not template_ok:
        _parts.append("The Excel Generation template is not configured.")
    if not vres["rows"]:
        _parts.append("No Ready candidates passed validation.")
    if mail_blocking:
        _parts.append("Mail-sheet data is incomplete for some candidates.")
    return JSONResponse({
        "template_configured": template_ok,
        "recruiter_name": recruiter_name,
        "batch_id": batch_id,
        "ready_count": len(ready),
        "included_count": len(vres["rows"]),
        "excluded_count": len(others) + len(invalid_ready),
        "excluded": {
            c["candidate_id"]: generation.validate_candidates([c])["errors"].get(c["candidate_id"], [])
            for c in others
        } | {c: vres["errors"][c] for c in vres["errors"]},
        "mail_blocking": mail_blocking,
        "backend_problem_count": len(mail_blocking),
        "output_folder": str(generation.get_output_base_dir()),
        "filename_preview": generation.onboarding_filename(ts),
        "message": " ".join(_parts),
    })


@app.post("/api/generate-excel")
async def generate_excel(request: Request):
    data = await request.json() or {}
    batch_id = int(data.get("batch_id", 1))
    only_ready = bool(data.get("only_ready", True))
    result = generation.generate_batch_excel(batch_id, only_ready=only_ready)
    status_code = 200 if result.get("success") else 422
    return JSONResponse(result, status_code=status_code)


@app.post("/api/generate-onboarding-pair")
async def generate_onboarding_pair(request: Request):
    """Generate BOTH workbooks (Self-Onboarding + TeamHR Backend Mail) for a batch."""
    data = await request.json() or {}
    batch_id = int(data.get("batch_id", 1))
    only_ready = bool(data.get("only_ready", True))
    result = generation.generate_onboarding_pair(batch_id, only_ready=only_ready)
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


@app.get("/api/recruiter-profile")
async def recruiter_profile_get():
    """Current default recruiter profile (name + first-run needs-set flag)."""
    profile = generation.get_recruiter_profile()
    name = profile.get("name", "")
    return JSONResponse({
        "recruiter_name": name,
        "needs_setup": not bool(name),
    })


@app.post("/api/recruiter-profile")
async def recruiter_profile_set(request: Request):
    """Save the default recruiter name (no other personal data stored)."""
    data = await request.json() or {}
    name = generation.save_recruiter_profile(data.get("recruiter_name", ""))
    return JSONResponse({"recruiter_name": name, "needs_setup": not bool(name)})


@app.get("/api/generated-pairs")
async def generated_pairs(limit: int = 20):
    """List onboarding pairs (Self-Onboarding + Backend Mail) newest first."""
    pairs = database.list_generation_pairs(limit=limit)
    # Never leak full Aadhaar through the API — filenames/paths only.
    for p in pairs:
        for key in ("self_onboarding_file_id_details", "backend_mail_file_id_details"):
            details = p.get(key)
            if details is None:
                continue
            p[key] = {
                "file_id": details["file_id"],
                "filename": details["filename"],
                "file_path": details["file_path"],
                "candidate_count": details["candidate_count"],
                "generated_at": details["generated_at"],
            }
    return JSONResponse({"pairs": pairs})


@app.post("/api/generated/open-file")
async def generated_open_file(request: Request):
    """Open a generated workbook in the default viewer (manual action)."""
    data = await request.json() or {}
    file_id = int(data.get("file_id") or 0)
    gf = database.get_generated_file(file_id) if file_id else None
    if gf is None:
        return JSONResponse({"ok": False, "error": "Generated file not found."},
                            status_code=404)
    path = Path(gf["file_path"])
    if not path.exists():
        return JSONResponse({"ok": False, "error": "File no longer exists on disk."},
                            status_code=404)
    _open_path(path)
    return JSONResponse({"ok": True, "filename": gf["filename"]})


@app.post("/api/generated/open-folder")
async def generated_open_folder(request: Request):
    """Open the containing folder of a generated workbook in Explorer."""
    data = await request.json() or {}
    file_id = int(data.get("file_id") or 0)
    folder = str(data.get("folder", "") or "")
    file_path = str(data.get("file_path", "") or "")
    if not folder and file_path:
        folder = str(Path(file_path).parent)
    if not folder and file_id:
        gf = database.get_generated_file(file_id)
        if gf:
            folder = str(Path(gf["file_path"]).parent)
    if not folder or not Path(folder).is_dir():
        return JSONResponse({"ok": False, "error": "Folder does not exist."},
                            status_code=404)
    _open_path(Path(folder))
    return JSONResponse({"ok": True, "folder": folder})


def _open_path(path: Path) -> None:
    """Open a file or folder with the OS default handler (best-effort)."""
    try:
        import subprocess
        subprocess.Popen(["explorer", str(path)])
    except Exception:  # noqa: BLE001
        try:
            os.startfile(str(path))  # noqa: S606 - local dev convenience
        except Exception:  # noqa: BLE001
            pass


@app.get("/api/generated")
async def generated_files(batch_id: Optional[int] = None):
    files = database.list_generated_files(batch_id)
    # keep sensitive fields out of API responses for the mirror
    mirror = database.get_daily_master_mirror()
    return JSONResponse({"files": files, "daily_master": mirror})


@app.get("/generated-files", response_class=HTMLResponse)
async def generated_files_page(request: Request):
    """Generated Files page: onboarding pairs shown together with actions."""
    pairs = database.list_generation_pairs(limit=50)
    legacy = [
        f for f in database.list_generated_files(limit=100)
        if not f.get("generation_pair_id")
    ]
    return templates.TemplateResponse(
        "generated_files.html",
        {
            "request": request,
            "pairs": pairs,
            "legacy_files": legacy,
            "nav_active": "generated_files",
        },
    )


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


# ── Phase 7: Manual Acceptance Test (UAT) ────────────────────────────────────


@app.get("/uat", response_class=HTMLResponse)
async def uat_page(request: Request):
    from app.uat_catalog import UAT_GROUPS, UAT_CRITICAL, UAT_CRITICAL_COUNT
    run_id = request.query_params.get("run")
    current_run = None
    results = []
    if run_id:
        try:
            run_id = int(run_id)
            current_run = database.uat_get_run(run_id)
            results = database.uat_get_results(run_id)
        except (ValueError, TypeError):
            run_id = None
    runs = database.uat_list_runs(50)
    # Build a lookup map for JS: {group_key: {test_id: test_name}}
    groups_map = {}
    for g in UAT_GROUPS:
        groups_map[g["key"]] = {tid: tname for tid, tname in g["tests"]}
    return templates.TemplateResponse("uat.html", {
        "request": request,
        "groups": UAT_GROUPS,
        "groups_map": groups_map,
        "critical": sorted(UAT_CRITICAL),
        "critical_count": UAT_CRITICAL_COUNT,
        "current_run": current_run,
        "results": results,
        "runs": runs,
    })


@app.post("/api/uat/run/start")
async def uat_start_run(request: Request):
    data = await request.json()
    notes = data.get("notes", "")
    run_id = database.uat_start_run(notes=notes)
    return JSONResponse({"ok": True, "run_id": run_id})


@app.get("/api/uat/runs")
async def uat_list_runs_api():
    runs = database.uat_list_runs(50)
    return JSONResponse({"ok": True, "runs": runs})


@app.get("/api/uat/run/{run_id}")
async def uat_get_run_api(run_id: int):
    run = database.uat_get_run(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    results = database.uat_get_results(run_id)
    summary = database.uat_get_summary(run_id)
    return JSONResponse({"ok": True, "run": run, "results": results, "summary": summary})


@app.post("/api/uat/run/{run_id}/finish")
async def uat_finish_run_api(run_id: int):
    run = database.uat_get_run(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    database.uat_finish_run(run_id)
    return JSONResponse({"ok": True})


@app.post("/api/uat/result")
async def uat_result_api(request: Request):
    data = await request.json()
    run_id = data.get("run_id")
    test_id = data.get("test_id", "").strip()
    status = data.get("status", "").strip().upper()
    notes = data.get("notes", "")
    if not run_id or not test_id:
        return JSONResponse({"error": "run_id and test_id required"}, status_code=400)
    if status not in ("PASS", "FAIL", "NOT_TESTED", "BLOCKED"):
        return JSONResponse({"error": f"Invalid status: {status}"}, status_code=400)
    if status in ("FAIL", "BLOCKED") and not notes.strip():
        return JSONResponse({"error": f"Notes required for {status}"}, status_code=400)
    try:
        result_id = database.uat_upsert_result(run_id, test_id, status, notes)
        run = database.uat_get_run(run_id)
        return JSONResponse({"ok": True, "result_id": result_id, "status": status,
                             "tested_at": run["started_at"] if run else ""})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/api/uat/export")
async def uat_export_api(request: Request):
    run_id = request.query_params.get("run_id")
    fmt = request.query_params.get("format", "csv")
    if not run_id:
        return JSONResponse({"error": "run_id required"}, status_code=400)
    try:
        run_id = int(run_id)
    except (ValueError, TypeError):
        return JSONResponse({"error": "Invalid run_id"}, status_code=400)
    results = database.uat_export_results(run_id)
    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["run_id", "group", "test_id", "status", "notes", "tested_at"])
        writer.writeheader()
        for r in results:
            writer.writerow(r)
        from starlette.responses import Response
        return Response(content=buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=uat_run_{run_id}.csv"})
    elif fmt == "xlsx":
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"UAT Run {run_id}"
            ws.append(["Run ID", "Group", "Test ID", "Status", "Notes", "Tested At"])
            for r in results:
                ws.append([r["run_id"], r["group"], r["test_id"], r["status"], r["notes"], r["tested_at"]])
            import io
            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            from starlette.responses import Response
            return Response(content=buf.getvalue(),
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f"attachment; filename=uat_run_{run_id}.xlsx"})
        except ImportError:
            return JSONResponse({"error": "openpyxl not installed"}, status_code=500)
    return JSONResponse({"error": "format must be csv or xlsx"}, status_code=400)


# ── Follow-ups / Task Tracking ────────────────────────────────────────────────

@app.get("/follow-ups", response_class=HTMLResponse)
async def follow_ups_page(request: Request):
    from datetime import date
    today = date.today().isoformat()
    all_follow_ups = database.list_follow_ups(limit=200)
    overdue = [f for f in all_follow_ups if f.get("due_date") and f["due_date"] < today and f["status"] == "open"]
    due_today = [f for f in all_follow_ups if f.get("due_date") == today and f["status"] == "open"]
    upcoming = [f for f in all_follow_ups if f.get("due_date") and f["due_date"] > today and f["status"] == "open"]
    completed = [f for f in all_follow_ups if f["status"] == "completed"]
    
    summary = database.get_follow_up_summary()
    
    # Enrich with candidate names
    for f in all_follow_ups:
        if f.get("candidate_id"):
            c = database.get_candidate(f["candidate_id"])
            f["candidate_name"] = c.get("name", "") if c else ""
    
    return templates.TemplateResponse("follow_ups.html", {
        "request": request,
        "follow_ups": all_follow_ups,
        "overdue": overdue,
        "due_today": due_today,
        "upcoming": upcoming,
        "completed": completed,
        "summary": summary,
        "today": today,
        "candidates": database.list_candidates(),
        "nav_active": "follow_ups",
    })

@app.post("/api/follow-ups")
async def api_create_follow_up(request: Request):
    data = await request.json() or {}
    candidate_id = data.get("candidate_id")
    reason = (data.get("reason") or "").strip()
    if not reason:
        return JSONResponse({"ok": False, "error": "Reason is required"}, status_code=422)
    fid = database.create_follow_up(
        candidate_id=int(candidate_id) if candidate_id else None,
        reason=reason,
        owner=data.get("owner", "recruiter"),
        notes=data.get("notes", ""),
        due_date=data.get("due_date"),
    )
    return JSONResponse({"ok": True, "follow_up_id": fid})

@app.put("/api/follow-ups/{follow_up_id}")
async def api_update_follow_up(follow_up_id: int, request: Request):
    data = await request.json() or {}
    ok = database.update_follow_up(follow_up_id, **data)
    if not ok:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})

@app.post("/api/follow-ups/{follow_up_id}/complete")
async def api_complete_follow_up(follow_up_id: int):
    from datetime import datetime
    ok = database.update_follow_up(follow_up_id, status="completed",
                                    completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if not ok:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})

@app.delete("/api/follow-ups/{follow_up_id}")
async def api_delete_follow_up(follow_up_id: int):
    ok = database.update_follow_up(follow_up_id, status="cancelled")
    if not ok:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})

@app.get("/api/follow-ups")
async def api_list_follow_ups(status: str = ""):
    follow_ups = database.list_follow_ups(status=status if status else None)
    return JSONResponse({"follow_ups": follow_ups})


# ── Issue Center ──────────────────────────────────────────────────────────────

@app.get("/issues", response_class=HTMLResponse)
async def issue_center_page(request: Request):
    issues = database.list_issues(limit=200)
    summary = database.get_issue_summary()
    return templates.TemplateResponse("issue_center.html", {
        "request": request,
        "issues": issues,
        "summary": summary,
        "candidates": database.list_candidates(),
        "nav_active": "issues",
    })

@app.post("/api/issues")
async def api_create_issue(request: Request):
    data = await request.json() or {}
    title = (data.get("title") or "").strip()
    issue_type = (data.get("issue_type") or "other").strip()
    if not title:
        return JSONResponse({"ok": False, "error": "Title is required"}, status_code=422)
    iid = database.create_issue(
        issue_type=issue_type,
        severity=data.get("severity", "warning"),
        title=title,
        detail=data.get("detail", ""),
        candidate_id=int(data["candidate_id"]) if data.get("candidate_id") else None,
        batch_id=int(data["batch_id"]) if data.get("batch_id") else None,
        source_page=data.get("source_page", ""),
    )
    return JSONResponse({"ok": True, "issue_id": iid})

@app.post("/api/issues/{issue_id}/resolve")
async def api_resolve_issue(issue_id: int, request: Request):
    data = await request.json() or {}
    notes = data.get("resolution_notes", "")
    ok = database.resolve_issue(issue_id, resolved_by="local-admin", resolution_notes=notes)
    if not ok:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})

@app.put("/api/issues/{issue_id}")
async def api_update_issue(issue_id: int, request: Request):
    data = await request.json() or {}
    ok = database.update_issue(issue_id, **data)
    if not ok:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})

@app.get("/api/issues")
async def api_list_issues(status: str = "", issue_type: str = ""):
    issues = database.list_issues(
        status=status if status else None,
        issue_type=issue_type if issue_type else None,
    )
    return JSONResponse({"issues": issues})


# ── Communications (Dry-Run by default) ───────────────────────────────────────

@app.get("/communications", response_class=HTMLResponse)
async def communications_page(request: Request):
    from app.communication import get_communication_service, TEMPLATES
    svc = get_communication_service()
    outbox = database.list_outbox_messages(limit=50)
    channels = {
        "whatsapp": svc.is_channel_enabled("whatsapp"),
        "email": svc.is_channel_enabled("email"),
        "sms": svc.is_channel_enabled("sms"),
        "voice": svc.is_channel_enabled("voice"),
    }
    template_list = [
        {"id": name, "name": name.replace("_", " ").title()}
        for name in TEMPLATES.keys()
    ]
    return templates.TemplateResponse("communications.html", {
        "request": request,
        "outbox": outbox,
        "channels": channels,
        "templates": template_list,
        "nav_active": "communications",
    })

@app.post("/api/communications/send")
async def api_communications_send(request: Request):
    from app.communication import get_communication_service
    data = await request.json() or {}
    svc = get_communication_service()
    template_name = data.get("template_name") or data.get("template_id", "")
    payload = data.get("payload") or {}
    pay = dict(payload)
    if data.get("recipient") and not pay.get("recipient"):
        pay["recipient"] = data["recipient"]
    result = svc.send_message(
        candidate_id=int(data["candidate_id"]) if data.get("candidate_id") else None,
        channel=data.get("channel", "whatsapp"),
        template_name=template_name,
        payload=pay,
        dry_run=True,  # ALWAYS dry-run in current production
    )
    return JSONResponse(result)

@app.post("/api/communications/preview")
async def api_communications_preview(request: Request):
    from app.communication import get_communication_service
    data = await request.json() or {}
    svc = get_communication_service()
    preview = svc.render_preview(
        channel=data.get("channel", "whatsapp"),
        template_name=data.get("template_name", ""),
        payload=data.get("payload", {}),
    )
    return JSONResponse(preview)

@app.get("/api/communications/outbox")
async def api_outbox(channel: str = "", status: str = ""):
    messages = database.list_outbox_messages(
        channel=channel if channel else None,
        status=status if status else None,
    )
    return JSONResponse({"messages": messages})

@app.post("/api/communications/outbox/{outbox_id}/cancel")
async def api_cancel_outbox(outbox_id: int):
    ok = database.cancel_outbox_message(outbox_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})


# ── Notifications ─────────────────────────────────────────────────────────────

@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    notifications = database.list_notifications(limit=100)
    unread_count = database.count_unread_notifications()
    return templates.TemplateResponse("notifications_center.html", {
        "request": request,
        "notifications": notifications,
        "unread_count": unread_count,
        "nav_active": "notifications",
    })

@app.get("/api/notifications")
async def api_list_notifications(is_read: str = ""):
    read_filter = None
    if is_read == "true":
        read_filter = True
    elif is_read == "false":
        read_filter = False
    notifications = database.list_notifications(is_read=read_filter)
    unread = database.count_unread_notifications()
    return JSONResponse({"notifications": notifications, "unread_count": unread})

@app.post("/api/notifications/{notification_id}/read")
async def api_mark_notification_read(notification_id: int):
    ok = database.mark_notification_read(notification_id)
    return JSONResponse({"ok": ok})

@app.post("/api/notifications/read-all")
async def api_mark_all_read():
    database.mark_all_notifications_read()
    return JSONResponse({"ok": True})

@app.get("/api/notifications/unread-count")
async def api_unread_count():
    return JSONResponse({"count": database.count_unread_notifications()})


# ── Diagnostic Bundle ─────────────────────────────────────────────────────────

@app.get("/api/diagnostics/bundle")
async def api_diagnostic_bundle():
    from app.logging_config import create_diagnostic_bundle
    return JSONResponse(create_diagnostic_bundle())

@app.get("/api/diagnostics/logs")
async def api_diagnostic_logs(category: str = "", lines: int = 50):
    from app.logging_config import get_recent_logs
    return JSONResponse({"logs": get_recent_logs(category=category or None, lines=lines)})


# ── Version / About ───────────────────────────────────────────────────────────

@app.get("/api/version")
async def api_version():
    from app.config import APP_VERSION
    from app.backup_service import git_commit
    return JSONResponse({
        "version": APP_VERSION,
        "git_commit": git_commit(),
        "python": f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}.{__import__('sys').version_info.micro}",
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
