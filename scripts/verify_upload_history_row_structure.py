r"""Stage 4D — REAL eSampark UPLOAD HISTORY ROW STRUCTURE inspection (headed).

Goal (live, against the real authenticated portal)
--------------------------------------------------
Open the real eSampark portal in HEADED Chromium (reusing any saved
authenticated session so the operator does NOT have to log in again unless the
cookie session has expired), navigate to Onboarding -> Self Onboarding -> FTC ->
Upload History, then inspect the ACTUAL Upload History row/grid DOM.

We do NOT assume a filename column exists, nor that it is literally called
"Filename". We inspect the real column headings and cell structure and report
what the portal ACTUALLY exposes for each history row:
    * row / container selector
    * status selector / representation
    * filename / uploaded-file representation (or "NOT EXPOSED")
    * upload timestamp
    * action / download control
    * portal reference / request / batch ID

Then we determine the strongest AVAILABLE row-matching strategy (reference ID,
then timestamp+status, then filename if exposed, then other unique metadata).

Safety contract
---------------
* HEADED browser; the authenticated cookie session from ``auth_state.json`` is
  reused. Manual login only if the session has expired.
* SAME browser context reused throughout — never reopened.
* NEVER selects an Excel; NEVER clicks Upload / Submit / Import.
* DOM inspection only. We do NOT click or download an old candidate's file
  unnecessarily. If a result/error download only appears on Failed rows and
  there are no Failed rows currently visible, we report it as
  CONDITIONAL-NOT-VERIFIED and do NOT manufacture a failure.
* Only safe diagnostics logged: title, URL, tag, id, name, data-*, aria-label,
  column heading labels, structural class cues, and heavily-truncated samples
  ONLY for filename/status/timestamp-like cells. Cookies, tokens, passwords,
  full Aadhaar, and full addresses are never logged.

Usage
-----
    .\venv\Scripts\python.exe scripts\verify_upload_history_row_structure.py
Optional:
    $env:TEAMHR_PORTAL_HISTORY_WAIT="900"   # manual nav/open wait seconds
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.portal import esampark, selectors  # noqa: E402

DEFAULT_WAIT = int(os.environ.get("TEAMHR_PORTAL_HISTORY_WAIT", "900"))
DEBUG = "--debug" in sys.argv
TRY_AUTO_NAV = "--auto-nav" not in sys.argv

# Path-safe fragments of the portal URL / title so we can recognise pages without
# keeping the full URL in the final report.
REAL_URL = os.environ.get("ESAMPARK_URL") or "https://esampark.beeforce.in/login"

# Safe heading candidates per column (kept in selectors.py, referenced via
# HISTORY_*_HEADER_CANDIDATES if present, else local fallback).
FILENAME_HEADERS = ["File Name", "Filename", "File", "Document", "Upload File",
                    "Uploaded File", "Excel", "Request", "Batch"]
TIMESTAMP_HEADERS = ["Date", "Time", "Uploaded On", "Date Time", "Uploaded Date",
                     "Timestamp", "Date & Time", "Created On"]
STATUS_HEADERS = ["Status", "State", "Process Status", "Upload Status", "Error Status"]
REFERENCE_HEADERS = ["Request ID", "Reference", "Ref ID", "Batch ID", "Batch",
                     "Request No", "ID", "Reference No"]

FILE_RE = re.compile(r"\.(xlsx|xls|csv)$", re.I)


def log(msg: str) -> None:
    print(msg, flush=True)


# ── safe diagnostics (shared shape with other verifiers) ─────────────────────

def safe_attrs(loc) -> dict:
    out = {}
    try:
        out["tag"] = loc.evaluate("e => e.tagName")
    except Exception:  # noqa: BLE001
        pass
    for a in ("id", "name", "type", "role", "aria-label", "placeholder"):
        try:
            v = loc.get_attribute(a)
            if v and len(str(v)) < 80:
                out[a] = v
        except Exception:  # noqa: BLE001
            pass
    try:
        data = loc.evaluate(
            "e => Object.fromEntries("
            "Array.from(e.attributes).filter(a => a.name.startsWith('data-'))"
            ".map(a => [a.name, a.value]))"
        )
        out["data"] = {k: v for k, v in data.items() if len(str(v)) < 45}
    except Exception:  # noqa: BLE001
        pass
    return out


def probe(page, selectors_list, element_type="generic", timeout_ms=2000):
    for sel in selectors_list:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            return sel, safe_attrs(loc)
        except Exception:  # noqa: BLE001
            continue
    return None, None


def wait_seconds(page, seconds: float = 1.0) -> None:
    try:
        if page is None:
            time.sleep(seconds)
        else:
            page.wait_for_timeout(int(seconds * 1000))
    except Exception:  # noqa: BLE001
        try:
            time.sleep(seconds)
        except Exception:  # noqa: BLE001
            pass


def page_alive(page) -> bool:
    try:
        if page is None or page.is_closed():
            return False
        _ = page.url
        return True
    except Exception:  # noqa: BLE001
        return False


def _safe_url(page) -> str:
    try:
        return page.url
    except Exception:  # noqa: BLE001
        return "?"


def debug_shot(page, name: str) -> None:
    if not DEBUG:
        return
    try:
        shots = PROJECT_ROOT / "data" / "portal" / "debug"
        shots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shots / f"{int(time.time())}_{name}.png"))
    except Exception:  # noqa: BLE001
        pass


# ── authentication (reuse saved session; no forced re-login) ─────────────────

def _is_login_url(url: str) -> bool:
    path = (url or "").split("?")[0].split("#")[0].rstrip("/").lower()
    return any(seg in path for seg in (
        "/login", "login.aspx", "/signin", "/sign-in", "/sign_in",
        "/auth", "/authenticate", "/ldap", "/login.aspx",
    ))


def detect_authenticated(page) -> bool:
    if page is None or page.is_closed():
        return False
    if _any_authenticated_nav_visible(page):
        return True
    try:
        login_url_gone = not _is_login_url(page.url)
    except Exception:  # noqa: BLE001
        login_url_gone = False
    if login_url_gone:
        for sel in selectors.LOGIN_FORM_SELECTORS:
            try:
                if page.locator(sel).first.is_visible(timeout=700):
                    return False
            except Exception:  # noqa: BLE001
                continue
        return True
    return False


def _any_authenticated_nav_visible(page) -> bool:
    for sel in selectors.AUTHENTICATED_NAV_SIGNALS:
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _live_pages(context):
    """Return all currently-alive pages in the Playwright context.

    Some portals open a NEW tab on successful login and close the original
    window/page. Watching only the original page then shows a false
    "window closed". Scanning the whole context lets us follow the session to
    whichever tab actually holds the authenticated content.
    """
    if context is None:
        return []
    try:
        return [p for p in context.pages if p and not p.is_closed()]
    except Exception:  # noqa: BLE001
        return []


def detect_authenticated_in_context(context) -> bool:
    """Detect an authenticated session across ALL live pages of the context."""
    for p in _live_pages(context):
        if detect_authenticated(p):
            return True
    return False


def context_alive(context) -> bool:
    """True when the Playwright browser context is still open (any page or not)."""
    try:
        if context is None:
            return False
        # Any live page proves the context is alive.
        if _live_pages(context):
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def wait_until_authenticated(session, timeout_s: int) -> bool:
    """Poll across every live tab/context for an authenticated session.

    The same headed browser/context is reused. If the emitted page closed (e.g.
    login opened a new tab), we still follow the session via the live pages in
    the context. If the user closes the whole browser, we stop cleanly.
    """
    deadline = time.time() + timeout_s
    attempts = 0
    ctx = session._context
    while time.time() < deadline:
        if not context_alive(ctx):
            log("[Stage 4D] Browser window was closed before authentication "
                "was confirmed. Stopping cleanly.")
            return False
        if detect_authenticated_in_context(ctx):
            # Promote an authenticated tab to the active page if the original died.
            for p in _live_pages(ctx):
                try:
                    if detect_authenticated(p) and session.page.is_closed():
                        session.page = p
                except Exception:  # noqa: BLE001
                    continue
            return True
        attempts += 1
        if attempts % 5 == 1 or DEBUG:
            alive = [_safe_url(p) for p in _live_pages(ctx)]
            log(f"[Stage 4D] Waiting for auth ({int(deadline - time.time())}s left) "
                f"pages={alive}")
        wait_seconds(None, 3)
    return False


# ── navigation ───────────────────────────────────────────────────────────────

def click_any(session, page, selectors_list, timeout_ms=4000) -> bool:
    for sel in selectors_list:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.click()
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def collect_nav_labels(page) -> list:
    """Collect visible navigation/menu labels to reconcile nav selectors.

    Reads menu/anchor/button/span text only (structural, truncated, safe). Used
    to discover the portal's actual Onboarding / Self Onboarding / FTC / Upload
    History wording so we never assume exact text.
    """
    found = []
    for sel in ('nav a', 'nav li', 'a', 'button', '[role="menuitem"]',
                '[class*="menu" i] a', '[class*="menu" i] li'):
        try:
            locs = page.locator(sel)
            n = min(locs.count(), 300)
            for i in range(n):
                try:
                    t = (locs.nth(i).inner_text(timeout=500) or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                if not t:
                    continue
                first = t.splitlines()[0].strip()
                if first and len(first) <= 60 and first not in found:
                    found.append(first)
        except Exception:  # noqa: BLE001
            continue
    return found


def navigate_toward_history(session, page, report) -> bool:
    """Walk Onboarding -> Self Onboarding -> FTC -> Upload History.

    Prefers reconciled/verified selectors; if a hop cannot be auto-located we do
    NOT guess — we note it and continue (the user may reach Upload History by any
    route). Never uploads anything.
    """
    hops = (
        ("Onboarding", selectors.NAV_ONBOARDING_SELECTORS, "onboarding"),
        ("Self Onboarding", selectors.NAV_SELF_ONBOARDING_SELECTORS, "self_onboarding"),
        ("FTC", selectors.NAV_FTC_SELECTORS, "ftc"),
    )
    for label, sels, key in hops:
        if report.get(f"{label} nav") == "YES":
            continue
        clicked = click_any(session, page, sels, timeout_ms=2500)
        session.mark_verified(key, bool(clicked))
        report[f"{label} nav"] = "YES" if clicked else "NO"
        if clicked:
            wait_seconds(page, 1)
        log(f"[Stage 4D] Nav {label}: {report[f'{label} nav']}")
        debug_shot(page, f"nav_{key}")
    report["FTC confirmed"] = report.get("FTC nav", "NO")
    # Now open Upload History.
    opened = click_any(session, page, selectors.UPLOAD_HISTORY_SELECTORS, timeout_ms=3000)
    report["Upload History nav found"] = "YES" if opened else "NO"
    if opened:
        wait_seconds(page, 2)
    log(f"[Stage 4D] Upload History nav found: {report['Upload History nav found']}")
    return _history_visible(page)


def _history_visible(page) -> bool:
    for sel in selectors.HISTORY_CONTAINER_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def wait_for_history(session, page, report, timeout_s: int) -> bool:
    if _history_visible(page):
        report["Upload History opened"] = "YES"
        return True
    log("\n" + "=" * 70)
    log("[Stage 4D] Please open Upload History manually in the browser "
        "(same window).")
    log("I will NOT upload anything. Watching this SAME context ...")
    log("=" * 70)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not page_alive(page):
            log("[Stage 4D] Browser closed before Upload History opened.")
            report["Upload History opened"] = "NO"
            return False
        if _history_visible(page):
            report["Upload History opened"] = "YES (manual)"
            return True
        if DEBUG:
            log(f"[Stage 4D] waiting for history ({int(deadline - time.time())}s left)")
        wait_seconds(page, 3)
    report["Upload History opened"] = "NO"
    log("[Stage 4D] Upload History was not opened within the wait window.")
    return False


# ── history row structure inspection ─────────────────────────────────────────

def _safe_sample(text: str, maxlen: int = 35) -> str:
    """Truncated, redacted sample — suppressed if it looks like candidate PII."""
    s = (text or "").strip()
    if not s:
        return ""
    low = s.lower()
    if re.search(r"\b\d{10,}\b", low):        # long numeric (aadhaar-like/contact)
        return "<redacted-number>"
    if re.search(r"[a-z0-9._%+-]+@", low):     # email
        return "<redacted-email>"
    if len(s) > maxlen:
        s = s[:maxlen] + "…"
    return s


def _looks_like_file(text: str) -> bool:
    return bool(FILE_RE.search((text or "").strip())) or ".xlsx" in text.lower()


def _looks_like_date(text: str) -> bool:
    return bool(
        re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", text)
        or re.search(r"\d{1,2}:\d{2}", text)
    )


def _row_text(page, row_loc) -> str:
    try:
        return (row_loc.inner_text(timeout=1200) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _first_visible_cell(page, row_loc, cell_selectors, label) -> dict:
    for sel in cell_selectors:
        try:
            cell = row_loc.locator(sel).first
            if not cell.is_visible(timeout=500):
                continue
            attrs = safe_attrs(cell)
            txt = (cell.inner_text(timeout=600) or "").strip()
            title = cell.get_attribute("title") or ""
            href = cell.get_attribute("href") or ""
            link_cnt = cell.locator("a").count()
            return {
                "found": True,
                "selector": sel,
                "tag": attrs.get("tag"),
                "text": _safe_sample(txt),
                "title": _safe_sample(title),
                "href_present": bool(href),
                "href": href or "",
                "href_safe": _safe_sample(Path(href).name if href else ""),
                "has_link": bool(link_cnt),
            }
        except Exception:  # noqa: BLE001
            continue
    return {"found": False}


def inspect_rows(page, report) -> None:
    """Inspect the real history DOM row-by-row (structure only, no downloads)."""
    row_sel, row_node = probe(page, selectors.HISTORY_ROW_SELECTORS, timeout_ms=1500)
    report["History row/container identified"] = "YES" if row_sel else "NO"
    report["row_primary"] = row_sel
    report["row_tag"] = (row_node or {}).get("tag")
    if not row_sel:
        log("[Stage 4D] No history row found to inspect.")
        return

    row_locs = page.locator(row_sel)
    total = row_locs.count()
    log(f"[Stage 4D] Rows found in container: {total}")
    report["row_count"] = total

    # Inspect up to first N rows for column structure.
    N = min(total, 5)
    filename_seen = False
    status_seen = False
    timestamp_seen = False
    reference_seen = False
    action_seen = False
    filename_desc = None
    status_desc = None
    timestamp_desc = None
    reference_desc = None
    action_desc = None

    for i in range(N):
        try:
            row = row_locs.nth(i)
            if not row.is_visible(timeout=500):
                continue
        except Exception:  # noqa: BLE001
            continue

        fn = _first_visible_cell(page, row, selectors.HISTORY_FILENAME_CELL_SELECTORS, "filename")
        st = _first_visible_cell(page, row, selectors.HISTORY_STATUS_CELL_SELECTORS, "status")
        ts = _first_visible_cell(page, row, selectors.HISTORY_TIMESTAMP_CELL_SELECTORS, "timestamp")
        rf = _first_visible_cell(page, row, selectors.HISTORY_REFERENCE_SELECTORS, "reference")
        ac = _first_visible_cell(page, row, selectors.HISTORY_ACTION_SELECTORS, "action")

        # Reference id: strongest signal is the download anchor's numeric id.
        ref_id = None
        if fn.get("href_present"):
            ref_id = selectors.ref_id_from_href(fn.get("href") or "")
        if not ref_id and rf.get("found"):
            ref_id = selectors.ref_id_from_href(rf.get("href") or "")

        # Status: fall back to row text markers when no clean status cell matches.
        row_text = _row_text(page, row) if not st.get("found") else ""
        if fn.get("found") and (_looks_like_file(fn.get("text") or "") or fn.get("href_present")):
            filename_seen = True
            filename_desc = filename_desc or fn
        if st.get("found") or esampark._map_row_status(row_text) != esampark.UNKNOWN:
            status_seen = True
            status_desc = status_desc or st
            status_desc = status_desc or {"found": True, "mapped": esampark._map_row_status(row_text)}
        if ts.get("found"):
            timestamp_seen = True
            timestamp_desc = timestamp_desc or ts
        if rf.get("found") or ref_id:
            reference_seen = True
            reference_desc = reference_desc or rf
            reference_desc = reference_desc or {"found": True, "ref_id": ref_id}
        if ac.get("found"):
            action_seen = True
            action_desc = action_desc or ac

        debug_shot(page, f"row_{i}")

    report["Filename field"] = "YES" if filename_seen else "NOT EXPOSED BY PORTAL"
    report["Status field found"] = "YES" if status_seen else "NO"
    report["Timestamp field"] = "YES" if timestamp_seen else "NOT PRESENT"
    report["Request/reference ID"] = "YES" if reference_seen else "NOT PRESENT"
    report["Result/error download control"] = _action_verdict(page)

    if filename_desc:
        report["filename_structure"] = filename_desc
    if status_desc:
        report["status_structure"] = status_desc
    if timestamp_desc:
        report["timestamp_structure"] = timestamp_desc
    if reference_desc:
        report["reference_structure"] = reference_desc
    if action_desc:
        report["action_structure"] = action_desc

    log(f"[Stage 4D] Filename exposed: {report['Filename field']} | "
        f"Status: {report['Status field found']} | "
        f"Timestamp: {report['Timestamp field']} | "
        f"Ref/ID: {report['Request/reference ID']}")


def _action_verdict(page) -> str:
    """Report the result/error download control presence, honestly.

    A plain file-download anchor (the uploaded Excel itself, e.g.
    ``.../download?id=...``) is NOT a result/error control — every row has that.
    A *dedicated* result/error workbook control only appears on rows in a
    failed/error/processing state. We do NOT click or download anything and we
    never manufacture a failure. So:
      * if a result/error-specific control is visible -> YES
      * else if history is visible but we only see the generic file download ->
        CONDITIONAL-NOT-VERIFIED (a result/error control likely exists but only
        shows on failed rows, none of which are currently visible)
      * else -> NO
    """
    # Dedicated result/error controls (per-row "Result"/"Error" download links).
    sel, _ = probe(page, selectors.RESULT_DOWNLOAD_SELECTORS
                   + selectors.ERROR_DOWNLOAD_SELECTORS, timeout_ms=1200)
    if sel:
        return "YES"
    # Generic uploaded-file download links are always present but are NOT
    # result/error controls, so they do not qualify for a YES verdict.
    if _history_visible(page):
        return "CONDITIONAL-NOT-VERIFIED"
    return "NO"


def _header_columns(page) -> dict:
    """Map heading labels -> column index/selector where determinable."""
    columns = {}
    for tag in ("th", "[role='columnheader']"):
        try:
            locs = page.locator(tag)
            n = min(locs.count(), 60)
            for i in range(n):
                try:
                    t = (locs.nth(i).inner_text(timeout=600) or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                if t and t not in columns.values():
                    columns[len(columns)] = t
        except Exception:  # noqa: BLE001
            continue
    return columns


def future_matching_strategy(report) -> str:
    """Return the exact future row-matching strategy in priority order.

    Based ONLY on what the real portal actually exposes (never assumes a filename
    column). 1 is the strongest signal actually present; later entries are used
    only as tie-breakers when the earlier one is ambiguous or still pending.
    """
    ref = report.get("Request/reference ID") == "YES"
    ts = report.get("Timestamp field") == "YES"
    st = report.get("Status field found") == "YES"
    fn = report.get("Filename field") == "YES"
    steps = []
    if ref:
        steps.append("1. portal reference/request ID (unique per upload)")
    else:
        steps.append("1. upload timestamp window (nearest recent row at upload time)")
    n = 2
    if ts and not ref:
        steps.append(f"{n}. upload timestamp + status")
        n += 1
    if st:
        steps.append(f"{n}. status field"); n += 1
    if fn:
        steps.append(f"{n}. filename (if exposed)")
    return " -> ".join(steps)


# ── verified persistence ─────────────────────────────────────────────────────

def _persist_verified_history(report: dict) -> bool:
    add = {}
    if report.get("row_primary"):
        add["HISTORY_ROW"] = report["row_primary"]
    for key, rname in (
        ("HISTORY_FILENAME_CELL", "filename_structure"),
        ("HISTORY_TIMESTAMP_CELL", "timestamp_structure"),
        ("HISTORY_STATUS_CELL", "status_structure"),
        ("HISTORY_REFERENCE", "reference_structure"),
        ("HISTORY_ACTION", "action_structure"),
    ):
        node = report.get(rname) or {}
        sel = node.get("selector")
        if sel:
            add[key] = sel

    path = PROJECT_ROOT / "app" / "portal" / "verified_selectors.py"
    try:
        import app.portal.verified_selectors as v
        existing = dict(v.VERIFIED)
    except Exception:  # noqa: BLE001
        existing = {}
    existing.update(add)

    order = ("LDAP_USER", "LDAP_PASSWORD", "LOGIN_SUBMIT", "NAV_ONBOARDING",
             "NAV_SELF_ONBOARDING", "NAV_FTC", "FILE_INPUT", "UPLOAD_SUBMIT",
             "UPLOAD_HISTORY", "RESULT_DOWNLOAD", "ERROR_DOWNLOAD",
             "HISTORY_CONTAINER", "HISTORY_ROW", "HISTORY_FILENAME_CELL",
             "HISTORY_TIMESTAMP_CELL", "HISTORY_STATUS_CELL", "HISTORY_REFERENCE",
             "HISTORY_ACTION", "HISTORY_SEARCH", "HISTORY_PAGINATION",
             "HISTORY_FILTER")
    lines = [
        "from __future__ import annotations",
        "",
        "# AUTO-GENERATED by TeamHR portal verifiers (Stage 4B/4C/4D) — do not edit by hand.",
        "# Live-verified eSampark selectors confirmed against the REAL portal DOM.",
        "# Consumed by app/portal/selectors.py (prepended as prioritized primaries).",
        "# No secrets are stored here.",
        "",
        "VERIFIED = {",
    ]
    for k in order:
        if k in existing:
            lines.append(f'    {k!r}: {existing[k]!r},')
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["Verified selectors integrated"] = "YES" if add else "NO"
    if add:
        log(f"[Stage 4D] Verified history selectors written -> {path}: {add}")
    else:
        log("[Stage 4D] No new verifiable history selectors to persist.")
    return bool(add)


# ── report ───────────────────────────────────────────────────────────────────

def _print_final(report: dict) -> None:
    print("\n" + "=" * 64)
    print("STAGE 4D — UPLOAD HISTORY ROW STRUCTURE (LIVE)")
    print("=" * 64)
    for key in (
        "Authenticated",
        "FTC confirmed",
        "Upload History opened",
        "History row/container identified",
        "Status field found",
        "Filename field",
        "Timestamp field",
        "Request/reference ID",
        "Result/error download control",
    ):
        print(f"{key:<42} {report.get(key, 'NO')}")
    print("-" * 64)
    print("Future row matching strategy:")
    print("  ", report.get("future_matching_strategy", "N/A"))
    print("-" * 64)
    print("Verified selectors integrated :",
          report.get("Verified selectors integrated", "NO"))
    print("Real Excel selected           : NO")
    print("Real candidate upload performed: NO")
    print("REAL_UPLOAD_ENABLED            : FALSE")
    print("=" * 64)
    print("Supplementary safe detail:")
    for sig in ("row_primary", "row_tag", "row_count", "filename_structure",
                "status_structure", "timestamp_structure", "reference_structure",
                "action_structure", "Onboarding nav", "Self Onboarding nav",
                "FTC nav", "Upload History nav found"):
        if report.get(sig):
            print(f"  {sig}: {report[sig]}")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    report = {"Authenticated": "NO"}

    session = esampark.PortalSession()
    log("\n[Stage 4D] Launching a HEADED Chromium browser "
        "(reusing any saved authenticated session) ...")
    session._open()  # headed; _storage_args() loads saved cookies
    page = session.page
    page.goto(REAL_URL, wait_until="domcontentloaded", timeout=60000)
    wait_seconds(page, 2)
    log(f"[Stage 4D] Opened (safe) : {_safe_url(page)}")
    log(f"[Stage 4D] Title          : {(page.title() or '')[:120]}")

    if not detect_authenticated_in_context(session._context):
        log("\n[Stage 4D] No usable session detected. Please log in manually in the")
        log(f"opened browser (up to {DEFAULT_WAIT}s). NO upload will happen.")
        if not wait_until_authenticated(session, DEFAULT_WAIT):
            log("[Stage 4D] Authentication not confirmed. Stopping cleanly.")
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
            report["Authenticated"] = "NO"
            _print_final(report)
            return 1
    # Promote to the authenticated page, then persist the FRESH cookies at once.
    session.page = next((p for p in _live_pages(session._context)
                         if detect_authenticated(p)), session.page)
    report["Authenticated"] = "YES"
    session.status = esampark.CONNECTED
    session._persist_session()  # cookies only — refresh the saved session now
    log("[Stage 4D] Authenticated session confirmed (fresh cookies persisted).")

    try:
        # Reconcile / collect nav labels first (centralized insight), but do not
        # depend on them to block progress.
        page = session.page  # may have been promoted to a fresh authenticated tab
        nav = collect_nav_labels(page)
        report["nav_labels_sample"] = nav[:25]
        if DEBUG:
            log(f"[Stage 4D] Nav labels sample: {nav[:25]}")

        navigate_toward_history(session, page, report)
        if not wait_for_history(session, page, report, DEFAULT_WAIT):
            log("[Stage 4D] Could not open Upload History; nothing to inspect.")
        else:
            wait_seconds(page, 2)
            report["History row/container identified"] = "NO"
            report["Status field found"] = "NO"
            report["Filename field"] = "NOT EXPOSED BY PORTAL"
            report["Timestamp field"] = "NOT PRESENT"
            report["Request/reference ID"] = "NOT PRESENT"
            report["Result/error download control"] = "NO"
            inspect_rows(page, report)
            report["future_matching_strategy"] = future_matching_strategy(report)
            _persist_verified_history(report)
    finally:
        log("\n[Stage 4D] Inspection complete. Keeping browser open briefly ...")
        wait_seconds(session.page, 3)
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    _print_final(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
