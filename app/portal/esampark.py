"""Playwright automation for the eSampark portal (Stage 4).

Responsibilities
----------------
* Load credentials from the environment only (``ESAMPARK_USERNAME`` and
  ``ESAMPARK_PASSWORD``). Never store a password in source, SQLite, logs,
  screenshots, localStorage, or the generated Excel.
* Manage a persistent authenticated browser session (cookies only — never the
  password) so the user does not have to log in for every batch.
* Automate LDAP login and **verify** the outcome (success / invalid
  credentials / timeout / portal unavailable / unexpected page).
* Detect CAPTCHA / OTP / MFA and PAUSE — never try to bypass it.
* Navigate Onboarding -> Self Onboarding -> FTC.
* Upload an already-generated Stage 3 workbook via Playwright's file-input API.
* Read Upload History, match the upload for *this* generated file, and download
  result / error workbooks when the portal offers them.

Selectors live in :mod:`app.portal.selectors`; this module never hard-codes any
selector strings in the business logic.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.portal import selectors

# ── Public portal-status vocabulary (page-facing) ────────────────────────────

NOT_CONNECTED = "Not Connected"
CONNECTED = "Connected"
LOGIN_REQUIRED = "Login Required"
MANUAL_VERIFICATION = "Manual Verification Required"
ERROR = "Error"

# Per-upload statuses (internal mapping of portal wording).
UPLOADING = "Uploading"
PROCESSING = "Processing"
SUCCESS = "Success"
FAILED = "Failed"
PARTIAL_FAILURE = "Partial Failure"
UNKNOWN = "Unknown"

# Per-candidate portal status vocabulary.
LINK_GENERATED = "Link Generated"
PORTAL_UPLOADED = "Portal Uploaded"

# Credentials environment variables.
ENV_USERNAME = "ESAMPARK_USERNAME"
ENV_PASSWORD = "ESAMPARK_PASSWORD"

# ── Real-upload safety configuration (controlled live-test mode) ─────────────
# A REAL live eSampark submission requires BOTH of these to be true. Neither is
# true by default. An operator must explicitly enable them before any live
# upload path will run. Each flag is read from the environment (not persisted,
# not committed) but both default to False so an unconfigured install never
# submits anything to the real portal.
#
#   REAL_UPLOAD_ENABLED  = the master "operator is responsible for a real
#                          upload" switch.
#   ESAMPARK_LIVE_TEST_MODE = the separate, explicit "run a controlled
#                          single-candidate live test" override.
#
# ``live_upload_allowed()`` returns True only when BOTH flags are set; every
# real-submit entry point must go through it.

REAL_UPLOAD_ENABLED = False
ESAMPARK_LIVE_TEST_MODE = False


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment override for a safety flag (fail closed)."""
    value = os.environ.get(name, "").strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


def load_upload_flags() -> None:
    """(Re)read the two live-upload safety flags from the environment.

    Called at import time and exposed so tests / operators can reload without a
    process restart. Both flags fail CLOSED (False) when unset or unparsable.
    """
    global REAL_UPLOAD_ENABLED, ESAMPARK_LIVE_TEST_MODE
    REAL_UPLOAD_ENABLED = _env_flag("REAL_UPLOAD_ENABLED", False)
    ESAMPARK_LIVE_TEST_MODE = _env_flag("ESAMPARK_LIVE_TEST_MODE", False)


def live_upload_allowed() -> bool:
    """True only when BOTH safety flags are set.

    Every real-submit path (session.upload_workbook and the live-test mode) must
    check this, so turning on one flag alone is never enough to submit to the
    real eSampark portal.
    """
    return bool(REAL_UPLOAD_ENABLED) and bool(ESAMPARK_LIVE_TEST_MODE)


load_upload_flags()

DISABLED_UPLOAD_MSG = (
    "Real eSampark upload is disabled. Both REAL_UPLOAD_ENABLED and "
    "ESAMPARK_LIVE_TEST_MODE must be true for a live single-candidate upload."
)


def _first_xlsx_name(text: str) -> Optional[str]:
    """Find the first ``<name>.xlsx``-like token in a row's raw text, if any."""
    m = re.search(r"([A-Za-z0-9_\-\.]+\.(?:xlsx|xls|csv))", text, re.I)
    return Path(m.group(1)).name if m else None


def _map_row_status(text: str) -> str:
    """Map a history row's raw text onto our internal status vocabulary.

    Uses the centralized ``selectors.HISTORY_ROW_STATUS_MARKERS`` so we never
    invent portal wording. Raw text is preserved separately by the caller.
    """
    low = (text or "").lower()
    if not low:
        return UNKNOWN
    if any(k in low for k in selectors.HISTORY_ROW_STATUS_MARKERS["partial_failure"]):
        return PARTIAL_FAILURE
    if any(k in low for k in selectors.HISTORY_ROW_STATUS_MARKERS["failed"]):
        return FAILED
    if any(k in low for k in selectors.HISTORY_ROW_STATUS_MARKERS["processing"]):
        return PROCESSING
    if any(k in low for k in selectors.HISTORY_ROW_STATUS_MARKERS["uploading"]):
        return UPLOADING
    if any(k in low for k in selectors.HISTORY_ROW_STATUS_MARKERS["success"]):
        return SUCCESS
    return UNKNOWN


def _yes(flag: bool) -> str:
    return "YES" if flag else "NO"


class PortalCredentialsError(Exception):
    """Raised when eSampark credentials are not configured."""


class PortalAutomationError(Exception):
    """Raised for any portal automation failure that should surface as clean UI."""


def get_credentials() -> tuple[str, str]:
    """Return (username, password) from the environment.

    Raises :class:`PortalCredentialsError` if either is missing so callers can
    show *"eSampark credentials are not configured."* without leaking details.
    """
    user = os.environ.get(ENV_USERNAME, "").strip()
    password = os.environ.get(ENV_PASSWORD, "").strip()
    if not user or not password:
        raise PortalCredentialsError(
            "eSampark credentials are not configured. "
            f"Set the {ENV_USERNAME} and {ENV_PASSWORD} environment variables."
        )
    return user, password


def credentials_configured() -> bool:
    try:
        get_credentials()
        return True
    except PortalCredentialsError:
        return False


# ── Persistent authenticated session (cookies only) ──────────────────────────


def storage_state_path() -> Path:
    """Path used to persist the authenticated browser cookies.

    Only cookies / localStorage are stored here — never the password.
    """
    data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    return data_dir / "portal" / "auth_state.json"


def has_saved_session() -> bool:
    return storage_state_path().exists()


def _storage_args() -> dict:
    """Build Playwright launch/storage kwargs for a saved session if present."""
    sp = storage_state_path()
    if sp.exists():
        return {"storage_state": str(sp)}
    return {}


# ── Playwright import guard ──────────────────────────────────────────────────


def _pw():
    """Lazily import Playwright so app import does not require it."""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise PortalAutomationError(
            "Playwright is not available. Run: python -m playwright install chromium"
        ) from exc


# ── Session ──────────────────────────────────────────────────────────────────


class PortalSession:
    """A single Playwright browser session with eSampark.

    ``status`` is one of the module-level status constants. Kept lightweight and
    CRUD-free: all durable state lives in SQLite via the ``service`` layer.
    """

    def __init__(self) -> None:
        self.status = NOT_CONNECTED
        self.username: Optional[str] = None
        self.page = None
        self._pw_ctx = None  # playwright context manager
        self._browser = None
        self._context = None
        # Stage 4B: live-verified element flags, populated only by inspecting the
        # real portal DOM (never guessed from mocked pages). Falsy until verified.
        self._verified: dict[str, bool] = {
            "login_page": False,
            "onboarding": False,
            "self_onboarding": False,
            "ftc": False,
            "upload_control": False,
            "submit_button": False,
            "upload_history": False,
            "history_filename": False,
            "history_status": False,
            "result_download": False,
            "error_download": False,
        }

    def diagnostic(self) -> dict:
        """Safe diagnostic payload — never exposes credentials or session cookies.

        Reports whether the app is connected / authenticated and which portal
        elements have been found on the live DOM (Stage 4B).
        """
        connected = self.status == CONNECTED
        authenticated = connected and self.page is not None and not self.page.is_closed()
        return {
            "real_upload_enabled": REAL_UPLOAD_ENABLED,
            "esampark_live_test_mode": ESAMPARK_LIVE_TEST_MODE,
            "live_upload_allowed": live_upload_allowed(),
            "portal_connected": "YES" if connected else "NO",
            "authenticated": "YES" if authenticated else "NO",
            "login_page_reached": _yes(self._verified.get("login_page")),
            "onboarding_found": _yes(self._verified.get("onboarding")),
            "self_onboarding_found": _yes(self._verified.get("self_onboarding")),
            "ftc_found": _yes(self._verified.get("ftc")),
            "upload_control_found": _yes(self._verified.get("upload_control")),
            "submit_button_found": _yes(self._verified.get("submit_button")),
            "upload_history_found": _yes(self._verified.get("upload_history")),
            "history_filename_found": _yes(self._verified.get("history_filename")),
            "history_status_found": _yes(self._verified.get("history_status")),
            "result_download_found": _yes(self._verified.get("result_download")),
            "error_download_found": _yes(self._verified.get("error_download")),
        }

    def mark_verified(self, key: str, value: bool = True) -> None:
        """Record that a live portal element was actually found (Stage 4B)."""
        if key in self._verified:
            self._verified[key] = bool(value)

    def close(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            if self._pw_ctx is not None:
                self._pw_ctx.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        self._context = None
        self._browser = None
        self._pw_ctx = None
        self.page = None
        status = self.status
        if status not in (NOT_CONNECTED,):
            self.status = NOT_CONNECTED
        return status

    # -- low-level helpers ----------------------------------------------------

    def _open(self) -> None:
        """Open (or reuse) a browser context and page.

        Uses the saved cookie session when available so the user does not need
        to log in for every batch. Passwords are never stored.
        """
        if self.page is not None and not self.page.is_closed():
            return
        sync_playwright = _pw()
        self._pw_ctx = sync_playwright().start()
        self._browser = self._pw_ctx.chromium.launch(headless=False)
        self._context = self._browser.new_context(**_storage_args())
        self.page = self._context.new_page()

    def _sync_status(self) -> None:
        """Refresh status from the current page/url when labelled Connected."""
        if self.page is None or self.page.is_closed():
            self.status = NOT_CONNECTED

    def _screenshot_safe(self, name: str) -> Optional[str]:
        """Save a debug screenshot guarded by portal debug mode.

        Returns the file path or None. Debug mode is OFF by default; enable with
        ``TEAMHR_PORTAL_DEBUG=1``.
        """
        if os.environ.get("TEAMHR_PORTAL_DEBUG", "").lower() not in ("1", "true", "yes"):
            return None
        if self.page is None or self.page.is_closed():
            return None
        try:
            data_dir = Path(__file__).resolve().parent.parent.parent / "data"
            shots = data_dir / "portal" / "debug"
            shots.mkdir(parents=True, exist_ok=True)
            path = shots / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}.png"
            self.page.screenshot(path=str(path))
            return str(path)
        except Exception:  # noqa: BLE001
            return None

    # -- login -----------------------------------------------------------------

    def _element_present(self, selectors_list: list[str], timeout_ms: int = 3000) -> bool:
        for sel in selectors_list:
            try:
                if self.page.locator(sel).first.is_visible(timeout=timeout_ms):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _click_any(self, selectors_list: list[str], timeout_ms: int = 5000) -> bool:
        for sel in selectors_list:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.click()
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def login(self, url: Optional[str] = None) -> str:
        """Perform LDAP login and verify the outcome.

        Returns the resulting status string. Never attempts CAPTCHA/OTP/MFA
        bypass — if a manual-verification signal is detected, it pauses and
        returns ``MANUAL_VERIFICATION``.
        """
        user, password = get_credentials()  # raises if unconfigured
        self._open()
        target = url or os.environ.get("ESAMPARK_URL") or selectors.PORTAL_URL
        try:
            self.page.goto(target, wait_until="domcontentloaded", timeout=40000)
        except Exception as exc:  # noqa: BLE001
            self.status = ERROR
            self._screenshot_safe("login_unreachable")
            # Distinguish "portal unavailable"/timeout from a page-level error.
            raise PortalAutomationError(
                f"Could not reach eSampark at {target}. ({exc.__class__.__name__})"
            ) from exc

        # Unexpected page guard: if there is no identifiable login form, pause.
        user_input = self._locate_login_user()
        if user_input is None:
            if self._element_present(selectors.MFA_MARKER_SELECTORS):
                self.status = MANUAL_VERIFICATION
                return MANUAL_VERIFICATION
            self.status = ERROR
            self._screenshot_safe("unexpected_page")
            raise PortalAutomationError(
                "Unexpected eSampark page; no LDAP login form was found."
            )

        user_input.fill(user)
        self._fill_password(password)
        self.page.wait_for_timeout(300)
        self._click_any(selectors.LOGIN_SUBMIT_SELECTORS)

        # Give the login round-trip a chance to settle.
        self.page.wait_for_timeout(2500)

        # Verify outcome.
        if self._element_present(selectors.MFA_MARKER_SELECTORS, timeout_ms=2000):
            self.status = MANUAL_VERIFICATION
            return MANUAL_VERIFICATION
        if self._element_present(selectors.LOGIN_SUCCESS_SIGNALS, timeout_ms=2000):
            self.status = CONNECTED
            self.username = user
            self._persist_session()
            return CONNECTED
        if self._element_present(selectors.LOGIN_INVALID_SIGNALS, timeout_ms=1500):
            self.status = ERROR
            raise PortalAutomationError("Invalid eSampark credentials.")
        if self._page_may_have_failed():
            self.status = ERROR
            raise PortalAutomationError("eSampark login did not complete (timeout or error).")

        # Ambiguous: keep the session but require confirmation.
        self.status = CONNECTED if self._element_present(
            selectors.LOGIN_SUCCESS_SIGNALS, timeout_ms=800
        ) else ERROR
        if self.status == ERROR:
            raise PortalAutomationError("eSampark login outcome could not be verified.")
        return self.status

    def _locate_login_user(self):
        for sel in selectors.LDAP_USER_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                if loc.is_visible(timeout=1500):
                    return loc
            except Exception:  # noqa: BLE001
                continue
        return None

    def _fill_password(self, password: str) -> None:
        for sel in selectors.LDAP_PASSWORD_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state="visible", timeout=2000)
                loc.fill(password)
                return
            except Exception:  # noqa: BLE001
                continue
        # Generic fallback: any visible password field on the page.
        try:
            loc = self.page.locator('input[type="password"]').first
            loc.wait_for(state="visible", timeout=2000)
            loc.fill(password)
        except Exception:  # noqa: BLE001
            pass

    def _page_may_have_failed(self) -> bool:
        try:
            body = self.page.locator("body").inner_text(timeout=1500).lower()
            markers = ("error", "timeout", "session expired", "something went wrong")
            return any(m in body for m in markers)
        except Exception:  # noqa: BLE001
            return False

    def _persist_session(self) -> None:
        """Save only the authenticated cookies (never the password)."""
        try:
            sp = storage_state_path()
            sp.parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(sp))
        except Exception:  # noqa: BLE001
            pass

    def verify_connected(self) -> str:
        """Confirm the saved session is still authenticated; reconnect if needed."""
        if self.status == CONNECTED and self.page is not None and not self.page.is_closed():
            return CONNECTED
        if not (has_saved_session() or credentials_configured()):
            self.status = LOGIN_REQUIRED
            return LOGIN_REQUIRED
        self._open()
        target = os.environ.get("ESAMPARK_URL") or selectors.PORTAL_URL
        try:
            self.page.goto(target, wait_until="domcontentloaded", timeout=30000)
        except Exception:  # noqa: BLE001
            self.status = ERROR
            return ERROR
        self.page.wait_for_timeout(1500)
        if self._element_present(selectors.LOGIN_SUCCESS_SIGNALS, timeout_ms=1500):
            self.status = CONNECTED
            return CONNECTED
        if self._element_present(
            selectors.LDAP_USER_SELECTORS, timeout_ms=1000
        ) or self._element_present(selectors.MFA_MARKER_SELECTORS, timeout_ms=800):
            self.status = LOGIN_REQUIRED
            return LOGIN_REQUIRED
        self.status = ERROR
        return ERROR

    # -- navigation -------------------------------------------------------------

    def navigate_to_ftc(self) -> None:
        """Navigate Onboarding -> Self Onboarding -> FTC.

        Raises :class:`PortalAutomationError` if a hop is not found, signalling
        that selectors may need reconciliation.
        """
        self._require_connected()
        for label, sels, flag in (
            ("Onboarding", selectors.NAV_ONBOARDING_SELECTORS, "onboarding"),
            ("Self Onboarding", selectors.NAV_SELF_ONBOARDING_SELECTORS, "self_onboarding"),
            ("FTC", selectors.NAV_FTC_SELECTORS, "ftc"),
        ):
            if self._click_any(sels, timeout_ms=6000):
                self.mark_verified(flag, True)
                continue
            # Stage 4B reconciliation (rule 5): the real portal may not surface
            # literal "FTC" text. When we are already on the Self Onboarding
            # context and a real upload interface is present, confirm the FTC
            # page without needing the FTC nav label.
            if flag == "ftc" and self._ftc_confirmed_by_context():
                self.mark_verified("ftc", True)
                continue
            self.status = ERROR
            self._screenshot_safe("nav_missing")
            raise PortalAutomationError(f"Could not locate menu: {label}.")
        self.page.wait_for_timeout(800)

    def _ftc_confirmed_by_context(self) -> bool:
        """Robust FTC confirmation when the current page's DOM markers confirm it.

        Verified live upload interface present AND Self Onboarding context.
        """
        try:
            for sel in selectors.NAV_SELF_ONBOARDING_SELECTORS:
                if self.page.locator(sel).first.is_visible(timeout=700):
                    break
            else:
                return False
        except Exception:  # noqa: BLE001
            return False
        for sel in selectors.FTC_FILE_INPUT_SELECTORS:
            try:
                if self.page.locator(sel).first.is_visible(timeout=700):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _require_connected(self) -> None:
        status = self.verify_connected()
        if status != CONNECTED:
            raise PortalAutomationError(f"eSampark is not connected ({status}).")

    # -- upload -----------------------------------------------------------------

    def upload_workbook(self, file_path: str) -> None:
        """Upload a generated workbook on the FTC page via the file input.

        BLOCKED unless BOTH safety flags are on (locked down by default): a live
        submission requires ``REAL_UPLOAD_ENABLED=True`` AND
        ``ESAMPARK_LIVE_TEST_MODE=True``. Otherwise it raises a clean
        :class:`PortalAutomationError` and performs no interaction.
        """
        if not live_upload_allowed():
            raise PortalAutomationError(DISABLED_UPLOAD_MSG)
        self._require_connected()
        if not file_path:
            raise PortalAutomationError("No workbook file path provided for upload.")
        p = Path(file_path)
        if not p.exists():
            raise PortalAutomationError(f"Generated file does not exist: {p.name}")
        if p.suffix.lower() != ".xlsx":
            raise PortalAutomationError(f"Only .xlsx workbooks can be uploaded (got {p.suffix}).")

        # Set the file on the matching <input type=file> directly.
        chosen = None
        for sel in selectors.FTC_FILE_INPUT_SELECTORS:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state="attached", timeout=5000)
                loc.set_input_files(str(p))
                chosen = True
                break
            except Exception:  # noqa: BLE001
                continue
        if not chosen:
            self.status = ERROR
            raise PortalAutomationError("Could not locate the eSampark file-upload control.")

        if not self._click_any(selectors.FTC_SUBMIT_SELECTORS, timeout_ms=6000):
            self.status = ERROR
            raise PortalAutomationError("Could not locate the eSampark upload/submit button.")

        # Allow the upload round-trip to begin.
        self.page.wait_for_timeout(1500)

    # -- upload history ----------------------------------------------------------

    def open_upload_history(self) -> None:
        self._require_connected()
        if not self._click_any(selectors.UPLOAD_HISTORY_SELECTORS, timeout_ms=6000):
            self.status = ERROR
            self._screenshot_safe("history_missing")
            raise PortalAutomationError("Could not locate the Upload History view.")
        self.page.wait_for_timeout(1000)

    def _history_rows(self):
        """Yield (row_locator, index) for each visible Upload History row.

        Uses the centralized row selectors in preference order; never assumes a
        specific grid technology (table rows, ARIA rows, div rows all probed).
        """
        if self.page is None or self.page.is_closed():
            return
        for row_sel in selectors.HISTORY_ROW_SELECTORS:
            try:
                locs = self.page.locator(row_sel)
                n = locs.count()
                if n <= 0:
                    continue
                # Skip the header row if the first row has no anchor/text.
                start = 1 if (
                    row_sel in ("tr", "table tr")
                    and n > 1
                    and self._row_is_header(locs.nth(0))
                ) else 0
                for i in range(start, n):
                    yield locs.nth(i), i
                return
            except Exception:  # noqa: BLE001
                continue

    def _row_is_header(self, row) -> bool:
        """Heuristic: a header row contains <th> (or no download reference)."""
        try:
            if not row.is_visible(timeout=400):
                return False
            if row.locator("th").count():
                return True
            # A real data row holds the uploaded-file anchor (download?id=...).
            if row.locator(selectors.HISTORY_FILENAME_CELL_SELECTORS[0]).count():
                return False
        except Exception:  # noqa: BLE001
            return False
        return False

    def _row_reference_id(self, row) -> Optional[str]:
        """Extract the portal request/reference id from a row's download href."""
        for sel in selectors.HISTORY_REFERENCE_SELECTORS:
            try:
                for k in range(row.locator(sel).count()):
                    href = row.locator(sel).nth(k).get_attribute("href")
                    rid = selectors.ref_id_from_href(href)
                    if rid:
                        return rid
            except Exception:  # noqa: BLE001
                continue
        return None

    def _row_filename(self, row) -> Optional[str]:
        """Read the uploaded-file name shown in the row (may be None if hidden)."""
        for sel in selectors.HISTORY_FILENAME_CELL_SELECTORS:
            try:
                cell = row.locator(sel).first
                if not cell.is_visible(timeout=400):
                    continue
                txt = (cell.inner_text(timeout=600) or "").strip()
                if txt:
                    return Path(txt).name
            except Exception:  # noqa: BLE001
                continue
        return None

    def _row_text(self, row) -> str:
        try:
            return (row.inner_text(timeout=1500) or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def list_history_rows(self, limit: int = 50) -> list[dict]:
        """Enumerate visible Upload History rows (safe metadata only).

        Each returned dict holds: reference_id, filename, status_raw, status
        (mapped), and row index. Never returns candidate PII beyond the uploaded
        filename itself.
        """
        out: list[dict] = []
        for row, i in self._history_rows():
            if len(out) >= limit:
                break
            txt = self._row_text(row)
            filename = self._row_filename(row) or _first_xlsx_name(txt)
            out.append({
                "row_index": i,
                "reference_id": self._row_reference_id(row),
                "filename": filename,
                "status_raw": " ".join(t.split() for t in txt.splitlines() if t.strip())[:200],
                "status": _map_row_status(txt),
            })
        return out

    def find_upload_row(self, reference_id: Optional[str] = None,
                        filename: Optional[str] = None,
                        prefer_recent: bool = True) -> Optional[dict]:
        """Locate the Upload History row that matches our upload.

        STRATEGY (in priority order, using only the metadata the real portal
        actually exposes — never assuming a "Filename" column exists):
          1. portal request/reference id (from the row's download href) when we
             know it;
          2. exact uploaded filename text when the portal shows one;
          3. the most recent row (our upload was just submitted) — safest when
             neither a reference id nor a filename is exposed.

        Returns a dict with ``row_index``, ``reference_id``, ``filename``,
        ``status_raw`` and ``status``; or None when nothing matched immediately.
        """
        if self.page is None or self.page.is_closed():
            return None
        rows = self.list_history_rows(limit=100)
        if not rows:
            return None
        if reference_id:
            for r in rows:
                if r["reference_id"] == str(reference_id):
                    return r
        if filename:
            target = Path(filename).name.lower()
            for r in rows:
                if r["filename"] and r["filename"].lower() == target:
                    return r
        if prefer_recent:
            return rows[0]
        return None

    # -- result / error download -------------------------------------------------

    def download_result_or_error(self, kind: str) -> Optional[bytes]:
        """Download a result or error workbook from the current history row.

        ``kind`` is ``"result"`` or ``"error"``. Returns the workbook bytes, or
        None when no download link is present.
        """
        if self.page is None or self.page.is_closed():
            return None
        sels = selectors.RESULT_DOWNLOAD_SELECTORS if kind == "result" else selectors.ERROR_DOWNLOAD_SELECTORS
        for sel in sels:
            try:
                loc = self.page.locator(sel).first
                loc.wait_for(state="visible", timeout=2500)
                with self.page.expect_download(timeout=15000) as dl_info:
                    loc.click()
                download = dl_info.value
                return download.path().read_bytes() if download.path() else None
            except Exception:  # noqa: BLE001
                continue
        return None
