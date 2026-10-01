# 03 - Retrieval

Find candidates by meaning and keywords.

[All five capabilities](../README.md) | [Talk run of show](../../../docs/TALK_RUN_OF_SHOW.md)

## Open this code

| Source | Role |
| --- | --- |
| [`supervisor.py`](supervisor.py) | The running Strands supervisor delegates to read-only specialists. |
| [`search_agent.py`](search_agent.py): `hybrid_search` | Vector and full-text candidates, deduplication and reranking. |
| [`package_agent.py`](package_agent.py), [`booking_agent.py`](booking_agent.py) | Availability and price estimates. Despite its historical class name, BookingAgent does not create bookings. |

## Live checkpoint

> Find a quiet, romantic wine-country retreat with a private villa.

Compare retrieved candidates and the reranked order. Read back actual package details before promising an amenity.

## Architectural takeaway

Relevance finds candidates. Availability, authorization and factual claims need separate checks.

## Transition

The next request requires the known traveler, saved preferences and governed actions.
