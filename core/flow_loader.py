from importlib import import_module
from pathlib import Path
from typing import Callable, List

from loguru import logger


def discover_warmup_functions(organization_slug: str) -> List[Callable]:
    """Discover warmup_openai functions for all flows in an organization.

    Returns list of async warmup functions that can be called with call_data.
    """
    warmup_functions = []
    org_path = Path(f"clients/{organization_slug}")

    if not org_path.exists():
        return warmup_functions

    for workflow_dir in org_path.iterdir():
        if not workflow_dir.is_dir():
            continue
        if not (workflow_dir / "flow_definition.py").exists():
            continue

        module_path = f"clients.{organization_slug}.{workflow_dir.name}.flow_definition"
        try:
            module = import_module(module_path)
            if hasattr(module, 'warmup_openai'):
                warmup_functions.append(module.warmup_openai)
                logger.debug(f"Found warmup function in {module_path}")
        except ImportError as e:
            logger.debug(f"Could not import {module_path}: {e}")

    return warmup_functions


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
            eligibility_verification -> EligibilityVerificationFlow
            patient_scheduling -> PatientSchedulingFlow
        """
        return ''.join(word.capitalize() for word in self.client_name.split('_')) + 'Flow'
