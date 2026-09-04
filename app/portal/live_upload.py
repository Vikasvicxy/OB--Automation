"""Controlled single-candidate LIVE eSampark upload mode.

This module gates + orchestrates a REAL, deliberate upload of ONE manually
reviewed generated onboarding workbook to the live eSampark portal.

Safety contract (fail closed)
-----------------------------
* A live submission is impossible unless BOTH ``REAL_UPLOAD_ENABLED`` and
  ``ESAMPARK_LIVE_TEST_MODE`` are true (see :func:`esampark.live_upload_allowed`).
* Live test mode permits EXACTLY ONE candidate per generated file. Any generated
  file holding more than one candidate is refused.
* Nothing is submitted on the first user action. The flow requires an explicit
  manual review checklist, then an explicit two-step confirmation, then a final
  guard re-check immediately before the single Upload click.
* A generated file that was already submitted is refused for this run (no
  automatic duplicate or retry).
* No WhatsApp / SMS / call / email automation lives here.
* Credentials / cookies / full Aadhaar / full address are never recorded.

The Playwright interaction stays in :mod:`app.portal.esampark`; here we only
orchestrate + validate state so the whole flow is unit-testable without a real
portal session.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from app import database
from app import generation
from app import rules
from app.portal import esampark

# Block messages (exact, stable text for the UI + tests).
LIVE_ONLY_ONE_MSG = "Live test mode allows exactly one candidate."
ALREADY_SUBMITTED_MSG = "This generated file has already been submitted."
PORTAL_LAYOUT_CHANGED_MSG = "Portal layout changed — manual review required."

# Four manual-review checklist items, in order (all must be ticked).
REVIEW_CHECKLIST = [
    "I checked the candidate details in the app",
    "I opened and checked the generated Excel",
    "I confirm this is the correct candidate",
    "I understand this will submit data to real eSampark",
]

# Two-step confirmation gateway.
FIRST_ACTION = "prepare_live_upload"
FINAL_ACTION = "submit_one_candidate"

# Internal flow states on the live_uploads record.
PROC_STATUS = {
    "PREPARED": "prepared",
    "CONFIRMED": "manual_confirmed",
    "STARTED": "started",
    "SUBMITTED": "submitted",
    "HISTORY_MATCHED": "history_matched",
    "SUCCESS": "success",
    "FAILED": "failed",
    "PENDING": "pending",
    "PARTIAL_FAILURE": "partial_failure",
    "UNKNOWN": "unknown",
}


class LiveUploadError(Exception):
    """A blocked condition in the controlled live-upload flow."""


# ── Flag / mode helpers ──────────────────────────────────────────────────────


def mode_enabled() -> bool:
    """True only when BOTH live safety flags are set."""
    return esampark.live_upload_allowed()


def assert_live_mode() -> None:
    """Raise ``LiveUploadError`` unless both safety flags are on."""
    if not mode_enabled():
        raise LiveUploadError(
            "Live upload is locked. Both REAL_UPLOAD_ENABLED and "
            "ESAMPARK_LIVE_TEST_MODE must be true."
        )


def safety_status() -> dict:
    """Safe snapshot of the live-mode safety configuration (no secrets)."""
    return {
        "REAL_UPLOAD_ENABLED": esampark.REAL_UPLOAD_ENABLED,
        "ESAMPARK_LIVE_TEST_MODE": esampark.ESAMPARK_LIVE_TEST_MODE,
        "live_upload_allowed": mode_enabled(),
        "single_candidate_limit": 1,
        "max_actions": (FIRST_ACTION, FINAL_ACTION),
    }


# ── Candidate + generated-file validation ────────────────────────────────────


def _norm_salary(candidate) -> Optional[int]:
    try:
        return rules.normalize_salary(candidate.get("salary"))[0]
    except Exception:  # noqa: BLE001
        return None


def validate_generated_file(file_id: int) -> dict:
    """Validate that ``file_id`` is a single, clean, ready-to-upload workbook.

    Checks (returns ``ok=False`` + reasons otherwise):
    * generated_files record exists
    * workbook exists on disk (and is an xlsx)
    * candidate_count == 1  (live test mode limitation)
    * generation_status == success/generated
    * the linked candidate is Ready/Generated (not Needs Review / draft)
    * no missing role / facility / location; salary + cost code + designation valid
    * the candidate is not flagged Needs Review or in a Conflict state
    """
    gf = database.get_generated_file(file_id)
    if gf is None:
        return {"ok": False, "errors": ["Generated file not found."]}
    errors: list[str] = []

    path = Path(gf.get("file_path") or "")
    if not path.exists():
        errors.append(f"Generated file does not exist on disk: {path.name}")
    if path.suffix.lower() != ".xlsx":
        errors.append("Generated file is not an .xlsx workbook.")

    try:
        count = int(gf.get("candidate_count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count != 1:
        errors.append(LIVE_ONLY_ONE_MSG)

    status = (gf.get("generation_status") or "").lower()
    if status not in ("generated", "success"):
        errors.append("Generation was not successful for this file.")

    # The single linked candidate.
    candidates = database.get_candidates_for_generated_file(file_id)
    candidate = candidates[0] if len(candidates) == 1 else None
    if candidate is None:
        errors.append("Generated file is not linked to exactly one candidate.")

    c_errors: list[str] = []
    if candidate is not None:
        cand_status = (candidate.get("status") or "").lower()
        if cand_status not in ("ready", "generated"):
            c_errors.append(f"Candidate status is '{candidate.get('status')}' (not Ready/Generated).")
        if not candidate.get("designation"):
            c_errors.append("Role / designation is missing.")
        else:
            cost_code = candidate.get("cost_code") or ""
            allowed = rules.get_roles_for_cost_code(cost_code) if cost_code else []
            if allowed and candidate.get("designation") not in allowed:
                c_errors.append(
                    f"Designation '{candidate.get('designation')}' is not valid for cost code {cost_code}."
                )
        if not candidate.get("facility_name"):
            c_errors.append("Facility is missing.")
        if not candidate.get("location_code"):
            c_errors.append("Location is missing.")
        salary = _norm_salary(candidate)
        if salary is None or salary <= 0:
            c_errors.append("Salary is missing or invalid.")
        if candidate.get("cost_code") not in rules.COST_CODES:
            c_errors.append(f"Invalid cost code: {candidate.get('cost_code')}")

    errors += c_errors

    if errors:
        return {"ok": False, "errors": errors, "file": gf, "candidate": candidate}
    return {"ok": True, "file": gf, "candidate": candidate, "errors": []}


# ── Manual review checklist + confirmation ──────────────────────────────────


def prepare(file_id: int) -> dict:
    """Step 1 (Prepare Live Upload): return the manual review payload.

    Also provisions the ``live_uploads`` record (persisted) so every subsequent
    guard reads from durable state. Does NOT submit anything.
    """
    res = validate_generated_file(file_id)
    if not res["ok"]:
        return {"ok": False, "errors": res["errors"]}
    gf = res["file"]
    candidate = res["candidate"]

    # Duplicate safety: never allow a second live submit of the same file.
    existing = database.get_live_upload_for_file(file_id)
    if existing and existing.get("flow_status") == PROC_STATUS["SUBMITTED"]:
        return {"ok": False, "errors": [ALREADY_SUBMITTED_MSG]}

    live_id = existing["live_upload_id"] if existing else database.create_live_upload(
        file_id, candidate["candidate_id"], flow_status=PROC_STATUS["PREPARED"]
    )
    database.update_live_upload(live_id, flow_status=PROC_STATUS["PREPARED"])
    database.record_portal_audit(event="live_upload_unlocked", status="info",
                                 generated_file_id=file_id)

    return {
        "ok": True,
        "live_upload_id": live_id,
        "file_id": file_id,
        "candidate": _candidate_review_payload(candidate),
        "generated_file": _file_review_payload(gf),
        "checklist": REVIEW_CHECKLIST,
        "safety": safety_status(),
    }


def _candidate_review_payload(candidate: dict) -> dict:
    return {
        "candidate_id": candidate["candidate_id"],
        "name": candidate.get("name"),
        "mobile": candidate.get("mobile"),
        "cost_code": candidate.get("cost_code"),
        "entity": candidate.get("entity"),
        "operation": candidate.get("operation"),
        "role": candidate.get("designation"),
        "facility": candidate.get("facility_name"),
        "location": candidate.get("location_code"),
        "salary": candidate.get("salary"),
    }


def _file_review_payload(gf: dict) -> dict:
    return {
        "filename": gf.get("filename"),
        "file_path": gf.get("file_path"),
        "candidate_count": gf.get("candidate_count"),
        "generated_at": gf.get("generated_at"),
        "batch_id": gf.get("batch_id"),
    }


def confirm(file_id: int, checked: list[str]) -> dict:
    """Step 1 completion: record that the human completed the review checklist.

    ``checked`` must include every item in ``REVIEW_CHECKLIST`` (otherwise the
    upload button remains disabled). Returns the resulting manually-confirmed
    state. Does NOT submit anything.
    """
    res = validate_generated_file(file_id)
    if not res["ok"]:
        return {"ok": False, "errors": res["errors"]}

    checked = list(checked or [])
    missing = [item for item in REVIEW_CHECKLIST if item not in checked]
    if missing:
        return {
            "ok": False,
            "missing": missing,
            "error": "Manual review checklist is not complete.",
        }

    live = database.get_live_upload_for_file(file_id)
    if not live:
        live_id = database.create_live_upload(
            file_id, res["candidate"]["candidate_id"], flow_status=PROC_STATUS["PREPARED"]
        )
    else:
        live_id = live["live_upload_id"]
    database.update_live_upload(live_id, manual_confirmed=1,
                                flow_status=PROC_STATUS["CONFIRMED"])
    database.record_portal_audit(event="manual_confirmation_complete", status="info",
                                 generated_file_id=file_id)
    return {"ok": True, "live_upload_id": live_id, "manual_confirmed": True}


def _is_confirmed(file_id: int) -> bool:
    live = database.get_live_upload_for_file(file_id)
    return bool(live and int(live.get("manual_confirmed") or 0) == 1)


# ── Final submit guard + orchestration ───────────────────────────────────────


def _final_submit_guard(file_id: int, file_path: str, session) -> None:
    """Re-check every safety condition immediately before the Upload click.

    Raises :class:`LiveUploadError` on any failure — live mode STOPS rather than
    guessing. This is the single chokepoint before a real upload.
    """
    if not mode_enabled():
        raise LiveUploadError("Live upload is locked (both flags must be true).")
    if not _is_confirmed(file_id):
        raise LiveUploadError("Manual review confirmation is incomplete.")
    res = validate_generated_file(file_id)
    if not res["ok"]:
        raise LiveUploadError("; ".join(res["errors"]))
    live = database.get_live_upload_for_file(file_id)
    if live and live.get("flow_status") == PROC_STATUS["SUBMITTED"]:
        raise LiveUploadError(ALREADY_SUBMITTED_MSG)
    if not Path(file_path).exists():
        raise LiveUploadError("Generated file is not on disk.")
    if session is None or session.page is None or session.page.is_closed():
        raise LiveUploadError("eSampark portal is not connected.")
    if session.status != esampark.CONNECTED:
        raise LiveUploadError(f"eSampark portal status is not Connected ({session.status}).")


def submit(file_id: int, action: str = FINAL_ACTION) -> dict:
    """Step 2 (Submit One Candidate): perform the real, single upload.

    Requires ``action == FINAL_ACTION`` (the second, explicit confirmation — the
    first click only prepares). Enforces every final guard, then via the portal
    session: navigate to FTC, set the file, click Upload ONCE, record
    ``portal_upload_started_at``, then match the history row and resolve status.
    """
    if action != FINAL_ACTION:
        return {
            "ok": False,
            "error": "Use 'prepare_live_upload' first; this is the final submit action.",
        }

    res = validate_generated_file(file_id)
    if not res["ok"]:
        return {"ok": False, "errors": res["errors"]}
    gf = res["file"]
    file_path = gf["file_path"]

    live = database.get_live_upload_for_file(file_id)
    if not live:
        return {"ok": False, "error": "Live upload record not found; prepare first."}

    # Duplicate submission safety (defensive even though prepare() checks).
    if live.get("flow_status") == PROC_STATUS["SUBMITTED"]:
        return {"ok": False, "error": ALREADY_SUBMITTED_MSG, "duplicate": True}

    live_id = live["live_upload_id"]

    # Re-check candidate_count == 1 immediately before selecting the file.
    try:
        if int(gf["candidate_count"]) != 1:
            raise LiveUploadError(LIVE_ONLY_ONE_MSG)
    except LiveUploadError:
        raise
    except (TypeError, ValueError):
        raise LiveUploadError(LIVE_ONLY_ONE_MSG)

    session = _get_session()
    if session.status != esampark.CONNECTED:
        return {"ok": False, "error": "eSampark is not connected.", "live_upload_id": live_id}

    try:
        assert_live_mode()
        session.navigate_to_ftc()
    except esampark.PortalAutomationError as exc:
        return {"ok": False, "error": PORTAL_LAYOUT_CHANGED_MSG if "Could not locate menu" in str(exc)
                else str(exc), "live_upload_id": live_id}
    except LiveUploadError as exc:
        return {"ok": False, "error": str(exc), "live_upload_id": live_id}

    # Fence: if the FTC page shape changed after navigation, STOP and never guess.
    try:
        if not _ftc_ready(session):
            database.update_live_upload(live_id, flow_status=PROC_STATUS["UNKNOWN"],
                                        error_message=PORTAL_LAYOUT_CHANGED_MSG)
            database.record_portal_audit(event="emergency_stop", status="error",
                                         generated_file_id=file_id,
                                         detail=PORTAL_LAYOUT_CHANGED_MSG)
            return {"ok": False, "error": PORTAL_LAYOUT_CHANGED_MSG, "live_upload_id": live_id}
    except Exception:  # noqa: BLE001
        database.update_live_upload(live_id, flow_status=PROC_STATUS["UNKNOWN"],
                                    error_message=PORTAL_LAYOUT_CHANGED_MSG)
        return {"ok": False, "error": PORTAL_LAYOUT_CHANGED_MSG, "live_upload_id": live_id}

    # THE final guard just before the Upload click.
    try:
        _final_submit_guard(file_id, file_path, session)
    except LiveUploadError as exc:
        return {"ok": False, "error": str(exc), "live_upload_id": live_id}

    now = _now()
    database.update_live_upload(live_id, flow_status=PROC_STATUS["STARTED"],
                                started_at=now)

    # Single Upload click (no automatic retry). Record it, then let history tell.
    try:
        session.upload_workbook(file_path)
    except esampark.PortalAutomationError as exc:
        database.update_live_upload(live_id, error_message=str(exc))
        database.record_portal_audit(event="upload_submit_failed", status="error",
                                     generated_file_id=file_id, detail=str(exc))
        return {"ok": False, "error": str(exc), "live_upload_id": live_id}

    database.update_live_upload(live_id, flow_status=PROC_STATUS["SUBMITTED"],
                                submitted_at=_now(),
                                portal_status=esampark.PROCESSING,
                                portal_status_raw="Submitted")
    database.record_portal_audit(event="upload_started", status="info",
                                 generated_file_id=file_id)
    database.record_portal_audit(event="upload_submitted", status="info",
                                 generated_file_id=file_id,
                                 detail=f"portal_upload_started_at={now}")

    # Best-effort: open history + match our row, then resolve status/error.
    return _poll_and_resolve(live_id, gf, file_id)


def _get_session():
    from app.portal import service as _svc
    return _svc._get_session()


def _ftc_ready(session) -> bool:
    """Robust FTC readiness: upload control + Self Onboarding context present.

    Uses the centralized selectors helper so we never guess during live mode.
    """
    from app.portal import selectors
    return selectors.ftc_page_confirmed(session.page)


def _poll_and_resolve(live_id: int, gf: dict, file_id: int) -> dict:
    """Open Upload History, match THIS file's row, and resolve final status.

    Matching priority: portal reference id -> exact filename -> upload time
    window (most-recent fallback). Never assumes the first row is ours.
    """
    session = _get_session()
    if session.status != esampark.CONNECTED:
        return {"ok": True, "live_upload_id": live_id, "portal_status": esampark.PROCESSING,
                "message": "Upload submitted; final portal status still pending."}
    try:
        session.open_upload_history()
    except esampark.PortalAutomationError:
        return {"ok": True, "live_upload_id": live_id, "portal_status": esampark.PROCESSING,
                "message": "Upload submitted; could not open history yet."}

    row = session.find_upload_row(reference_id=None, filename=gf["filename"],
                                  prefer_recent=True)
    if not row:
        return {"ok": True, "live_upload_id": live_id, "portal_status": esampark.PROCESSING,
                "message": "Upload submitted; history row not matched yet."}

    database.update_live_upload(live_id, flow_status=PROC_STATUS["HISTORY_MATCHED"],
                                portal_status_raw=row.get("status_raw", ""),
                                portal_reference=row.get("reference_id"))
    database.record_portal_audit(event="history_row_matched", status="info",
                                 generated_file_id=file_id)

    raw = row.get("status_raw", "")
    mapped = _map_status(raw)
    counts = _extract_counts(raw)
    database.update_live_upload(live_id, portal_status=mapped,
                                success_count=counts["success"],
                                failed_count=counts["failed"],
                                total_count=counts["total"])

    _apply_final(live_id, gf, file_id, mapped, counts, raw)
    return {
        "ok": True,
        "live_upload_id": live_id,
        "portal_status": mapped,
        "portal_status_raw": raw,
        "reference_id": row.get("reference_id"),
        "success_count": counts["success"],
        "failed_count": counts["failed"],
        "total_count": counts["total"],
    }


# ── Status / counts / result handling ───────────────────────────────────────


def _map_status(raw_text: str) -> str:
    """Map raw portal status text to internal vocabulary.

    Supports the portal wording already observed: 'Completed' and
    'Completed With Errors'. We never invent new wording; we map what exists.
    """
    low = (raw_text or "").lower()
    if not low:
        return esampark.PROCESSING
    if any(k in low for k in esampark.selectors.UPLOAD_STATUS_MARKERS["partial_failure"]):
        return esampark.PARTIAL_FAILURE
    if any(k in low for k in esampark.selectors.UPLOAD_STATUS_MARKERS["failed"]):
        return esampark.FAILED
    if any(k in low for k in esampark.selectors.UPLOAD_STATUS_MARKERS["processing"]):
        return esampark.PROCESSING
    if any(k in low for k in esampark.selectors.UPLOAD_STATUS_MARKERS["uploading"]):
        return esampark.UPLOADING
    if any(k in low for k in esampark.selectors.UPLOAD_STATUS_MARKERS["success"]):
        return esampark.SUCCESS
    return esampark.UNKNOWN


# Labels the real portal may prefix counts with (kept verbatim here).
_COUNT_LABELS = (
    "success count", "successful count", "success",
    "failed count", "failure count", "failed",
    "total count", "total",
)


def _extract_counts(raw_text: str) -> dict:
    """Best-effort parse of Success/Failed/Total counts from a history row.

    Returns ``{"success", "failed", "total"}`` with ints (None when absent).
    Only matches our known labels; never guesses numbers from foreign text.
    """
    import re
    text = (raw_text or "").lower()
    result = {}
    for label in _COUNT_LABELS:
        m = re.search(rf"{re.escape(label)}\s*[:=]?\s*(\d+)", text)
        if m and label.startswith("success"):
            result.setdefault("success", int(m.group(1)))
        elif m and label.startswith("failed"):
            result.setdefault("failed", int(m.group(1)))
        elif m and label.startswith("total"):
            result.setdefault("total", int(m.group(1)))
    return {
        "success": result.get("success"),
        "failed": result.get("failed"),
        "total": result.get("total"),
    }


def _apply_final(live_id: int, gf: dict, file_id: int, mapped: str,
                 counts: dict, raw_text: str) -> None:
    """Persist final status + download relevant result/error file for THIS row."""
    if mapped == esampark.SUCCESS and counts.get("success") == 1 and not counts.get("failed"):
        database.update_live_upload(live_id, flow_status=PROC_STATUS["SUCCESS"],
                                    portal_status=esampark.SUCCESS)
        database.record_portal_audit(event="final_status", status="info",
                                     generated_file_id=file_id, detail="Completed / success=1")
        _mark_candidate(gf, success=True, remarks="")
        return

    if mapped in (esampark.PARTIAL_FAILURE, esampark.FAILED) or counts.get("failed"):
        database.update_live_upload(live_id, flow_status=PROC_STATUS["FAILED"],
                                    portal_status=mapped,
                                    portal_status_raw=raw_text)
        database.record_portal_audit(event="final_status", status="warning",
                                     generated_file_id=file_id,
                                     detail=f"{mapped} / failed={counts.get('failed')}")
        _download_error(live_id, gf, file_id)
        return

    # Ambiguous / still processing: leave as-is (no invented status).
    database.update_live_upload(live_id, flow_status=PROC_STATUS["UNKNOWN"])


def _download_error(live_id: int, gf: dict, file_id: int) -> None:
    """Download ONLY the current row's error workbook, save + parse it.

    Never touches historical download links (the matched row is current).
    """
    session = _get_session()
    if session.status != esampark.CONNECTED:
        return
    try:
        data = session.download_result_or_error("error")
    except Exception:  # noqa: BLE001
        return
    if not data:
        return

    rel = gf.get("date_folder") or datetime.now().strftime("%Y-%m-%d")
    errors_dir = generation.get_output_base_dir() / rel / "errors"
    errors_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_file(errors_dir, gf["filename"], "FAILED")
    try:
        dest.write_bytes(data)
    except Exception:  # noqa: BLE001
        return

    database.update_live_upload(live_id, error_file_path=str(dest))
    database.record_portal_audit(event="result_downloaded", status="info",
                                 generated_file_id=file_id)

    remarks = _extract_creation_remarks(data, gf)
    if remarks is not None:
        database.update_live_upload(live_id, creation_remarks=remarks)
    _mark_candidate(gf, success=False, remarks=remarks or "")


def _unique_file(base, orig_filename: str, suffix: str) -> Path:
    stem = Path(orig_filename).stem
    candidate = base / f"{stem}_{suffix}.xlsx"
    counter = 1
    while candidate.exists():
        candidate = base / f"{stem}_{suffix}_{counter}.xlsx"
        counter += 1
    return candidate


def _extract_creation_remarks(data, gf: dict) -> Optional[str]:
    """Parse the error workbook for the candidate's Creation Remarks."""
    from app import result_parser as rp
    try:
        candidates = database.get_candidates_for_generated_file(gf["file_id"])
        parsed = rp.parse_workbook(data)
        matched = rp.match_parsed_rows(parsed, candidates)
    except rp.ResultParseError:
        return None
    if not matched["matches"]:
        return None
    return matched["matches"][0].get("portal_remarks") or None


def _mark_candidate(gf: dict, success: bool, remarks: str) -> None:
    candidates = database.get_candidates_for_generated_file(gf["file_id"])
    for c in candidates:
        status = esampark.PORTAL_UPLOADED if success else esampark.FAILED
        database.update_candidate(c["candidate_id"], {
            "portal_status": status,
            "portal_remarks": remarks,
        })
        database.upsert_daily_master({
            "candidate_id": c["candidate_id"],
            "portal_status": status,
            "portal_remarks": remarks,
        })


# ── Status page payload ──────────────────────────────────────────────────────


def report(file_id: int) -> dict:
    """Full live-test status page payload for a generated file (or latest)."""
    live = database.get_live_upload_for_file(file_id) if file_id else None
    if live is None and file_id:
        gf = database.get_generated_file(file_id)
        if gf:
            live = database.get_live_upload_for_file(file_id)
    if not live:
        return {"ok": True, "found": False, "record": None,
                "safety": safety_status()}
    gf = database.get_generated_file(live["generated_file_id"])
    candidate = database.get_candidate(live["candidate_id"]) if live["candidate_id"] else None
    return {
        "ok": True,
        "found": True,
        "safety": safety_status(),
        "record": live,
        "generated_file": _file_review_payload(gf) if gf else None,
        "candidate": _candidate_review_payload(candidate) if candidate else None,
        "creation_remarks": live.get("creation_remarks"),
        "result_file_path": live.get("result_file_path"),
        "error_file_path": live.get("error_file_path"),
    }


def list_reports(limit: int = 50) -> dict:
    return {"uploads": database.list_live_uploads(limit)}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
