"""A workflow worker in its own process, for the live crash test.

Usage: python -m tests.workflow_process <thread_id> <mode>
  start     fresh run; prints the result status
  hold      resume and commit the hold, then sleep holding the lease until killed
"""

import asyncio
import json
import sys
from unittest.mock import AsyncMock

from dotenv import load_dotenv

load_dotenv()

from backend.agents.phase_05_workflow.runner import WorkflowCommand  # noqa: E402
from backend.agents.phase_05_workflow.service import build_workflow_runner  # noqa: E402
from tests.phase5_support import GatewayFake, fake_availability, fake_search  # noqa: E402

QUERY = "My flight was canceled. Rework my Tokyo trip and check availability."


async def main(thread_id: str, mode: str) -> None:
    runner = build_workflow_runner(
        search_fn=fake_search, availability_fn=fake_availability,
        gateway_call=GatewayFake(), lease_seconds=15, heartbeat_seconds=3,
        pause_after="hold" if mode == "hold" else None,
    )
    runner._nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_live", 400000))
    runner._nodes._booking_status = AsyncMock(return_value=None)

    async def hold_forever(result, claim):
        print(json.dumps({"event": "paused", "worker": claim.worker_id}), flush=True)
        await asyncio.Event().wait()

    command = WorkflowCommand(
        query=QUERY, traveler_id="trv_meridian_demo", thread_id=thread_id,
        resume=mode == "hold", travelers_count=2,
    )
    result = await runner.run(command, after_pause=hold_forever if mode == "hold" else None)
    print(json.dumps({"event": "done", "status": result["workflow_status"]}), flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
