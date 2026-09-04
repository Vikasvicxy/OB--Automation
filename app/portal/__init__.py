"""eSampark portal automation package (Stage 4).

Submodules
----------
``selectors``
    Centralized, named DOM selectors / role / label / text hints. Kept in one
    place so portal changes can be absorbed without touching business logic.
``esampark``
    Playwright automation: LDAP login, session management, navigation to
    Onboarding -> Self Onboarding -> FTC, upload, upload-history reading,
    and result/error download.
``service``
    High-level orchestrator used by the FastAPI routes. Owns the "Portal
    Status" state machine and the per-upload state machine, links automation
    events back to the SQLite ``portal_uploads``/``generated_files`` records,
    and refuses to bypass CAPTCHA/OTP/MFA.

The package does NOT store credentials anywhere except the caller-provided
environment (``ESAMPARK_USERNAME`` / ``ESAMPARK_PASSWORD``).
"""

from app.portal import esampark, selectors, service

__all__ = ["esampark", "selectors", "service"]
