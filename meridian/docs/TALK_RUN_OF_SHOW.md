# Meridian re:Invent run of show

**60-minute chalk talk: 40 minutes of content, 20 minutes for discussion.**
The sequence follows the app: Concierge, Capability ladder, Recovery desk,
System evidence, then Solution briefing as the discussion aid.

## Story and five live checkpoints

| Clock | Screen and code | Say and show | Evidence before moving on |
| --- | --- | --- | --- |
| 00-04 | Concierge | Start with the finished product: Jordan needs a trip that fits saved preferences. Show the catalog and one streamed response. Two builders started with a travel app; each new requirement adds a capability. | Real catalog rows, saved context and the runtime trace. No purchase or supplier integration. |
| 04-06 | Capability ladder | Preview the five steps. Aurora holds the business facts, traveler context and durable workflow state. | Name the failure each step solves. |
| 06-10 | **01 SQL**: [guide](../backend/agents/phase_01_sql/) | Query city trips under $2,000 per traveler. Open only the filter construction and parameterized execution. | Executed SQL and matching rows. State the unit: price per traveler. |
| 10-14 | **02 MCP**: [guide](../backend/agents/phase_02_mcp/) | Compare packages and convert prices. Show the named tool's input and output. | Tool name, arguments and actual response. FX data is indicative. |
| 14-20 | **03 Retrieval**: [guide](../backend/agents/phase_03_retrieval/) | Find a quiet wine-country retreat. Show the vector and lexical candidate paths, then reranking. | Candidate and rerank scores. Relevance does not prove availability or amenities. |
| 20-29 | **04 Production**: [guide](../backend/agents/phase_04_production/) | Introduce AgentCore before invoking it: a laptop does not provide a managed runtime, shared identity boundary or governed tool access. Enable traveler context, recall preferences, then inspect an action's policy and receipt. After the row-level security code, spend about one minute on the identity slide; read only the highlighted lines on the two code slides to keep the block at nine minutes. | Workload grant, RLS scope, Runtime/Gateway spans and Cedar decision. After the release, the traveler comes from a signed-in Amazon Cognito token that each layer checks, and the decoy user is refused at all four layers. |
| 29-37 | **05 Workflow**: [guide](../backend/agents/phase_05_workflow/) and Recovery desk | Recover the canceled trip. Pause after search, choose Stop runtime session on the continuity rail and resume. Open the graph, snapshot storage and stable action identity. | Same thread, replacement execution and authoritative hold readback. The lost-response exercise is a separate proof if time allows. |
| 37-40 | System evidence | Connect the decision to the committed result. Summarize the five takeaways in Solution briefing. | Snapshot, worker, authorization and hold identity. No claim of exactly-once execution. |
| 40-60 | Solution briefing and chalkboard | Take questions. Open one numbered briefing section at a time. Use optional Cedar/Dogwood and failure-recovery depth only when useful. | Distinguish shipped behavior, optional patterns and asks for AWS. |

## If time compresses

- At 20 minutes, move to Production even if retrieval questions remain. Keep one code highlight per capability.
- At 29 minutes, start Workflow. Omit the extra lost-response exercise, secondary prompts and detailed policy source reading.
- Preserve all five checkpoints, the recovery outcome and at least 15 minutes of discussion. A slow service call is an opportunity to open its architecture and explain the boundary; do not replay an uncertain write.
- If a live step fails, show the failure honestly and use the architecture/code for explanation. Previously captured evidence must be labeled as a prior run.

## Before the room opens

1. Confirm the intended AWS identity and that `MeridianWorkflow` is READY. Load the catalog and Jordan's travel brief through the exact URL you will present.
2. Verify the deployed bundle and backend image, then run the targeted hosted checks. Git push and a healthy process alone are not deployment parity.
3. Rehearse the session stop on a rehearsal journey; do not stop the hosted service on stage. Keep the same conversation and journey references.
4. Open the five source highlights and the deck ahead of time. Keep terminals, credentials and operational logs off the projected screen.
5. Use dark presentation mode, blue buttons and readable browser zoom. Check the physical projector from the back row.
6. Review the sample traveler's bookings with `release_demo_bookings.py --dry-run`; release only the specific rehearsal records you own.

Automated checks do not replace a timed human rehearsal, projector check or
slide-to-app playback review. Record those separately before calling the event ready.
