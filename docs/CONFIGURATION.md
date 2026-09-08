# Configuration

TeamHR-Automation is configured through environment variables and an optional `.env` file.

## Quick Start

1. Copy `.env.example` to `.env`
2. Edit `.env` with your values
3. Run the application

The `.env` file is loaded automatically on startup and is gitignored.

## Application Settings

| Variable       | Default         | Description                              |
|----------------|-----------------|------------------------------------------|
| `APP_ENV`      | `development`   | Environment: `development`, `test`, or `production` |
| `APP_HOST`     | `127.0.0.1`     | Host to bind to                          |
| `APP_PORT`     | `8000`          | Port to listen on                        |
| `SECRET_KEY`   | `dev-secret-change-me` | Secret key (change in production)  |
| `OUTPUT_DIR`   | `output`        | Directory for generated outputs          |

## Feature Flags

All feature flags default to `false` unless noted. Set to `true`/`1`/`yes` or `false`/`0`/`no`.

| Variable                   | Default | Description                             |
|----------------------------|---------|-----------------------------------------|
| `REAL_UPLOAD_ENABLED`      | `false` | Enable real uploads to eSampark         |
| `ESAMPARK_LIVE_TEST_MODE` | `false` | Run eSampark integration in live test   |
| `COMMUNICATION_ENABLED`   | `false` | Master switch for all communication     |
| `WHATSAPP_ENABLED`        | `false` | Enable WhatsApp messaging               |
| `EMAIL_ENABLED`           | `false` | Enable email sending                    |
| `SMS_ENABLED`             | `false` | Enable SMS sending                      |
| `VOICE_ENABLED`           | `false` | Enable voice calls                      |
| `ADMIN_FEATURE_ENABLED`   | `true`  | Enable admin-only features              |
| `DEMO_MODE`               | `false` | Skip external calls, use mock data      |
| `OCR_DEBUG_VIEW`          | `false` | Show OCR debug views during processing  |

## Communication Providers

### Gupshup (WhatsApp / SMS)

| Variable           | Default | Description          |
|--------------------|---------|----------------------|
| `GUPSHUP_API_KEY`  | `""`    | Gupshup API key      |
| `GUPSHUP_APP_NAME` | `""`    | Gupshup app name     |

### Email (SMTP)

| Variable         | Default              | Description       |
|------------------|----------------------|--------------------|
| `SMTP_HOST`      | `""`                 | SMTP server host   |
| `SMTP_PORT`      | `587`                | SMTP server port   |
| `SMTP_USERNAME`  | `""`                 | SMTP username      |
| `SMTP_PASSWORD`  | `""`                 | SMTP password      |
| `EMAIL_FROM`     | `""`                 | Sender email addr  |

## Configuration Classes

The `app.config` module provides config classes per environment:

- `DevelopmentConfig` — `DEBUG=True`
- `TestConfig` — `TESTING=True`
- `ProductionConfig` — strict defaults

The active class is selected by `APP_ENV`.

## Programmatic Access

```python
from app.config import get_config_value, get_safe_config, get_feature_flags

# Get a single value with type safety
port = get_config_value("APP_PORT", default=8000, cast=int)

# Get all non-secret config (for /health endpoint)
safe = get_safe_config()

# Get feature flags (for health display)
flags = get_feature_flags()
```

## Security Notes

- **Never commit `.env`** with real secrets to version control.
- All secrets (API keys, passwords) are excluded from `/health` and diagnostic responses.
- In production, set `SECRET_KEY` to a strong random value.
