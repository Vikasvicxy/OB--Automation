"""Centralized eSampark DOM selectors.

Rationale
---------
Selector strings are declared here as named constants so the automation layer
never scatters raw CSS / text / role strings through the code. When the portal
changes, this is the single place to reconcile selectors.

Preference order (per prompt)
-----------------------------
1. stable ``id``
2. ``label`` / placeholder
3. ARIA ``role`` + accessible name
4. stable CSS attribute (``[name=...]``, ``[data-*]``)
5. visible ``text``

We deliberately avoid ``nth-child``-heavy chains, screen coordinates, and
absolute XPath.

IMPORTANT
---------
These selectors encode the *intended* portal structure described in the Stage 4
brief (LDAP login -> Onboarding -> Self Onboarding -> FTC). They are **not**
verified against a live portal (no credentials are available in this
environment). A developer with an authenticated session should reconcile them
against the real DOM before real end-to-end uploads. The architecture and the
unit-tested logic are complete regardless.
"""

from __future__ import annotations

# ── Login ────────────────────────────────────────────────────────────────────

# Portal login URL. Overridable via ESAMPARK_URL if the environment differs.
PORTAL_URL = "https://esampark.example.in/login"

# LDAP username + password field hints (id > name > placeholder).
LDAP_USER_SELECTORS = [
    'input[id*="user"]',
    'input[name*="user"]',
    'input[name="username"]',
    'input[placeholder*="user" i]',
    'input[type="text"]',
]
LDAP_PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[id*="pass"]',
]
LOGIN_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button:has-text("Login")',
    'button:has-text("Sign In")',
    'button:has-text("Sign in")',
    'text="Login"',
]

# Signals that appear when login succeeded vs. failed.
LOGIN_SUCCESS_SIGNALS = [
    "text=Logout",
    "text=Sign Out",
    "text=Sign out",
    "text=Welcome",
    'a[href*="logout" i]',
]
LOGIN_INVALID_SIGNALS = [
    "text=Invalid username",
    "text=Invalid credentials",
    "text=Invalid user",
    "text=Invalid password",
    "text=Authentication failed",
    "text=Login failed",
    "text=incorrect",
    "text=Incorrect",
]

# Selectors that prove a LOGIN FORM is still present. Used as the *negative*
# authenticated signal: once none of these is visible the login form has
# disappeared, which is a strong (and portal-agony-agnostic) sign the session is
# authenticated. Falls back to the same user/password heuristics as the login
# path so we never guess at exotic portal wording.
LOGIN_FORM_SELECTORS = [
    'input[type="password"]',
    'input[name*="password" i]',
    'input[id*="password" i]',
    'input[placeholder*="password" i]',
    'input[name*="user" i]',
    'input[id*="user" i]',
    'input[name="username"]',
    'input[placeholder*="user" i]',
]

# CAPTCHA / OTP / MFA marker selectors.
MFA_MARKER_SELECTORS = [
    'input[name*="captcha" i]',
    'input[id*="captcha" i]',
    'iframe[src*="recaptcha" i]',
    '.g-recaptcha',
    'input[name*="otp" i]',
    'input[id*="otp" i]',
    'input[placeholder*="OTP" i]',
    'input[placeholder*="One Time" i]',
    'input[placeholder*="verification code" i]',
    'text=Enter the code',
    'text=Verification Code',
    'text=Google Authenticator',
    'text=Two-factor',
]

# ── Navigation: Onboarding -> Self Onboarding -> FTC ─────────────────────────

NAV_ONBOARDING_SELECTORS = [
    'a:has-text("Onboarding")',
    'li:has-text("Onboarding")',
    '*[data-menu="Onboarding"]',
    'button:has-text("Onboarding")',
]
NAV_SELF_ONBOARDING_SELECTORS = [
    'a:has-text("Self Onboarding")',
    'a:has-text("Self-Onboarding")',
    'a:has-text("Self onboarding")',
    '*[data-menu="Self Onboarding"]',
]
NAV_FTC_SELECTORS = [
    'a:has-text("FTC")',
    'span:has-text("FTC")',
    '*[data-menu="FTC"]',
    'text="FTC"',
]

# ── FTC upload page ──────────────────────────────────────────────────────────

# The file <input> that accepts the generated Self Onboarding workbook. We set
# files directly on this input (Playwright's file chooser API), so we never need
# the OS file dialog.
FTC_FILE_INPUT_SELECTORS = [
    'input[type="file"]',
    'input[accept*="xlsx" i]',
    'input[accept*="excel" i]',
    'input[name*="file" i]',
    'input[id*="upload" i]',
    'input[id*="file" i]',
]

# Stage 4B — robust FTC-page confirmation (reconciliation rule #5).
# The real eSampark portal may label its FTC upload screen with a name that does
# not literally contain the text "FTC". We therefore do NOT require a literal
# "FTC" string. Instead the FTC page is considered CONFIRMED when BOTH of these
# independent, stable live-DOM signals are present:
#   1. a real file upload control (``input[type="file"]``); AND
#   2. the expected Self Onboarding navigation context.
# ``FTC_PAGE_UPLOAD_CONTROLS`` detects the upload interface; the Self Onboarding
# context is checked against ``NAV_SELF_ONBOARDING_SELECTORS`` by the caller.
# This keeps both signals centralized so the automation never hard-codes a DOM
# string in business logic.
FTC_PAGE_UPLOAD_CONTROLS = [
    'input[type="file"]',
    'input[accept*="xlsx" i]',
    'input[accept*="excel" i]',
    'input[name*="file" i]',
    'input[id*="upload" i]',
]

# Submit button(s) that begin processing the uploaded workbook.
FTC_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button:has-text("Upload")',
    'button:has-text("Submit")',
    'button:has-text("Import")',
    'button:has-text("Process")',
]

# Upload History tab/menu link.
UPLOAD_HISTORY_SELECTORS = [
    'a:has-text("Upload History")',
    'a:has-text("Upload history")',
    'span:has-text("Upload History")',
    '*[data-menu="Upload History"]',
    'text="Upload History"',
]

# Downloadable result / error file links within an upload-history row.
RESULT_DOWNLOAD_SELECTORS = [
    'a:has-text("Result")',
    'a:has-text("Download Result")',
    'a[href*="result" i]',
    'button:has-text("Download Result")',
]
ERROR_DOWNLOAD_SELECTORS = [
    'a:has-text("Error")',
    'a:has-text("Failed")',
    'a:has-text("Download Error")',
    'a[href*="error" i]',
    'a[href*="fail" i]',
]

# ── Multi-signal authenticated-session detection (Stage 4B fix) ──────────────
# The Stage 4B verifier no longer depends on a single placeholder "success"
# selector. It proves an authenticated session with SEVERAL independent, safe
# signals and accepts the session when any of them fires:
#   1. login URL is gone  AND  the login form disappeared  (page moved past login)
#   2. any authenticated navigation/menu is visible (Onboarding / Self Onboarding
#      / FTC / Upload History / Logout / Welcome)
#   3. the Onboarding menu exists (treated as authenticated nav evidence)
# These are combined in ``detect_authenticated`` (in the verifier); this list
# centralizes the *positive* DOM evidence so the verifier never hard-codes a
# selector string and so live-verified primaries can be pre-pended below.
AUTHENTICATED_NAV_SIGNALS = list(dict.fromkeys([
    *LOGIN_SUCCESS_SIGNALS,
    *NAV_ONBOARDING_SELECTORS,
    *NAV_SELF_ONBOARDING_SELECTORS,
    *NAV_FTC_SELECTORS,
    *UPLOAD_HISTORY_SELECTORS,
]))

# ── Upload history page (Stage 4C — live verified) ───────────────────────────

# The FTC Upload History view may be opened via a control of any shape: link,
# anchor, button, tab, menu item, or a generic element with the label. These run
# in preference order; the verified live primary ``text="Upload History"`` is
# listed first explicitly so it leads regardless of snapshot ordering.
HISTORY_OPEN_SELECTORS = list(dict.fromkeys([
    'text="Upload History"',
    *UPLOAD_HISTORY_SELECTORS,
    'button:has-text("Upload History")',
    'a:has-text("History")',
    'button:has-text("History")',
    '[role="tab"]:has-text("Upload History")',
    '[role="menuitem"]:has-text("Upload History")',
]))

# History container / grid. The portal may render a <table>, a div grid, or a
# virtualized list — so we probe for ALL of these and never assume <table>.
HISTORY_CONTAINER_SELECTORS = [
    'table:has-text("Upload History")',
    'table',
    '[class*="history" i] table',
    '[role="grid"]',
    '[role="table"]',
    '[class*="history" i]',
    '[class*="upload" i][class*="history" i]',
]

# Column headings we expect to map. We preserve the actual live heading text
# rather than inventing status names.
HISTORY_FILENAME_HEADER_CANDIDATES = ["File Name", "Filename", "File", "Document", "Upload File"]
HISTORY_TIMESTAMP_HEADER_CANDIDATES = ["Date", "Time", "Uploaded On", "Date Time", "Uploaded Date",
                                       "Timestamp", "Date & Time", "Created On"]
HISTORY_STATUS_HEADER_CANDIDATES = ["Status", "State", "Process Status", "Upload Status",
                                    "Error Status"]
# Cell-level selectors. We deliberately avoid ``nth-child``/first-child positions
# (they are brittle and depend on column order) and prefer stable class / text /
# ARIA cues so the mapping survives portal re-ordering. The verifier reconciles
# these against the live DOM; the automation uses them only when a real matching
# attribute is actually present.

# Uploaded-file representation within a history row: plain text, a link/anchor,
# an Excel/attachment icon, or a ``title``/tooltip on a cell.
HISTORY_FILENAME_CELL_SELECTORS = [
    '[class*="filename" i]',
    '[class*="file-name" i]',
    '[class*="uploaded" i]',
    '[class*="document" i]',
    'a[href*=".xlsx" i]',
    'a[href*=".xls" i]',
    'a[href*=".csv" i]',
    '[title*=".xlsx" i]',
    '[title*="xls" i]',
    '[title*="csv" i]',
    'td a:has-text("xlsx")',
    'td a:has-text("xls")',
    'td[class*="excel" i]',
    'td[class*="attachment" i]',
]

# Status label within a row: a cell with class/role cue, or a cell whose text
# maps to a known processing state (Success / Failed / Processing / Rejected…).
HISTORY_STATUS_CELL_SELECTORS = [
    '[class*="status" i]',
    '[role="status"]',
    '[class*="state" i]',
    '[class*="process" i]',
    'td[class*="success" i]',
    'td[class*="fail" i]',
    'td[class*="error" i]',
    'td[class*="reject" i]',
    'td[class*="processing" i]',
]

# Upload timestamp / date / time shown for a row.
HISTORY_TIMESTAMP_CELL_SELECTORS = [
    '[class*="timestamp" i]',
    '[class*="date" i]',
    '[class*="time" i]',
    '[class*="uploaded-on" i]',
    '[class*="created" i]',
    'time',
    'td[class*="date" i]',
]

# Portal reference / request / batch ID (a stable unique value the portal uses
# for a submitted worksheet). Priority matching key when no filename is exposed.
# LIVE-CONFIRMED (Stage 4D): the uploaded-file anchor carries the per-row
# reference id in its href, e.g. ``download?id=724648``.
HISTORY_REFERENCE_SELECTORS = [
    'a[href*="download?id="]',
    'a[href*="request" i]',
    'a[href*="reference" i]',
    '[class*="request" i]',
    '[class*="reference" i]',
    '[class*="ref" i]',
    '[class*="batch" i]',
    '[class*="request-id" i]',
    '[class*="reference-id" i]',
    '[class*="id" i]',
    'td[class*="request" i]',
    'td[class*="batch" i]',
]

# Row-level container. We probe the portal's actual structural cues in order:
# first a contextual row under the history container, then generic table row,
# then ARIA grid/row, then class-named rows. Never assumed to be <tr>.
HISTORY_ROW_SELECTORS = [
    '[role="row"]',
    'table tr',
    'tr',
    '[class*="row" i]',
    '[class*="history" i] li',
    '[class*="history" i] [class*="item" i]',
]

# In-row action area: any Download / Result / Error / View / Excel / attachment
# / action-menu control. Kept inside the row when downloadable results are shown.
HISTORY_ACTION_SELECTORS = list(dict.fromkeys([
    'a:has-text("Download")',
    'button:has-text("Download")',
    'a:has-text("Result")',
    'a:has-text("Error")',
    'a:has-text("View")',
    'button:has-text("View")',
    'a[href*=".xlsx" i]',
    'a[href*=".xls" i]',
    'a[href*=".csv" i]',
    'a[href*="download" i]',
    '[class*="download" i]',
    '[class*="excel" i]',
    '[class*="attachment" i]',
    '[title*="download" i]',
]))

# Result / error download actions per row. Live file names / labels are kept
# verbatim; nothing here invents status names.
HISTORY_ACTION_LABEL_CANDIDATES = ["Download", "Result", "Error", "View", "Download Result",
                                   "Download Error", "Action"]
HISTORY_DOWNLOAD_SELECTORS = list(dict.fromkeys([
    *RESULT_DOWNLOAD_SELECTORS,
    *ERROR_DOWNLOAD_SELECTORS,
    'a:has-text("Download")',
    'button:has-text("Download")',
    'a:has-text("View")',
    'button:has-text("View")',
    'a[class*="download" i]',
    'button[class*="download" i]',
    '[title*="download" i]',
]))

# Pagination / search / filter controls.
HISTORY_PAGINATION_SELECTORS = [
    '[class*="pagination" i]',
    '[class*="pager" i]',
    '[aria-label*="page" i]',
    'button:has-text("Next")',
    'a:has-text("Next")',
]
HISTORY_SEARCH_SELECTORS = [
    'input[placeholder*="search" i]',
    'input[placeholder*="filename" i]',
    'input[placeholder*="Search" i]',
    'input[type="search"]',
]
HISTORY_FILTER_SELECTORS = [
    'button:has-text("Filter")',
    'select',
    '[class*="filter" i]',
]

# ── Upload history row matching ──────────────────────────────────────────────

# Row-matching strategy (Stage 4C): a future upload must match its OWN history
# row using the strongest signals, in priority order, and must NEVER assume the
# first row belongs to us. Relevant local context:
#   * exact filename (our generated filename, e.g. OB_BATCH-....xlsx)
#   * timestamp / time window (upload documented just now)
#   * generated_file_id-linked local filename (service layer ties it to the row)
# The portal column headings let us locate the filename/status cells per row.
# This module centralizes the *selector* signals; the service layer is what maps
# a specific generated file to a specific row.

# Text markers used to identify an upload's processing state (mapped to our
# internal statuses without inventing portal wording; raw text is preserved).
UPLOAD_STATUS_MARKERS = {
    "success": ["success", "completed", "completed successfully", "processed", "uploaded"],
    "failed": ["failed", "error", "rejected", "unsuccessful"],
    "partial_failure": ["partial", "partially failed", "some failed"],
    "processing": ["processing", "in progress", "pending", "in queue", "submitted"],
    "uploading": ["uploading"],
}

# ── Helpers ──────────────────────────────────────────────────────────────────


def first(existing: list[str], *candidates: list[str]) -> str | None:
    """Return the first non-None selector, preferring the caller's candidate.

    ``existing`` may be empty; ``candidates`` are the default fallback lists in
    order. Returns ``None`` when nothing is configured.
    """
    for group in (existing, *candidates):
        for s in group:
            if s:
                return s
    return None


# ── Row-matching helpers (Stage 4D) ──────────────────────────────────────────

# Status wording the portal may use inside a history row. We never invent status
# names; we only map raw row text onto our internal vocabulary. Highly
# portal-agnostic: the actual representation is whatever text the real grid
# shows (a coloured badge, a <span>, a plain cell). We scan the row's visible
# text and match these markers so we do not depend on a specific DOM class.
HISTORY_ROW_STATUS_MARKERS = {
    "success": ["success", "completed", "processed", "uploaded", "submitted"],
    "failed": ["failed", "error", "rejected", "unsuccessful", "invalid"],
    "partial_failure": ["partial", "partially", "some failed"],
    "processing": ["processing", "in progress", "pending", "in queue"],
    "uploading": ["uploading"],
}


def ref_id_from_href(href: str | None) -> str | None:
    """Extract the portal request/reference id from a row's download href.

    Handles ``download?id=724648``, ``download/724648``, ``...?id=724648`` and a
    trailing numeric segment. Returns the numeric id, or None if none found.
    """
    import re
    if not href:
        return None
    m = re.search(r"[?&]id=(\d+)", href)
    if m:
        return m.group(1)
    m = re.search(r"(?:download|request|reference)[/=](\d+)", href, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4,})", href)
    if m:
        return m.group(1)
    return None


# ── Live-verified selector merge (Stage 4B) ──────────────────────────────────
# ``verified_selectors.py`` (if present) is written ONLY by the interactive
# ``scripts/verify_real_portal.py`` tool, from selectors actually confirmed on
# the real eSampark DOM. Each verified primary is PREPENDED to its default list
# so the real, stable selector wins while the original fallbacks remain intact.

_VERIFIED_BY_KEY: dict[str, str] = {
    "LDAP_USER_SELECTORS": "LDAP_USER",
    "LDAP_PASSWORD_SELECTORS": "LDAP_PASSWORD",
    "LOGIN_SUBMIT_SELECTORS": "LOGIN_SUBMIT",
    "LOGIN_FORM_SELECTORS": "LOGIN_FORM",
    "AUTHENTICATED_NAV_SIGNALS": "AUTHENTICATED_NAV",
    "NAV_ONBOARDING_SELECTORS": "NAV_ONBOARDING",
    "NAV_SELF_ONBOARDING_SELECTORS": "NAV_SELF_ONBOARDING",
    "NAV_FTC_SELECTORS": "NAV_FTC",
    "FTC_FILE_INPUT_SELECTORS": "FILE_INPUT",
    "FTC_SUBMIT_SELECTORS": "UPLOAD_SUBMIT",
    "UPLOAD_HISTORY_SELECTORS": "UPLOAD_HISTORY",
    "RESULT_DOWNLOAD_SELECTORS": "RESULT_DOWNLOAD",
    "ERROR_DOWNLOAD_SELECTORS": "ERROR_DOWNLOAD",
    "HISTORY_CONTAINER_SELECTORS": "HISTORY_CONTAINER",
    "HISTORY_ROW_SELECTORS": "HISTORY_ROW",
    "HISTORY_FILENAME_CELL_SELECTORS": "HISTORY_FILENAME_CELL",
    "HISTORY_TIMESTAMP_CELL_SELECTORS": "HISTORY_TIMESTAMP_CELL",
    "HISTORY_STATUS_CELL_SELECTORS": "HISTORY_STATUS_CELL",
    "HISTORY_REFERENCE_SELECTORS": "HISTORY_REFERENCE",
    "HISTORY_ACTION_SELECTORS": "HISTORY_ACTION",
    "HISTORY_SEARCH_SELECTORS": "HISTORY_SEARCH",
    "HISTORY_PAGINATION_SELECTORS": "HISTORY_PAGINATION",
    "HISTORY_FILTER_SELECTORS": "HISTORY_FILTER",
}

try:  # pragma: no cover - absent until the live tool writes it
    from app.portal import verified_selectors as _v  # type: ignore
    _live = getattr(_v, "VERIFIED", {}) or {}
    for _list_name, _key in _VERIFIED_BY_KEY.items():
        _primary = _live.get(_key)
        if not _primary:
            continue
        _lst = globals()[_list_name]
        if _lst and _lst[0] == _primary:
            continue  # already the primary; nothing to do
        # PROMOTE: put the live-verified selector first, and drop any duplicate
        # copy further down so the verified primary always wins.
        _lst[:] = [_primary] + [s for s in _lst if s != _primary]
except Exception:  # noqa: BLE001 - verified file is optional
    pass


# ── Robust FTC-page confirmation helper (Stage 4B reconciliation) ────────────
# Rule 5: consider the FTC page CONFIRMED when the real live upload interface
# AND the expected Self Onboarding context are present, even if literal "FTC"
# text is not discoverable. The verifier and the automation both call this so
# the rule lives in exactly one place.
#
# ``has_upload_control(page)``  -> True if a real file-upload input is visible.
# ``has_self_onboarding(page)`` -> True if the Self Onboarding nav context is
#                                  visible.
# ``ftc_page_confirmed(page)``  -> True when BOTH hold (the robust rule).
def has_upload_control(page) -> bool:
    """True when a real live file-upload control is visible on ``page``."""
    if page is None:
        return False
    try:
        if page.is_closed():
            return False
    except Exception:  # noqa: BLE001
        return False
    for sel in FTC_PAGE_UPLOAD_CONTROLS:
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def has_self_onboarding(page) -> bool:
    """True when the expected Self Onboarding navigation context is visible."""
    if page is None:
        return False
    try:
        if page.is_closed():
            return False
    except Exception:  # noqa: BLE001
        return False
    for sel in NAV_SELF_ONBOARDING_SELECTORS:
        try:
            if page.locator(sel).first.is_visible(timeout=700):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def ftc_page_confirmed(page) -> bool:
    """Robust FTC-page confirmation (rule 5).

    Confirmed when the real upload interface AND the Self Onboarding context are
    present, regardless of whether literal "FTC" text exists on the page.
    """
    return has_upload_control(page) and has_self_onboarding(page)

