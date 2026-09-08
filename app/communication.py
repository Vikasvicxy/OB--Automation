"""
Provider-independent communication architecture for TeamHR Automation.

All sending is DRY RUN by default. No real external API calls are made.
Each provider validates recipients before attempting any send operation.
Templates are admin-configurable via the TEMPLATES dict.
"""

import re
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


TEMPLATES: dict[str, dict[str, str]] = {
    'onboarding_started': {
        'whatsapp': 'Hello {name}, your onboarding process for {role} at TeamHR has started. '
                    'Please complete the required steps in your dashboard.',
        'email': 'Subject: Onboarding Started - TeamHR\n\n'
                 'Dear {name},\n\n'
                 'Your onboarding process for the role of {role} at TeamHR has officially started.\n'
                 'Please log in to your dashboard to complete the required steps.\n\n'
                 'Best regards,\nTeamHR Onboarding',
        'sms': 'Hi {name}, your TeamHR onboarding for {role} has started. '
               'Complete steps at your dashboard.',
        'voice': 'Hello {name}. Your onboarding process for {role} at TeamHR has started. '
                 'Please complete the required steps.',
    },
    'onboarding_success': {
        'whatsapp': 'Congratulations {name}! Your onboarding for {role} is complete. '
                    'Welcome to TeamHR!',
        'email': 'Subject: Onboarding Complete - Welcome to TeamHR!\n\n'
                 'Dear {name},\n\n'
                 'Congratulations! Your onboarding for the role of {role} is now complete.\n'
                 'Welcome aboard TeamHR!\n\n'
                 'Best regards,\nTeamHR Onboarding',
        'sms': 'Congratulations {name}! Your TeamHR onboarding for {role} is complete. Welcome!',
        'voice': 'Congratulations {name}. Your onboarding for {role} at TeamHR is complete. '
                 'Welcome to the team.',
    },
    'onboarding_failed': {
        'whatsapp': 'Hi {name}, there was an issue with your onboarding for {role}. '
                    'Reason: {reason}. Please contact support.',
        'email': 'Subject: Onboarding Issue - Action Required\n\n'
                 'Dear {name},\n\n'
                 'Unfortunately there was an issue with your onboarding for the role of {role}.\n'
                 'Reason: {reason}\n\n'
                 'Please contact support or try again.\n\n'
                 'Best regards,\nTeamHR Onboarding',
        'sms': 'Hi {name}, your TeamHR onboarding for {role} has an issue: {reason}. '
               'Contact support.',
        'voice': 'Hello {name}. There was an issue with your onboarding for {role}. '
                 'Reason: {reason}. Please contact support.',
    },
    'document_correction': {
        'whatsapp': 'Hi {name}, your {document_type} needs correction: {reason}. '
                    'Please re-upload the document.',
        'email': 'Subject: Document Correction Required\n\n'
                 'Dear {name},\n\n'
                 'Your {document_type} requires correction.\n'
                 'Reason: {reason}\n\n'
                 'Please re-upload the corrected document through your dashboard.\n\n'
                 'Best regards,\nTeamHR Onboarding',
        'sms': 'Hi {name}, your {document_type} needs correction: {reason}. '
               'Re-upload required.',
        'voice': 'Hello {name}. Your {document_type} needs correction. '
                 'Reason: {reason}. Please re-upload the document.',
    },
    'follow_up_reminder': {
        'whatsapp': 'Hi {name}, this is a friendly reminder to complete your pending task: '
                    '{task}. Deadline: {deadline}.',
        'email': 'Subject: Reminder - Pending Task\n\n'
                 'Dear {name},\n\n'
                 'This is a friendly reminder that you have a pending task:\n'
                 'Task: {task}\n'
                 'Deadline: {deadline}\n\n'
                 'Please complete it at your earliest convenience.\n\n'
                 'Best regards,\nTeamHR Onboarding',
        'sms': 'Hi {name}, reminder: complete "{task}" by {deadline}.',
        'voice': 'Hello {name}. This is a reminder to complete your pending task: {task}. '
                 'The deadline is {deadline}.',
    },
}

DEFAULT_TEMPLATE_CHANNEL_FALLBACK = 'email'


class BaseProvider(ABC):
    """Abstract base class for all communication providers."""

    @abstractmethod
    def send(self, recipient: str, template_name: str, payload: dict[str, Any],
             dry_run: bool = True) -> dict[str, Any]:
        """
        Send a message to the given recipient using the named template.

        Args:
            recipient: Validated recipient address (phone, email, etc.).
            template_name: Key into TEMPLATES dict.
            payload: Template variables for string interpolation.
            dry_run: If True, return a preview dict without sending.

        Returns:
            dict with keys: status, channel, recipient, message_preview, sent_at, dry_run.
        """
        ...

    @abstractmethod
    def validate_recipient(self, recipient: str) -> tuple[bool, str]:
        """
        Validate the recipient format for this channel.

        Returns:
            (is_valid, error_message).  error_message is empty string on success.
        """
        ...


class WhatsAppProvider(BaseProvider):
    """WhatsApp provider (Gupshup adapter placeholder)."""

    INDIAN_MOBILE_RE = re.compile(r'^(\+91|91|0)?[6-9]\d{9}$')

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def validate_recipient(self, recipient: str) -> tuple[bool, str]:
        clean = recipient.strip().replace(' ', '').replace('-', '')
        if self.INDIAN_MOBILE_RE.match(clean):
            return True, ''
        return False, (
            f'Invalid Indian mobile number for WhatsApp: {recipient}. '
            'Expected format: +91XXXXXXXXXX or 10-digit mobile starting with 6-9.'
        )

    def send(self, recipient: str, template_name: str, payload: dict[str, Any],
             dry_run: bool = True) -> dict[str, Any]:
        is_valid, error = self.validate_recipient(recipient)
        if not is_valid:
            return {
                'status': 'failed',
                'channel': 'whatsapp',
                'recipient': recipient,
                'error': error,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'dry_run': dry_run,
            }

        message = self._render_message(template_name, payload)

        if dry_run:
            logger.info('[DRY RUN][WhatsApp] Would send to %s: %s', recipient, message)
            return {
                'status': 'dry_run_ok',
                'channel': 'whatsapp',
                'recipient': recipient,
                'template': template_name,
                'message_preview': message,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'dry_run': True,
                'note': 'Real WhatsApp sending requires Gupshup API credentials. '
                        'No external call was made.',
            }

        logger.warning('[WhatsApp] Real send requested for %s but Gupshup adapter is a '
                       'placeholder. No message sent.', recipient)
        return {
            'status': 'not_implemented',
            'channel': 'whatsapp',
            'recipient': recipient,
            'error': 'Gupshup API integration not yet implemented.',
            'sent_at': datetime.now(timezone.utc).isoformat(),
            'dry_run': False,
        }

    def _render_message(self, template_name: str, payload: dict[str, Any]) -> str:
        channel_templates = TEMPLATES.get(template_name, {})
        template_str = channel_templates.get('whatsapp', '')
        if not template_str:
            return f'[No template for {template_name} on whatsapp]'
        try:
            return template_str.format(**payload)
        except KeyError as exc:
            return f'[Template missing variable {exc} for {template_name}]'


class EmailProvider(BaseProvider):
    """Email provider (SMTP placeholder)."""

    EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def validate_recipient(self, recipient: str) -> tuple[bool, str]:
        clean = recipient.strip().lower()
        if self.EMAIL_RE.match(clean):
            return True, ''
        return False, f'Invalid email address: {recipient}'

    def send(self, recipient: str, template_name: str, payload: dict[str, Any],
             dry_run: bool = True) -> dict[str, Any]:
        is_valid, error = self.validate_recipient(recipient)
        if not is_valid:
            return {
                'status': 'failed',
                'channel': 'email',
                'recipient': recipient,
                'error': error,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'dry_run': dry_run,
            }

        message = self._render_message(template_name, payload)
        subject = self._extract_subject(message)

        if dry_run:
            logger.info('[DRY RUN][Email] Would send to %s (subject: %s)', recipient, subject)
            return {
                'status': 'dry_run_ok',
                'channel': 'email',
                'recipient': recipient,
                'template': template_name,
                'subject': subject,
                'message_preview': message,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'dry_run': True,
                'note': 'Real email sending requires SMTP credentials. No external call was made.',
            }

        logger.warning('[Email] Real send requested for %s but SMTP adapter is a '
                       'placeholder. No message sent.', recipient)
        return {
            'status': 'not_implemented',
            'channel': 'email',
            'recipient': recipient,
            'error': 'SMTP integration not yet implemented.',
            'sent_at': datetime.now(timezone.utc).isoformat(),
            'dry_run': False,
        }

    def _render_message(self, template_name: str, payload: dict[str, Any]) -> str:
        channel_templates = TEMPLATES.get(template_name, {})
        template_str = channel_templates.get(
            'email', channel_templates.get(DEFAULT_TEMPLATE_CHANNEL_FALLBACK, '')
        )
        if not template_str:
            return f'[No template for {template_name} on email]'
        try:
            return template_str.format(**payload)
        except KeyError as exc:
            return f'[Template missing variable {exc} for {template_name}]'

    @staticmethod
    def _extract_subject(message: str) -> str:
        for line in message.split('\n'):
            if line.startswith('Subject:'):
                return line.replace('Subject:', '').strip()
        return '(no subject)'


class SMSProvider(BaseProvider):
    """SMS provider (adapter placeholder)."""

    INDIAN_MOBILE_RE = re.compile(r'^(\+91|91|0)?[6-9]\d{9}$')

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def validate_recipient(self, recipient: str) -> tuple[bool, str]:
        clean = recipient.strip().replace(' ', '').replace('-', '')
        if self.INDIAN_MOBILE_RE.match(clean):
            return True, ''
        return False, (
            f'Invalid Indian mobile number for SMS: {recipient}. '
            'Expected format: +91XXXXXXXXXX or 10-digit mobile starting with 6-9.'
        )

    def send(self, recipient: str, template_name: str, payload: dict[str, Any],
             dry_run: bool = True) -> dict[str, Any]:
        is_valid, error = self.validate_recipient(recipient)
        if not is_valid:
            return {
                'status': 'failed',
                'channel': 'sms',
                'recipient': recipient,
                'error': error,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'dry_run': dry_run,
            }

        message = self._render_message(template_name, payload)

        if dry_run:
            logger.info('[DRY RUN][SMS] Would send to %s: %s', recipient, message)
            return {
                'status': 'dry_run_ok',
                'channel': 'sms',
                'recipient': recipient,
                'template': template_name,
                'message_preview': message,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'dry_run': True,
                'note': 'Real SMS sending requires a gateway adapter. No external call was made.',
            }

        logger.warning('[SMS] Real send requested for %s but SMS adapter is a '
                       'placeholder. No message sent.', recipient)
        return {
            'status': 'not_implemented',
            'channel': 'sms',
            'recipient': recipient,
            'error': 'SMS gateway integration not yet implemented.',
            'sent_at': datetime.now(timezone.utc).isoformat(),
            'dry_run': False,
        }

    def _render_message(self, template_name: str, payload: dict[str, Any]) -> str:
        channel_templates = TEMPLATES.get(template_name, {})
        template_str = channel_templates.get('sms', '')
        if not template_str:
            return f'[No template for {template_name} on sms]'
        try:
            return template_str.format(**payload)
        except KeyError as exc:
            return f'[Template missing variable {exc} for {template_name}]'


class VoiceProvider(BaseProvider):
    """Voice call provider (adapter placeholder)."""

    INDIAN_MOBILE_RE = re.compile(r'^(\+91|91|0)?[6-9]\d{9}$')

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def validate_recipient(self, recipient: str) -> tuple[bool, str]:
        clean = recipient.strip().replace(' ', '').replace('-', '')
        if self.INDIAN_MOBILE_RE.match(clean):
            return True, ''
        return False, (
            f'Invalid Indian mobile number for voice call: {recipient}. '
            'Expected format: +91XXXXXXXXXX or 10-digit mobile starting with 6-9.'
        )

    def send(self, recipient: str, template_name: str, payload: dict[str, Any],
             dry_run: bool = True) -> dict[str, Any]:
        is_valid, error = self.validate_recipient(recipient)
        if not is_valid:
            return {
                'status': 'failed',
                'channel': 'voice',
                'recipient': recipient,
                'error': error,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'dry_run': dry_run,
            }

        message = self._render_message(template_name, payload)

        if dry_run:
            logger.info('[DRY RUN][Voice] Would call %s with script: %s', recipient, message)
            return {
                'status': 'dry_run_ok',
                'channel': 'voice',
                'recipient': recipient,
                'template': template_name,
                'message_preview': message,
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'dry_run': True,
                'note': 'Real voice calls require a telephony adapter. No external call was made.',
            }

        logger.warning('[Voice] Real call requested for %s but voice adapter is a '
                       'placeholder. No call placed.', recipient)
        return {
            'status': 'not_implemented',
            'channel': 'voice',
            'recipient': recipient,
            'error': 'Voice telephony integration not yet implemented.',
            'sent_at': datetime.now(timezone.utc).isoformat(),
            'dry_run': False,
        }

    def _render_message(self, template_name: str, payload: dict[str, Any]) -> str:
        channel_templates = TEMPLATES.get(template_name, {})
        template_str = channel_templates.get('voice', '')
        if not template_str:
            return f'[No template for {template_name} on voice]'
        try:
            return template_str.format(**payload)
        except KeyError as exc:
            return f'[Template missing variable {exc} for {template_name}]'


class CommunicationService:
    """
    Central service that routes messages through the appropriate provider.

    All sending is DRY RUN by default. Real sends require explicit dry_run=False
    AND working provider credentials.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

        # If the caller did not supply feature flags, source them from the
        # centralized app/config.py so .env flags (COMMUNICATION_ENABLED,
        # WHATSAPP_ENABLED, ...) actually drive channel enablement.
        if not self.config.get("feature_flags"):
            from app.config import FeatureFlags
            ff = FeatureFlags()
            self.config["feature_flags"] = {
                "comm_enabled": ff.COMMUNICATION_ENABLED,
                "channel_whatsapp_enabled": ff.WHATSAPP_ENABLED,
                "channel_email_enabled": ff.EMAIL_ENABLED,
                "channel_sms_enabled": ff.SMS_ENABLED,
                "channel_voice_enabled": ff.VOICE_ENABLED,
            }

        self.providers: dict[str, BaseProvider] = {
            'whatsapp': WhatsAppProvider(self.config),
            'email': EmailProvider(self.config),
            'sms': SMSProvider(self.config),
            'voice': VoiceProvider(self.config),
        }

    def is_channel_enabled(self, channel: str) -> bool:
        """Check whether a channel is enabled via feature flags in config."""
        feature_flags = self.config.get('feature_flags', {})
        # Master communication switch must be on for any channel.
        if not feature_flags.get('comm_enabled', False):
            return False
        channel_flag_key = f'channel_{channel}_enabled'
        enabled = feature_flags.get(channel_flag_key, False)
        if not enabled:
            logger.debug('Channel %s is disabled (feature flag %s is not set).',
                         channel, channel_flag_key)
        return enabled

    def get_provider(self, channel: str) -> BaseProvider | None:
        """Return the provider for the given channel, or None if unsupported."""
        provider = self.providers.get(channel)
        if provider is None:
            logger.warning('Unsupported communication channel: %s', channel)
        return provider

    def _record_outbox(self, candidate_id: Any, channel: str, template_name: str,
                       payload: dict[str, Any], result: dict[str, Any]) -> None:
        """
        Record the message attempt in the communication_outbox table.

        This is a placeholder that logs the record. The actual database insert
        should use the project's database module once it is wired up.
        """
        record = {
            'candidate_id': candidate_id,
            'channel': channel,
            'template_name': template_name,
            'payload': payload,
            'status': result.get('status', 'unknown'),
            'message_preview': result.get('message_preview', ''),
            'error': result.get('error', ''),
            'dry_run': result.get('dry_run', True),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'sent_at': result.get('sent_at', ''),
        }
        logger.info('[Outbox] Recorded message attempt: %s', record)

    def send_message(self, candidate_id: Any, channel: str, template_name: str,
                     payload: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        """
        Send a message through the specified channel.

        Steps:
            1. Check channel is enabled via feature flags.
            2. Validate recipient.
            3. Get provider.
            4. Send (or dry_run).
            5. Record in outbox.
            6. Return result.

        Args:
            candidate_id: The candidate this message pertains to.
            channel: One of 'whatsapp', 'email', 'sms', 'voice'.
            template_name: Key into TEMPLATES dict.
            payload: Template variables.
            dry_run: If True (default), no real message is sent.

        Returns:
            dict with status, channel, recipient, message_preview, etc.
        """
        recipient = payload.get('recipient') or payload.get('email') or payload.get('phone', '')
        if not recipient:
            return {
                'status': 'failed',
                'channel': channel,
                'error': 'No recipient found in payload. '
                         'Provide "recipient", "email", or "phone" in payload.',
                'dry_run': dry_run,
            }

        if not self.is_channel_enabled(channel):
            return {
                'status': 'skipped',
                'channel': channel,
                'recipient': recipient,
                'error': f'Channel "{channel}" is not enabled.',
                'dry_run': dry_run,
            }

        if channel not in TEMPLATES.get(template_name, {}):
            return {
                'status': 'failed',
                'channel': channel,
                'recipient': recipient,
                'error': f'Template "{template_name}" has no variant for channel "{channel}".',
                'dry_run': dry_run,
            }

        provider = self.get_provider(channel)
        if provider is None:
            return {
                'status': 'failed',
                'channel': channel,
                'recipient': recipient,
                'error': f'No provider registered for channel "{channel}".',
                'dry_run': dry_run,
            }

        result = provider.send(recipient, template_name, payload, dry_run=dry_run)

        self._record_outbox(candidate_id, channel, template_name, payload, result)

        return result

    def render_preview(self, channel: str, template_name: str,
                       payload: dict[str, Any]) -> dict[str, Any]:
        """
        Render a dry-run preview of what the message would look like.

        Returns:
            dict with recipient, template_name, channel, rendered_message, subject (email only).
        """
        recipient = payload.get('recipient') or payload.get('email') or payload.get('phone', '')

        channel_templates = TEMPLATES.get(template_name, {})
        template_str = channel_templates.get(channel, '')
        if not template_str:
            return {
                'recipient': recipient,
                'template_name': template_name,
                'channel': channel,
                'rendered_message': f'[No template found for "{template_name}" on channel "{channel}"]',
                'available_channels': list(channel_templates.keys()),
            }

        try:
            rendered = template_str.format(**payload)
        except KeyError as exc:
            rendered = f'[Template missing variable {exc} for {template_name}]'

        result: dict[str, Any] = {
            'recipient': recipient,
            'template_name': template_name,
            'channel': channel,
            'rendered_message': rendered,
        }

        if channel == 'email':
            result['subject'] = EmailProvider._extract_subject(rendered)

        return result


_communication_service: CommunicationService | None = None


def get_communication_service(config: dict[str, Any] | None = None) -> CommunicationService:
    """Return the singleton CommunicationService instance, creating it if needed."""
    global _communication_service
    if _communication_service is None:
        _communication_service = CommunicationService(config=config)
    return _communication_service
