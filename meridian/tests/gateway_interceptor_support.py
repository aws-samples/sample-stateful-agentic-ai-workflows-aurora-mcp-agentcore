"""Load the interceptor Lambda under a unique module name.

Every Lambda in this repo is called ``lambda_function``; importing two of them by that name in one
pytest session would return whichever was imported first.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "meridian_agentcore" / "agentcore" / "interceptors" / "traveler_pin" / "lambda_function.py"
)


def load_interceptor() -> ModuleType:
    """The production interceptor module, imported from its file."""
    spec = importlib.util.spec_from_file_location("traveler_pin_interceptor", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
