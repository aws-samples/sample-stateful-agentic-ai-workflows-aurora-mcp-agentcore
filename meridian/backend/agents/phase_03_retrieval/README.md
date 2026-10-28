# 03 - Retrieval

Find candidates by meaning and keywords.

[All five capabilities](../README.md)

## Open this code

| Source | Role |
| --- | --- |
| [`agent.py`](agent.py): `run_retrieval_turn` | One Strands agent loop over the cumulative tools. The agent's own reply is the visible reply. |
| [`tools.py`](tools.py) | The two tools that run in process: `hybrid_trip_search`, which calls the one hybrid search in [`retrieval/hybrid.py`](../../retrieval/hybrid.py) (vector and full-text candidates, a price and places-left gate, and reranking), and `get_package_details`. |
| [`mcp_session.py`](mcp_session.py) | The `meridian-concierge` stdio MCP client that serves the other Phase 2 tools. |
| [`hooks.py`](hooks.py) | The trace spans and the packages a turn observed. |

## Live checkpoint

> Find a quiet, romantic wine-country retreat with a private villa.

Compare retrieved candidates and the reranked order. Read back actual package details before promising an amenity.

## Architectural takeaway

Relevance finds candidates. Availability, authorization and factual claims need separate checks.

## Transition

The next request requires the known traveler, saved preferences and governed actions.
