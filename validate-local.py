#!/usr/bin/env python3
"""
Local Development Python Validation
Runs comprehensive checks for imports, configs, connectivity, and API keys.
Called by validate-local.sh after venv activation.
"""

import os
import sys
import asyncio
from pathlib import Path
from typing import Tuple, List

# Load environment before any imports that might need it
from dotenv import load_dotenv
load_dotenv()

# Colors for terminal output
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
NC = '\033[0m'

errors = 0
warnings = 0


def error(msg: str) -> None:
    global errors
    print(f"{RED}[ERROR]{NC} {msg}")
    errors += 1


def warn(msg: str) -> None:
    global warnings
    print(f"{YELLOW}[WARN]{NC} {msg}")
    warnings += 1


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{NC} {msg}")


def info(msg: str) -> None:
    print(f"     {msg}")


def check_critical_imports() -> None:
    """Verify all critical Python packages can be imported."""
    print("Checking critical imports...")

    critical_imports = [
        ("fastapi", "FastAPI web framework"),
        ("uvicorn", "ASGI server"),
        ("motor", "MongoDB async driver"),
        ("pydantic", "Data validation"),
        ("httpx", "HTTP client"),
        ("yaml", "YAML parsing"),
        ("loguru", "Logging"),
    ]

    for module, description in critical_imports:
        try:
            __import__(module)
            ok(f"{module} ({description})")
        except ImportError as e:
            error(f"Cannot import {module}: {e}")
            info(f"Run: ./setup-local.sh to reinstall dependencies")

    # Pipecat-specific imports (have extras)
    pipecat_imports = [
        ("pipecat", "Pipecat AI core"),
        ("pipecat.runner.types", "Pipecat runner (DailyRunnerArguments)"),
    ]

    for module, description in pipecat_imports:
        try:
            __import__(module)
            ok(f"{module} ({description})")
        except ImportError as e:
            error(f"Cannot import {module}: {e}")
            info("Ensure pipecat-ai[daily,cartesia,deepgram,groq,tracing] is installed")

    # Backend module imports
    backend_imports = [
        ("backend.config", "Backend configuration"),
        ("backend.database", "Database connection"),
        ("backend.main", "FastAPI app"),
    ]

    for module, description in backend_imports:
        try:
            __import__(module)
            ok(f"{module} ({description})")
        except ImportError as e:
            error(f"Cannot import {module}: {e}")
        except Exception as e:
            # Config validation might fail, that's OK here
            if "environment variable" in str(e).lower():
                warn(f"{module}: {e}")
            else:
                error(f"Error loading {module}: {e}")

    print()


def check_workflow_configs() -> None:
    """Validate all workflow services.yaml files."""
    print("Checking workflow configurations...")

    try:
        from bot_validation import validate_all_configs
        valid, config_errors = validate_all_configs()
        if valid:
            ok("All workflow configs valid")
        else:
            for err in config_errors:
                error(err)
    except Exception as e:
        error(f"Config validation failed: {e}")

    print()


def check_flow_definitions() -> None:
    """Verify FlowLoader can load all flow definitions."""
    print("Checking flow definitions...")

    try:
        from core.flow_loader import FlowLoader

        # Find all flow_definition.py files
        clients_dir = Path("clients")
        flow_files = list(clients_dir.glob("*/*/flow_definition.py"))

        if not flow_files:
            error("No flow_definition.py files found in clients/")
            return

        loaded = 0
        for flow_file in flow_files:
            # Extract org_slug and client_name from path
            # clients/<org_slug>/<client_name>/flow_definition.py
            parts = flow_file.parts
            org_slug = parts[-3]
            client_name = parts[-2]

            try:
                loader = FlowLoader(org_slug, client_name)
                flow_class = loader.load_flow_class()
                loaded += 1
            except Exception as e:
                error(f"Failed to load {org_slug}/{client_name}: {e}")

        if loaded == len(flow_files):
            ok(f"All {loaded} flow definitions load successfully")
        else:
            warn(f"Loaded {loaded}/{len(flow_files)} flow definitions")

    except ImportError as e:
        warn(f"Cannot import FlowLoader: {e}")
    except Exception as e:
        error(f"FlowLoader check failed: {e}")

    print()


async def check_mongodb() -> None:
    """Test MongoDB connectivity."""
    print("Checking MongoDB connectivity...")

    try:
        from backend.database import check_connection, close_mongo_client

        is_healthy, err = await check_connection()
        if is_healthy:
            ok("MongoDB connection successful")
        else:
            error(f"MongoDB connection failed: {err}")
            info("Check MONGO_URI in .env; verify IP whitelist in MongoDB Atlas")

        await close_mongo_client()

    except Exception as e:
        error(f"MongoDB check error: {e}")

    print()


async def check_api_keys() -> None:
    """Validate API keys for external services."""
    print("Checking API key connectivity...")

    try:
        from bot_validation import check_api_connectivity

        api_ok, api_errors = await check_api_connectivity()
        if api_ok:
            ok("All API keys valid")
        else:
            for err in api_errors:
                # Treat as warnings since keys might be rate-limited
                warn(err)
                info("Regenerate key from provider dashboard if invalid")

    except Exception as e:
        warn(f"API key check failed: {e}")

    print()


def check_daily_phone_number() -> None:
    """Verify DAILY_PHONE_NUMBER_ID is configured."""
    print("Checking Daily.co phone configuration...")

    phone_id = os.getenv("DAILY_PHONE_NUMBER_ID")
    api_key = os.getenv("DAILY_API_KEY")

    if not phone_id:
        warn("DAILY_PHONE_NUMBER_ID not set (required for dial-out)")
        return

    if not api_key:
        warn("DAILY_API_KEY not set")
        return

    # Phone number IDs are UUIDs from Daily dashboard - validate format
    import re
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    if re.match(uuid_pattern, phone_id, re.IGNORECASE):
        ok(f"DAILY_PHONE_NUMBER_ID configured: {phone_id[:8]}...")
    else:
        error(f"DAILY_PHONE_NUMBER_ID invalid format: {phone_id}")
        info("Should be UUID from Daily dashboard (e.g., 94d0eef5-d134-...)")

    # Note: Daily's /v1/phone-numbers endpoint doesn't exist in public API
    # Phone numbers are managed via dashboard only

    print()


def check_tracing_config() -> None:
    """Validate tracing configuration if enabled."""
    print("Checking observability configuration...")

    tracing_enabled = os.getenv("ENABLE_TRACING", "").lower() in ["true", "1", "yes"]

    if not tracing_enabled:
        ok("Tracing disabled (set ENABLE_TRACING=true to enable)")
        print()
        return

    # Check OTLP endpoint
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if not otlp_endpoint:
        warn("ENABLE_TRACING=true but OTEL_EXPORTER_OTLP_TRACES_ENDPOINT not set")
    else:
        ok(f"OTLP endpoint configured: {otlp_endpoint[:50]}...")

    # Check Langfuse credentials
    langfuse_public = os.getenv("LANGFUSE_PUBLIC_KEY")
    langfuse_secret = os.getenv("LANGFUSE_SECRET_KEY")

    if langfuse_public and langfuse_secret:
        ok("Langfuse credentials configured")
    elif tracing_enabled:
        warn("Langfuse credentials not set (tracing may fail)")

    print()


def check_local_vs_test_risks() -> None:
    """Warn about potential local vs test environment differences."""
    print("Checking local/test environment alignment...")

    # Check if using production database locally
    mongo_uri = os.getenv("MONGO_URI", "")
    db_name = os.getenv("MONGO_DB_NAME", "")

    if "production" in mongo_uri.lower() or db_name == "alfons":
        warn(f"Using database '{db_name}' - same as production/test")
        info("Consider MONGO_DB_NAME=alfons_dev for isolated local development")

    # Check bot lockfile exists for test deployment
    if not Path("uv.bot.lock").exists():
        warn("uv.bot.lock not found - required for test deployment")
        info("Run: ./update-bot-deps.sh to generate bot lockfile")
    else:
        ok("uv.bot.lock exists for test deployment")

    # Check pyproject.bot.toml exists
    if not Path("pyproject.bot.toml").exists():
        warn("pyproject.bot.toml not found - required for test deployment")
    else:
        ok("pyproject.bot.toml exists")

    print()


async def main() -> int:
    """Run all validation checks."""
    global errors, warnings

    check_critical_imports()
    check_workflow_configs()
    check_flow_definitions()
    await check_mongodb()
    await check_api_keys()
    check_daily_phone_number()
    check_tracing_config()
    check_local_vs_test_risks()

    # Return error count for shell script
    return errors


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
