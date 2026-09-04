r"""Stage 4C — REAL eSampark LIVE UPLOAD HISTORY verification (headed, interactive).

Goal
----
Using the real authenticated eSampark session (saved cookies are REUSED when
present, otherwise manual login is allowed), verify the **Upload History** view
only:

    * history container / grid
    * filename field/column
    * upload date/time field
    * status field/column
    * result/error download control
    * pagination / search / filter

Then STOP. No Excel is ever selected, no upload is submitted, and the FTC upload
interface is never engaged for uploading.

Safety contract
---------------
* HEADED Playwright Chromium so the operator can see and, if needed, interact.
* SAME browser context is reused end-to-end — never reopened.
* NEVER calls ``set_input_files`` / never clicks an upload/import/submit.
* NEVER downloads a real historical file unless it is safe and necessary; default
  is DOM inspection only.
* Only safe diagnostics log: title, URL, tag, id, name, data-*, aria-label,
  column headings, and safe status labels. Cookies, tokens, passwords, Aadhaar,
  addresses, and candidate-sensitive row data are never logged.
* If Upload History must be opened by manual interaction that automation cannot
  safely identify, the script pauses, prints the instruction, and waits for the
  operator to open it, then inspects the resulting page IN THE SAME context.

Usage
-----
    $env:ESAMPARK_URL="https://<real-portal>/login"
    $env:TEAMHR_PORTAL_HISTORY_WAIT="900"   # optional manual-open/wait seconds
    .\venv\Scripts\python.exe scripts\verify_upload_history.py ["--open-history"]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.portal import esampark, selectors  # noqa: E402

# Optional wait window (seconds); default 900s (15 min).
DEFAULT_WAIT = int(os.environ.get("TEAMHR_PORTAL_HISTORY_WAIT", "900"))
DEBUG = "--debug" in sys.argv
TRY_AUTO_OPEN = "--open-history" in sys.argv


def log(msg: str) -> None:
    print(msg, flush=True)


# ── safe diagnostics (mirrors Stage 4B verifier; no secrets) ─────────────────

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
        out["data"] = {k: v for k, v in data.items() if len(str(v)) < 60}
    except Exception:  # noqa: BLE001
        pass
    return out


def primary_from(node: dict, fallback: str, element_type: str) -> list[str]:
    """Build a prioritized selector list from a discovered element's stable attrs."""
    prio: list[str] = []
    attrs = node.get("attrs") or {}
    i = attrs.get("id")
    if i and element_type == "file":
        prio.append(f'input[id="{i}"]')
    elif i:
        prio.append(f"#{i}")
    n = attrs.get("name")
    if n:
        prio.append(f'[name="{n}"]')
    for k, v in (attrs.get("data") or {}).items():
        prio.append(f'[{k}="{v}"]')
    role = attrs.get("role")
    label = attrs.get("aria-label") or attrs.get("placeholder")
    if role and label:
        prio.append(f'[role="{role}"]:has-text("{label}")')
    for s in [fallback]:
        if s and s not in prio:
            prio.append(s)
    return prio


def probe(page, selectors_list, element_type="generic", timeout_ms=3000):
    """Return (primary_selector_list, safe_attrs) for first visible match."""
    for sel in selectors_list:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            return [sel], safe_attrs(loc)
        except Exception:  # noqa: BLE001
            continue
    return None, None


def probe_any_of(page, candidates, timeout_ms=1500):
    """Return the first candidate whose text is visible, else None."""
    for cand in candidates:
        try:
            if page.get_by_text(cand, exact=False).first.is_visible(timeout=timeout_ms):
                return cand
        except Exception:  # noqa: BLE001
            continue
    return None


def wait_seconds(page, seconds: float = 1.0) -> None:
    try:
        page.wait_for_timeout(int(seconds * 1000))
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


def debug_shot(page, name: str) -> None:
    if not DEBUG:
        return
    try:
        shots = PROJECT_ROOT / "data" / "portal" / "debug"
        shots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shots / f"{int(time.time())}_{name}.png"))
    except Exception:  # noqa: BLE001
        pass


# ── detection (reuses centralized logic) ─────────────────────────────────────

def _is_login_url(url: str) -> bool:
    path = (url or "").split("?")[0].split("#")[0].rstrip("/").lower()
    return any(seg in path for seg in (
        "/login", "login.aspx", "/signin", "/sign-in", "/sign_in",
        "/auth", "/authenticate", "/ldap", "/login.aspx",
    ))


def _any_authenticated_nav_visible(page) -> bool:
    for sel in selectors.AUTHENTICATED_NAV_SIGNALS:
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


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
        form = False
        for sel in selectors.LOGIN_FORM_SELECTORS:
            try:
                if page.locator(sel).first.is_visible(timeout=700):
                    form = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not form:
            return True
    return False


def wait_until_authenticated(page, timeout_s: int) -> bool:
    deadline = time.time() + timeout_s
    attempts = 0
    while time.time() < deadline:
        if not page_alive(page):
            log("[Stage 4C] Browser window closed before auth confirmed. Stopping cleanly.")
            return False
        if detect_authenticated(page):
            return True
        attempts += 1
        if attempts % 5 == 1 or DEBUG:
            try:
                cur = (page.url, (page.title() or "")[:80])
            except Exception:  # noqa: BLE001
                cur = ("?", "?")
            log(f"[Stage 4C] Waiting for auth ({int(deadline - time.time())}s left) "
                f"url={cur[0]} title={cur[1]!r}")
        wait_seconds(page, 3)
    return False


# ── navigation ───────────────────────────────────────────────────────────────

def navigate_to_ftc(session, page, report) -> bool:
    """Reach the FTC upload page via reconciled selectors; robust confirmation.

    Does not require literal 'FTC' text (rules 4-5). Returns True if the FTC page
    is confirmed by upload interface + Self Onboarding context or nav click.
    """
    for label, sels, key in (
        ("Onboarding", selectors.NAV_ONBOARDING_SELECTORS, "onboarding"),
        ("Self Onboarding", selectors.NAV_SELF_ONBOARDING_SELECTORS, "self_onboarding"),
        ("FTC", selectors.NAV_FTC_SELECTORS, "ftc"),
    ):
        clicked = session._click_any(sels, timeout_ms=6000)
        if not clicked:
            log(f"\n[Stage 4C] Could not auto-locate '{label}'. "
                f"Please navigate to '{label}' manually, up to 60s ...")
            deadline = time.time() + 60
            while time.time() < deadline:
                if session._click_any(sels, timeout_ms=2000):
                    clicked = True
                    break
                wait_seconds(page, 2)
        if clicked:
            session.mark_verified(key, True)
            wait_seconds(page, 1)
        report[f"{label} nav"] = "YES" if clicked else "NO"
        log(f"[Stage 4C] {label}: found={clicked}")
        debug_shot(page, f"nav_{key}")

    # Robust FTC confirmation (rule 5): upload interface + Self Onboarding ctx.
    ftc_confirmed = selectors.ftc_page_confirmed(page)
    report["FTC page confirmed"] = "YES" if ftc_confirmed else "NO"
    log(f"[Stage 4C] FTC page confirmed (upload+ctx): {ftc_confirmed}")
    return ftc_confirmed


# ── history inspection ───────────────────────────────────────────────────────

def open_upload_history(session, page, report) -> bool:
    """Open Upload History via the verified primary selector.

    Preference order: verified live primary (text="Upload History"), then
    button/tab/link/anchor/menu. If automation cannot safely identify it, print
    the manual instruction and wait in the SAME context for the operator to open
    it, polling until the history grid appears.
    """
    if TRY_AUTO_OPEN:
        opened = session._click_any(selectors.HISTORY_OPEN_SELECTORS, timeout_ms=4000)
        if opened:
            wait_seconds(page, 2)
            if _history_visible(page):
                report["Upload History opened"] = "YES (auto)"
                return True
    # Fallback: manual open with a clear instruction.
    log("\n" + "=" * 70)
    log("[Stage 4C] Please open Upload History manually in the browser.")
    log("Select the 'Upload History' tab/menu item in the still-open window.")
    log("I will NOT upload anything. I'm watching this SAME browser context ...")
    log("=" * 70)
    deadline = time.time() + DEFAULT_WAIT
    while time.time() < deadline:
        if not page_alive(page):
            log("[Stage 4C] Browser closed before Upload History opened.")
            return False
        if _history_visible(page):
            report["Upload History opened"] = "YES (manual)"
            return True
        if DEBUG:
            log(f"[Stage 4C] Still waiting for Upload History "
                f"({int(deadline - time.time())}s left) url={_safe_url(page)}")
        wait_seconds(page, 3)
    report["Upload History opened"] = "NO"
    log("[Stage 4C] Upload History was not opened within the wait window.")
    return False


def _safe_url(page) -> str:
    try:
        return page.url
    except Exception:  # noqa: BLE001
        return "?"


def _history_visible(page) -> bool:
    """True when an upload-history grid/container appears on the page."""
    try:
        title = (page.title() or "").lower()
        if "history" in title or "upload" in title:
            pass
    except Exception:  # noqa: BLE001
        return False
    for sel in selectors.HISTORY_CONTAINER_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def inspect_history(page, report) -> None:
    """Inspect the live history DOM safely (table / div grid / virtualized)."""
    # A. Container
    csel, cnode = probe(page, selectors.HISTORY_CONTAINER_SELECTORS, timeout_ms=2000)
    report["History container found"] = "YES" if csel else "NO"
    if csel:
        report["container_primary"] = csel[0]
        report["container_tag"] = cnode.get("tag")
        report["container_attrs"] = {k: v for k, v in cnode.items() if k != "data"} or None
        if cnode.get("data"):
            report["container_data"] = cnode["data"]
        log(f"[Stage 4C] History container: primary={csel[0]} tag={cnode.get('tag')}")

    # Determine structure type (do NOT assume table > tr > td).
    struct = _detect_structure(page)
    report["History structure"] = struct
    log(f"[Stage 4C] History structure: {struct}")

    # B/C/D. Column headings -> map filename / timestamp / status.
    headings = _collect_headings(page)
    report["history_headings"] = headings or []
    fh = _match_heading(headings, selectors.HISTORY_FILENAME_HEADER_CANDIDATES)
    th = _match_heading(headings, selectors.HISTORY_TIMESTAMP_HEADER_CANDIDATES)
    sh = _match_heading(headings, selectors.HISTORY_STATUS_HEADER_CANDIDATES)
    report["Filename field identified"] = "YES" if fh else "NO"
    report["filename_column_label"] = fh
    report["Timestamp field identified"] = "YES" if th else ("NOT PRESENT" if headings else "NO")
    report["timestamp_column_label"] = th
    report["Status field identified"] = "YES" if sh else "NO"
    report["status_column_label"] = sh
    log(f"[Stage 4C] headings={headings} | filename={fh} timestamp={th} status={sh}")

    # Record filename representation from the first data row (if any).
    rep = _inspect_filename_representation(page, fh)
    report["filename_representation"] = rep
    log(f"[Stage 4C] filename representation: {rep}")

    # E. Result/error download control
    dsel, dnode = probe(page, selectors.HISTORY_DOWNLOAD_SELECTORS,
                        element_type="link", timeout_ms=1500)
    report["Result/error control identified"] = (
        "YES" if dsel else ("NOT PRESENT" if _history_visible(page) else "NO"))
    if dsel:
        report["download_primary"] = dsel[0]
        report["download_label"] = _visible_text(page, dsel[0])
        log(f"[Stage 4C] download control: primary={dsel[0]} "
            f"text={report['download_label']!r}")
        report["download_row_relation"] = _download_row_relation(page, dsel[0])

    # F. Pagination / filter / search
    psel, _ = probe(page, selectors.HISTORY_PAGINATION_SELECTORS, timeout_ms=1200)
    ssel, _ = probe(page, selectors.HISTORY_SEARCH_SELECTORS, timeout_ms=1200)
    fsel, _ = probe(page, selectors.HISTORY_FILTER_SELECTORS, timeout_ms=1200)
    report["Pagination/filter identified"] = (
        "YES" if (psel or fsel or ssel) else
        ("NOT PRESENT" if _history_visible(page) else "NO"))
    if psel:
        report["pagination_primary"] = psel[0]
    if fsel:
        report["filter_primary"] = fsel[0]
    if ssel:
        report["search_primary"] = ssel[0]

    debug_shot(page, "history_inspected")


def _detect_structure(page) -> str:
    for sel, name in (
        ('table', 'table'),
        ('[role="grid"]', 'aria-grid'),
        ('[role="table"]', 'aria-table'),
    ):
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return name
        except Exception:  # noqa: BLE001
            continue
    # React / Angular / virtualized grids usually have a scroll container.
    for sel in ('[class*="cdk-virtual" i]', '[class*="ag-body" i]',
                '[class*="dx-datagrid" i]', '[class*="k-grid" i]',
                '[class*="mat-table" i]', '[class*="p-datatable" i]'):
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return "virtualized/grid"
        except Exception:  # noqa: BLE001
            continue
    return "unknown"


def _collect_headings(page, limit=40) -> list:
    """Collect visible heading-like cells once, then reuse for column mapping."""
    found = []
    for tag in ("th", "[role='columnheader']",
                "[class*='header' i] th", "[class*='header' i] [role='columnheader']"):
        try:
            locs = page.locator(tag)
            n = min(locs.count(), limit)
            for idx in range(n):
                t = (locs.nth(idx).inner_text(timeout=800) or "").strip()
                if t and t not in found:
                    found.append(t)
        except Exception:  # noqa: BLE001
            continue
    return found[:limit]


def _match_heading(headings, candidates):
    for cand in candidates:
        for h in headings:
            if cand.lower() in h.lower():
                return cand
    return None


def _visible_text(page, sel) -> str:
    try:
        return (page.locator(sel).first.inner_text(timeout=1000) or "").strip()[:60]
    except Exception:  # noqa: BLE001
        return ""


def _inspect_filename_representation(page, filename_heading) -> dict:
    """Describe how an uploaded filename is represented in the first data row.

    Records: selector, whether plain text / link / title, and truncated or full.
    """
    out = {"found": False}
    # If we know the filename column heading, try to read a cell under it.
    cell_made = False
    if filename_heading:
        pass
    # Heuristic: first non-header cell-like element among row candidates.
    for row_sel in selectors.HISTORY_ROW_SELECTORS:
        try:
            row = page.locator(row_sel).first
            if not row.is_visible(timeout=700):
                continue
            # Try a filename-ish cell within the row.
            for cell_sel in selectors.HISTORY_FILENAME_CELL_SELECTORS:
                try:
                    cell = row.locator(cell_sel).first
                    if cell.is_visible(timeout=700):
                        txt = (cell.inner_text(timeout=800) or "").strip()
                        if txt:
                            tag = cell.evaluate("e => e.tagName").lower()
                            is_link = bool(cell.locator("a").count())
                            out = {
                                "found": True,
                                "row_selector": row_sel,
                                "cell_selector": cell_sel,
                                "tag": tag,
                                "is_link": bool(is_link),
                                "has_title": bool(cell.get_attribute("title")),
                                "sample_length": len(txt),
                                "sample_preview": txt[:40],
                            }
                            cell_made = True
                            break
                except Exception:  # noqa: BLE001
                    continue
            if cell_made:
                break
        except Exception:  # noqa: BLE001
            continue
    if not out.get("found"):
        # Last resort: any visible link containing ".xlsx" or ".csv".
        for sel in ('a[href*=".xlsx" i]', 'a[href*=".xls" i]', 'a[href*=".csv" i]'):
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=700):
                    txt = (loc.inner_text(timeout=800) or "").strip()
                    out = {"found": True, "selector": sel, "is_link": True,
                           "sample_preview": txt[:40], "sample_length": len(txt)}
                    cell_made = True
                    break
            except Exception:  # noqa: BLE001
                continue
    return out


def _download_row_relation(page, sel) -> str:
    """Describe how a download control relates to its row, if determinable."""
    try:
        loc = page.locator(sel).first
        # Is there an ancestor row / grid cell holding this control?
        for anc in ("tr", "[role='row']"):
            c = loc.locator(f"xpath=ancestor::{anc}").count()
            if c:
                return f"within {anc} (count={c})"
    except Exception:  # noqa: BLE001
        pass
    return "unknown (isolated control)"


# ── report ───────────────────────────────────────────────────────────────────

def _print_final(report: dict) -> None:
    print("\n" + "=" * 60)
    print("LIVE UPLOAD HISTORY VERIFICATION (Stage 4C)")
    print("=" * 60)
    keys = [
        ("Authenticated", "Authenticated"),
        ("FTC page confirmed", "FTC page confirmed"),
        ("Upload History opened", "Upload History opened"),
        ("History container", "History container found"),
        ("Filename field", "Filename field identified"),
        ("Status field", "Status field identified"),
        ("Timestamp field", "Timestamp field identified"),
        ("Result/error download control", "Result/error control identified"),
        ("Pagination/filter", "Pagination/filter identified"),
    ]
    for label, key in keys:
        print(f"{label:<30} {report.get(key, 'NO')}")
    print("-" * 60)
    print("Verified selectors integrated : " +
          ("YES" if report.get("selectors_written") else "NO"))
    print("Real Excel selected           : NO")
    print("Real candidate upload performed: NO")
    print("REAL_UPLOAD_ENABLED            : FALSE")
    print("-" * 60)
    print("History structure :", report.get("History structure"))
    print("Heading labels    :", ", ".join(report.get("history_headings") or []) or "none")
    fr = report.get("filename_representation") or {}
    if fr.get("found"):
        print("Filename repr     : selector=", fr.get("cell_selector") or fr.get("selector"),
              "| tag=", fr.get("tag"), "| is_link=", fr.get("is_link"),
              "| title=", fr.get("has_title"), "| len=", fr.get("sample_length"))
    print("=" * 60)
    print("\nAdditional safe detail:")
    for k, v in report.items():
        if isinstance(v, list):
            continue
        if k in {label for _, label in keys} or k in (
                "Authenticated", "FTC page confirmed", "Upload History opened"):
            continue
        if v in (None, "", False):
            continue
        print(f"  {k}: {v}")
    # Anonymized sample (safe, truncated) for offline reference only.
    if (report.get("filename_representation") or {}).get("found"):
        prev = (report["filename_representation"].get("sample_preview") or "")[:25]
        print("  filename sample (truncated)  :", prev + ("..." if len(prev) == 25 else ""))


def main() -> int:
    url = os.environ.get("ESAMPARK_URL") or "https://esampark.beeforce.in/login"
    report = {"Authenticated": "NO"}

    session = esampark.PortalSession()
    log("\n[Stage 4C] Launching a HEADED Chromium browser (reusing any saved session) ...")
    session._open()  # headless=False; _storage_args() loads saved cookies if present
    page = session.page
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    wait_seconds(page, 2)
    log(f"[Stage 4C] Opened: {page.url}")
    log(f"[Stage 4C] Title : {(page.title() or '')[:120]}")

    if not detect_authenticated(page):
        log("\n[Stage 4C] No usable session was found. Please log in manually in the")
        log(f"opened browser (up to {DEFAULT_WAIT}s). NO upload will happen.")
        if not wait_until_authenticated(page, DEFAULT_WAIT):
            log("[Stage 4C] Authentication was not confirmed. Stopping cleanly.")
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
            return 1
    session.status = esampark.CONNECTED
    session._persist_session()  # cookies only
    report["Authenticated"] = "YES"
    log("[Stage 4C] Authenticated session confirmed.")

    try:
        ftc = navigate_to_ftc(session, page, report)
        if not ftc:
            log("[Stage 4C] FTC page not robustly confirmed by DOM; continuing to "
                "attempt history open only if a history grid is reachable.")
        report["History container found"] = "NO"
        report["Filename field identified"] = "NO"
        report["Timestamp field identified"] = "NO"
        report["Status field identified"] = "NO"
        report["Result/error control identified"] = "NO"
        report["Pagination/filter identified"] = "NO"

        ok = open_upload_history(session, page, report)
        if ok:
            wait_seconds(page, 2)
            inspect_history(page, report)
        else:
            log("[Stage 4C] Could not open Upload History; nothing to inspect.")

        _persist_verified_history(report)
    finally:
        log("\n[Stage 4C] Verification complete. Keeping browser open briefly ...")
        wait_seconds(page, 4)
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    _print_final(report)
    return 0


def _persist_verified_history(report: dict) -> None:
    """Merge confirmed live history selectors into verified_selectors.py.

    Only writes keys that were actually confirmed on the live DOM. Upload-related
    keys (FILE_INPUT / UPLOAD_SUBMIT) are preserved untouched. Keeps centralized.
    """
    add = {}
    if report.get("History container found") == "YES" and report.get("container_primary"):
        add["HISTORY_CONTAINER"] = report["container_primary"]
    if report.get("Pagination/filter identified") == "YES":
        if report.get("search_primary"):
            add["HISTORY_SEARCH"] = report["search_primary"]
        if report.get("pagination_primary"):
            add["HISTORY_PAGINATION"] = report["pagination_primary"]
        if report.get("filter_primary"):
            add["HISTORY_FILTER"] = report["filter_primary"]

    path = PROJECT_ROOT / "app" / "portal" / "verified_selectors.py"
    try:
        import app.portal.verified_selectors as v
        existing = dict(v.VERIFIED)
    except Exception:  # noqa: BLE001
        existing = {}
    existing.update(add)

    lines = [
        "from __future__ import annotations",
        "",
        "# AUTO-GENERATED by scripts/verify_real_portal.py and scripts/verify_upload_history.py",
        "# Live-verified eSampark selectors confirmed against the REAL portal DOM.",
        "# Consumed by app/portal/selectors.py (prepended as prioritized primaries).",
        "# No secrets are stored here.",
        "",
        "VERIFIED = {",
    ]
    for k in ("LDAP_USER", "LDAP_PASSWORD", "LOGIN_SUBMIT", "NAV_ONBOARDING",
              "NAV_SELF_ONBOARDING", "NAV_FTC", "FILE_INPUT", "UPLOAD_SUBMIT",
              "UPLOAD_HISTORY", "RESULT_DOWNLOAD", "ERROR_DOWNLOAD",
              "HISTORY_CONTAINER", "HISTORY_SEARCH", "HISTORY_PAGINATION",
              "HISTORY_FILTER"):
        if k in existing:
            lines.append(f'    {k!r}: {existing[k]!r},')
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["selectors_written"] = bool(add)
    if add:
        log(f"[Stage 4C] Verified history selectors written -> {path}: {add}")
    else:
        log("[Stage 4C] No new verifiable history selectors to persist.")


if __name__ == "__main__":
    sys.exit(main())
