"""Email alerting for critical failures using Gmail SMTP."""

import os
import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from functools import partial
from typing import Optional
from loguru import logger


class EmailAlerter:
    """Send email alerts for critical failures."""

    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username = os.getenv("SMTP_USERNAME")
        self.smtp_password = os.getenv("SMTP_PASSWORD")
        self.recipients = [
            r.strip() for r in os.getenv("ALERT_RECIPIENTS", "").split(",") if r.strip()
        ]
        self.environment = os.getenv("ENV", "local")
        self._enabled = bool(
            self.smtp_username and self.smtp_password and self.recipients
        )

        if self._enabled:
            logger.info(f"Email alerting enabled: {len(self.recipients)} recipients")
        else:
            logger.warning("Email alerting not configured (set SMTP_USERNAME, SMTP_PASSWORD, ALERT_RECIPIENTS)")

    def is_enabled(self) -> bool:
        """Check if alerting is enabled."""
        return self._enabled

    async def send_alert(
        self,
        subject: str,
        body: str,
        priority: str = "normal",
    ):
        """Send alert email asynchronously.

        Args:
            subject: Email subject
            body: Email body text
            priority: "high" or "normal"
        """
        if not self._enabled:
            logger.warning(f"Alert not sent (alerting disabled): {subject}")
            return

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                partial(self._send_sync, subject, body, priority)
            )
            logger.info(f"Alert sent: {subject}")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

    def _send_sync(self, subject: str, body: str, priority: str):
        """Synchronous email send (run in executor)."""
        msg = MIMEMultipart()
        msg["Subject"] = f"[OptimalBot {self.environment.upper()}] {subject}"
        msg["From"] = self.smtp_username
        msg["To"] = ", ".join(self.recipients)

        if priority == "high":
            msg["X-Priority"] = "1"
            msg["X-MSMail-Priority"] = "High"

        # Add timestamp to body
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        full_body = f"{body}\n\n---\nTimestamp: {timestamp}\nEnvironment: {self.environment}"

        msg.attach(MIMEText(full_body, "plain"))

        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.smtp_username, self.smtp_password)
            server.sendmail(self.smtp_username, self.recipients, msg.as_string())

    async def alert_service_degraded(self, service: str, error: str):
        """Alert when external service is degraded."""
        subject = f"Service Degraded: {service}"
        body = f"""Service {service} is experiencing issues.

Error: {error}

Please investigate the service health and take appropriate action."""
        await self.send_alert(subject, body, priority="high")

    async def alert_call_failure_spike(
        self,
        organization_id: str,
        failure_count: int,
        window_minutes: int = 15,
    ):
        """Alert when call failures exceed threshold."""
        subject = f"Call Failure Spike Detected"
        body = f"""High number of call failures detected.

Organization: {organization_id}
Failures in last {window_minutes} minutes: {failure_count}

Please review call logs and investigate the root cause."""
        await self.send_alert(subject, body, priority="high")

    async def alert_bot_start_failure(
        self,
        session_id: str,
        error: str,
        patient_id: str = None,
    ):
        """Alert when bot fails to start."""
        subject = "Bot Start Failure"
        body = f"""Bot failed to start for a call.

Session ID: {session_id}
Patient ID: {patient_id or 'N/A'}
Error: {error}

The patient's call status has been marked as Failed."""
        await self.send_alert(subject, body, priority="high")

    async def alert_dialout_exhausted(
        self,
        session_id: str,
        phone_number: str,
        attempts: int,
        patient_id: str = None,
    ):
        """Alert when all dialout attempts are exhausted."""
        subject = "Dialout Attempts Exhausted"
        body = f"""All dialout attempts failed for a call.

Session ID: {session_id}
Patient ID: {patient_id or 'N/A'}
Phone Number: {phone_number}
Attempts: {attempts}

The call could not be connected after multiple retries."""
        await self.send_alert(subject, body, priority="normal")

    async def alert_critical_error(
        self,
        error_type: str,
        error_message: str,
        context: dict = None,
    ):
        """Alert on critical system errors."""
        subject = f"Critical Error: {error_type}"

        context_str = ""
        if context:
            context_str = "\nContext:\n" + "\n".join(
                f"  {k}: {v}" for k, v in context.items()
            )

        body = f"""A critical error occurred in the system.

Error Type: {error_type}
Message: {error_message}
{context_str}

Immediate attention may be required."""
        await self.send_alert(subject, body, priority="high")


# Singleton instance
_alerter_instance: Optional[EmailAlerter] = None


def get_email_alerter() -> EmailAlerter:
    """Get the singleton EmailAlerter instance."""
    global _alerter_instance
    if _alerter_instance is None:
        _alerter_instance = EmailAlerter()
    return _alerter_instance
