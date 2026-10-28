"""The backend and the frontend name a worded hold or booking failure with the same code."""

from __future__ import annotations

import re
from pathlib import Path

from backend.agents.phase_04_production.write_outcome import GOVERNED_WRITE_CODE

REQUEST_TS = Path(__file__).resolve().parents[1] / "frontend/src/api/request.ts"
FRONTEND_CONSTANT = re.compile(r"^export const GOVERNED_WRITE_CODE = '([^']+)';$", re.MULTILINE)


def test_the_frontend_constant_is_the_backend_constant():
    found = FRONTEND_CONSTANT.findall(REQUEST_TS.read_text(encoding="utf-8"))
    assert found == [GOVERNED_WRITE_CODE]
