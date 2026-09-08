"""Centralized configuration module for TeamHR-Automation.

Reads from environment variables and .env file.
"""

import os
from typing import Any, Dict, Optional

APP_VERSION = "0.1.0"
TEAMHR_VERSION = "0.1.0"


class FeatureFlags:
    """Feature flags with safe defaults."""

    REAL_UPLOAD_ENABLED: bool = False
    ESAMPARK_LIVE_TEST_MODE: bool = False
    COMMUNICATION_ENABLED: bool = False
    WHATSAPP_ENABLED: bool = False
    EMAIL_ENABLED: bool = False
    SMS_ENABLED: bool = False
    VOICE_ENABLED: bool = False
    ADMIN_FEATURE_ENABLED: bool = True
    DEMO_MODE: bool = False
    OCR_DEBUG_VIEW: bool = False

    def __init__(self) -> None:
        self.REAL_UPLOAD_ENABLED = _get_bool("REAL_UPLOAD_ENABLED", False)
        self.ESAMPARK_LIVE_TEST_MODE = _get_bool("ESAMPARK_LIVE_TEST_MODE", False)
        self.COMMUNICATION_ENABLED = _get_bool("COMMUNICATION_ENABLED", False)
        self.WHATSAPP_ENABLED = _get_bool("WHATSAPP_ENABLED", False)
        self.EMAIL_ENABLED = _get_bool("EMAIL_ENABLED", False)
        self.SMS_ENABLED = _get_bool("SMS_ENABLED", False)
        self.VOICE_ENABLED = _get_bool("VOICE_ENABLED", False)
        self.ADMIN_FEATURE_ENABLED = _get_bool("ADMIN_FEATURE_ENABLED", True)
        self.DEMO_MODE = _get_bool("DEMO_MODE", False)
        self.OCR_DEBUG_VIEW = _get_bool("OCR_DEBUG_VIEW", False)

    def as_dict(self) -> Dict[str, bool]:
        """Return all feature flags as a dictionary."""
        return {k: v for k, v in self.__dict__.items() if k.isupper()}


class BaseConfig:
    """Base configuration class."""

    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-me")
    DEBUG: bool = False
    TESTING: bool = False

    APP_HOST: str = os.getenv("APP_HOST", "127.0.0.1")
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")

    GUPSHUP_API_KEY: str = os.getenv("GUPSHUP_API_KEY", "")
    GUPSHUP_APP_NAME: str = os.getenv("GUPSHUP_APP_NAME", "")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "")

    def __init__(self) -> None:
        self.features = FeatureFlags()

    def safe_dict(self) -> Dict[str, Any]:
        """Return non-secret config values for diagnostics."""
        return {
            "app_version": APP_VERSION,
            "teamhr_version": TEAMHR_VERSION,
            "app_host": self.APP_HOST,
            "app_port": self.APP_PORT,
            "output_dir": self.OUTPUT_DIR,
            "debug": self.DEBUG,
            "testing": self.TESTING,
            "features": self.features.as_dict(),
        }


class DevelopmentConfig(BaseConfig):
    """Development configuration."""

    DEBUG: bool = True


class TestConfig(BaseConfig):
    """Test configuration."""

    TESTING: bool = True


class ProductionConfig(BaseConfig):
    """Production configuration."""


CONFIG_MAP: Dict[str, type] = {
    "development": DevelopmentConfig,
    "test": TestConfig,
    "production": ProductionConfig,
}


def _load_dotenv(filepath: str = ".env") -> None:
    """Load .env file into os.environ if the file exists.

    Simple parser: no comments, no external dependencies.
    Lines starting with # are ignored. Empty lines are ignored.
    """
    if not os.path.isfile(filepath):
        return

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            os.environ.setdefault(key, value)


def _get_bool(key: str, default: bool) -> bool:
    """Get a boolean value from environment variables with type safety."""
    raw = os.getenv(key, "").lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return default


def get_config_value(key: str, default: Any = None, cast: type = str) -> Any:
    """Get a config value with type safety.

    Args:
        key: Environment variable name.
        default: Fallback value if key is missing.
        cast: Type to cast the value to (str, int, float, bool).

    Returns:
        The casted config value or the default.
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        if cast is bool:
            return _get_bool(key, bool(default))
        return cast(raw)
    except (ValueError, TypeError):
        return default


def get_safe_config() -> Dict[str, Any]:
    """Get all non-secret configuration for health/diagnostic endpoints."""
    config = _get_active_config()
    return config.safe_dict()


def get_feature_flags() -> Dict[str, bool]:
    """Get all feature flags for health display."""
    features = FeatureFlags()
    return features.as_dict()


def _get_active_config() -> BaseConfig:
    """Instantiate the configuration class based on the APP_ENV variable."""
    env = os.getenv("APP_ENV", "development").lower()
    config_cls = CONFIG_MAP.get(env, DevelopmentConfig)
    return config_cls()


_load_dotenv()

config = _get_active_config()
