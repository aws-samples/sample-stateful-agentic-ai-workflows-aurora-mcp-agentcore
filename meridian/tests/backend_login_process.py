"""Run the backend's own routes as meridian_backend, the way App Runner will.

Usage: python -m tests.backend_login_process

A fresh interpreter, because get_rds_data_client() caches the first client it builds and the
test process already holds the master's. Prints one line, ``RESULT <json>``, with the user the
database saw and the status of each route.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()
os.environ["AURORA_SECRET_ARN"] = os.environ["AURORA_BACKEND_SECRET_ARN"]
os.environ["ENVIRONMENT"] = "development"
for name in (
    "MERIDIAN_API_TOKEN",
    "MERIDIAN_COGNITO_REGION",
    "MERIDIAN_COGNITO_USER_POOL_ID",
    "MERIDIAN_COGNITO_APP_CLIENT_ID",
):
    os.environ.pop(name, None)

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from backend.db.rds_data_client import get_rds_data_client  # noqa: E402
from backend.main import app  # noqa: E402

DECOY = "trv_demo_decoy"
CALLS = (
    ("GET", "/api/me", None),
    ("GET", "/api/health", None),
    ("GET", "/api/packages?limit=3", None),
    ("GET", "/api/memory/me", None),
    ("GET", f"/api/memory/{DECOY}", None),
    ("POST", "/api/diagnostics/rls-probe", {}),
    ("POST", "/api/diagnostics/session-receipt", {"window_minutes": 5}),
    ("GET", "/api/journeys", None),
    ("POST", "/api/chat", {"message": "Show me city trips", "phase": 1}),
)


def main() -> None:
    who = asyncio.run(get_rds_data_client().execute("SELECT current_user AS u"))[0]["u"]
    client = TestClient(app)
    statuses, bodies = {}, {}
    for method, path, body in CALLS:
        response = client.request(method, path, json=body)
        statuses[path] = response.status_code
        bodies[path] = response.json() if response.headers.get(
            "content-type", "").startswith("application/json") else {}
    probe = bodies["/api/diagnostics/rls-probe"]
    print("RESULT " + json.dumps({
        "current_user": who,
        "statuses": statuses,
        "probe": [[t["table"], t["scoped_count"], t["unscoped_count"], t["error"]]
                  for t in probe.get("tables", [])],
        "effective_role": (probe.get("debug") or {}).get("effective_role"),
        "receipt": [[line["table"], line["count"]]
                    for line in bodies["/api/diagnostics/session-receipt"].get("lines", [])],
    }), flush=True)


if __name__ == "__main__":
    main()
