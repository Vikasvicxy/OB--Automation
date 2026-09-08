"""
Structured logging module for TeamHR Automation.

Provides category-specific loggers with rotation, console output,
sanitization of sensitive data, and diagnostic bundle generation.

Usage:
    from app.logging_config import setup_logging, get_logger
    setup_logging(log_level='INFO')
    logger = get_logger('APP')
    logger.info('Application started')
"""

import logging
import logging.handlers
import os
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_DIR = Path('data/logs')

CATEGORIES = [
    'APP', 'OCR', 'RESOLVER', 'MASTER', 'EXCEL',
    'PORTAL', 'BACKUP', 'COMMUNICATION', 'DATABASE',
]

MAX_BYTES = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 5

LOG_FORMAT = '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# Sensitive value masks
# Aadhaar: exactly 12 digits, optionally space-separated in 4-4-4 groups
_AADHAAR_RE = re.compile(r'\b\d{4}\s\d{4}\s\d{4}\b')
# Email addresses
_EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
# Phone numbers longer than 10 digits
_PHONE_RE = re.compile(r'\b\d{11,15}\b')
# Auth / credential key=value pairs
_AUTH_RE = re.compile(
    r'(auth|token|cookie|password|passwd|secret|api[_-]?key)'
    r'\s*[=:]\s*\S+',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

def sanitize_for_log(text: str) -> str:
    """Return *text* with sensitive patterns replaced by safe placeholders.

    Replacements:
        - 12-digit Aadhaar (with optional spaces) -> XXXX XXXX XXXX
        - Email addresses                       -> [EMAIL]
        - Phone numbers (>10 digits)            -> [PHONE]
        - auth/token/cookie/password … values    -> [REDACTED]
    """
    if not isinstance(text, str):
        text = str(text)

    text = _AADHAAR_RE.sub('XXXX XXXX XXXX', text)
    text = _EMAIL_RE.sub('[EMAIL]', text)
    text = _PHONE_RE.sub('[PHONE]', text)
    text = _AUTH_RE.sub(lambda m: f'{m.group(1)}=[REDACTED]', text)
    return text


class _SanitizingFilter(logging.Filter):
    """Logging filter that sanitizes every log record's message."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_for_log(record.msg)
        if record.args:
            record.args = tuple(
                sanitize_for_log(a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


# ---------------------------------------------------------------------------
# Logger setup
# ---------------------------------------------------------------------------

_initialized = False


def setup_logging(log_level: str = 'INFO') -> None:
    """Configure all category loggers and the root logger.

    Each category gets:
        - A ``RotatingFileHandler`` writing to ``data/logs/<category>.log``
        - A shared ``StreamHandler`` (console) for development

    Calling more than once is safe; reconfiguration happens only once.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    sanitizing_filter = _SanitizingFilter()

    # Shared console handler (development)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sanitizing_filter)

    for category in CATEGORIES:
        logger = logging.getLogger(f'TeamHR.{category}')
        logger.setLevel(numeric_level)
        logger.propagate = False

        # File handler with rotation
        log_path = LOG_DIR / f'{category}.log'
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding='utf-8',
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(sanitizing_filter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)


def get_logger(category: str) -> logging.Logger:
    """Return the logger for *category* (e.g. ``'APP'``, ``'OCR'``).

    Raises ``ValueError`` if *category* is not a known category.
    """
    cat_upper = category.upper()
    if cat_upper not in CATEGORIES:
        raise ValueError(
            f"Unknown category '{category}'. "
            f"Valid categories: {', '.join(CATEGORIES)}"
        )
    return logging.getLogger(f'TeamHR.{cat_upper}')


# ---------------------------------------------------------------------------
# Log file introspection
# ---------------------------------------------------------------------------

def get_log_files() -> list[str]:
    """Return a list of current log file paths for every category."""
    return [str(LOG_DIR / f'{c}.log') for c in CATEGORIES]


def get_recent_logs(category: Optional[str] = None,
                    lines: int = 50) -> list[str]:
    """Return the last *lines* (sanitized) log entries.

    Args:
        category: If provided, only return logs for this category.
                  If ``None``, return logs from **all** categories.
        lines: Maximum number of lines per category (default 50).

    Returns:
        List of sanitised log-line strings, newest last.
    """
    categories = [category.upper()] if category else CATEGORIES
    result: list[str] = []

    for cat in categories:
        log_path = LOG_DIR / f'{cat}.log'
        if not log_path.exists():
            continue
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as fh:
                all_lines = fh.readlines()
            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
            for line in tail:
                result.append(sanitize_for_log(line.rstrip('\n')))
        except OSError:
            continue

    return result


# ---------------------------------------------------------------------------
# Diagnostic bundle
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    """Best-effort short git SHA; returns 'unknown' on failure."""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def _recent_error_summaries(n: int = 10) -> list[str]:
    """Return up to *n* sanitised ERROR/CRITICAL lines from all log files."""
    errors: list[str] = []
    for cat in CATEGORIES:
        log_path = LOG_DIR / f'{cat}.log'
        if not log_path.exists():
            continue
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    stripped = line.strip()
                    if '[ERROR]' in stripped or '[CRITICAL]' in stripped:
                        errors.append(sanitize_for_log(stripped))
        except OSError:
            continue
    return errors[-n:]


def _health_status() -> dict:
    """Attempt to import and call the project health module."""
    try:
        from app.health import build_health_report  # type: ignore[import-untyped]
        return build_health_report()
    except Exception as exc:
        return {'status': 'unavailable', 'error': str(exc)}


def _master_counts() -> dict:
    """Attempt to retrieve master record counts."""
    try:
        from app.master_counter import get_counts  # type: ignore[import-untyped]
        return get_counts()
    except Exception:
        return {}


def _db_schema_version() -> str:
    """Attempt to retrieve the database schema version."""
    try:
        from app.database import get_schema_version  # type: ignore[import-untyped]
        return get_schema_version()
    except Exception:
        return 'unknown'


def _configuration_flags() -> dict:
    """Return feature-flag / config flags (no secrets)."""
    try:
        from app.config import get_safe_config  # type: ignore[import-untyped]
        return get_safe_config()
    except Exception:
        return {}


def _category_counts() -> dict[str, int]:
    """Count lines in each category's log file."""
    counts: dict[str, int] = {}
    for cat in CATEGORIES:
        log_path = LOG_DIR / f'{cat}.log'
        if log_path.exists():
            try:
                with open(log_path, 'r', encoding='utf-8', errors='replace') as fh:
                    counts[cat] = sum(1 for _ in fh)
            except OSError:
                counts[cat] = -1
        else:
            counts[cat] = 0
    return counts


def create_diagnostic_bundle() -> dict:
    """Build and return a *safe* diagnostic dictionary.

    Contents:
        - app_version
        - git_commit
        - health_status
        - recent_logs (sanitised, last 20 per category)
        - master_counts
        - db_schema_version
        - configuration_flags (feature flags only, no secrets)
        - recent_errors (sanitised)
        - log_file_line_counts

    **NEVER** includes: Aadhaar numbers, addresses, documents,
    credentials, cookies, or tokens.
    """
    # Import version from app.config
    try:
        from app.config import APP_VERSION  # type: ignore[import-untyped]
        app_version = APP_VERSION
    except Exception:
        app_version = 'unknown'

    # Gather sanitised recent logs per category
    recent_logs: dict[str, list[str]] = {}
    for cat in CATEGORIES:
        log_path = LOG_DIR / f'{cat}.log'
        if not log_path.exists():
            recent_logs[cat] = []
            continue
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as fh:
                all_lines = fh.readlines()
            tail = all_lines[-20:] if len(all_lines) > 20 else all_lines
            recent_logs[cat] = [sanitize_for_log(l.rstrip('\n')) for l in tail]
        except OSError:
            recent_logs[cat] = []

    bundle = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'app_version': app_version,
        'git_commit': _git_commit(),
        'health_status': _health_status(),
        'recent_logs': recent_logs,
        'master_counts': _master_counts(),
        'db_schema_version': _db_schema_version(),
        'configuration_flags': _configuration_flags(),
        'recent_errors': _recent_error_summaries(20),
        'log_file_line_counts': _category_counts(),
    }

    # Final sanitisation pass on all string values in the bundle
    _sanitize_dict(bundle)
    return bundle


def _sanitize_dict(d: dict) -> None:
    """Recursively sanitize all string values in *d* in-place."""
    for key, value in d.items():
        if isinstance(value, str):
            d[key] = sanitize_for_log(value)
        elif isinstance(value, dict):
            _sanitize_dict(value)
        elif isinstance(value, list):
            d[key] = [
                sanitize_for_log(v) if isinstance(v, str)
                else _sanitize_dict(v) if isinstance(v, dict)
                else v
                for v in value
            ]
