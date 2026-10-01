# 01 - SQL

Ground an answer in catalog facts.

[All five capabilities](../README.md) | [Talk run of show](../../../docs/TALK_RUN_OF_SHOW.md)

## Open this code

| Source | Role |
| --- | --- |
| [`routers/chat.py`](../../routers/chat.py): `sql_search` | The live request parses supported filters, binds parameters and calls the RDS Data API. |
| [`db/rds_data_client.py`](../../db/rds_data_client.py) | The shared Aurora transport and parameter handling. |
| [`agent.py`](agent.py) | Reference Strands tool wrapper. The live ladder deliberately uses the procedural route above, without a model loop. |

## Live checkpoint

> Show me city trips under $2,000 per traveler.

Open SQL in the activity trace. Match the per-traveler price predicate to the returned catalog rows.

## Architectural takeaway

A valid query can answer the wrong business question. Define the metric and its units before executing SQL.

## Transition

The next request needs reusable operations: compare three packages and convert prices.
