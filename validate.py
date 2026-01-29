#!/usr/bin/env python3
"""
Local Development Validation

Validates environment, dependencies, configs, and connectivity before starting services.
Called by run.sh before launching backend/bot/frontend.

Usage:
    python validate.py          # Full validation
    python validate.py --quick  # Skip API connectivity checks
"""

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from dotenv import load_dotenv

load_dotenv()

# Terminal colors
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


# =============================================================================
# Environment Checks
# =============================================================================

def check_env_vars() -> None:
    """Check required environment variables."""
    print("Checking environment...")

    required = [
        "MONGO_URI",
        "JWT_SECRET_KEY",
        "OPENAI_API_KEY",
        "DEEPGRAM_API_KEY",
        "DAILY_API_KEY",
        "DAILY_PHONE_NUMBER_ID",
    ]

    for var in required:
        value = os.getenv(var)
        if not value:
            error(f"Missing: {var}")
        elif "<your-" in value or "your_" in value:
            error(f"{var} contains placeholder value")

    # JWT key length
    jwt_key = os.getenv("JWT_SECRET_KEY", "")
    if jwt_key and len(jwt_key) < 32:
        error(f"JWT_SECRET_KEY must be >= 32 chars (found {len(jwt_key)})")

    # Daily phone number format
    phone_id = os.getenv("DAILY_PHONE_NUMBER_ID", "")
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    if phone_id and not re.match(uuid_pattern, phone_id, re.IGNORECASE):
        error(f"DAILY_PHONE_NUMBER_ID invalid format: {phone_id[:20]}...")

    # ENV should be local
    if os.getenv("ENV") != "local":
        warn(f"ENV={os.getenv('ENV')} (expected 'local')")

    print()


# =============================================================================
# Import Checks
# =============================================================================

def check_imports() -> None:
    """Verify critical Python packages can be imported."""
    print("Checking imports...")

    modules = [
        ("fastapi", "FastAPI"),
        ("uvicorn", "ASGI server"),
        ("motor", "MongoDB driver"),
        ("pipecat", "Pipecat AI"),
        ("pipecat_flows", "Pipecat Flows"),
    ]

    for module, desc in modules:
        try:
            __import__(module)
            ok(f"{module}")
        except ImportError as e:
            error(f"Cannot import {module}: {e}")

    # Backend modules
    try:
        __import__("backend.main")
        ok("backend.main")
    except Exception as e:
        error(f"Error loading backend.main: {e}")

    print()


# =============================================================================
# Workflow Config Validation
# =============================================================================

def find_services_configs() -> List[Path]:
    """Find all services.yaml files in clients/."""
    clients_dir = Path("clients")
    if not clients_dir.exists():
        return []
    return list(clients_dir.glob("*/*/services.yaml"))


def substitute_env_vars(config: Dict[str, Any], path: str = "") -> Dict[str, Any]:
    """Replace ${VAR_NAME} with environment variable values."""
    for key, value in config.items():
        current_path = f"{path}.{key}" if path else key
        if isinstance(value, dict):
            config[key] = substitute_env_vars(value, current_path)
        elif isinstance(value, str) and value.startswith('${') and value.endswith('}'):
            env_var_name = value[2:-1]
            env_value = os.getenv(env_var_name)
            if env_value is None:
                raise ValueError(f"Missing env var '{env_var_name}' required by {current_path}")
            config[key] = env_value
    return config


def check_workflow_configs() -> None:
    """Validate all workflow services.yaml files."""
    print("Checking workflow configs...")

    configs = find_services_configs()
    if not configs:
        warn("No services.yaml files found in clients/")
        print()
        return

    for config_path in configs:
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)

            # Check required fields
            if 'services' not in config:
                error(f"{config_path}: missing 'services' section")
                continue

            required_services = ['stt', 'llm', 'tts', 'transport']
            for svc in required_services:
                if svc not in config['services']:
                    error(f"{config_path}: missing service '{svc}'")

            # Validate env var substitution
            substitute_env_vars(config.copy(), str(config_path))
            ok(f"{config_path.parent.name}/{config_path.parents[0].name}")

        except ValueError as e:
            error(str(e))
        except Exception as e:
            error(f"{config_path}: {e}")

    print()


# =============================================================================
# Flow Definition Validation
# =============================================================================

def check_flow_definitions() -> None:
    """Verify FlowLoader can load all flow definitions."""
    print("Checking flow definitions...")

    try:
        from core.flow_loader import FlowLoader

        clients_dir = Path("clients")
        flow_files = list(clients_dir.glob("*/*/flow_definition.py"))

        if not flow_files:
            warn("No flow_definition.py files found")
            print()
            return

        for flow_file in flow_files:
            org_slug = flow_file.parts[-3]
            workflow_name = flow_file.parts[-2]

            try:
                loader = FlowLoader(org_slug, workflow_name)
                loader.load_flow_class()
                ok(f"{org_slug}/{workflow_name}")
            except Exception as e:
                error(f"Failed to load {org_slug}/{workflow_name}: {e}")

    except ImportError as e:
        warn(f"Cannot import FlowLoader: {e}")

    print()


# =============================================================================
# Connectivity Checks
# =============================================================================

async def check_mongodb() -> None:
    """Test MongoDB connectivity."""
    print("Checking MongoDB...")

    try:
        from backend.database import check_connection, close_mongo_client

        is_healthy, err = await check_connection()
        if is_healthy:
            ok("MongoDB connected")
        else:
            error(f"MongoDB failed: {err}")

        await close_mongo_client()

    except Exception as e:
        error(f"MongoDB error: {e}")

    print()


async def check_api_key(client, name: str, url: str, headers: dict) -> Tuple[bool, str]:
    """Check a single API key."""
    try:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 401:
            return False, f"{name} invalid (401)"
        elif resp.status_code == 200:
            return True, ""
        else:
            return False, f"{name} returned {resp.status_code}"
    except Exception as e:
        return False, f"{name} failed: {e}"


async def check_api_connectivity() -> None:
    """Validate API keys for external services."""
    print("Checking API keys...")

    import httpx

    checks = []

    if os.getenv("DEEPGRAM_API_KEY"):
        checks.append(("DEEPGRAM", "https://api.deepgram.com/v1/projects",
                      {"Authorization": f"Token {os.getenv('DEEPGRAM_API_KEY')}"}))

    if os.getenv("OPENAI_API_KEY"):
        checks.append(("OPENAI", "https://api.openai.com/v1/models",
                      {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}))

    if os.getenv("GROQ_API_KEY"):
        checks.append(("GROQ", "https://api.groq.com/openai/v1/models",
                      {"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"}))

    if os.getenv("DAILY_API_KEY"):
        checks.append(("DAILY", "https://api.daily.co/v1/rooms",
                      {"Authorization": f"Bearer {os.getenv('DAILY_API_KEY')}"}))

    if os.getenv("CARTESIA_API_KEY"):
        checks.append(("CARTESIA", "https://api.cartesia.ai/voices?limit=1",
                      {"X-API-Key": os.getenv("CARTESIA_API_KEY"), "Cartesia-Version": "2024-06-10"}))

    if os.getenv("ELEVENLABS_API_KEY"):
        checks.append(("ELEVENLABS", "https://api.elevenlabs.io/v1/user",
                      {"xi-api-key": os.getenv("ELEVENLABS_API_KEY")}))

    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, url, headers in checks:
            valid, err = await check_api_key(client, name, url, headers)
            if valid:
                ok(name)
            else:
                warn(err)

    print()


# =============================================================================
# Bot Startup Validation (called by bot.py)
# =============================================================================

async def validate_bot_startup(check_api_keys: bool = True) -> None:
    """
    Validates bot configuration before starting.
    Raises RuntimeError if validation fails.
    """
    from loguru import logger

    from backend.database import check_connection, close_mongo_client

    logger.info("=" * 60)
    logger.info("Bot Startup Validation")
    logger.info("=" * 60)

    # 1. Validate workflow configs
    logger.info("Validating workflow configurations...")
    configs = find_services_configs()
    config_errors = []

    for config_path in configs:
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
            if 'services' not in config:
                config_errors.append(f"{config_path}: missing 'services'")
                continue
            substitute_env_vars(config.copy(), str(config_path))
            logger.info(f"  ✓ {config_path}")
        except Exception as e:
            config_errors.append(str(e))

    if config_errors:
        for err in config_errors:
            logger.error(f"  ✗ {err}")
        raise RuntimeError(f"Config validation failed with {len(config_errors)} error(s)")
    logger.info("✓ All workflow configs valid")

    # 2. Check MongoDB
    logger.info("Checking MongoDB connectivity...")
    is_healthy, err = await check_connection()
    if not is_healthy:
        raise RuntimeError(f"MongoDB health check failed: {err}")
    logger.info("✓ MongoDB connection successful")
    await close_mongo_client()

    # 3. Check API keys
    if check_api_keys:
        logger.info("Checking API connectivity...")
        import httpx
        api_errors = []

        checks = []
        if os.getenv("DEEPGRAM_API_KEY"):
            checks.append(("DEEPGRAM_API_KEY", "https://api.deepgram.com/v1/projects",
                          {"Authorization": f"Token {os.getenv('DEEPGRAM_API_KEY')}"}))
        if os.getenv("OPENAI_API_KEY"):
            checks.append(("OPENAI_API_KEY", "https://api.openai.com/v1/models",
                          {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}))
        if os.getenv("DAILY_API_KEY"):
            checks.append(("DAILY_API_KEY", "https://api.daily.co/v1/rooms",
                          {"Authorization": f"Bearer {os.getenv('DAILY_API_KEY')}"}))

        async with httpx.AsyncClient(timeout=10.0) as client:
            for name, url, headers in checks:
                valid, err = await check_api_key(client, name, url, headers)
                if valid:
                    logger.info(f"  ✓ {name}")
                else:
                    api_errors.append(err)

        if api_errors:
            for err in api_errors:
                logger.error(f"  ✗ {err}")
            raise RuntimeError(f"API connectivity failed with {len(api_errors)} error(s)")
        logger.info("✓ All API keys valid")

    logger.info("=" * 60)
    logger.info("✓ Bot validation complete - ready to start")
    logger.info("=" * 60)


# =============================================================================
# Main
# =============================================================================

async def main() -> int:
    global errors, warnings

    quick = "--quick" in sys.argv

    print("=" * 60)
    print("Local Development Validation")
    print("=" * 60)
    print()

    check_env_vars()
    check_imports()
    check_workflow_configs()
    check_flow_definitions()
    await check_mongodb()

    if not quick:
        await check_api_connectivity()
    else:
        print("Skipping API connectivity (--quick)\n")

    print("=" * 60)
    if errors > 0:
        print(f"{RED}FAILED{NC}: {errors} error(s), {warnings} warning(s)")
        return 1
    elif warnings > 0:
        print(f"{YELLOW}PASSED WITH WARNINGS{NC}: {warnings} warning(s)")
        return 0
    else:
        print(f"{GREEN}PASSED{NC}")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
