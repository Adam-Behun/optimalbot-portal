import logging
import os
import smtplib
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from email.mime.text import MIMEText

from loguru import logger

# TRACE level is built-in to loguru (level 5)

# Verbose log patterns to suppress (we have better alternatives)
SUPPRESSED_LOG_PATTERNS = [
    "Generating chat from universal context",  # Replaced by LLMContextObserver
    "Retrieving the tools using the adapter",  # No useful info
]


def _should_suppress(record) -> bool:
    """Filter out verbose logs we've replaced with better alternatives."""
    msg = str(record["message"])
    return not any(pattern in msg for pattern in SUPPRESSED_LOG_PATTERNS)

NOISY_LIBRARIES = [
    # Third-party
    'pymongo', 'websockets', 'websockets.client', 'httpx', 'httpcore', 'urllib3',
    'uvicorn.access',  # HTTP request logs
    # Pipecat internals (suppress frame-level noise)
    # Keep: pipecat.services.deepgram (transcriptions), pipecat.services.cartesia (TTS text)
    # Keep: pipecat_flows.manager (function calls)
    'pipecat.processors.aggregators',
    'pipecat.processors.metrics',
    'pipecat.adapters',
    'pipecat.services.openai.base_llm',  # Dumps full LLM context at DEBUG
    'pipecat.services.llm_service',
    'pipecat.pipeline.task',
    'pipecat.transports',
    'pipecat.utils.tracing',
]

# Rate limiting for email alerts: max 1 per error type per 5 minutes
_error_timestamps: OrderedDict[str, datetime] = OrderedDict()
_RATE_LIMIT_SECONDS = 300
_MAX_ERROR_TYPES = 100


def _email_sink(message):
    """Send email on ERROR/CRITICAL logs with rate limiting."""
    record = message.record
    level = record["level"].name

    if level not in ("ERROR", "CRITICAL"):
        return

    # Skip startup validation errors (caught by deploy validation)
    msg_str = str(record["message"])
    if "Startup validation failed" in msg_str or "fix configuration" in msg_str:
        return

    # Get email config
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    recipients = [r.strip() for r in os.getenv("ALERT_RECIPIENTS", "").split(",") if r.strip()]

    if not (smtp_user and smtp_pass and recipients):
        return

    # Rate limit by error message (first 100 chars)
    error_key = f"{record['name']}:{str(record['message'])[:100]}"
    now = datetime.now(timezone.utc)

    if error_key in _error_timestamps:
        elapsed = (now - _error_timestamps[error_key]).total_seconds()
        if elapsed < _RATE_LIMIT_SECONDS:
            return

    # Record timestamp and evict old entries
    if len(_error_timestamps) >= _MAX_ERROR_TYPES:
        _error_timestamps.popitem(last=False)
    _error_timestamps[error_key] = now

    # Build email
    env = os.getenv("ENV", "local")
    subject = f"[OptimalBot {env.upper()}] {level}: {record['name']}"
    body = f"""{level} in {record['name']}:{record['function']}

Message: {record['message']}

File: {record['file'].path}:{record['line']}
Time: {record['time'].strftime('%Y-%m-%d %H:%M:%S UTC')}
Environment: {env}
"""
    if record["exception"]:
        body += f"\nException:\n{record['exception']}"

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = ", ".join(recipients)

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg.as_string())
    except Exception:
        pass  # Don't log email failures to avoid recursion


def setup_logging(debug: bool = None, trace: bool = None):
    if trace is None:
        trace = os.getenv("TRACE", "false").lower() in ["true", "1", "yes"]
    if debug is None:
        debug = trace or os.getenv("DEBUG", "false").lower() in ["true", "1", "yes"]

    env = os.getenv("ENV", "local")

    if trace:
        level = "TRACE"
    elif debug:
        level = "DEBUG"
    else:
        level = "INFO"

    logger.remove()

    if env != "local":
        logger.add(
            sys.stderr,
            level=level,
            format="{message}",
            serialize=True,
            filter=_should_suppress,
        )
        # Email alerts for errors in deployed environments (not local)
        logger.add(_email_sink, level="ERROR")
    else:
        logger.add(
            sys.stderr,
            level=level,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
            filter=_should_suppress,
        )

    for lib in NOISY_LIBRARIES:
        logging.getLogger(lib).setLevel(logging.WARNING)

    if trace:
        logger.info(f"Trace logging enabled (env={env})")
    elif debug:
        logger.info(f"Debug logging enabled (env={env})")
