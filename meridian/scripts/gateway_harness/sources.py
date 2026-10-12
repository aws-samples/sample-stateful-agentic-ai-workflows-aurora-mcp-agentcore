"""The two Lambda deployment packages the harness creates, built in memory."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

HARNESS_LAMBDAS = Path(__file__).resolve().parent / "lambdas"
PRODUCTION_INTERCEPTOR = (
    Path(__file__).resolve().parents[2]
    / "meridian_agentcore" / "agentcore" / "interceptors" / "traveler_pin" / "lambda_function.py"
)
ECHO_HANDLER = "echo_target.lambda_handler"
INTERCEPTOR_HANDLER = "interceptor_wrapper.lambda_handler"


def _zip(files: dict[str, Path]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def echo_package() -> bytes:
    """The echo target: ``echo_target.py`` only."""
    return _zip({"echo_target.py": HARNESS_LAMBDAS / "echo_target.py"})


def interceptor_package() -> bytes:
    """The wrapper plus the production interceptor, byte for byte."""
    return _zip({
        "interceptor_wrapper.py": HARNESS_LAMBDAS / "interceptor_wrapper.py",
        "lambda_function.py": PRODUCTION_INTERCEPTOR,
    })
