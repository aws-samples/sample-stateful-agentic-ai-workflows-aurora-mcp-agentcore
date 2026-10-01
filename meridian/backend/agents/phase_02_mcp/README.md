# 02 - MCP

Expose a reusable, named tool contract.

[All five capabilities](../README.md) | [Talk run of show](../../../docs/TALK_RUN_OF_SHOW.md)

## Open this code

| Source | Role |
| --- | --- |
| [`routers/chat.py`](../../routers/chat.py): `mcp_search`, `_call_domain_tool` | Live routing to the PostgreSQL and Meridian Concierge MCP servers. |
| [`mcp/`](../../mcp/) | Server contracts, clients, argument validation and result handling. |
| [`agent.py`](agent.py) | Reference Strands MCP agent. The live ladder executes the MCP steps directly. |

## Live checkpoint

> Compare three trip types and convert each price to euros.

Inspect the tool names, inputs and responses in the trace. The comparison and conversion come from meridian-concierge MCP.

## Architectural takeaway

MCP standardizes the interface; teams still own validation, permissions and execution budgets. Currency rates are illustrative.

## Transition

The next request describes a feeling rather than exact catalog filters: a quiet wine-country retreat.
