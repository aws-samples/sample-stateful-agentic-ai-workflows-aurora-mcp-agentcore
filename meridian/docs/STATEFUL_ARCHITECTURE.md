# Stateful Architecture Decision

## Session promise

**Title:** Build stateful agentic AI workflows with Aurora, MCP, and AgentCore

The architecture is stateful because agents externalize conversational,
operational, governance, and workflow state into durable stores. It does not
depend on a long-lived database connection to remember prior work.

> Statefulness lives in durable stores, not database connections.

## State and transport

| State | Durable store | Access path |
| --- | --- | --- |
| Traveler profile, preferences, conversations, and interactions | Aurora PostgreSQL | RDS Data API |
| Operational records, authorization bindings, and audit evidence | Aurora PostgreSQL | RDS Data API |
| Managed session and semantic memory across turns | Bedrock AgentCore Memory | AgentCore APIs |
| LangGraph execution position, channel values, and pending writes | Aurora PostgreSQL | PostgresSaver over psycopg |
| In-turn model reasoning | Agent process | Transient by design |

The RDS Data API remains a connectionless, IAM-authorized HTTPS transport. It
uses database credentials stored in Secrets Manager to read and write durable
Aurora state. A Data API transaction ID keeps `SET LOCAL ROLE`, traveler GUCs,
and one read or write unit together; it is not long-lived workflow state.
Phase 4 commits its authorized read unit before calling AgentCore or Gateway,
then reauthorizes in a separate short write-and-audit unit.

MCP is orthogonal to the database transport. It defines governed tool contracts;
an MCP server can use the Data API or PostgreSQL wire protocol internally.

## Phase contract

| Phase | State and transport contract |
| --- | --- |
| SQL | Parameterized catalog reads through the Data API |
| MCP | Governed tools whose current database implementation uses the Data API |
| Retrieval | Structured, pgvector, and full-text retrieval from durable Aurora data |
| Production | AgentCore context plus authorized, RLS-scoped Aurora memory and audit |
| Workflow | The same domain-data paths composed by LangGraph, with durable PostgresSaver checkpoints in Aurora |

Phase 5 is intentionally hybrid: workflow nodes can keep using the Data API for
domain data while PostgresSaver uses psycopg for the high-frequency checkpoint
protocol.

## Production target

- Run the workflow worker with network access to the private Aurora endpoint.
- Create one bounded application-lifetime psycopg pool and one shared
  `AsyncPostgresSaver`; run saver setup once during process initialization.
- Use a dedicated least-privilege checkpoint role and secret. Do not use the
  cluster master role.
- Require PostgresSaver in production. `MemorySaver` is an explicit local-demo
  fallback and must never be represented as durable.
- Add RDS Proxy only when replica count, connection churn, or connection-storm
  protection justifies it. The application pool remains bounded either way.
- Keep database transactions short. Do not hold an RLS transaction open while
  waiting for model or external service calls.
- Treat checkpoints and business side effects as separate consistency domains.
  Use idempotency keys plus an outbox, saga, or compensating action for booking
  and rebooking operations.

## Live proof contract

The strongest Phase 5 proof is:

1. Run a multi-node workflow with PostgresSaver.
2. Pause after a committed worker-node checkpoint.
3. Stop and restart the workflow worker.
4. Resume with the same `thread_id`.
5. Show that execution continues from Aurora's checkpoint tables:
   `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, and
   `checkpoint_migrations`.

If the trace says `MemorySaver`, describe the run as in-process only. It does not
satisfy the live Aurora-checkpoint proof.

## Presenter wording

> The Data API remains connectionless, but every turn reads and writes durable
> state in Aurora. AgentCore Memory carries conversational context across turns.
> When execution becomes multi-step, LangGraph externalizes workflow state
> through PostgresSaver into Aurora. We can terminate the worker, restart it,
> and resume from the last committed node.

Avoid these claims:

- "The Data API becomes stateful."
- "The Data API is IAM-only."
- "All five phases run entirely over the Data API."
- "RDS Proxy is always preferred."
- "MemorySaver proves durable workflow recovery."
