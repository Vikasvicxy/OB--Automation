"""Business rules, mappings, and normalization logic for TeamHR Automation.

Keep all domain logic here. UI code should import from this module.
Future: replace temporary data with Excel master imports.
"""

import re
import difflib
from datetime import datetime
from typing import Optional

from app import master_data

# ── Cost Code Mappings ───────────────────────────────────────────────────────

COST_CODES = {
    "4421": {
        "code": "4421",
        "label": "4421 - Flipkart - Last Mile",
        "entity": "Flipkart",
        "operation": "Last Mile",
        "team": "LAST MILE - OPERATIONS",
        "prefix": "LM",
    },
    "4441": {
        "code": "4441",
        "label": "4441 - Flipkart - First Mile",
        "entity": "Flipkart",
        "operation": "First Mile",
        "team": "FIRST MILE - OPERATIONS",
        "prefix": "FM",
    },
    "8751": {
        "code": "8751",
        "label": "8751 - Myntra - Last Mile",
        "entity": "Myntra",
        "operation": "Last Mile",
        "team": "LAST MILE - OPERATIONS",
        "prefix": "LM",
    },
    "8752": {
        "code": "8752",
        "label": "8752 - Myntra - First Mile",
        "entity": "Myntra",
        "operation": "First Mile",
        "team": "FIRST MILE - OPERATIONS",
        "prefix": "FM",
    },
}

# ── Facility Type Rules (Cost Code -> Auto Facility Type) ────────────────────
# Auto-set from the cost code's operation:
#   Last Mile  -> "Delivery Hub"
#   First Mile -> "Pickup Hub"
# 8752 (Myntra First Mile) has NO production hub master yet, so it stays
# unresolved ("") and is marked "Needs Review" instead of auto-assigned.
# Keep this mapping centralized here so it can be changed if business rules
# change later.

COST_CODE_FACILITY_TYPE = {
    "4421": "Delivery Hub",
    "4441": "Pickup Hub",
    "8751": "Delivery Hub",
    "8752": "",
}

# Facility Type by operation (used when the facility master row provides the
# operation). Convenience view of the same rule above.
OPERATION_FACILITY_TYPE = {
    "Last Mile": "Delivery Hub",
    "First Mile": "Pickup Hub",
}

# Canonical values written to the Self-Onboarding workbook's Facility Type* col.
FACILITY_TYPE_DELIVERY = "DELIVERY_HUB"
FACILITY_TYPE_PICKUP = "PICKUP_HUB"
FACILITY_TYPE_REVIEW = "Needs Review"


def facility_type_display(cost_code: str) -> str:
    """Display value for the given cost code ('' means unresolved / review)."""
    return COST_CODE_FACILITY_TYPE.get(cost_code, "")


def facility_type_for_master_row(master_row: Optional[dict]) -> str:
    """Facility Type from an effective facility master row.

    The effective row already carries a facility_type; when it is missing we
    fall back to the operation-derived rule. Returns '' when unresolved.
    """
    if not master_row:
        return ""
    row_type = (master_row.get("facility_type") or "").strip()
    if row_type:
        return row_type
    operation = (master_row.get("operation") or "").strip()
    return OPERATION_FACILITY_TYPE.get(operation, "")


def facility_type_output(cost_code: str, master_row: Optional[dict] = None) -> str:
    """Canonical Facility Type value written to the Self-Onboarding workbook.

    Resolved in this priority:
      1. Effective facility master row facility_type (authoritative, e.g. an
         admin override), translated to the canonical DELIVERY_HUB/PICKUP_HUB.
      2. Cost-code rule (COST_CODE_FACILITY_TYPE) as a safe fallback.
      3. "" => caller should flag "Needs Review".
    """
    row_type = facility_type_for_master_row(master_row) if master_row else ""
    resolved = row_type or facility_type_display(cost_code)
    if not resolved:
        return ""
    return {
        "Delivery Hub": FACILITY_TYPE_DELIVERY,
        "Pickup Hub": FACILITY_TYPE_PICKUP,
    }.get(resolved, "")


def update_facility_type_rule(cost_code: str, display_type: str) -> None:
    """Admin-master override hook (not used by default; kept for future use)."""
    if cost_code in COST_CODE_FACILITY_TYPE:
        COST_CODE_FACILITY_TYPE[cost_code] = display_type

# ── Role Definitions ─────────────────────────────────────────────────────────

# Generic role names keyed by operational prefix. Used to derive role name
# formatting. The authoritative per cost-code role lists are in
# COST_CODE_ROLES below.
ROLES = {
    "LM": [
        "LM - Delivery Executive",
        "LM - Sorter",
        "LM - Team Leader",
        "LM - Prexo Delivery Executive",
    ],
    "FM": [
        "FM - Delivery Executive",
        "FM - Sorter",
        "FM - Team Leader",
    ],
}

# ── Role Compatibility (Cost Code -> compatible role names) ─────────────────
# Authoritative role list shown/validated for each cost code comes from the
# official Designation Master (master_data.get_roles_for_cost_code).
# 8751 (Myntra Last Mile) intentionally EXCLUDES Prexo Delivery Executive.
# These module attributes are snapshots refreshed by refresh_masters().
COST_CODE_ROLES: dict[str, list[str]] = {}


def _build_cost_code_roles() -> dict[str, list[str]]:
    return {cc: master_data.get_roles_for_cost_code(cc) for cc in ("4421", "4441", "8751", "8752")}


# ── Role Aliases ─────────────────────────────────────────────────────────────
# Intelligent aliases that a user/OCR may type. An alias only resolves to an
# OFFICIAL master designation and only when valid for the selected cost code.
_BASE_ROLE_ALIASES = {
    "biker": "Delivery Executive",
    "delivery": "Delivery Executive",
    "delivery executive": "Delivery Executive",
    "sort": "Sorter",
    "sorter": "Sorter",
    "tl": "Team Leader",
    "team lead": "Team Leader",
    "team leader": "Team Leader",
    "prexo": "Prexo Delivery Executive",
    "prexo delivery": "Prexo Delivery Executive",
}

# ROLE_ALIASES is refreshed by refresh_masters() to also include admin-managed
# aliases (merged through the production master). Kept as a module-level dict so
# the resolver reads one moving snapshot.
ROLE_ALIASES = dict(_BASE_ROLE_ALIASES)

# ── Facility Types ───────────────────────────────────────────────────────────

FACILITY_TYPES = ["Delivery Hub", "Pickup Hub"]

FACILITY_TYPE_ALIASES = {
    "delivery": "Delivery Hub",
    "del": "Delivery Hub",
    "del hub": "Delivery Hub",
    "delivery hub": "Delivery Hub",
    "dly hub": "Delivery Hub",
    "dly": "Delivery Hub",
    "hub delivery": "Delivery Hub",
    "pickup": "Pickup Hub",
    "pick": "Pickup Hub",
    "pick up": "Pickup Hub",
    "pickup hub": "Pickup Hub",
    "pikup": "Pickup Hub",
    "pickhub": "Pickup Hub",
}

# ── Hub Master Data ──────────────────────────────────────────────────────────
# Official master facility names (from the official Facility / Location Master
# in master_data). These are preserved exactly as-is and never renamed.
# Classification rules (in priority order):
#   - facility name contains "MYNTRA"    -> Myntra hub
#   - facility name ends with "_PL"      -> Flipkart First Mile
#   - otherwise                          -> Flipkart Last Mile
HUB_MASTER: list[str] = []


def classify_hub(hub_name: str) -> str:
    """Classify a facility as LM, FM, or Myntra based on name.

    Priority order:
        1. name contains "MYNTRA"        -> "Myntra"
        2. name ends with "_PL"          -> "FM"  (Flipkart First Mile)
        3. otherwise                      -> "LM"  (Flipkart Last Mile)
    """
    upper = hub_name.upper()
    if "MYNTRA" in upper:
        return "Myntra"
    if hub_name.endswith("_PL"):
        return "FM"
    return "LM"


def refresh_masters() -> None:
    """Re-sync snapshot attributes after a master-data reload."""
    global COST_CODE_ROLES, HUB_MASTER, ROLE_ALIASES
    COST_CODE_ROLES = _build_cost_code_roles()
    HUB_MASTER = master_data.get_facility_names()
    ROLE_ALIASES = dict(_BASE_ROLE_ALIASES)
    # Merge admin-managed role aliases through the production master. Admin
    # aliases map alias -> OFFICIAL role name (e.g. 'tl' -> 'LM - Team Leader');
    # normalize them to a base keyword so prefix+base resolution still works.
    try:
        admin_aliases = master_data.get_role_aliases()
    except Exception:  # noqa: BLE001
        admin_aliases = {}
    for key, official in admin_aliases.items():
        k = str(key).strip().lower()
        if not k:
            continue
        off = str(official)
        base = re.sub(r"^(LM|FM)\s*-\s*", "", off, flags=re.IGNORECASE).strip()
        ROLE_ALIASES[k] = base or off


def get_hubs_for_cost_code(cost_code: str) -> list[str]:
    """Return filtered official hub list for the given cost code.

    4421 -> Flipkart LM hubs only
    4441 -> Flipkart FM hubs only
    8751 -> Myntra LM hubs only
    8752 -> EMPTY (Myntra First Mile hub master not configured yet)
    """
    return master_data.get_hubs_for_cost_code(cost_code)


def get_location_for_facility(facility_name: str) -> str:
    """Exact official LOCATION code for a facility (from the master row)."""
    return master_data.get_location_for_facility(facility_name)


def fuzzy_find_hub(query: str, hubs: list[str], top_n: int = 5) -> list[str]:
    """Return up to top_n closest official facility names to ``query``.

    The returned values are ALWAYS exact master values (never invented).
    """
    q = (query or "").strip()
    if not q or not hubs:
        return hubs[:top_n]
    q_lower = q.lower()
    if len(q_lower) < 3:
        return hubs[:top_n]
    scored = []
    for hub in hubs:
        hl = hub.lower()
        if q_lower in hl:
            scored.append((hub, 1.0))
            continue
        # Compare against name with noise tokens stripped to absorb typos.
        hub_clean = re.sub(r"[\s_\-]+", "", hl).replace("hub", "").replace("blr", "").replace("pl", "")
        if len(hub_clean) >= 4:
            base = difflib.SequenceMatcher(None, q_lower.replace(" ", ""), hub_clean).ratio()
        else:
            base = difflib.SequenceMatcher(None, q_lower, hl).ratio()
        # Token overlap: partial words like "nelmangla" / "banas" / "hebb".
        q_tokens = [t for t in re.split(r"[\s_\-]+", q_lower) if t]
        hits = sum(1 for t in q_tokens if len(t) >= 3 and t in hl)
        token_score = hits / max(len(q_tokens), 1)
        score = max(base, token_score * 0.85)
        if score >= 0.4:
            scored.append((hub, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:top_n]]


# ── Candidate Data Structure ─────────────────────────────────────────────────

CANDIDATE_FIELDS = [
    "candidate_id",
    "batch_id",
    "candidate_number",
    "name",
    "mobile",
    "aadhaar_filename",
    "aadhaar_number",
    "address",
    "dob",
    "doj",
    "entity",
    "cost_code",
    "operation",
    "team",
    "designation",
    "facility_type",
    "facility_name",
    "location_code",
    "salary",
    "salary_display",
    "migrant",
    "contractor",
    "lob",
    "status",
    "created_date",
    "created_time",
    "upload_filename",
    "portal_status",
    "portal_remarks",
]

# ── Mobile Normalization ─────────────────────────────────────────────────────


def normalize_mobile(raw: str) -> tuple[str, Optional[str]]:
    """Normalize Indian mobile number to exactly 10 digits.

    Accepts formats like:
        +91 98765 43210
        +91-98765-43210
        91 98765 43210
        91-9876543210
        98765 43210
        9876543210

    Returns (normalized_digits, error_message_or_None).
    """
    digits = re.sub(r"\D", "", raw.strip())
    if digits.startswith("91") and len(digits) > 10:
        digits = digits[2:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits, None
    return digits, "Enter a valid 10-digit Indian mobile number"


# ── Role Resolution ──────────────────────────────────────────────────────────


def resolve_role(query: str, prefix: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a role query to an official role name within the given prefix.

    Supports:
        - Direct alias lookup (biker -> Delivery Executive)
        - Exact full role name match
        - Fuzzy matching via difflib

    Returns (full_role_name, error_or_None).
    """
    query_lower = query.strip().lower()
    available = ROLES.get(prefix, [])
    if not available:
        return None, f"No roles available for prefix {prefix}"

    # 1. Direct alias match
    base = ROLE_ALIASES.get(query_lower)
    if base:
        full = f"{prefix} - {base}"
        if full in available:
            return full, None

    # 2. Exact match on full role name
    for role in available:
        if query_lower == role.lower():
            return role, None

    # 3. Fuzzy match on full role names
    role_lower_map = {r.lower(): r for r in available}
    matches = difflib.get_close_matches(
        query_lower, list(role_lower_map.keys()), n=1, cutoff=0.55
    )
    if matches:
        return role_lower_map[matches[0]], None

    # 4. Fuzzy match on alias keys
    alias_matches = difflib.get_close_matches(
        query_lower, list(ROLE_ALIASES.keys()), n=1, cutoff=0.55
    )
    if alias_matches:
        base = ROLE_ALIASES[alias_matches[0]]
        full = f"{prefix} - {base}"
        if full in available:
            return full, None

    return None, f'No matching role found for "{query}"'


def get_roles_for_cost_code(cost_code: str) -> list[str]:
    """Return the authoritative compatible role list for a cost code."""
    return master_data.get_roles_for_cost_code(cost_code)


def resolve_role_for_cost_code(
    query: str, cost_code: str
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a role query to an official role name valid for the cost code.

    Role must be compatible with the selected cost code. Entity- and
    operation-specific rules are applied (e.g. Prexo is NOT available for
    Myntra Last Mile / 8751).

    Returns (official_role, error_or_None).
    """
    info = COST_CODES.get(cost_code)
    if not info:
        return None, f"No roles available for cost code {cost_code}"
    prefix = info["prefix"]
    available = get_roles_for_cost_code(cost_code)
    if not available:
        return None, f"No roles available for cost code {cost_code}"

    query_lower = query.strip().lower()
    if not query_lower:
        return None, "Role is required"

    # Specific rejection message for Prexo on Myntra Last Mile (8751)
    if cost_code == "8751" and "prexo" in query_lower:
        return None, "Prexo Delivery Executive is not available for Myntra Last Mile."

    # 1. Direct alias match
    base = ROLE_ALIASES.get(query_lower)
    if base:
        full = f"{prefix} - {base}"
        if full in available:
            return full, None

    available_lower = [r.lower() for r in available]

    # 2. Exact match on full official role name
    if query_lower in available_lower:
        return available[available_lower.index(query_lower)], None

    # 3. Fuzzy match on full official role names
    matches = difflib.get_close_matches(
        query_lower, available_lower, n=1, cutoff=0.55
    )
    if matches:
        return available[available_lower.index(matches[0])], None

    # 4. Fuzzy match on alias keys
    alias_matches = difflib.get_close_matches(
        query_lower, list(ROLE_ALIASES.keys()), n=1, cutoff=0.55
    )
    if alias_matches:
        base = ROLE_ALIASES[alias_matches[0]]
        full = f"{prefix} - {base}"
        if full in available:
            return full, None

    return None, f'No matching role found for "{query}" in cost code {cost_code}'


# ── Document Intelligence / Extraction Rule Passes ───────────────────────────
# Same-family helpers for Smart Upload OCR. These pass detected raw values
# through the authoritative business rules so we never trust OCR blindly.


# Phrase forms that express an explicit operation (LM / FM) even when the bare
# "lm"/"fm" token is not present. These have the HIGHEST precedence so that
# "first mile sorter" is read as First Mile (+ Sorter) and never falls back to
# a role/facility-derived operation.
_OPERATION_PHRASE_PATTERNS = {
    "LM": [
        re.compile(r"\blast\s*-?\s*mile\b"),
        re.compile(r"\blm\b"),
        re.compile(r"(^|\s|\b)lm(\s|\b)"),
    ],
    "FM": [
        re.compile(r"\bfirst\s*-?\s*mile\b"),
        re.compile(r"\bfm\b"),
        re.compile(r"(^|\s|\b)fm(\s|\b)"),
    ],
}


def _detect_operation_prefix(text: str) -> Optional[str]:
    """Return "LM" or "FM" if the recruiter text expresses an explicit operation.

    Recognizes bare LM/FM tokens AND the phrases "last mile" / "first mile"
    (with optional hyphen or spacing). Explicit LM/FM text wins over any
    role/facility fallback.
    """
    lower = (text or "").strip().lower()
    if not lower:
        return None
    # "last mile" / "first mile" phrases first (most specific).
    for phrase in (r"\blast\s*-?\s*mile\b", r"\bfirst\s*-?\s*mile\b"):
        if re.search(phrase, lower):
            return "LM" if phrase.startswith(r"\blast") else "FM"
    # Bare lm/fm tokens, bounded by word boundaries.
    if re.search(r"(^|[\s\W])fm([\s\W]|$)", lower):
        return "FM"
    if re.search(r"(^|[\s\W])lm([\s\W]|$)", lower):
        return "LM"
    # Prefix-adjacent forms like "LM sorter" / "fmsorter".
    if re.search(r"(^|\s)lm[\s\-]?\w", lower):
        return "LM"
    if re.search(r"(^|\s)fm[\s\-]?\w", lower):
        return "FM"
    return None


def parse_role_text(text: str) -> tuple[Optional[str], Optional[str]]:
    """Parse free role text like 'LM sorter' / 'FM biker' / 'team lead'.

    Returns (prefix_hint, base_role_keyword) or (None, None).
    base_role_keyword is an official base like 'Sorter' / 'Delivery Executive'.
    """
    lower = (text or "").strip().lower()
    if not lower:
        return None, None

    prefix_hint = _detect_operation_prefix(lower)

    # Strip the operation phrase/token so it can never be misread as a role
    # base keyword (e.g. "first mile sorter" -> "sorter", not "mile").
    role_scan = lower
    if prefix_hint:
        role_scan = re.sub(r"\blast\s*-?\s*mile\b", " ", role_scan)
        role_scan = re.sub(r"\bfirst\s*-?\s*mile\b", " ", role_scan)
        role_scan = re.sub(r"(^|[\s\W])fm([\s\W]|$)", " ", role_scan)
        role_scan = re.sub(r"(^|[\s\W])lm([\s\W]|$)", " ", role_scan)

    # Match the longest known alias/keyword first for accurate base detection
    for keyword, base in sorted(ROLE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if keyword and keyword in role_scan:
            return prefix_hint, base

    # Fuzzy fallback against keywords (handles OCR noise like "deliver").
    # Threshold 0.8 cleanly separates real role keywords from coincidental
    # token similarity ("note"/"sorter" ~0.6 is NOT a role signal).
    FUZZY_ROLE_MIN = 0.8
    best_keyword = None
    best_score = 0.0
    for keyword in ROLE_ALIASES:
        if not keyword or len(keyword) < 3:
            continue
        # token-level fuzzy: compare each token
        for tok in re.split(r"[\s_\-]+", role_scan):
            if not tok:
                continue
            if tok in keyword:
                return prefix_hint, ROLE_ALIASES[keyword]
            score = difflib.SequenceMatcher(None, tok, keyword).ratio()
            if score > best_score:
                best_score = score
                best_keyword = keyword
    if best_keyword and best_score >= FUZZY_ROLE_MIN:
        return prefix_hint, ROLE_ALIASES[best_keyword]

    return prefix_hint, None


def _cost_code_by_prefix_hint(prefix_hint: Optional[str]) -> list[str]:
    """Return candidate cost codes for a role prefix hint (or all valid codes)."""
    if prefix_hint == "LM":
        return ["4421", "8751"]
    if prefix_hint == "FM":
        return ["4441", "8752"]
    return list(COST_CODES.keys())


def match_role_from_text(
    text: str, cost_code: Optional[str] = None
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve a detected role phrase to an official role name.

    Returns (official_role, matched_cost_code, error_or_None).
    """
    prefix_hint, base = parse_role_text(text)
    if not base:
        return None, None, 'Could not determine a role from "{text}"'

    codes: list[str]
    if cost_code and cost_code in COST_CODES:
        codes = [cost_code]
    else:
        codes = _cost_code_by_prefix_hint(prefix_hint)

    for code in codes:
        prefix = COST_CODES[code]["prefix"]
        full = f"{prefix} - {base}"
        if full in get_roles_for_cost_code(code):
            return full, code, None

    hint = "-".join(codes) if codes else "any cost code"
    return None, None, f'"{base}" has no compatible role for {hint}'


def infer_cost_code_from_hub(
    hub_name: str,
) -> tuple[Optional[str], Optional[str]]:
    """Suggest a cost code from a master hub name using classification rules.

    contains MYNTRA     -> 8751 (Myntra Last Mile)
    ends with _PL       -> 4441 (Flipkart First Mile)
    otherwise           -> 4421 (Flipkart Last Mile)
    Never infers 8752 from a Myntra LM hub.

    Returns (cost_code, reason) or (None, None).
    """
    if not hub_name:
        return None, None
    cls = classify_hub(hub_name)
    if cls == "Myntra":
        return "8751", "Derived from Myntra LM hub"
    if cls == "FM":
        return "4441", "Derived from Flipkart FM hub"
    if cls == "LM":
        return "4421", "Derived from Flipkart LM hub"
    return None, None


def match_hub_from_text(
    text: str, cost_code: Optional[str] = None
) -> tuple[Optional[str], Optional[str]]:
    """Fuzzy match a detected hub phrase against the official master hub list.

    When ``cost_code`` is provided, matching is constrained to the hubs valid
    for that cost code (e.g. Flipkart Last Mile -> 4421 hubs only), so an
    explicit LM operation can never match an FM (``_PL``) facility and vice
    versa. Uses the global master when ``cost_code`` is omitted.

    Combines whole-phrase similarity with token-level signals so realistic
    OCR typos/misspellings are accepted while random/ambiguous phrases are
    rejected.

    Facility safety:
      * Generic/structural tokens (Myntra, hub, LM/FM, first/last mile, _PL)
        never create a strong match by themselves — a meaningfully overlapping
        locality token (e.g. "Peenya", "Banaswadi", "Hebbal") is required.
      * A query with no such locality token (e.g. "unknownmyntrahub") yields no
        match, so the facility is left blank and sent to Needs Review rather
        than over-matched to an arbitrary real hub.

    Returns (official_hub, reason) or (None, None). The final value always comes
    from the master list; we never invent a facility.
    """
    query = (text or "").strip()
    if not query or len(query) < 4:
        return None, None
    candidates = get_hubs_for_cost_code(cost_code) if cost_code else HUB_MASTER
    if not candidates:
        return None, None
    q_low = query.lower()

    # Exact match always wins. Without this, a query like "PeenyaHub_BLR" could
    # be overtaken by the substring-extended "PeenyaHub_BLR_PL", incorrectly
    # flipping an exact LM hub into an FM (_PL) facility.
    for hub in candidates:
        if hub.lower() == q_low:
            return hub, "Screenshot + master match"

    q_tokens = [t for t in re.split(r"[\s_\-]+", q_low) if t]
    # Discriminative locality tokens — excludes generic/structural tokens such
    # as myntra/hub/lm/fm/first/last/mile/pl. No locality token -> no strong
    # match; this prevents "unknownmyntrahub" from matching any Myntra hub.
    q_disc = [t for t in q_tokens if t not in _GENERIC_FACILITY_TOKENS]
    if not q_disc:
        return None, None

    q_core = _facility_core(q_low)

    best_hub: Optional[str] = None
    best_score = 0.0
    best_token = 0.0

    for hub in candidates:
        h_low = hub.lower()
        # Whole-phrase similarity on discriminative locality cores.
        h_core = _facility_core(h_low)
        base = difflib.SequenceMatcher(None, q_core, h_core).ratio()
        # Meaningful single-locality-token overlap (typo tolerant).
        h_tokens = [t for t in re.split(r"[\s_\-]+", h_core) if t]
        token = 0.0
        for qt in q_disc:
            if not qt:
                continue
            for ht in h_tokens:
                if not ht:
                    continue
                if qt == ht:
                    token = max(token, 1.0)
                elif qt in ht or ht in qt:
                    token = max(token, 0.9)
                else:
                    token = max(token, difflib.SequenceMatcher(None, qt, ht).ratio())
        score = max(base, token * 0.9)
        if score > best_score:
            best_score, best_hub, best_token = score, hub, token

    if not best_hub:
        return None, None

    # Require a strong whole-core match OR a strong locality-token overlap.
    # This rejects coincidental generic matches ("myntra hub" against any
    # Myntra hub; "unknown" against unrelated localities).
    if best_score >= 0.55 or best_token >= 0.8:
        return best_hub, "Screenshot + master match"
    return None, None


# Generic / structural tokens that must never, on their own, produce a strong
# facility match. They are removed before scoring so only real locality overlap
# (Peenya / Banaswadi / Hebbal / Nelamangala ...) can drive a fuzzy hit.
_GENERIC_FACILITY_TOKENS = {
    "myntra", "mynt", "hub", "hubs", "lm", "fm", "first", "last", "mile", "pl",
}

_GENERIC_FACILITY_STRIP = re.compile(
    r"(myntra|mynt|hub|_pl|_blr|pl|\blm\b|\bfm\b|\bfirst\b|\blast\b|\bmile\b)",
    re.IGNORECASE,
)


def _facility_core(s: str) -> str:
    """Reduce a facility/query string to its discriminative locality tokens by
    removing generic/structural tokens (Myntra, hub, _PL, BLR, LM/FM, first/last
    mile). Used so only real locality overlap can produce a strong fuzzy match."""
    s = _GENERIC_FACILITY_STRIP.sub(" ", s or "")
    return re.sub(r"[\s_\-]+", " ", s).strip()


def _hub_query_op_signal(query: str) -> Optional[str]:
    """Return the explicit operation ('LM'/'FM') signaled by a hub query's own
    text, or None.

    A hub query that itself carries a ``_PL`` / first-mile / ``fm`` marker (e.g.
    "WhitefieldHub_BLR_PL" or "Whitefield-PL") explicitly denotes First Mile, so
    it is NOT ambiguous. A bare locality query such as "Peenya hub" carries no
    operation signal and is ambiguous when BOTH a Last Mile and a First Mile hub
    of the same locality exist.
    """
    low = (query or "").strip().lower()
    if not low:
        return None
    if "_pl" in low or re.search(r"(^|[\s_\-])pl([\s_\-]|$)", low):
        return "FM"
    if re.search(r"\bfirst\s*-?\s*mile\b", low) or re.search(r"(^|[\s_\-])fm([\s_\-]|$)", low):
        return "FM"
    if re.search(r"\blast\s*-?\s*mile\b", low) or re.search(r"(^|[\s_\-])lm([\s_\-]|$)", low):
        return "LM"
    return None


# Tokens that signal the Myntra entity in recruiter screenshot text.
_MYNTRA_TOKENS = ("myntra", "mynt ")


def _detect_myntra(*texts: Optional[str]) -> bool:
    """Return True if any recruiter text mentions Myntra (case-insensitive)."""
    for text in texts:
        if not text:
            continue
        low = (text or "").lower()
        for tok in _MYNTRA_TOKENS:
            if tok in low:
                return True
    return False


def detect_entity(role_text: Optional[str], hub_text: Optional[str]) -> tuple[str, bool]:
    """Determine the entity (Flipkart / Myntra) from recruiter screenshot text.

    Returns (entity, myntra_detected).  Myntra is preferred whenever the
    recruiter text mentions Myntra; otherwise the entity defaults to Flipkart.
    """
    if _detect_myntra(role_text, hub_text):
        return "Myntra", True
    return "Flipkart", False


def _cost_code_candidates(prefix: Optional[str], myntra: bool) -> list[str]:
    """Candidate cost codes ordered by entity preference for the given prefix.

    When Myntra is detected the Myntra cost code comes first so it wins
    resolution; otherwise the Flipkart cost code comes first.
    """
    if prefix == "LM":
        return ["8751", "4421"] if myntra else ["4421", "8751"]
    if prefix == "FM":
        return ["8752", "4441"] if myntra else ["4441", "8752"]
    if myntra:
        return ["8751", "8752"]
    return ["4421", "4441", "8751", "8752"]


def resolve_smart_onboarding(
    role_text: Optional[str], hub_text: Optional[str]
) -> dict:
    """Resolve recruiter screenshot evidence into official role/cost code/facility.

    Required precedence (fixes the Pallavi regression where an explicit "LM
    sorter" was overridden by a hub-suffix ``_PL``/FM inference, and the Prem
    case where a Myntra hub text was forced into Flipkart 4421):
      1. Explicit recruiter operation text (LM / FM) — from the role phrase.
      2. Entity detection (Myntra vs Flipkart) from the recruiter text.
      3. Explicit recruiter role text — base keyword from the role phrase.
      4. Filter designation & facility masters using those constraints.
      5. Fuzzy-match the facility within the constrained master set.
      6. Use hub-suffix inference ONLY when LM/FM is NOT explicitly known.

    Entity precedence:
      * Myntra + LM -> 8751    Myntra + FM -> 8752
      * Flipkart + LM -> 4421  Flipkart + FM -> 4441
      * A Myntra hub text never resolves to a Flipkart cost code / hub.

    Conflict handling: an explicit LM (or FM) that cannot be reconciled with
    the available facility master keeps the explicit operation; it never flips
    LM -> FM. Incompatible facilities (_PL hub for an LM cost code, or a
    Flipkart hub for a Myntra cost code) are HARD REJECTED and never selected.

    Returns a dict with key: operation_known, prefix, entity, myntra,
    cost_code, cost_source, role, role_source, facility, facility_source,
    role_unresolved, needs_attention (list of str).
    """
    prefix, base = parse_role_text(role_text or "")
    operation_known = prefix in ("LM", "FM")

    entity, myntra = detect_entity(role_text, hub_text)

    cost_code: Optional[str] = None
    cost_source = "Manual"
    role_val = ""
    role_source = "Manual"
    facility_val = ""
    facility_source = "Manual"
    needs_attention: list[str] = []
    role_unresolved = False

    candidate_codes = _cost_code_candidates(prefix, myntra)

    if operation_known:
        # Default to the entity-preferred Flipkart code (or Myntra when detected).
        default_code = {
            ("LM", True): "8751", ("FM", True): "8752",
            ("LM", False): "4421", ("FM", False): "4441",
        }[(prefix, myntra)]
        if default_code not in candidate_codes:
            default_code = candidate_codes[0]

        # Step 2-3: resolve role within the CURRENT ENTITY's constrained
        # designation masters ONLY.  A role that is invalid for the detected
        # entity (e.g. Prexo on Myntra/8751) must NEVER pull the resolution
        # back across to the other entity's cost code (4421). Invalid role ->
        # blank role + Needs Review, keeping the Myntra entity.
        entity_codes = {
            ("LM", True): ["8751"], ("FM", True): ["8752"],
            ("LM", False): ["4421"], ("FM", False): ["4441"],
        }[(prefix, myntra)]
        if base:
            for code in entity_codes:
                full = f"{prefix} - {base}"
                if full in get_roles_for_cost_code(code):
                    role_val = full
                    cost_code = code
                    role_source = "Screenshot + master match"
                    break
        cost_source = (
            f"Screenshot {entity} {prefix} evidence"
            if myntra else f"Screenshot {prefix} evidence"
        )
        if not role_val:
            # Role base not resolvable for any candidate code; keep operation.
            role_unresolved = True
            role_source = "Screenshot"
            cost_code = default_code

        # Step 4: fuzzy-match facility constrained to the chosen cost code so an
        # explicit LM never matches an FM (_PL) hub (and vice versa), and a
        # Myntra cost code never matches a Flipkart hub.
        if hub_text:
            hub, reason = match_hub_from_text(hub_text, cost_code)
            if hub:
                facility_val = hub
                facility_source = reason or "Screenshot + master match"
            else:
                # No compatible hub matched. Confirm an incompatible hub exists to
                # give a precise conflict message without flipping the operation
                # or selecting the incompatible facility.
                other_code = _opposite_code_for_prefix(prefix, myntra)
                other_hub, _ = match_hub_from_text(hub_text, other_code)
                what = COST_CODES.get(cost_code, {}).get("operation", "selection")
                if other_hub:
                    needs_attention.append(
                        f'Facility "{hub_text.strip()}" resolves to a hub incompatible with '
                        f"{entity} {what} (cost code {cost_code}) — review required."
                    )
                else:
                    needs_attention.append(
                        f'Facility "{hub_text.strip()}" does not match any {entity} {what} hub for cost code {cost_code} — review required.'
                    )
    else:
        # Step 5: operation NOT explicit. Per the precedence matrix, explicit
        # entity + exact/strong official facility evidence are evaluated BEFORE
        # any role-default inference, so a Myntra hub or a Flipkart _PL hub can
        # never be erased by a "role sorter -> LM/4421" default.
        facility_op: Optional[str] = None
        hub_exact = False
        if hub_text:
            # Facility matching respects entity precedence: once the entity is
            # Myntra (from any recruiter text), the search is restricted to the
            # Myntra facility set so a Myntra case can NEVER land on a Flipkart
            # hub (HebbalHub_BLR / HebbalHub_BLR_PL) and vice versa.
            hub, reason = match_hub_from_text(hub_text, "8751" if myntra else None)
            if hub:
                hub_exact = (hub.lower() == hub_text.strip().lower())
                facility_val = hub
                facility_source = reason or "Screenshot + master match"
                cls = classify_hub(hub)
                if cls == "Myntra":
                    myntra, entity = True, "Myntra"
                    facility_op = "LM"
                elif cls == "FM":
                    facility_op = "FM"
                else:
                    facility_op = "LM"

        # Ambiguity guard: a BARE hub — no exact official facility, no explicit
        # LM/FM, no role base — that matches BOTH a Last Mile and a First Mile
        # hub of the same entity must NOT be auto-committed to one operation
        # (e.g. "Peenya hub" -> both PeenyaHub_BLR and PeenyaHub_BLR_PL exist).
        ambiguous = bool(
            hub_text
            and not myntra
            and base is None
            and facility_op
            and not hub_exact
            and _hub_query_op_signal(hub_text) is None
            and match_hub_from_text(hub_text, "4421")[0] is not None
            and match_hub_from_text(hub_text, "4441")[0] is not None
        )
        if ambiguous:
            needs_attention.append(
                f'Facility "{hub_text.strip()}" is ambiguous between Last Mile and First Mile hubs — review required.'
            )
            facility_val = ""
            facility_source = "Manual"
        elif facility_op:
            # Derive cost code from entity + facility-derived operation. Entity
            # (explicit Myntra text or exact Myntra hub) wins over Flipkart.
            cost_code = {
                ("LM", True): "8751", ("FM", True): "8752",
                ("LM", False): "4421", ("FM", False): "4441",
            }[(facility_op, myntra)]
            cost_source = (
                "Derived from Myntra facility evidence" if myntra
                else "Derived from facility operation evidence"
            )
            # Resolve role constrained to the facility-derived cost code.
            if base:
                full = f"{COST_CODES[cost_code]['prefix']} - {base}"
                if full in get_roles_for_cost_code(cost_code):
                    role_val = full
                    role_source = "Screenshot + master match"
            # A decisive facility/operation with no resolvable role -> Needs
            # Review (the recruiter gave a role-less, operation-committed case).
            if not role_val:
                role_unresolved = True
                role_source = "Screenshot"
            # Confirm the facility belongs to the derived cost code's hub set.
            allowed = get_hubs_for_cost_code(cost_code)
            if facility_val and allowed and facility_val not in allowed:
                needs_attention.append(
                    f'Hub "{facility_val}" conflicts with cost code {cost_code} — review required.'
                )
        else:
            # No strong facility evidence -> fall back to role text alone.
            if base:
                role_val, cost_code, _err = match_role_from_text(role_text, None)
                if role_val:
                    role_source = "Screenshot + master match"
                    cost_source = "Derived from recruiter role"
            if not role_val:
                role_unresolved = True
            if hub_text and cost_code:
                hub2, reason2 = match_hub_from_text(hub_text, cost_code)
                if hub2:
                    facility_val = hub2
                    facility_source = reason2 or "Screenshot + master match"
            if facility_val and cost_code:
                allowed = get_hubs_for_cost_code(cost_code)
                if allowed and facility_val not in allowed:
                    needs_attention.append(
                        f'Hub "{facility_val}" conflicts with role/operation-derived cost code {cost_code}.'
                    )

    return {
        "operation_known": operation_known,
        "prefix": prefix,
        "entity": entity,
        "myntra": myntra,
        "cost_code": cost_code or "",
        "cost_source": cost_source,
        "role": role_val,
        "role_source": role_source,
        "facility": facility_val,
        "facility_source": facility_source,
        "role_unresolved": role_unresolved,
        "needs_attention": needs_attention,
    }


def _opposite_code_for_prefix(prefix: str, myntra: bool) -> str:
    """Return the cost code for the OTHER entity of the same operation prefix.

    Used to confirm an incompatible facility conflict without selecting it.
    """
    if prefix == "LM":
        return "8751" if not myntra else "4421"
    if prefix == "FM":
        return "8752" if not myntra else "4441"
    return ""


SNIPPET_ROLE_SOURCE = "Screenshot + master match"
SNIPPET_HUB_SOURCE = "Screenshot + master match"


# ── Facility Type Resolution ─────────────────────────────────────────────────


def resolve_facility_type(query: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a facility type query to an official value.

    Returns (official_value, error_or_None).
    """
    query_lower = query.strip().lower()

    # 1. Direct alias match
    official = FACILITY_TYPE_ALIASES.get(query_lower)
    if official:
        return official, None

    # 2. Exact match
    for ft in FACILITY_TYPES:
        if query_lower == ft.lower():
            return ft, None

    # 3. Fuzzy match on official types
    ft_lower_map = {f.lower(): f for f in FACILITY_TYPES}
    matches = difflib.get_close_matches(
        query_lower, list(ft_lower_map.keys()), n=1, cutoff=0.55
    )
    if matches:
        return ft_lower_map[matches[0]], None

    # 4. Fuzzy match on alias keys
    alias_matches = difflib.get_close_matches(
        query_lower, list(FACILITY_TYPE_ALIASES.keys()), n=1, cutoff=0.55
    )
    if alias_matches:
        return FACILITY_TYPE_ALIASES[alias_matches[0]], None

    return None, f'No matching facility type for "{query}"'


# ── Salary Normalization ─────────────────────────────────────────────────────


def normalize_salary(raw: str) -> tuple[Optional[int], Optional[str]]:
    """Normalize salary input to integer rupees.

    Accepts: 18k, 18 k, 18 K, 18.5k, 18.5 k, ₹18,000, 18000, 18,000, etc.
    Also accepts a trailing 'k' with optional whitespace, and ignores a
    surrounding 'salary' keyword. Returns (integer_value, error_or_None).
    """
    if raw is None or not str(raw).strip():
        return None, "Salary is required"
    raw = str(raw).strip().lower().replace(",", "")

    # Strip inline clock timestamps ("10:30 am", "18:45") so they can never be
    # read as a salary. A string that is ONLY a timestamp becomes empty below
    # and is rejected.
    raw = re.sub(r"\b\d{1,2}:\d{2}\s*(am|pm|a\.m\.|p\.m\.)?\b", " ", raw)
    if not raw.strip():
        return None, "Invalid salary format"

    # Extract a salary-like number (optionally followed by an optional-space
    # 'k'), ignoring a leading currency symbol. Requires an explicitly-scaled
    # 'k' value OR an absolute amount >= 1000 so a leftover bare digit from a
    # timestamp is not accepted.
    m = re.search(r"(?:rs|inr)?\s*₹?\s*(\d+(?:\.\d+)?)\s*(k)?\b", raw)
    if not m:
        return None, "Invalid salary format"
    number, suffix = m.group(1), m.group(2)
    try:
        val = float(number)
    except ValueError:
        return None, "Invalid salary format"
    if suffix:  # k / K
        return int(val * 1000), None
    if val < 1000:
        return None, "Invalid salary format"
    return int(val), None


# ── Cost Code Lookup ─────────────────────────────────────────────────────────


def get_cost_code_info(code: str) -> Optional[dict]:
    """Return cost code mapping dict or None."""
    return COST_CODES.get(code.strip())


# ── Role List for Prefix ─────────────────────────────────────────────────────


def get_roles_for_prefix(prefix: str) -> list[str]:
    """Return list of official role names for the given prefix."""
    return ROLES.get(prefix, [])


# ── Candidate Validation ─────────────────────────────────────────────────────


def validate_candidate(data: dict) -> dict[str, str]:
    """Validate candidate data. Returns dict of field -> error_message.

    Empty dict means all valid.
    """
    errors = {}

    # Candidate Name — required and must not be a gender label.
    name_raw = str(data.get("name", "")).strip()
    name_lower = name_raw.lower()
    if not name_raw:
        errors["name"] = "Candidate Name is required"
    elif name_lower in {"male", "female"}:
        errors["name"] = "Candidate name appears to be a gender label (MALE/FEMALE), not a real name. Please enter the actual name."
    elif any(c.isdigit() for c in name_raw) and len([c for c in name_raw if c.isdigit()]) > len(name_raw) * 0.4:
        errors["name"] = "Candidate name does not look valid. Please enter the actual name."

    # Mobile
    mobile_raw = data.get("mobile", "")
    _, mobile_err = normalize_mobile(mobile_raw)
    if mobile_err:
        errors["mobile"] = mobile_err

    # Cost Code
    cost_code = data.get("cost_code", "").strip()
    if not cost_code or cost_code not in COST_CODES:
        errors["cost_code"] = "Please select a valid cost code"
    else:
        # Role compatibility check (cost-code aware)
        role = data.get("role", "").strip()
        if not role:
            errors["role"] = "Role is required"
        else:
            info = COST_CODES.get(cost_code)
            expected_prefix = info["prefix"] if info else None
            # The role's prefix (e.g. "LM - ...") must match the cost code's
            # prefix.  This prevents a stale LM role from being accepted for an
            # FM cost code via fuzzy matching.
            role_prefix = role.split(" - ", 1)[0].strip().upper() \
                if " - " in role else ""
            if expected_prefix and role_prefix and role_prefix != expected_prefix:
                errors["role"] = (
                    f'Role "{role}" belongs to {role_prefix} but cost code '
                    f"{cost_code} requires {expected_prefix} roles. "
                    f"Please clear it and select a valid {expected_prefix} role."
                )
            else:
                _, role_err = resolve_role_for_cost_code(role, cost_code)
                if role_err:
                    errors["role"] = role_err

    # Facility Type - skip validation if auto-derived from cost code
    auto_ft = COST_CODE_FACILITY_TYPE.get(cost_code)
    if not auto_ft:
        ft_raw = data.get("facility_type", "").strip()
        if not ft_raw:
            errors["facility_type"] = "Facility type is required"
        else:
            _, ft_err = resolve_facility_type(ft_raw)
            if ft_err:
                errors["facility_type"] = ft_err

    # Facility / Hub - must be an official hub valid for the selected cost code
    facility = data.get("facility", "").strip()
    allowed_hubs = get_hubs_for_cost_code(cost_code) if cost_code in COST_CODES else []
    if cost_code == "8752" or not allowed_hubs:
        errors["facility"] = "No Myntra First Mile hub master configured yet."
    elif not facility:
        errors["facility"] = "Facility / Hub is required"
    elif facility not in allowed_hubs:
        errors["facility"] = (
            f'"{facility}" is not a valid hub for cost code {cost_code}. '
            f"Please select an official hub from the list."
        )

    # Salary
    salary_raw = data.get("salary", "")
    _, salary_err = normalize_salary(str(salary_raw))
    if salary_err:
        errors["salary"] = salary_err

    return errors


# ── Serialize Rules for Frontend ─────────────────────────────────────────────


# ── Backend / New candidate field normalizers ────────────────────────────────
# Normalizers for the fields the TeamHR Backend Mail workbook needs. They are
# deliberately conservative: garbage/uncertain values stay empty so the review
# screen (not silent inference) resolves them.

GENDER_ALIASES = {
    "male": "Male",
    "m": "Male",
    "female": "Female",
    "f": "Female",
    "transgender": "Transgender",
    "trans": "Transgender",
    "t": "Transgender",
}
GENDERS = ["Male", "Female", "Transgender"]


def normalize_gender(raw: object) -> tuple[str, Optional[str]]:
    """Normalize a gender value. Valid values are Male/Female/Transgender.

    Gender must only ever be taken from Aadhaar OCR; anything uncertain returns
    an empty value (caller flags for review) rather than a guess. OCR artifacts
    like "FEMALEM" / "FEMALEMALE" are collapsed to a clean label.
    """
    if raw is None:
        return "", None
    text = str(raw).strip()
    if not text:
        return "", None
    key = text.lower().replace(" ", "")
    if "female" in key and "male" not in key.replace("female", "", 1):
        return "Female", None
    if "trans" in key:
        return "Transgender", None
    if "female" in key or "femal" in key:
        return "Female", None
    value = GENDER_ALIASES.get(key)
    if value:
        return value, None
    if "male" in key:
        return "Male", None
    return "", "Gender should be Male, Female or Transgender (from Aadhaar only)."


def normalize_pin_code(raw: object) -> tuple[str, Optional[str]]:
    """Extract a 6-digit PIN from free text (prefix + 6 digits)."""
    if raw is None:
        return "", None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) >= 6:
        digits = digits[:6]
        if digits[0] in "123456789":
            return digits, None
        return "", "Pin Code must not start with 0."
    if digits:
        return "", "Pin Code must be exactly 6 digits."
    return "", None


def normalize_father_name(raw: object) -> tuple[str, Optional[str]]:
    """Strip relation markers (S/O, D/O, C/O) from a father/guardian name.

    Blank is allowed (some candidates have no father record). Never inferred.
    """
    if raw is None:
        return "", None
    text = str(raw).strip()
    if not text:
        return "", None
    text = re.sub(r"\b(?:s/o|d/o|c/o|w/o)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,.:;")
    if len(text) < 2:
        return "", None
    return text, None


def normalize_uan(raw: object) -> tuple[str, Optional[str]]:
    """Normalize a UAN number (digits only, kept as text). Optional field."""
    if raw is None:
        return "", None
    digits = re.sub(r"\D", "", str(raw))
    return digits, None


def parse_date(raw: object) -> str:
    """Parse a date to YYYY-MM-DD. Accepts DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD.

    Returns '' when the value cannot be safely parsed (review item, never a
    silent fallback to today).
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def normalize_doj(raw: object) -> tuple[str, Optional[str]]:
    """Normalize Date of Joining. Required for backend generation."""
    value = parse_date(raw)
    if not value:
        if raw is None or not str(raw).strip():
            return "", None
        return "", "Date of Joining must be a valid DD/MM/YYYY date."
    return value, None


def validate_new_candidate_fields(data: dict) -> dict[str, str]:
    """Validate the backend-only fields added for the TeamHR workbook."""
    errors: dict[str, str] = {}

    gender_raw = data.get("gender", "")
    _, gender_err = normalize_gender(gender_raw)
    if gender_err:
        errors["gender"] = gender_err

    pin_raw = data.get("pin_code", "")
    _, pin_err = normalize_pin_code(pin_raw)
    if pin_err:
        errors["pin_code"] = pin_err

    doj_raw = data.get("doj", "")
    _, doj_err = normalize_doj(doj_raw)
    if doj_err:
        errors["doj"] = doj_err

    return errors


# ── Backend workbook validation ──────────────────────────────────────────────

BACKEND_REQUIRED_FIELDS = [
    ("recruiter_name", "Recruiter Name"),
    ("doj", "Date of Joining"),
    ("name", "Name"),
    ("mobile", "Mobile No"),
    ("designation", "Designation"),
    ("location_code", "Branch"),
    ("facility_name", "Vertical (Facility)"),
    ("state", "State"),
    ("salary", "Net Salary"),
    ("aadhaar_number", "Aadhaar No"),
    ("dob", "DOB"),
    ("address", "Address"),
    ("pin_code", "Pin Code"),
    ("gender", "Gender"),
]
BACKEND_OPTIONAL_FIELDS = ["father_name", "uan_no"]


def validate_backend_candidate(candidate: dict) -> list[str]:
    """Return the list of problems blocking this candidate from the Backend workbook.

    Empty list means the candidate is backend-ready.
    """
    problems: list[str] = []
    for key, label in BACKEND_REQUIRED_FIELDS:
        value = candidate.get(key)
        if value is None or str(value).strip() == "":
            problems.append(f"{label} is required for the Backend workbook.")
    return problems


def get_rules_for_frontend() -> dict:
    """Return all rule data needed by the frontend as plain dicts/lists.

    Reads live from master_data so reloads are reflected without a restart.
    """
    cc_roles = _build_cost_code_roles()
    locations = {
        f["facility_name"]: f["location"] for f in master_data.get_facilities()
    }
    return {
        "cost_codes": COST_CODES,
        "roles": ROLES,
        "cost_code_roles": cc_roles,
        "role_aliases": ROLE_ALIASES,
        "facility_types": FACILITY_TYPES,
        "facility_type_aliases": FACILITY_TYPE_ALIASES,
        "cost_code_facility_type": COST_CODE_FACILITY_TYPE,
        "hub_master": master_data.get_facility_names(),
        "location_by_facility": locations,
    }


# Refresh snapshots once at import time so module-level COST_CODE_ROLES /
# HUB_MASTER are populated before any caller uses them.
refresh_masters()
