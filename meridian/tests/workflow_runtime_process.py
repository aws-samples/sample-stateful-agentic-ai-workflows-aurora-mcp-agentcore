"""Run the MeridianWorkflow Runtime entry the way the Runtime would, as meridian_workflow.

Usage: python -m tests.workflow_runtime_process '<payload json>' <session_id>

The entry uses the default runner, so retrieval is real and holds go through the live
Gateway. The first line is {"current_user": ...} from the run's own client, then each event
prints as one JSON line.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()
if not os.environ.get("AURORA_WORKFLOW_SECRET_ARN"):
    raise SystemExit(
        "AURORA_WORKFLOW_SECRET_ARN is not set: run scripts/provision_workflow_login.py")
os.environ["AURORA_SECRET_ARN"] = os.environ["AURORA_WORKFLOW_SECRET_ARN"]
os.environ["ENVIRONMENT"] = "production"
os.environ["AGENTCORE_SKIP_CLI_SYNC"] = "1"

import asyncio  # noqa: E402
import json  # noqa: E402

from backend.agents.phase_05_workflow.runtime_entry import workflow_turn  # noqa: E402
from backend.db.rds_data_client import get_rds_data_client  # noqa: E402


async def main(payload: dict, session_id: str) -> None:
    rows = await get_rds_data_client().execute("SELECT current_user AS u")
    print(json.dumps({"current_user": rows[0]["u"]}), flush=True)
    async for event in workflow_turn(payload, session_id=session_id):
        print(json.dumps(event, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main(json.loads(sys.argv[1]), sys.argv[2]))
