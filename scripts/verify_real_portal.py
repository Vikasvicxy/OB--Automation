r"""Stage 4B — REAL eSampark portal selector reconciliation (headed, interactive).

Safety contract
---------------
* Launches Playwright in HEADED mode so the operator sees the browser.
* NEVER uploads a candidate Excel; NEVER clicks the final Upload/Submit.
* NEVER bypasses CAPTCHA / OTP / MFA — the operator completes them manually.
* Credentials are entered MANUALLY in the opened browser (nothing touches env,
  source, SQLite, logs, localStorage, or the password field via code).
* Only safe diagnostics are logged (title, url, tag, id, name, data-*, label);
  passwords, session cookies, authorization headers, full Aadhaar, and full
  addresses are never captured.
* Session cookies are persisted (cookies only) for reuse within the run.

Interaction model (non-TTY friendly)
------------------------------------
This script never reads from stdin (it cannot rely on an interactive TTY). After
opening the login page it POLLS the live DOM for an authenticated session for a
configurable window, so the operator can log in manually to the still-open
browser at their own pace. Once authenticated is detected it continues
automatically. The same browser context is reused throughout — it is never
closed or reopened.

Usage
-----
    $env:ESAMPARK_URL="https://<real-portal>/login"
    $env:TEAMHR_PORTAL_AUTH_WAIT="900"    # optional seconds to wait for login
    .\venv\Scripts\python.exe scripts\verify_real_portal.py --url "https://<real-portal>/login"

Then log in manually in the opened browser. Optionally pass --debug to keep the
browser open and dump a debug screenshot after each discovery step.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.portal import esampark, selectors  # noqa: E402

# Optional wait window (seconds) for manual login; default 900s (15 min).
DEFAULT_AUTH_WAIT = int(os.environ.get("TEAMHR_PORTAL_AUTH_WAIT", "900"))
DEBUG = "--debug" in sys.argv


def log(msg: str) -> None:
    print(msg, flush=True)


# ── safe diagnostics ─────────────────────────────────────────────────────────

def safe_attrs(loc) -> dict:
    """Capture stable, safe attributes of a matched element (no secrets)."""
    out = {}
    try:
        out["tag"] = loc.evaluate("e => e.tagName")
    except Exception:  # noqa: BLE001
        pass
    for a in ("id", "name", "type", "role", "aria-label", "placeholder"):
        try:
            v = loc.get_attribute(a)
            if v:
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
    """Build a prioritized selector list from a discovered element's stable attrs.

    Preference order (per brief): id > name/data-attribute > role+label > text.
    Falls back to the matched selector (already proven on the live DOM).
    """
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


# ── live discovery ───────────────────────────────────────────────────────────

def probe_and_discover(page, selectors_list, element_type, timeout_ms=4000):
    """Return (primary_selector_list, safe_node) for the first matching element,
    or (None, None). Marks nothing itself (caller marks verified)."""
    for sel in selectors_list:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            attrs = safe_attrs(loc)
            return primary_from({"attrs": attrs}, sel, element_type), attrs
        except Exception:  # noqa: BLE001
            continue
    return None, None


def _is_login_url(url: str) -> bool:
    """True when ``url`` still points at a login/auth page (path-level check)."""
    path = (url or "").split("?")[0].split("#")[0].rstrip("/").lower()
    return any(seg in path for seg in (
        "/login", "login.aspx", "/signin", "/sign-in", "/sign_in",
        "/auth", "/authenticate", "/ldap", "/login.aspx",
    ))


def _login_form_visible(page) -> bool:
    """True when a login form (user/password input) is currently visible."""
    if page is None or page.is_closed():
        return False
    for sel in selectors.LOGIN_FORM_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _any_authenticated_nav_visible(page) -> bool:
    """True when any post-login navigation/menu element is visible.

    Uses the centralized ``AUTHENTICATED_NAV_SIGNALS`` (Logout / Sign Out /
    Welcome / Onboarding / Self Onboarding / FTC / Upload History) so we never
    rely on a single placeholder selector and never hard-code a string here.
    """
    if page is None or page.is_closed():
        return False
    for sel in selectors.AUTHENTICATED_NAV_SIGNALS:
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def detect_authenticated(page) -> bool:
    """Robust, multi-signal authenticated-session detection.

    Accepts the session when ANY strong independent signal fires:
      * an authenticated navigation/menu is visible (Onboarding, Self Onboarding,
        FTC, Upload History, Logout, Sign Out, Welcome); or
      * the login form has disappeared AND the URL has left the /login page.

    Returns False (never raises) so a transient probe error simply means
    "not detected yet" rather than crashing the polling loop. Safe-only: reads
    URL, title and element visibility; never reads secrets.
    """
    if page is None or page.is_closed():
        return False
    if _any_authenticated_nav_visible(page):
        return True
    try:
        login_url_gone = not _is_login_url(page.url)
    except Exception:  # noqa: BLE001
        login_url_gone = False
    if login_url_gone and not _login_form_visible(page):
        return True
    return False


def page_alive(page) -> bool:
    """True when the controlled page/browser is still open and responsive."""
    try:
        if page is None or page.is_closed():
            return False
        _ = page.url  # round-trip proves the control channel is alive
        return True
    except Exception:  # noqa: BLE001
        return False


def wait_until_authenticated(page, timeout_s: int) -> bool:
    """Poll the live DOM for an authenticated session; browser stays open.

    The same headed browser/context is reused the whole time (never reopened).
    If the operator closes the window mid-wait, we detect it and stop cleanly
    instead of crashing with a Playwright ``TargetClosedError``.
    """
    deadline = time.time() + timeout_s
    attempts = 0
    while time.time() < deadline:
        if not page_alive(page):
            log("[Stage 4B] Browser window was closed before authentication "
                "was confirmed. Stopping cleanly.")
            return False
        if detect_authenticated(page):
            return True
        attempts += 1
        if attempts % 5 == 1 or DEBUG:
            try:
                cur_url = page.url
                cur_title = (page.title() or "")[:80]
            except Exception:  # noqa: BLE001
                cur_url, cur_title = "?", "?"
            log(f"[Stage 4B] Waiting for auth ({int(deadline - time.time())}s left) "
                f"url={cur_url} title={cur_title!r}")
        page.wait_for_timeout(3000)
    return False


def wait_seconds(page, seconds: float = 1.0) -> None:
    page.wait_for_timeout(int(seconds * 1000))


# ── helpers ──────────────────────────────────────────────────────────────────

def debug_shot(page, name: str) -> None:
    if not DEBUG:
        return
    try:
        shots = PROJECT_ROOT / "data" / "portal" / "debug"
        shots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shots / f"{int(time.time())}_{name}.png"))
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    url = os.environ.get("ESAMPARK_URL")
    if len(sys.argv) > 2 and sys.argv[-2] == "--url":
        url = sys.argv[-1]
    if not url or not url.lower().startswith("http"):
        log("\nREAL ESAMPARK URL is required.\n")
        log("Pass it with either:\n  --url https://<real-portal>/login\n"
            "or the environment variable ESAMPARK_URL.\n")
        return 2

    report: dict = {"Login page reached": "NO", "Authentication": "NOT COMPLETED"}

    session = esampark.PortalSession()
    log("\n[Stage 4B] Launching a HEADED Chromium browser ...")
    session._open()  # headless=False
    page = session.page
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    wait_seconds(page, 2)
    session.mark_verified("login_page", True)
    report["Login page reached"] = "YES"
    report["URL"] = page.url
    report["Title"] = (page.title() or "")[:200]
    log(f"[Stage 4B] Opened: {page.url}")
    log(f"[Stage 4B] Title  : {report['Title']}")
    debug_shot(page, "login_page")

    # ── MANUAL LOGIN FALLBACK (poll-based) ─────────────────────────────────
    log("\n" + "=" * 70)
    log("Please complete eSampark login in the opened browser.")
    log("Enter your LDAP username, password, and OTP/CAPTCHA/MFA if shown.")
    log("I will NOT bypass any security prompt, and NO upload will happen.")
    log(f"Waiting up to {DEFAULT_AUTH_WAIT}s for manual login ...")
    log("=" * 70)

    authenticated = wait_until_authenticated(page, DEFAULT_AUTH_WAIT)

    if not authenticated:
        session.status = esampark.LOGIN_REQUIRED
        log("\n[Stage 4B] Authentication was not confirmed within the wait window. Stopping.")
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass
        return 1

    session.status = esampark.CONNECTED
    session._persist_session()  # cookies only — never the password
    report["Authentication"] = "MANUAL"
    report["Authenticated session confirmed"] = "YES"
    log("\n[Stage 4B] Authenticated session confirmed.")
    debug_shot(page, "authenticated")

    try:
        # ── NAVIGATION: Onboarding -> Self Onboarding -> FTC ───────────────
        for label, sels, key in (
            ("Onboarding", selectors.NAV_ONBOARDING_SELECTORS, "onboarding"),
            ("Self Onboarding", selectors.NAV_SELF_ONBOARDING_SELECTORS, "self_onboarding"),
            ("FTC", selectors.NAV_FTC_SELECTORS, "ftc"),
        ):
            clicked = session._click_any(sels, timeout_ms=6000)
            if not clicked:
                # Manual-nav fallback: poll briefly so the operator can click.
                log(f"\n[Stage 4B] Could not auto-locate '{label}'. "
                    f"Please navigate to '{label}' manually, up to 60s ...")
                deadline = time.time() + 60
                while time.time() < deadline:
                    if session._click_any(sels, timeout_ms=2000):
                        clicked = True
                        break
                    page.wait_for_timeout(2000)
            if clicked:
                session.mark_verified(key, True)
                wait_seconds(page, 1)
            report[f"{label} found"] = "YES" if clicked else "NO"
            log(f"[Stage 4B] {label}: found={clicked}")
            debug_shot(page, f"nav_{key}")

        # ── FTC PAGE: file input + submit + upload history ────────────────
        file_sel, _ = probe_and_discover(page, selectors.FTC_FILE_INPUT_SELECTORS,
                                         element_type="file")
        if file_sel:
            session.mark_verified("upload_control", True)
            report["Upload control found"] = "YES"
            report["upload_control_primary"] = file_sel[0]
            log(f"[Stage 4B] Upload file control found: primary={file_sel[0]}")
        else:
            report["Upload control found"] = "NO"

        # Stage 4B reconciliation (rule 5): confirm the FTC page from the real
        # upload interface + Self Onboarding context even if literal "FTC" text
        # is not discoverable. Updates the previously set "FTC found" verdict.
        robust_ftc = selectors.ftc_page_confirmed(page)
        robust_ftc_ctx = (
            report.get("Self Onboarding found") == "YES"
            and report.get("Upload control found") == "YES"
        )
        if robust_ftc or robust_ftc_ctx:
            session.mark_verified("ftc", True)
            report["FTC found"] = "YES (robust: upload interface + Self Onboarding context)"
            report["FTC detection basis"] = "robust (rule 5)"
            log("[Stage 4B] FTC page confirmed via upload interface + Self "
                "Onboarding context (literal 'FTC' text not required).")
        else:
            report["FTC detection basis"] = "text/direct only"

        sub_sel, _ = probe_and_discover(page, selectors.FTC_SUBMIT_SELECTORS,
                                        element_type="button")
        if sub_sel:
            session.mark_verified("submit_button", True)
            report["Upload/Submit button found"] = "YES"
            report["submit_button_primary"] = sub_sel[0]
        else:
            report["Upload/Submit button found"] = "NO (not auto-findable; left untouched)"

        log("\n[Stage 4B] NOT uploading anything. Opening Upload History only if it")
        log("           can be done without submitting a file.")

        # ── UPLOAD HISTORY ─────────────────────────────────────────────────
        hist_sel, _ = probe_and_discover(page, selectors.UPLOAD_HISTORY_SELECTORS,
                                         element_type="tab")
        if hist_sel:
            if not session._click_any([hist_sel[0]], timeout_ms=4000):
                # only open via a click on the located element; never a navigation guess
                log("[Stage 4B] Upload History located but could not be opened without "
                    "an explicit click; reporting located only.")
            else:
                wait_seconds(page, 2)
            session.mark_verified("upload_history", True)
            report["Upload History found"] = "YES"
            debug_shot(page, "upload_history")

            report["History filename field found"] = "NO"
            report["History status field found"] = "NO"
            for header_text, key in (("Filename", "history_filename"),
                                     ("Status", "history_status")):
                found_hdr = False
                for tag in ("th", "td", "div", "span"):
                    probe_sel = f"{tag}:has-text('{header_text}')"
                    try:
                        if page.locator(probe_sel).first.is_visible(timeout=1200):
                            session.mark_verified(key, True)
                            found_hdr = True
                            break
                    except Exception:  # noqa: BLE001
                        continue
                if found_hdr:
                    report["History filename field found" if key == "history_filename"
                           else "History status field found"] = "YES"

            r_sel, _ = probe_and_discover(page, selectors.RESULT_DOWNLOAD_SELECTORS,
                                          element_type="link", timeout_ms=2000)
            e_sel, _ = probe_and_discover(page, selectors.ERROR_DOWNLOAD_SELECTORS,
                                          element_type="link", timeout_ms=2000)
            if r_sel:
                session.mark_verified("result_download", True)
            if e_sel:
                session.mark_verified("error_download", True)
            report["Result/error download control found"] = (
                "YES" if (r_sel or e_sel) else "NOT PRESENT")
        else:
            report["Upload History found"] = "NO (not auto-findable on current view)"
            report["Result/error download control found"] = "NOT CHECKED"

        debug_shot(page, "final")

        # ── WRITE verified selectors (centralized, non-destructive) ───────
        verified = {}
        if file_sel:
            verified["FILE_INPUT"] = file_sel[0]
        if sub_sel:
            verified["UPLOAD_SUBMIT"] = sub_sel[0]
        if hist_sel:
            verified["UPLOAD_HISTORY"] = hist_sel[0]
        if r_sel:
            verified["RESULT_DOWNLOAD"] = r_sel[0]
        if e_sel:
            verified["ERROR_DOWNLOAD"] = e_sel[0]

        verified_path = PROJECT_ROOT / "app" / "portal" / "verified_selectors.py"
        if verified:
            _write_verified(verified_path, verified)
            report["Selectors updated"] = str(verified_path)
        else:
            report["Selectors updated"] = "NONE (no primary selectors were confirmed)"

    finally:
        log("\n[Stage 4B] Verification complete. Keeping browser open briefly ...")
        wait_seconds(page, 4)
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    _print_report(report, session)
    return 0


def _write_verified(path: Path, verified: dict) -> None:
    lines = [
        "from __future__ import annotations",
        "",
        "# AUTO-GENERATED by scripts/verify_real_portal.py — do not edit by hand.",
        "# Live-verified eSampark selectors confirmed against the REAL portal DOM.",
        "# Consumed by app/portal/selectors.py (prepended as prioritized primaries).",
        "# No secrets are stored here.",
        "",
        "VERIFIED = {",
    ]
    for k in ("LDAP_USER", "LDAP_PASSWORD", "LOGIN_SUBMIT", "NAV_ONBOARDING",
              "NAV_SELF_ONBOARDING", "NAV_FTC", "FILE_INPUT", "UPLOAD_SUBMIT",
              "UPLOAD_HISTORY", "RESULT_DOWNLOAD", "ERROR_DOWNLOAD"):
        if k in verified:
            lines.append(f'    {k!r}: {verified[k]!r},')
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"[Stage 4B] Wrote verified selectors -> {path}")


def _print_report(report: dict, session) -> None:
    diag = session.diagnostic()
    print("\n" + "=" * 72)
    print("REAL ESAMPARK VERIFICATION")
    print("=" * 72)
    lines = [
        ("Authenticated session confirmed", report.get("Authenticated session confirmed", "NO")),
        ("Onboarding found", report.get("Onboarding found", "NO")),
        ("Self Onboarding found", report.get("Self Onboarding found", "NO")),
        ("FTC found", report.get("FTC found", "NO")),
        ("Upload control found", report.get("Upload control found", "NO")),
        ("Upload/Submit button found", report.get("Upload/Submit button found", "NO")),
        ("Upload History found", report.get("Upload History found", "NO")),
        ("History filename field found", report.get("History filename field found", "NO")),
        ("History status field found", report.get("History status field found", "NO")),
        ("Result/error download control found",
         report.get("Result/error download control found", "NOT PRESENT")),
    ]
    for k, v in lines:
        print(f"{k:<46} {v}")
    print("-" * 72)
    print("Real candidate upload performed : NO")
    print("REAL_UPLOAD_ENABLED             :",
          "FALSE" if diag.get("real_upload_enabled") is False else "TRUE")
    print("=" * 72)

    # Supplementary detail (not part of the YES/NO verdict block).
    print("Additional detail:")
    for k, v in report.items():
        if k in {key for key, _ in lines}:
            continue
        print(f"  {k}: {v}")
    print("  portal_connected  :", diag.get("portal_connected"))
    print("  login_page_reached:", diag.get("login_page_reached"))
    print("=" * 72)


if __name__ == "__main__":
    sys.exit(main())
