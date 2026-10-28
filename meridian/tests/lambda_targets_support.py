"""Load a Gateway target's lambda_function.py under its own module name."""

import importlib.util
from pathlib import Path
from types import ModuleType

TARGETS = (Path(__file__).resolve().parents[1] / "meridian_agentcore" / "agentcore"
           / "gateway_targets")


def load_target(folder: str, module_name: str) -> ModuleType:
    """Import ``gateway_targets/<folder>/lambda_function.py`` as ``module_name``."""
    path = TARGETS / folder / "lambda_function.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
