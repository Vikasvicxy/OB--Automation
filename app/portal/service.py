"""High-level eSampark orchestration used by the FastAPI routes (Stage 4).

This layer:
* owns the "Portal Status" state machine (Not Connected / Connected /
  Login Required / Manual Verification Required / Error);
* owns the per-upload state machine (Uploading / Processing / Success /
  Failed / Partial Failure / Unknown);
* enforces duplicate-upload prevention using database state;
* links automation events + downloaded result/error files back to
  ``generated_files`` / ``portal_uploads``;
* records a non-sensitive automation audit trail.

It never stores credentials, never writes full Aadhaar / full address to logs,
and never bypasses CAPTCHA/OTP/MFA.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from app import database
from app import generation
from app import result_parser
from app.portal import esampark

# Re-export status vocabulary for callers.
NOT_CONNECTED = esampark.NOT_CONNECTED
CONNECTED = esampark.CONNECTED
LOGIN_REQUIRED = esampark.LOGIN_REQUIRED
MANUAL_VERIFICATION = esampark.MANUAL_VERIFICATION
ERROR = esampark.ERROR

UPLOADING = esampark.UPLOADING
PROCESSING = esampark.PROCESSING
SUCCESS = esampark.SUCCESS
FAILED = esampark.FAILED
PARTIAL_FAILURE = esampark.PARTIAL_FAILURE
UNKNOWN = esampark.UNKNOWN

# Sentinel for "not yet started".
NOT_SUBMITTED = "Not Submitted"

_session: Optional["esampark.PortalSession"] = None


def _get_session():
    global _session
    if _session is None:
        _session = esampark.PortalSession()
    elif _session.page is not None and _session.page.is_closed():
        _session.close()
        _session = esampark.PortalSession()
    return _session


def close_session() -> None:
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:  # noqa: BLE001
            pass
    _session = None


# ── Portal status ─────────────────────────────────────────────────────────────


def portal_status() -> dict:
    """Return the current portal status for the UI.

    Never triggers a network call; reflects the last known state. If credentials
    are not configured the status is ``Login Required`` with ``configured=False``.
    """
    s = _get_session()
    configured = esampark.credentials_configured()
    if s.status == CONNECTED and (s.page is None or s.page.is_closed()):
        s.status = NOT_CONNECTED
    if s.status in (NOT_CONNECTED,) and not configured:
        s.status = LOGIN_REQUIRED
    return {
        "status": s.status,
        "configured": configured,
        "username": s.username,
        "session_saved": esampark.has_saved_session(),
    }


def connect() -> dict:
    """Establish an eSampark session.

    Tries the saved session first; if it fails or is absent, performs an LDAP
    login. Returns a status dict. May set ``manual_verification`` when CAPTCHA /
    OTP / MFA is encountered — the caller must pause and never bypass it.
    """
    if not esampark.credentials_configured() and not esampark.has_saved_session():
        return {
            "status": LOGIN_REQUIRED,
            "message": "eSampark credentials are not configured.",
        }
    s = _get_session()
    database.record_portal_audit(event="login_started", status="info")
    try:
        status = s.login()
        database.record_portal_audit(event="login_success" if status == CONNECTED
                                     else "login_manual_verification",
                                     status=status.lower())
        if status == MANUAL_VERIFICATION:
            return {
                "status": MANUAL_VERIFICATION,
                "message": "Manual verification required in eSampark.",
            }
        if status == CONNECTED:
            return {"status": CONNECTED, "message": "Connected to eSampark."}
        return {"status": status, "message": f"eSampark status: {status}."}
    except esampark.PortalCredentialsError as exc:
        database.record_portal_audit(event="login_failed", status="error",
                                     detail=str(exc))
        return {"status": LOGIN_REQUIRED, "message": str(exc)}
    except esampark.PortalAutomationError as exc:
        database.record_portal_audit(event="login_failed", status="error",
                                     detail=str(exc))
        return {"status": ERROR, "message": str(exc)}


def reconnect() -> dict:
    """Force a fresh session (close existing context, then connect)."""
    close_session()
    return connect()


def diagnostic() -> dict:
    """Safe Stage-4B diagnostic for the UI: connected/authenticated + which live
    portal elements have been verified. Never exposes credentials or cookies."""
    s = _get_session()
    base = portal_status()
    diag = s.diagnostic()
    diag["status"] = base["status"]
    diag["configured"] = base["configured"]
    diag["session_saved"] = base["session_saved"]
    return diag


# ── Upload ────────────────────────────────────────────────────────────────────


def resolve_generated_file(file_id: int) -> dict:
    """Validate that ``file_id`` refers to a Stage 3 generated file.

    Returns a dict with either ``ok`` (True) and the file record, or ``ok``
    (False) with a reason. Only files already generated by Stage 3 may be
    uploaded.
    """
    gf = database.get_generated_file(file_id)
    if gf is None:
        return {"ok": False, "reason": "Generated file not found."}
    path = Path(gf.get("file_path") or "")
    if not path.exists():
        return {"ok": False, "reason": f"Generated file does not exist on disk: {path.name}", "file": gf}
    if path.suffix.lower() != ".xlsx":
        return {"ok": False, "reason": "Only .xlsx workbooks can be uploaded.", "file": gf}
    if (gf.get("generation_status") or "").lower() not in ("generated", "success"):
        return {"ok": False,
                "reason": "Generation was not successful for this file.",
                "file": gf}
    if int(gf.get("candidate_count") or 0) <= 0:
        return {"ok": False, "reason": "Generated file has no candidates.", "file": gf}
    if database.get_batch(gf.get("batch_id")) is None:
        return {"ok": False, "reason": "Linked batch does not exist.", "file": gf}
    return {"ok": True, "file": gf}


def existing_submission(file_id: int) -> Optional[dict]:
    """Return the most recent portal_uploads record for a generated file, if any."""
    return database.get_portal_upload_for_file(file_id)


def upload_generated_file(file_id: int, confirm: bool = False) -> dict:
    """Submit a generated file to eSampark (no network is required to create the
    pending record; the actual Playwright interaction may follow).

    Duplicate prevention: if this ``file_id`` was already submitted and
    ``confirm`` is False, return a block/warning instead of uploading again.
    """
    resolved = resolve_generated_file(file_id)
    if not resolved["ok"]:
        return {"ok": False, "error": resolved["reason"]}

    gf = resolved["file"]
    existing = existing_submission(file_id)
    if existing and not confirm:
        return {
            "ok": False,
            "error": "This file has already been submitted to eSampark.",
            "duplicate": True,
            "existing": {
                "portal_upload_id": existing["portal_upload_id"],
                "portal_status": existing.get("portal_status"),
                "portal_status_raw": existing.get("portal_status_raw"),
                "started_at": existing.get("started_at"),
                "completed_at": existing.get("completed_at"),
            },
        }

    # If it's a retry (explicit), keep the same record updated.
    if existing:
        upload_id = existing["portal_upload_id"]
        database.update_portal_upload(upload_id, started_at=_now(),
                                      portal_status=UPLOADING,
                                      portal_status_raw="", completed_at=None)
    else:
        upload_id = database.create_portal_upload(
            generated_file_id=file_id,
            batch_id=gf["batch_id"],
            filename=gf["filename"],
            started_at=_now(),
            portal_status=UPLOADING,
        )
    database.update_generated_file(file_id, portal_status=UPLOADING)
    database.record_portal_audit(event="upload_started", status="info",
                                 generated_file_id=file_id)

    # Attempt the portal interaction — only when credentials/session allow.
    result = {"ok": True, "upload_id": upload_id, "portal_status": PROCESSING}
    try:
        from app.portal import esampark as es
        if not (es.credentials_configured() or es.has_saved_session()):
            raise es.PortalCredentialsError(
                "eSampark credentials are not configured."
            )
        s = _get_session()
        s._require_connected()
        s.navigate_to_ftc()
        s.upload_workbook(gf["file_path"])
        database.update_portal_upload(upload_id, portal_status=PROCESSING,
                                      portal_status_raw="Submitted")
        database.record_portal_audit(event="upload_submitted", status="info",
                                     generated_file_id=file_id)
    except es.PortalCredentialsError as exc:
        # Kept pending; user can configure credentials and retry.
        database.update_portal_upload(upload_id, portal_status=UNKNOWN,
                                      portal_status_raw=str(exc),
                                      error_message=str(exc))
        return {"ok": False, "upload_id": upload_id, "error": str(exc),
                "portal_status": LOGIN_REQUIRED}
    except es.PortalAutomationError as exc:
        database.update_portal_upload(upload_id, portal_status=UNKNOWN,
                                      portal_status_raw=str(exc),
                                      error_message=str(exc))
        return {"ok": False, "upload_id": upload_id, "error": str(exc),
                "portal_status": ERROR}

    # Poll upload history for a final status within a bounded window.
    final = _poll_upload(upload_id, gf, upload_id)
    if final:
        result.update(final)
    else:
        result.update({
            "ok": True,
            "upload_id": upload_id,
            "portal_status": PROCESSING,
            "message": "Upload submitted but final portal status is still pending.",
        })
    return result


def _poll_upload(upload_id: int, gf: dict, file_id: int) -> Optional[dict]:
    """Best-effort poll of upload history (bounded, non-infinite).

    Returns a result dict, or None if the status did not resolve.
    """
    s = _get_session()
    if s.status != CONNECTED:
        return None
    try:
        s.open_upload_history()
    except esampark.PortalAutomationError:
        return None

    row = s.find_upload_row(reference_id=None, filename=gf["filename"],
                            prefer_recent=True)
    if not row:
        return None

    mapped = _map_status_text(row.get("status_raw", ""))
    database.update_portal_upload(upload_id, portal_status=mapped,
                                  portal_status_raw=row.get("status_raw", ""),
                                  portal_reference=row.get("reference_id"),
                                  completed_at=_now())
    if mapped in (SUCCESS, FAILED, PARTIAL_FAILURE):
        _handle_final_state(upload_id, gf, mapped)
        return {"ok": True, "upload_id": upload_id, "portal_status": mapped,
                "portal_status_raw": row.get("status_raw", ""),
                "reference_id": row.get("reference_id")}
    return {"ok": True, "upload_id": upload_id, "portal_status": mapped,
            "portal_status_raw": row.get("status_raw", ""),
            "reference_id": row.get("reference_id")}


def _map_status_text(raw_text: str) -> str:
    text = (raw_text or "").lower()
    if not text:
        return PROCESSING
    if any(k in text for k in esampark.selectors.UPLOAD_STATUS_MARKERS["partial_failure"]):
        return PARTIAL_FAILURE
    if any(k in text for k in esampark.selectors.UPLOAD_STATUS_MARKERS["failed"]):
        return FAILED
    if any(k in text for k in esampark.selectors.UPLOAD_STATUS_MARKERS["processing"]):
        return PROCESSING
    if any(k in text for k in esampark.selectors.UPLOAD_STATUS_MARKERS["uploading"]):
        return UPLOADING
    if any(k in text for k in esampark.selectors.UPLOAD_STATUS_MARKERS["success"]):
        return SUCCESS
    return UNKNOWN


def _handle_final_state(upload_id: int, gf: dict, status: str) -> None:
    """Download + parse result/error file and update candidates individually."""
    if status == SUCCESS:
        _try_download_and_apply(upload_id, gf, kind="result")
    elif status in (FAILED, PARTIAL_FAILURE):
        _try_download_and_apply(upload_id, gf, kind="error")


def _try_download_and_apply(upload_id: int, gf: dict, kind: str) -> None:
    """Download a result/error workbook, save it, parse and apply to candidates."""
    s = _get_session()
    if s.status != CONNECTED:
        return
    try:
        data = s.download_result_or_error(kind)
    except Exception:  # noqa: BLE001
        return
    if not data:
        return

    # Persist into the Stage 3 errors/ or results/ date folder.
    dest_subdir = "errors" if kind == "error" else "results"
    rel = gf.get("date_folder") or datetime.now().strftime("%Y-%m-%d")
    base = generation.get_output_base_dir() / rel / dest_subdir
    base.mkdir(parents=True, exist_ok=True)
    suffix = "FAILED" if kind == "error" else "RESULT"
    file_path = _unique_file(base, gf["filename"], suffix)

    try:
        file_path.write_bytes(data)
    except Exception:  # noqa: BLE001
        return

    if kind == "error":
        database.update_portal_upload(upload_id, error_file_path=str(file_path))
        database.update_generated_file(gf["file_id"],
                                       portal_status=FAILED,
                                       portal_failure_path=str(file_path))
    else:
        database.update_portal_upload(upload_id, result_file_path=str(file_path))
        database.update_generated_file(gf["file_id"],
                                       portal_status=SUCCESS,
                                       portal_result_path=str(file_path))
    database.record_portal_audit(event="result_downloaded", status="info",
                                 generated_file_id=gf["file_id"])

    # Parse + apply candidate statuses.
    apply_result_file(str(file_path), gf["file_id"])


def _unique_file(base: Path, orig_filename: str, suffix: str) -> Path:
    stem = Path(orig_filename).stem
    candidate = base / f"{stem}_{suffix}.xlsx"
    counter = 1
    while candidate.exists():
        candidate = base / f"{stem}_{suffix}_{counter}.xlsx"
        counter += 1
    return candidate


# ── Import (manual fallback) ──────────────────────────────────────────────────


def apply_result_file(source, generated_file_id: Optional[int]) -> dict:
    """Parse a result/error workbook and update candidates to match.

    ``source`` may be a file path or raw bytes. This is shared by the automatic
    download path and the manual ``Import eSampark Result`` feature.
    """
    candidates = database.get_candidates_for_generated_file(generated_file_id) \
        if generated_file_id else database.list_candidates()
    try:
        parsed = result_parser.parse_workbook(source)
    except result_parser.ResultParseError as exc:
        return {"ok": False, "error": str(exc)}
    matched = result_parser.match_parsed_rows(parsed, candidates)

    updated = 0
    failed = 0
    for m in matched["matches"]:
        database.update_candidate(m["candidate_id"], {
            "portal_status": m["portal_status"],
            "portal_remarks": m["portal_remarks"],
        })
        # keep the daily-master mirror in sync
        database.upsert_daily_master({
            "candidate_id": m["candidate_id"],
            "portal_status": m["portal_status"],
            "portal_remarks": m["portal_remarks"],
        })
        updated += 1
        if m["portal_status"] == result_parser.FAILED:
            failed += 1

    if generated_file_id:
        database.record_portal_audit(event="result_parsed", status="info",
                                     generated_file_id=generated_file_id,
                                     detail=f"{updated} candidates updated")
    return {
        "ok": True,
        "rows_parsed": len(parsed),
        "matched": len(matched["matches"]),
        "updated": updated,
        "failed": failed,
        "unmatched": len(matched["unmatched"]),
        "partial_failure": 0 < failed < updated,
    }


def check_upload_status(upload_id: int) -> dict:
    """Re-read upload history for an existing upload and update its status."""
    up = database.get_portal_upload(upload_id)
    if up is None:
        return {"ok": False, "error": "Upload record not found."}
    gf = database.get_generated_file(up["generated_file_id"])
    if gf is None:
        return {"ok": False, "error": "Linked generated file not found."}
    s = _get_session()
    if s.status != CONNECTED:
        return {"ok": False, "error": "eSampark is not connected.",
                "portal_status": s.status}
    try:
        s.open_upload_history()
        row = s.find_upload_row(reference_id=up.get("portal_reference"),
                                filename=gf["filename"], prefer_recent=True)
    except esampark.PortalAutomationError as exc:
        return {"ok": False, "error": str(exc)}
    if not row:
        return {"ok": False, "error": "Upload not found in eSampark history.",
                "pending": True}
    mapped = _map_status_text(row.get("status_raw", ""))
    database.update_portal_upload(upload_id, portal_status=mapped,
                                  portal_status_raw=row.get("status_raw", ""),
                                  portal_reference=row.get("reference_id") or up.get("portal_reference"),
                                  completed_at=_now() if mapped in (SUCCESS, FAILED, PARTIAL_FAILURE) else None)
    if mapped in (SUCCESS, FAILED, PARTIAL_FAILURE):
        _handle_final_state(upload_id, gf, mapped)
    return {"ok": True, "upload_id": upload_id, "portal_status": mapped,
            "portal_status_raw": row.get("status_raw", ""),
            "reference_id": row.get("reference_id")}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
