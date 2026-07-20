# Meridian Presenter Guide

This guide is the concise run-of-show for demonstrating Meridian. It focuses on
what to run, what to point out, and which claims the application proves.

For a full talk track, optional code walkthroughs, and query reference, see
[`DEMO_SCRIPT.md`](../DEMO_SCRIPT.md). For deployment and recovery procedures,
see [`OPERATIONS.md`](OPERATIONS.md).

## Audience Takeaways

Meridian builds one agentic travel experience in five capability steps:

1. **SQL** provides precise, structured access to live Aurora data.
2. **MCP** turns database and business operations into reusable tool contracts.
3. **Retrieval** combines pgvector, PostgreSQL full-text search, and reranking.
4. **Production** adds identity, traveler authorization, RLS, memory, and audit.
5. **Workflow** makes multi-step execution explicit, checkpointed, and resumable.

The central message is:

> Stateful systems persist context and execution state in durable stores. They
> do not depend on keeping a database connection alive.

## Before the Demo

Start the backend:

```bash
cd meridian
source venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

Start the frontend:

```bash
cd meridian/frontend
npm run dev
```

Open [`http://localhost:5173/showcase`](http://localhost:5173/showcase).

Verify:

- `/health` reports `status: healthy`.
- The configured Bedrock model is `global.anthropic.claude-sonnet-5`.
- Alex Morgan's profile loads with JFK, party of two, and both loyalty programs.
- The first SQL query returns product cards with images and live inventory.
- Phase 4 shows an authenticated subject and traveler authorization decision.
- Phase 5 reports `PostgresSaver (Aurora - pooled)` before claiming durable resume.

Use the dark theme in a dim room and the light theme when projector contrast is
poor. Keep browser zoom at 100 percent.

## Suggested Run Of Show

Allow about 30 minutes for the walkthrough and leave additional time for
questions.

| Phase | Run this query | Point to | Transition |
| --- | --- | --- | --- |
| **1 - SQL** | `Show me city trips under $2,000 per traveler.` | Parameterized SQL, live rows, inventory | Structured filters work, but business operations need a contract. |
| **2 - MCP** | `Compare three trips from different categories and show their prices in euros.` | MCP tool discovery, comparison, FX conversion | Tools improve interoperability, not semantic understanding. |
| **3 - Retrieval** | `Find a slow, romantic week in wine country with a villa stay.` | pgvector, full-text candidates, Cohere rerank | Intent works, but the system still needs trusted memory. |
| **4 - Production** | `What did we decide about my October Tokyo trip last time? Continue from there.` | Memory facts, identity, ALLOW/DENY, RLS, audit | A multi-step disruption plan now needs durable execution state. |
| **5 - Workflow** | `My JFK flight to Tokyo just got cancelled. Rework the trip and check which departures are still open.` | Named graph nodes, checkpoints, same-thread resume | The plan survives process interruption because state is externalized. |

## Presentation Flow

### 1. Establish the Traveler Problem

Start in **Experience**. Point out:

- The cancelled JFK to HND flight.
- Alex's United Premier 1K and Marriott Bonvoy Platinum status.
- The recovery action and persistent journey workspace.

Explain that the user experience stays consistent while the implementation
gains capabilities phase by phase.

### 2. SQL: Precise Live Data

Run the Phase 1 query and expand the result cards.

In **System proof**, point to the parameterized filter and execution timing.
Explain that the RDS Data API is a connectionless transport to durable Aurora
data. It does not make the application stateless.

Boundary to state: SQL handles exact filters well. Comparison, currency
conversion, loyalty, and other business operations should be owned as explicit
tools.

### 3. MCP: Governed Tool Contracts

Run the comparison query in Phase 2.

Point to `tools/list` and `tools/call`, then the typed comparison and currency
results. MCP standardizes how agents discover and invoke tools; the server can
still choose the appropriate database transport internally.

Boundary to state: MCP improves the interface and governance boundary. It does
not, by itself, solve semantic intent or conversational memory.

### 4. Retrieval: Semantic Plus Lexical

Run the wine-country query in Phase 3.

Point to the three retrieval stages:

1. Cohere Embed v4 creates the query vector.
2. pgvector and PostgreSQL full-text search produce hybrid candidates.
3. Cohere Rerank 3.5 orders the final results.

Explain that candidate generation and reranking are separate concerns. The
trace should make both visible.

### 5. Production: Trusted Memory

Run the Tokyo recall query in Phase 4. Point to recalled facts such as JFK and
the shellfish allergy, then open the RLS proof.

Describe the control chain in order:

1. Authenticate the AWS or AgentCore workload.
2. Authorize that subject for Alex's traveler record.
3. Set the traveler scope and least-privilege database role.
4. Let Aurora RLS filter rows.
5. Audit the authorization decision and data access.

Use the Alex ALLOW and Jordan DENY results as the negative control.

Be precise: this sample authorizes a workload to access a traveler record. A
shared application must also authenticate the human user and bind the verified
user subject, such as a Cognito `sub`, to that traveler.

Run the disruption query once in Phase 4. Production should recall Alex's
context and identify alternatives, then surface `Checkpointed workflow
required` rather than claiming the dependent recovery steps completed. Use that
handoff to introduce Phase 5.

### 6. Workflow: Durable Multi-Step Execution

Run the disruption query in Phase 5.

Point to:

- `classify`
- `search`
- checkpoint write
- `availability`
- checkpoint write
- `synthesize`

Explain the transport split:

- Domain reads and writes use the RDS Data API.
- LangGraph PostgresSaver uses a bounded psycopg pool for checkpoint traffic.
- Both persist durable state in the same Aurora system.

If demonstrating restart recovery, pause after `search`, restart the backend,
and resume the same thread. The proof is the same thread continuing from an
Aurora checkpoint, not an in-memory object surviving.

## Claim Boundaries

Keep these statements explicit:

- **RLS is row filtering, not authentication.** Authorization must establish
  which traveler scope the workload may claim before RLS is set.
- **AgentCore Memory and Aurora have different jobs.** AgentCore carries managed
  session context; Aurora is the durable, RLS-scoped system of record.
- **Data API is connectionless, not stateless.** State lives in committed rows,
  memory records, and checkpoints.
- **MemorySaver is a local fallback.** It does not prove recovery after process
  loss. Use PostgresSaver for the durable workflow claim.
- **The sample plans recovery; it does not issue an airline ticket.** A real
  booking workflow would add payment, approval, and carrier integration steps.

## Readiness Checklist

- [ ] Warm Aurora with one Phase 1 query.
- [ ] Confirm all five phase prompts return their expected proof.
- [ ] Confirm product cards have images, inventory, and aligned actions.
- [ ] Confirm Alex ALLOW and Jordan DENY are both visible.
- [ ] Confirm recalled facts come from Aurora and are highlighted in the reply.
- [ ] Confirm `/health` reports a durable checkpoint backend for Phase 5.
- [ ] Confirm resume continues the same workflow thread after a backend restart.
- [ ] Keep the light theme available for low-contrast projectors.

## Fast Recovery

- **Frontend says offline:** verify the backend is listening on port 8000.
- **First query is slow:** wait for Aurora Serverless v2 and Bedrock cold paths,
  then run the query again before presenting.
- **Memory profile is empty:** rerun `python scripts/seed_data.py`.
- **Checkpoint proof says MemorySaver:** restore the checkpoint connection and
  restart with `LANGGRAPH_CHECKPOINT_REQUIRED=true`.
- **Live service is unavailable:** use the committed screenshot in this
  repository and walk through System proof using the recorded evidence.

## References

- [`DEMO_SCRIPT.md`](../DEMO_SCRIPT.md) - extended narration and query details
- [`STATEFUL_ARCHITECTURE.md`](STATEFUL_ARCHITECTURE.md) - state and transport design
- [`OPERATIONS.md`](OPERATIONS.md) - deployment and operational runbook
- [`CODE_WALKTHROUGH.md`](CODE_WALKTHROUGH.md) - source-oriented walkthrough
