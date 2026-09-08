# Communication Module

## Architecture

The communication module uses a **provider pattern** with an abstract `BaseProvider` class and a central `CommunicationService` that routes messages to the appropriate channel.

```
CommunicationService
├── WhatsAppProvider (Gupshup adapter)
├── EmailProvider (SMTP adapter)
├── SMSProvider (adapter)
└── VoiceProvider (adapter)
```

All providers implement:
- `validate_recipient(recipient)` → `(is_valid, error_message)`
- `send(recipient, template_name, payload, dry_run=True)` → result dict

## Channels

### WhatsApp

- Validates Indian mobile numbers: `+91XXXXXXXXXX` or 10-digit starting with 6-9
- Provider: Gupshup API (placeholder — not yet implemented)
- Config: `GUPSHUP_API_KEY`, `GUPSHUP_APP_NAME`

### Email

- Validates email addresses via regex
- Provider: SMTP (placeholder — not yet implemented)
- Config: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`
- Email templates include a `Subject:` line that is extracted separately

### SMS

- Validates Indian mobile numbers (same as WhatsApp)
- Provider: Gateway adapter (placeholder — not yet implemented)
- Shares Gupshup configuration with WhatsApp

### Voice

- Validates Indian mobile numbers (same as WhatsApp)
- Provider: Telephony adapter (placeholder — not yet implemented)

## Templates

Templates are defined in `app/communication.py` in the `TEMPLATES` dict. Each template has variants for all four channels:

| Template Key | Purpose |
|-------------|---------|
| `onboarding_started` | Notify candidate that onboarding has started |
| `onboarding_success` | Congratulate candidate on completing onboarding |
| `onboarding_failed` | Notify candidate of an onboarding issue |
| `document_correction` | Request document correction from candidate |
| `follow_up_reminder` | Reminder for a pending task |

Template variables use Python string formatting: `{name}`, `{role}`, `{reason}`, `{task}`, `{deadline}`, `{document_type}`.

To add a new template, add an entry to the `TEMPLATES` dict with all four channel variants.

## Dry-Run Mode

All sending is **dry-run by default**. When `dry_run=True` (the default):
- No external API calls are made
- The result includes `message_preview` showing what would be sent
- The result status is `dry_run_ok`

Real sends require:
1. The channel feature flag enabled (`WHATSAPP_ENABLED=true`, etc.)
2. The master switch enabled (`COMMUNICATION_ENABLED=true`)
3. Valid provider credentials configured
4. `dry_run=False` explicitly passed

In the current production API (`/api/communications/send`), `dry_run=True` is hardcoded.

## Outbox Model

Every message attempt is recorded in the `communication_outbox` table:

| Column | Description |
|--------|-------------|
| `outbox_id` | Auto-increment primary key |
| `candidate_id` | Linked candidate (nullable) |
| `channel` | whatsapp, email, sms, or voice |
| `template_name` | Template key used |
| `safe_payload` | Template variables (JSON) |
| `status` | draft, dry_run_ok, sent, failed, skipped, not_implemented |
| `attempt_count` | Number of send attempts |
| `max_attempts` | Maximum retries (default 3) |
| `last_attempt_at` | Timestamp of last attempt |
| `next_attempt_at` | Scheduled retry time |
| `provider_reference` | External provider reference ID |
| `error_summary` | Error message if failed |
| `created_at` | Creation timestamp |
| `updated_at` | Last update timestamp |

Messages can be cancelled via `/api/communications/outbox/{id}/cancel`.

## Safety Guards

1. **Dry-run default**: no real messages sent unless explicitly requested
2. **Feature flags**: three levels of gating (master switch + per-channel)
3. **Recipient validation**: invalid phone/email addresses are rejected before sending
4. **Template validation**: missing templates for a channel return an error
5. **Provider check**: unsupported channels return an error
6. **Outbox recording**: all attempts are logged for audit
7. **No PII in logs**: message content is logged at INFO level only; sensitive data (Aadhaar, address) is never included in message payloads

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/communications` | GET | Communications management page |
| `/api/communications/send` | POST | Send a message (always dry-run) |
| `/api/communications/preview` | POST | Preview rendered message without sending |
| `/api/communications/outbox` | GET | List outbox messages (filter by channel/status) |
| `/api/communications/outbox/{id}/cancel` | POST | Cancel a pending outbox message |
