"""Every backend path an identity probe calls is a route the application serves."""

import re
from pathlib import Path

from backend.main import app

PROBES = Path(__file__).resolve().parents[1] / "scripts" / "identity_probes" / "probes.py"
CALLED = re.compile(r'ports\.http\(\s*\w+,\s*"(?:GET|POST)",\s*f?"(/api/[^"]*)"')


def test_each_probe_path_is_a_served_route():
    paths = CALLED.findall(PROBES.read_text())
    assert len(paths) >= 4
    templates = app.openapi()["paths"]
    served = [re.compile(re.sub(r"\{[^}]*\}", "[^/]+", route) + "$") for route in templates]
    unserved = [
        path for path in paths
        if not any(regex.match(re.sub(r"\{[^}]*\}", "x", path)) for regex in served)
    ]
    assert unserved == []
