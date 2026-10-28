# 02 - MCP

Expose a reusable, named tool contract.

[All five capabilities](../README.md)

## Open this code

| Source | Role |
| --- | --- |
| [`routers/chat.py`](../../routers/chat.py): `mcp_search`, `_call_domain_tool` | Live routing to the typed tools of the Meridian Concierge MCP server: `search_trips` for a filter, then comparison, price range, inventory, currency and loyalty. |
| [`mcp/`](../../mcp/) | Server contracts, clients, argument validation and result handling. |
| [`agent.py`](agent.py) | Reference Strands MCP agent over the generic PostgreSQL MCP server. The live ladder does not run it. |

## Live check

> Compare three trip types and convert each price to euros.

Inspect the tool names, inputs and responses in the trace. The comparison and conversion come from meridian-concierge MCP.

## Architectural takeaway

MCP standardizes the interface; teams still own validation, permissions and execution budgets. Currency rates are illustrative.

## Transition

The next request describes a feeling rather than exact catalog filters: a quiet wine-country retreat.
