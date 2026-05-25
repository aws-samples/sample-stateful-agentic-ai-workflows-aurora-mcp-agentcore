"""
Provision (or look up) a Bedrock AgentCore Memory store for the Phase 4 demo.

Usage:
    python scripts/provision_agentcore_memory.py --name meridian-session

Prints the memory id on success.  Set it as AGENTCORE_MEMORY_ID in .env to
enable the AgentCore Memory path in `backend/agents/phase4/concierge.py`.

Docs: https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/create_memory.html
"""

import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="meridian-session", help="Memory store name")
    parser.add_argument(
        "--expiry-days", type=int, default=30, help="eventExpiryDuration (days)"
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AGENTCORE_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")),
    )
    args = parser.parse_args()

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)

    # If a memory with this name already exists, reuse it instead of erroring.
    paginator = client.get_paginator("list_memories")
    for page in paginator.paginate():
        for mem in page.get("memories", []):
            if mem.get("name") == args.name:
                print(f"Reusing existing memory: id={mem['id']} status={mem.get('status')}")
                return 0

    try:
        response = client.create_memory(
            name=args.name,
            eventExpiryDuration=args.expiry_days,
            description="Meridian Phase 4 session store",
            memoryStrategies=[{"semanticMemoryStrategy": {}}],
        )
    except ClientError as exc:
        print(f"create_memory failed: {exc}", file=sys.stderr)
        return 1

    memory = response.get("memory", {})
    print(f"Created memory: id={memory.get('id')} status={memory.get('status')}")
    print("Set AGENTCORE_MEMORY_ID in .env to enable Phase 4 AgentCore Memory recall.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
