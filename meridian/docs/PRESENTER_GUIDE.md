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

> Remember what the traveler wants. Save where the work stopped. Check who
> may read or change it. Show the database records that prove each claim.

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

Use the **Presenter controls** bar while preparing with your co-presenter:

- **Preview audience layout** widens the workspace and hides the service sidebar while keeping preparation controls available.
- **Projector readability** increases type size and secondary-text contrast in the preview and fullscreen. Leave it enabled for the room check.
- **Room check** lists the checks to perform on the actual projector. Check a trip price and an evidence label from the back of the room.
- Select **Present fullscreen** before sharing. The entire preparation bar and any open room-check panel disappear; the Meridian brand, four views, and evidence remain usable. Press **Esc** to return to the windowed layout. The current conversation and preparation settings are preserved.

Use the app's fullscreen button for the demo. Controls are visible on a shared windowed screen, so stop sharing before exiting fullscreen. This is a preparation toolbar, not a separate private presenter monitor.

Verify:

- `/health` reports `status: healthy`. This checks process configuration, so also confirm that live catalog and profile reads succeed. An expired AWS session can leave process health green while those reads fail.
- The configured Bedrock model is `global.anthropic.claude-sonnet-5`.
- Alex Morgan's profile loads with JFK, party of two, and both loyalty programs.
- The first SQL query returns product cards with images and live inventory.
- Phase 4 shows an authenticated subject and traveler authorization decision.
- `python scripts/verify_agentcore.py` reports Runtime, Gateway and Memory ready, the policy engine `ACTIVE · ENFORCE`, three gateway tools, and observability on.
- `python scripts/smoke_production_turn.py` ends with three PASS lines: unconfirmed denied, confirmed held, over budget denied.
- Phase 5 reports `checkpoint_durable: true` with `AuroraDataApiSaver` or `PostgresSaver (Aurora · pooled)`. `MemorySaver` cannot prove restart recovery.

Use the dark theme in a dim room and the light theme when projector contrast is
poor. Keep browser zoom at 100 percent.

## Suggested Run Of Show

[`DEMO_SCRIPT.md`](../DEMO_SCRIPT.md) holds the authoritative timing budget:
about 45 minutes of content in a 60-minute slot, leaving the balance as
distributed Q&A. Use the table below as the at-a-glance card and that budget
for pacing.

**Start with Concierge.** Send one trip request to show the traveler experience.
Then open **Capability ladder** for the five phases, boundary queries, and
technical evidence. Use **Architecture & evidence** to open the deeper explanation
when needed; keep it closed while introducing a phase.

**Expect a pause on Phases 3 to 5.** On a warm cluster, Phase 1 returns in
about a second and Phase 2 in about two. Phases 3 and 4 take roughly 13 to 25
seconds because each turn makes two Bedrock round trips (specialist routing
and the concierge rewrite) on top of embedding, pgvector, and rerank. That is
time to explain the request path. The chat endpoint returns the trace with its
completed response; the UI does not stream live step completion while it waits.

| Phase | Run this query | Point to | Transition |
| --- | --- | --- | --- |
| **1 - SQL** | `Show me city trips under $2,000 per traveler.` | Parameterized SQL, live rows, inventory | Structured filters work, but business operations need a contract. |
| **2 - MCP** | `Compare three trip types and convert each price to euros.` | MCP tool discovery, comparison, FX conversion | Tools improve interoperability, not semantic understanding. |
| **3 - Retrieval** | `Find a quiet, romantic wine-country retreat with a private villa.` | pgvector, full-text candidates, Cohere rerank | Intent works, but the system still needs trusted memory. |
| **4 - Production** | `Recall my Tokyo plan and saved preferences: home airport, food needs, and budget.` then click **Hold** on a trip, then type `Hold the first option for two travelers now.` | Memory facts, identity, ALLOW/DENY, RLS, audit, the runtime's gateway tool calls, one Cedar permit and one Cedar deny | A multi-step disruption plan now needs durable execution state. |
| **5 - Workflow** | `My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.` | Named graph nodes, checkpoints, same-thread resume | The plan survives process interruption because state is externalized. |

## Presentation Flow

### 1. Establish the Traveler Problem

Start in **Experience**. Point out:

- The canceled JFK to HND flight.
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
5. Hand the authorized context to the agent in AgentCore Runtime, which discovers its tools from AgentCore Gateway over MCP with its own signed identity.
6. Let the gateway's Cedar policy engine decide every tool call on the arguments the runtime pinned: the traveler id, the confirmation flag and the budget ceiling come from the request, never from the model.
7. Audit the authorization decision and data access; the hold Lambda proves its own grant before it writes.

Use the Alex ALLOW and Jordan DENY results as the negative control, then click
**Hold** for the Cedar permit and type a hold request for the Cedar deny.

Be precise: this sample authorizes a workload to access a traveler record. A
shared application must also authenticate the human user and bind the verified
user subject, such as a Cognito `sub`, to that traveler.

Run the disruption query once in Phase 4. Production should recall Alex's
context and identify alternatives, then surface `Checkpointed workflow
required` rather than claiming the dependent recovery steps completed. Use that
handoff to introduce Phase 5.

### 6. Workflow: Durable Multi-Step Execution

In Phase 5, select **Run to checkpoint**. This view explains the mechanism:
search for alternatives, save the shortlist, and record the next step.
The Recovery desk continues this run with the traveler’s decision.

Point to:

- `classify`
- `search`
- checkpoint write
- `availability`
- checkpoint write
- `synthesize`

Explain the transport split:

- Domain reads and writes use the RDS Data API.
- LangGraph saves checkpoints through `AuroraDataApiSaver` over the RDS Data API, or `PostgresSaver` through a bounded psycopg pool when a DSN is configured. Name the backend reported by this run.
- Both persist durable state in the same Aurora system.

If demonstrating restart recovery, pause after `search`, restart the backend,
and resume the same thread. The proof is the same thread continuing from an
Aurora checkpoint, not an in-memory object surviving.

### 7. Handoff: From Saved Progress to Traveler Recovery

When the workflow pauses, say:

> “We have saved the shortlist and the next step. Now let’s pick up this
> traveler’s plan at the recovery desk.”

Select **Continue at recovery desk**. This changes the view without sending
another chat request, clearing the shortlist, or changing the thread.
Then select **Resume and verify**. Point to the available packages and the
hold receipt near the itinerary. Open **View system evidence** to read the
checkpoint, worker executions, access decision, and hold back from Aurora.
Only claim a worker restart when the recorded executions show it.

Keep the two hold policies distinct:

- The recovery workflow requests a **15-minute package hold** after resume.
  Its receipt uses booking creation and expiry timestamps. A saved shortlist
  alone does not reserve inventory.
- **Request 12-hour hold** in trip details creates a separate courtesy hold.
  Its countdown appears in the drawer, the Concierge travel brief, and the
  Recovery desk. Closing a drawer or moving between views keeps the original
  expiry. The app blocks another request for that package while its known
  hold is active in this browser session.
- The timer displays the expiry; Aurora enforces it. Inventory queries stop
  counting expired holds. Neither policy reserves flight seats or charges
  payment.
- Direct-hold receipts stay in this browser session’s app state. Refreshing
  clears that local display; the booking and its expiry remain in Aurora.
  The 12-hour clock uses device time. Workflow receipts read back from
  Aurora can use the database observation time.

### 8. Close, Then Open the Room (60–90 Seconds)

From **System evidence**, select **Session takeaways**, available in fullscreen
too. The headline is **A canceled flight. A clear way forward.** Lead with the
service Alex needs: relevant alternatives, preferences carried into the plan,
and a package hold that gives time to decide. This summarizes the intended
customer experience; System evidence establishes what this run actually did.

> “Alex needs a way forward after a canceled flight: options that fit, a plan
> that keeps the details, and time to decide. Aurora brings the search, traveler
> context, checkpoints, and holds into one PostgreSQL database. AgentCore gives
> the concierge a managed runtime, session memory, and workload identity.
> Traveler grants and RLS in Aurora control which records it can use. MCP
> connects the tools, and LangGraph continues from the saved step. That is how
> these building blocks support a recovery experience the traveler can follow.”

Point to three takeaways:

- **Amazon Aurora: Find the options. Keep the plan.** Hybrid retrieval,
  traveler context, workflow checkpoints, and business records share one
  database. This is the consolidation argument from the session abstract.
- **Amazon Bedrock AgentCore: Run the concierge. Carry context forward.**
  Runtime, Memory, and Identity support the agent. Identity identifies the
  workload; application grants and Aurora RLS enforce traveler access.
- **MCP + LangGraph: Resume from the saved step.** Named tool contracts and
  checkpointed execution connect the recovery steps. Stable hold request IDs
  protect retries; a checkpoint alone does not make every action exactly-once.

For deeper questions, explain that Strands implements the agent tool loop;
MCP standardizes the tool interface; LangGraph owns explicit workflow steps.
Keep those implementation details in the discussion rather than adding more
rows to the closing screen.

Select **Open for questions** and pause:

> “What would you build for your customers? From disrupted trips to delayed
> orders, which customer journey needs a better way forward?”

**Explore the live evidence** returns to the same journey for technical
questions. **Back to takeaways** revisits the three patterns. **Build from
the sample** opens the repository. **Return to Meridian** returns to the
Concierge. None of these actions resets the workflow or places a hold.

Presenter preparation controls remain hidden in fullscreen. The takeaways
and Q&A are audience content, so they remain visible while sharing.

## Governance Q&A

These three come up every time the RLS probe runs. DEMO_SCRIPT.md defers to
this section for them.

**"Why is `trip_interactions` 33 of 34 when preferences drop to 17 of 22?"**
The row counts differ because the decoy traveler owns one interaction and five
preferences. RLS is doing identical work in both cases; only the seed
distribution differs. Lead with `traveler_preferences`, which shows the
collapse clearly, and treat the interactions row as a second table under the
same policy rather than a second proof.

**"What happens when the traveler scope is missing?"**
The current policies compare each row’s traveler ID to
`current_setting('app.current_traveler_id', true)`. Missing scope does not match
traveler rows. There is no allow-all empty-scope branch. `scoped_session` also
requires an application role and an authorized traveler before querying.

**"Why use a restricted role as well as FORCE ROW LEVEL SECURITY?"**
The current SQL uses both. The application sets a restricted role inside the
transaction; that role owns no protected tables and has no RLS bypass. `FORCE`
adds protection for table owners, but superusers and roles with `BYPASSRLS` can
still bypass policies. Verify the active role and the returned rows in the
negative-control probe. See [`rls_for_agents.sql`](../examples/rls_for_agents.sql).

## Claim Boundaries

Keep these statements explicit:

- **RLS is row filtering, not authentication.** Authorization must establish
  which traveler scope the workload may claim before RLS is set.
- **AgentCore Memory and Aurora have different jobs.** AgentCore carries managed
  session context; Aurora is the durable, RLS-scoped system of record.
- **Data API is connectionless, not stateless.** State lives in committed rows,
  memory records, and checkpoints.
- **MemorySaver is a local fallback.** It does not prove recovery after process
  loss. Use a verified Aurora checkpointer for the durable workflow claim.
- **The sample plans recovery; it does not issue an airline ticket.** A real
  booking workflow would add payment, approval, and carrier integration steps.
- **Cedar governs tool arguments; it does not authenticate the human.** The
  gateway sees the confirmation flag, the budget ceiling and the traveler id
  that the runtime pinned from the authorized request. The click on **Hold** is
  the confirmation. A deny is the engine finding no permit for those arguments;
  the runtime explains which condition failed from the same arguments.
- **One permit, four conditions, default deny.** The hold policy is a single
  `permit` with `when` conditions. There is no separate `forbid`: a forbid
  created alongside its permit fails Cedar validation as overly restrictive
  when CloudFormation creates them in parallel.

## Readiness Checklist

- [ ] Warm Aurora with one Phase 1 query.
- [ ] Confirm all five phase prompts return their expected proof.
- [ ] Confirm product cards have images, inventory, and aligned actions.
- [ ] Confirm Alex ALLOW and Jordan DENY are both visible.
- [ ] Confirm `scripts/verify_agentcore.py` exits 0 and `scripts/smoke_production_turn.py` prints three PASS lines.
- [ ] Confirm a **Hold** click in Phase 4 shows the Cedar permit and a hold id, and a typed hold shows **Denied by policy**.
- [ ] Confirm recalled facts come from Aurora and are highlighted in the reply.
- [ ] Confirm `/health` reports a durable checkpoint backend for Phase 5.
- [ ] Confirm resume continues the same workflow thread after a backend restart.
- [ ] Keep the light theme available for low-contrast projectors.

## Fast Recovery

- **Frontend says offline:** check the backend and AWS session. If you refreshed the session but the backend still reports `ExpiredTokenException`, restart Uvicorn so its AWS clients load the new session, then select **Reconnect**. Reconnect retries the data reads; it does not restart the backend. Process health alone is not enough; trips and traveler details must load.
- **First query is slow:** wait for Aurora Serverless v2 and Bedrock cold paths,
  then run the query again before presenting.
- **Memory profile is empty:** check the session, traveler authorization, and seed records. Re-seed only a fresh disposable database; preserve an existing demo journey.
- **Checkpoint proof says MemorySaver:** restore the checkpoint connection and
  restart with `LANGGRAPH_CHECKPOINT_REQUIRED=true`.
- **A Hold click is refused with "traveler_not_authorized":** the holds Lambda
  role lost its grant. Run `python scripts/bind_gateway_workload.py` from
  `meridian/` and click again.
- **Phase 4 says the platform is not configured or the gateway lists fewer
  than three tools:** run `python scripts/verify_agentcore.py`; if the policy
  engine row is MISSING, `agentcore deploy -y` from `meridian_agentcore`
  reattaches it.
- **Live service is unavailable:** use the committed screenshot in this
  repository and walk through System evidence using saved screenshots or exported records. Label that walkthrough as recorded; do not imply it is a live run.

## References

- [`DEMO_SCRIPT.md`](../DEMO_SCRIPT.md) - extended narration and query details
- [`STATEFUL_ARCHITECTURE.md`](STATEFUL_ARCHITECTURE.md) - state and transport design
- [`OPERATIONS.md`](OPERATIONS.md) - deployment and operational runbook
- [`CODE_WALKTHROUGH.md`](CODE_WALKTHROUGH.md) - source-oriented walkthrough
