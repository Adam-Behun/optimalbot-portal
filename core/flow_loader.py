import logging
from importlib import import_module
from pathlib import Path

logger = logging.getLogger(__name__)


class FlowLoader:

    def __init__(self, organization_slug: str, client_name: str):
        self.organization_slug = organization_slug
        self.client_name = client_name
        self.client_path = Path(f"clients/{organization_slug}/{client_name}")

        if not self.client_path.exists():
            raise ValueError(f"Client directory not found: {self.client_path}")

    def load_flow_class(self):
        """Dynamically import and return the client's flow class."""
        module_path = f"clients.{self.organization_slug}.{self.client_name}.flow_definition"

        try:
            module = import_module(module_path)
        except ImportError as e:
            raise ImportError(f"Failed to import {module_path}: {e}")

        class_name = self._get_flow_class_name()

        try:
            flow_class = getattr(module, class_name)
        except AttributeError:
            raise AttributeError(
                f"Flow class '{class_name}' not found in {module_path}. "
                f"Available attributes: {dir(module)}"
            )

        return flow_class

    def _get_flow_class_name(self) -> str:
        """Convert client_name to flow class name.

        Examples:
            prior_auth -> PriorAuthFlow
            insurance_verification -> InsuranceVerificationFlow
        """
        return ''.join(word.capitalize() for word in self.client_name.split('_')) + 'Flow'
