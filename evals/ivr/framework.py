import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from evals.llm.framework import LLMEvaluationFramework


class IVRNavigationFramework(LLMEvaluationFramework):
    pass
