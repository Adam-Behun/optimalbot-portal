"""
Client configuration loader.
Loads all YAML files for a client and returns ready-to-use objects.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any
from dataclasses import dataclass
from loguru import logger

from core.schema_parser import ConversationSchema
from core.data_formatter import DataFormatter
from core.prompt_renderer import PromptRenderer


@dataclass
class ClientConfig:
    """Container for all loaded client components"""
    schema: ConversationSchema
    data_formatter: DataFormatter
    prompt_renderer: PromptRenderer
    services_config: Dict[str, Any]


class ClientLoader:
    """Loads and prepares all client configuration"""
    
    def __init__(self, client_name: str):
        """
        Args:
            client_name: Name of client directory in clients/
        """
        self.client_name = client_name
        self.client_path = Path(f"clients/{client_name}")
        
        if not self.client_path.exists():
            raise ValueError(f"Client directory not found: {self.client_path}")
    
    def load_all(self) -> ClientConfig:
        """
        Load all client configuration and create helper objects.
        
        Returns:
            ClientConfig with schema, formatters, and services
        """
        # Load and parse schema + prompts
        schema_data = self._load_schema_yaml()
        prompts_data = self._load_prompts_yaml()
        
        schema = ConversationSchema(
            base_path=self.client_path,
            prompts=prompts_data,
            **schema_data
        )
        
        # Create helpers
        data_formatter = DataFormatter(schema)
        prompt_renderer = PromptRenderer(schema)
        
        # Load services with env substitution
        services_config = self._load_services()
        
        return ClientConfig(
            schema=schema,
            data_formatter=data_formatter,
            prompt_renderer=prompt_renderer,
            services_config=services_config
        )
    
    def _load_schema_yaml(self) -> Dict[str, Any]:
        """Load schema.yaml"""
        with open(self.client_path / 'schema.yaml', 'r') as f:
            return yaml.safe_load(f)
    
    def _load_prompts_yaml(self) -> Dict[str, Any]:
        """Load prompts.yaml"""
        with open(self.client_path / 'prompts.yaml', 'r') as f:
            return yaml.safe_load(f)
    
    def _load_services(self) -> Dict[str, Any]:
        """Load and parse services.yaml with env substitution"""
        services_path = self.client_path / "services.yaml"
        with open(services_path, 'r') as f:
            config = yaml.safe_load(f)
        return self._substitute_env_vars(config)
    
    def _substitute_env_vars(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Replace ${ENV_VAR} placeholders with actual values"""
        for key, value in config.items():
            if isinstance(value, dict):
                config[key] = self._substitute_env_vars(value)
            elif isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                env_var_name = value[2:-1]
                env_value = os.getenv(env_var_name)

                if env_value is None:
                    logger.error(f"❌ Environment variable '{env_var_name}' is not set! Check your .env file.")
                    raise ValueError(f"Required environment variable '{env_var_name}' is not set")

                logger.debug(f"✓ Loaded {env_var_name}: {env_value[:10]}..." if len(env_value) > 10 else f"✓ Loaded {env_var_name}")
                config[key] = env_value
        return config