import os
import yaml
from pathlib import Path
from typing import List, Tuple, Dict, Any
from loguru import logger
import httpx

ENV = os.getenv("ENV", "local")


def find_all_services_configs() -> List[Path]:
    clients_dir = Path("clients")
    if not clients_dir.exists():
        raise RuntimeError("clients/ directory not found")
    configs = list(clients_dir.glob("*/*/services.yaml"))
    if not configs:
        raise RuntimeError("No services.yaml files found in clients/")
    return configs


def substitute_env_vars(config: Dict[str, Any], path: str = "") -> Dict[str, Any]:
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


def validate_services_config(config_path: Path) -> Tuple[bool, List[str]]:
    errors = []

    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        return False, [f"Failed to load {config_path}: {e}"]

    if 'call_type' not in config:
        errors.append(f"{config_path}: missing required 'call_type' field")

    if 'services' not in config:
        errors.append(f"{config_path}: missing required 'services' section")
        return False, errors

    required_services = ['stt', 'llm', 'tts', 'transport']
    for svc in required_services:
        if svc not in config['services']:
            errors.append(f"{config_path}: missing required service '{svc}'")

    # Validate env vars can be substituted
    try:
        substitute_env_vars(config.copy(), str(config_path))
    except ValueError as e:
        errors.append(str(e))

    # Check optional packages if referenced in config
    turn_detection = config.get('turn_detection', {})
    if turn_detection.get('vad', {}).get('type') == 'silero':
        try:
            from pipecat.audio.vad.silero import SileroVADAnalyzer
        except ImportError:
            errors.append(f"{config_path}: uses silero VAD but pipecat-ai[silero] not installed")

    if turn_detection.get('smart_turn', {}).get('type') == 'local_v3':
        try:
            from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
        except ImportError:
            errors.append(f"{config_path}: uses smart_turn v3 but pipecat-ai[local-smart-turn-v3] not installed")

    return len(errors) == 0, errors


def validate_all_configs() -> Tuple[bool, List[str]]:
    all_errors = []
    try:
        configs = find_all_services_configs()
    except RuntimeError as e:
        return False, [str(e)]

    for config_path in configs:
        valid, errors = validate_services_config(config_path)
        if not valid:
            all_errors.extend(errors)
        else:
            logger.info(f"  ✓ {config_path}")

    return len(all_errors) == 0, all_errors


async def check_api_key(client: httpx.AsyncClient, name: str, url: str, headers: dict) -> Tuple[bool, str]:
    try:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 401:
            return False, f"{name} is invalid (401 Unauthorized)"
        elif resp.status_code == 200:
            return True, ""
        else:
            return False, f"{name} returned status {resp.status_code}"
    except Exception as e:
        return False, f"{name} connectivity failed: {e}"


async def check_api_connectivity() -> Tuple[bool, List[str]]:
    errors = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Only check keys that are set
        checks = []

        if os.getenv("DEEPGRAM_API_KEY"):
            checks.append(("DEEPGRAM_API_KEY", "https://api.deepgram.com/v1/projects",
                          {"Authorization": f"Token {os.getenv('DEEPGRAM_API_KEY')}"}))

        if os.getenv("OPENAI_API_KEY"):
            checks.append(("OPENAI_API_KEY", "https://api.openai.com/v1/models",
                          {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}))

        if os.getenv("GROQ_API_KEY"):
            checks.append(("GROQ_API_KEY", "https://api.groq.com/openai/v1/models",
                          {"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"}))

        if os.getenv("CARTESIA_API_KEY"):
            checks.append(("CARTESIA_API_KEY", "https://api.cartesia.ai/voices?limit=1",
                          {"X-API-Key": os.getenv("CARTESIA_API_KEY"), "Cartesia-Version": "2024-06-10"}))

        if os.getenv("ELEVENLABS_API_KEY"):
            checks.append(("ELEVENLABS_API_KEY", "https://api.elevenlabs.io/v1/user",
                          {"xi-api-key": os.getenv("ELEVENLABS_API_KEY")}))

        if os.getenv("DAILY_API_KEY"):
            checks.append(("DAILY_API_KEY", "https://api.daily.co/v1/rooms",
                          {"Authorization": f"Bearer {os.getenv('DAILY_API_KEY')}"}))

        for name, url, headers in checks:
            ok, error = await check_api_key(client, name, url, headers)
            if ok:
                logger.info(f"  ✓ {name}")
            else:
                errors.append(error)

    return len(errors) == 0, errors


async def validate_bot_startup(check_api_keys: bool = True) -> None:
    from backend.database import check_connection, close_mongo_client

    logger.info("=" * 60)
    logger.info("Bot Startup Validation")
    logger.info("=" * 60)

    # 1. Validate all workflow configs and env vars
    logger.info("Validating workflow configurations...")
    configs_valid, config_errors = validate_all_configs()
    if not configs_valid:
        for err in config_errors:
            logger.error(f"  ✗ {err}")
        raise RuntimeError(f"Workflow config validation failed with {len(config_errors)} error(s)")
    logger.info("✓ All workflow configs valid")

    # 2. Check MongoDB connectivity
    logger.info("Checking MongoDB connectivity...")
    is_healthy, error = await check_connection()
    if not is_healthy:
        raise RuntimeError(f"MongoDB health check failed: {error}")
    logger.info("✓ MongoDB connection successful")

    # Close client so caller's event loop gets fresh connection
    await close_mongo_client()

    # 3. Check API key connectivity
    if check_api_keys:
        logger.info("Checking API connectivity...")
        api_ok, api_errors = await check_api_connectivity()
        if not api_ok:
            for err in api_errors:
                logger.error(f"  ✗ {err}")
            raise RuntimeError(f"API connectivity check failed with {len(api_errors)} error(s)")
        logger.info("✓ All API keys valid")

    logger.info("=" * 60)
    logger.info("✓ Bot validation complete - ready to start")
    logger.info("=" * 60)


if __name__ == "__main__":
    import asyncio
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(validate_bot_startup())
