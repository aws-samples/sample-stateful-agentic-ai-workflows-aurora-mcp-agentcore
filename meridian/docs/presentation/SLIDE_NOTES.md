# Meridian slide text and speaker notes

[Editable PowerPoint](Meridian-reInvent-chalk-talk.pptx) · [Previous PDF export - refresh pending](Meridian-reInvent-chalk-talk.pdf) · [Presenter runbook](../PRESENTER_RUNBOOK_2026-09-20.md)

Updated September 21, 2026. The 23-slide core budgets 40 minutes, with 20 minutes for discussion/flex and two optional slides. Delivery timings are estimates, not a measured human rehearsal. Dated API timings and release evidence remain in the runbook and readiness reports.

Use one example per capability, one refusal, the recovery hold as the permitted path, and the lost-response fault as the central recovery example. Keep broader AWS asks in audience-led discussion.

## 1. Build stateful agentic workflows with Aurora, MCP, and AgentCore

**On slide:**

re:Invent · L300
Aditya Samant
Principal DB Specialist Solutions Architect
Shayon Sanyal
Principal DB Specialist Solutions Architect

**Speaker notes:**

CORE 0:00-0:30. Today we follow a travel request from a grounded query to a governed action that survives an interrupted worker. Use the application, inspect the code behind three decisions, then verify the database records. The pattern applies to reservations, orders, refunds and approvals. Introduce Aditya and Shayon briefly. The slot is 60 minutes: 40 minutes prepared content and 20 minutes discussion/flex. No event session code is assigned in this source.

## 2. Alex needs a plan that can survive an interruption

**On slide:**

FICTIONAL TRAVEL SCENARIO · LIVE AWS IMPLEMENTATION
A canceled JFK-to-Tokyo trip. · Saved preferences. · A limited package hold.
What must survive when the worker disappears?

**Speaker notes:**

CORE 0:30-2:00. Alex's JFK-to-Tokyo trip is disrupted. We know saved preferences, need suitable alternatives and may place a temporary package hold. Ask: What would you need to know before safely retrying an interrupted action? Take one or two answers. Show the traveler brief. Alex and the catalog are fictional; the implementation uses real AWS services. A Meridian hold changes its own catalog records, without reserving airline seats, contacting suppliers or taking payment. Slide screenshots are dated local-build captures.

## 3. Three questions organize the talk

**On slide:**

IMPLEMENTED IN MERIDIAN
Who may act?
Browser access · Workload-to-traveler grant · Cedar before a tool runs
What must persist?
Traveler facts · Conversation context · Checkpoint + hold intent
How do we prove it?
A new worker · The same thread · One persisted booking

**Speaker notes:**

CORE 2:00-4:00. Organize the talk around three questions: Who may act? What must persist? What proves the outcome? Sketch traveler facts/conversation context, workflow progress/saved intent, and committed business records. Connect those records as the demo progresses. A fluent answer cannot establish that a booking exists. Timing gates: leave capabilities by minute 12; begin recovery at 20; open evidence at 32; close from 37. These are delivery estimates, not a measured human rehearsal.

## 4. SQL → MCP → retrieval

**On slide:**

MERIDIAN CAPABILITY LADDER
Add capabilities when the question requires them.

**Speaker notes:**

CORE 4:00-4:20. Add capabilities when the question requires them: filter known fields, invoke named tools, then search by meaning. These are configured teaching paths, not universal limits of SQL, MCP or Strands. Move directly to the first query.

## 5. Start with grounded catalog rows

**On slide:**

PHASE 1 · DIRECT RDS DATA API
Prompt
City trips under $2,000 · per traveler
→
FastAPI
Validate and parameterize
→
Aurora
Filter catalog rows
Evidence: executed SQL + returned prices
Define the price unit before querying. This path uses bounded SQL filters.

**Speaker notes:**

CORE 4:20-6:00. Run: Show me city trips under $2,000 per traveler. Show the executed SQL, bound parameters and returned prices. This route interprets a bounded request and executes parameterized filters; it is not unrestricted model-generated SQL. Ask briefly: Does under $2,000 mean each traveler, the whole party, or the complete itinerary? Correct SQL can answer the wrong business question. Teams own these definitions and validated regression examples. Backend source: routers/chat.py. The separate Strands SQLAgent is reference code. Cut the extra SQL stretch prompt if time is tight.

## 6. Tools make capabilities explicit

**On slide:**

PHASE 2 · MCP CONTRACTS
Client
List tools · Call named tool
→
meridian-concierge
compare_packages · currency_convert
→
Aurora + rates
Catalog records · Illustrative FX data
One server for this comparison prompt. PostgreSQL MCP is a separate catalog path.

**Speaker notes:**

CORE 6:00-8:00. Run: Compare three trip types and convert each price to euros. Show named calls and their inputs/results. MCP provides a discoverable interface and argument contract. meridian-concierge handles this comparison and conversion; PostgreSQL MCP is a separate catalog path. FX rates are configured demonstration data, not live market quotes. A tool contract still needs trusted inputs, authorization and execution limits. Keep one comparison example and move on.

## 7. Search by meaning, then verify

**On slide:**

MERIDIAN CAPABILITY LADDER
Retrieval finds candidates. Inventory decides availability.

**Speaker notes:**

CORE 8:00-8:20. Alex may describe an experience rather than a database category. Retrieval identifies promising candidates. Authoritative checks still govern price, availability, access and the eventual write. Transition to the meaning-based query.

## 8. Words and meaning produce candidates

**On slide:**

PHASE 3 · HYBRID RETRIEVAL + RERANK
Retrieve
Full-text search · pgvector similarity
→
Fuse
Merge + deduplicate · Bound candidates
→
Rerank
Bedrock relevance · Return a shortlist
“Find a quiet, romantic wine-country retreat with a private villa.”
If reranking is unavailable, label the hybrid-order fallback.

**Speaker notes:**

CORE 8:20-10:00. Run: Find a quiet, romantic wine-country retreat with a private villa. Trace semantic candidates, lexical candidates, fusion and reranking. Full-text preserves exact terms, vectors find related descriptions, and reranking evaluates the candidate set against the request. Show the observed order without promising a fixed winner or accuracy gain. At scale, evaluate recall under actual eligibility/authorization filters and load; this small catalog is not a performance benchmark. Label hybrid-order fallback if reranking is unavailable. The activity animation replays returned evidence, not a measured execution timeline.

## 9. Finding a trip does not authorize a reservation

**On slide:**

IMPLEMENTED IN MERIDIAN
Search
Interpret the request · Retrieve grounded candidates
Details
Read package attributes · Check published durations
Availability
Read inventory · No booking write in Retrieval

**Speaker notes:**

CORE 10:00-12:00. A relevant result is a candidate. Price, duration, availability and permission require authoritative checks. Show search, details and availability as distinct read operations. The configured Retrieval specialists are read-only; the legacy BookingAgent name means availability checking here. The write boundary comes later. Use the saved-preferences phase-boundary prompt only if time permits. Transition: We found something plausible; now establish traveler context and authority.

## 10. Remember the traveler. Govern the action.

**On slide:**

MERIDIAN CAPABILITY LADDER
Context is useful. Authority must be explicit.

**Speaker notes:**

CORE 12:00-12:20. Saved preferences improve a recommendation. They do not grant permission to reserve it. Move from finding an option to establishing the authority behind an action.

## 11. Three kinds of state have different owners

**On slide:**

IMPLEMENTED IN MERIDIAN
Traveler facts
Aurora preferences · Saved budget · Read under RLS
Conversation
AgentCore Memory · Runtime session context · Aurora turn mirror
Execution
Aurora checkpoints · Worker leases · Stable hold intent

**Speaker notes:**

CORE 12:20-15:00. Enable Use traveler context and wait for On. Run: Recall my Tokyo plan and saved preferences: home airport, food needs, and budget. Identify JFK, the shellfish exclusion and the saved budget. Aurora stores traveler facts under a scoped role/RLS; Phase 4 uses AgentCore Memory for conversation context; Phase 5 uses Aurora checkpoints for execution. These are implementation choices. AgentCoreMemorySaver also persists LangGraph checkpoints. A checkpoint provider does not replace the authoritative business receipt. The sample binds a shared presenter principal to Alex, not a multi-user sign-in system. Skip a second memory toggle demonstration in the core. Reference: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-integrate-lang.html

## 12. Put authority outside the model

**On slide:**

PHASE 4 · TRUST BOUNDARIES
Browser → API
Authenticate caller · Bind Alex
→
Runtime hooks
Pin traveler · Confirmation + budget
→
Gateway + Cedar
Evaluate permission · Then invoke Lambda
Aurora transaction
Authorize the Lambda workload; apply RLS; validate ownership, price and inventory.

**Speaker notes:**

CORE 15:00-17:00. The model cannot appoint itself Alex. Trace authenticated application access, workload-to-traveler binding, trusted arguments, Gateway policy and the database transaction. CODE STOP 1: meridian_agentcore/app/MeridianConcierge/turn_trace.py::_pin_arguments. Traveler, confirmation and budget come from trusted application context. Cedar evaluates that supplied context; trusted callers must not invent it. This deployment uses IAM workload grants, not AgentCore Identity as human sign-in. The database scopes access and validates the operation. Subtle tenancy callout: RLS does not reserve compute or provide tenant-scoped recovery. Those need separate design decisions.

## 13. An applicable permit is required

**On slide:**

IMPLEMENTED IN MERIDIAN
Read
Search packages · Read package details · Authenticated tools
Hold
Traveler confirmed · ≤6 travelers · ≤12 hours · Within saved budget
Confirm
Traveler confirmed · Within saved budget · Aurora checks owner + expiry

**Speaker notes:**

CORE 17:00-20:00. Show one actual unconfirmed or over-budget refusal, then inspect its evidence. ENFORCE is required. An applicable permit is needed before the Gateway target runs. The configured hold rule checks confirmation, party size, duration and budget. Database ownership and inventory validation still follow a permit. Only call it a Cedar denial if the actual policy evidence establishes that; distinguish application refusal, IAM failure and traveler-grant failure. Do not quietly change constraints to manufacture success. The recovery hold supplies the permitted path. Workflow courtesy holds last 15 minutes; the separate direct path uses 12 hours.

## 14. The worker can disappear

**On slide:**

MERIDIAN CAPABILITY LADDER
The journey should not.

**Speaker notes:**

CORE 20:00-20:20. We have candidates and a governed action. What happens when the process coordinating the action disappears? Return to workflow progress, saved intent and business receipt on the board. Protect this section when earlier calls run slowly.

## 15. Checkpoint progress; transact the business write

**On slide:**

PHASE 5 · LANGGRAPH IN FASTAPI
Search
Rank alternatives · Save shortlist
→
Availability
Check durations · Prepare hold intent
→
Hold
Gateway + Cedar · Atomic Aurora write
AuroraDataApiSaver stores checkpoints between nodes over HTTPS Data API.
Execution lease → one active worker for a thread → saved intent reused on resume

**Speaker notes:**

CORE 20:20-23:00. Show search, availability, prepare_hold, hold and synthesis. The LangGraph workflow runs in FastAPI, not inside AgentCore Runtime. The default durable saver is AuroraDataApiSaver over HTTPS Data API. CODE STOP 2: backend/agents/orchestration_05/workflow.py::_node_prepare_hold then _node_hold. Stable request/booking identity is checkpointed before the write. A replacement execution reuses the intent. Worker leases and fencing control who may proceed; the transaction controls whether the business action can commit. Checkpoint and Gateway write are separate transactions. Do not claim exactly-once execution.

## 16. Pause. Reload. Resume the same journey.

**On slide:**

LIVE DEMO · RECORDS, NOT A SIMULATED RESTART
1  Start recovery · 2  Reload the saved URL · 3  Resume the hold · 4  Open evidence
A normal pause/resume is not proof that a worker was killed.

**Speaker notes:**

CORE 23:00-27:00. Run: My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options. Start recovery and show its saved shortlist, durable backend and paused workflow. Preserve the journey URL. Reload and read the same journey. This proves saved-state readback, not worker death. Explain that Resume and request hold requests the disclosed 15-minute courtesy hold before selecting it. Inspect journey, booking and expiry. A production purchase needs approval bound to exact terms and a verified human. Keep these records for the failure discussion.

## 17. Three failure windows, three recovery rules

**On slide:**

CORE FAULT · COMMIT SUCCEEDS, REPLY IS LOST
Before commit
No visible business write · Rollback incomplete work · Retry from saved state
Commit, reply lost
Booking may already exist · Replay the same request ID · Return the original receipt
After checkpoint
Read the saved thread · Wait for the old lease · Continue on a new worker

**Speaker notes:**

CORE 27:00-29:00. Before commit, incomplete transactional work can roll back. A committed write with a lost reply leaves the caller uncertain. After a checkpoint, a replacement worker can continue saved progress. Ask: If the caller times out, do we know the hold failed? No: inspect or replay the same intent. CORE FAULT: use scripts/lost_response_demo.py from meridian/ in a prepared terminal, or explicitly labelled dated evidence. Its separate test journey commits a real hold, deliberately drops the acknowledgement at the worker boundary, then resumes on a replacement worker. Do not identify it as the open browser journey or as a real network outage. Rehearse the full helper including cleanup within this slot; otherwise use recorded evidence/source and say what was not run. Keep scripts/kill_and_resume_demo.py for optional discussion.

## 18. The transaction owns replay protection

**On slide:**

ONE HOLD PER REQUEST ID · NOT UNIVERSAL EXACTLY ONCE
Stable intent
Request ID · Canonical fingerprint
→
Aurora lock
Serialize request · Validate same terms
→
Receipt
Same booking ID · Same original expiry
A reused request ID with different terms is an error.
Inventory locking and lease fencing protect different races.

**Speaker notes:**

CORE 29:00-32:00. CODE STOP 3: scripts/migrations/008_hold_request_identity.sql::create_courtesy_hold. Journey plus request ID identifies the intent; a canonical fingerprint binds it to the terms. Same identity and terms returns the original booking without extending its expiry. Changed terms with the same identity are an error. The inventory advisory lock serializes capacity checks among cooperating writers. Request uniqueness, inventory locking and execution fencing protect different races. The checkpoint and hold are separate transactions. This is a replay-safe catalog hold within this boundary; external payments or suppliers need their own idempotency and reconciliation.

## 19. Follow each claim to an independent record

**On slide:**

IMPLEMENTED IN MERIDIAN
Workflow
Thread + checkpoint · Attempt + worker ID · Saved resume receipt
Authority
Workload binding audit · Cedar allow or deny · Pinned arguments
Business result
hold_requests + bookings · One request, one record · Original expiry retained

**Speaker notes:**

CORE 32:00-35:00. Open System evidence: workflow thread/checkpoint/attempt/worker, authority binding/pinned arguments/policy decision, then business request/booking/original expiry. An allowed tool call does not prove a committed booking; a checkpoint does not prove that the side effect completed. Compare records. For the fault demonstration, compare before/after request ID, booking ID and expiry plus one booking for that request. Its test journey is separate from the UI journey and its helper cleans its rows. Identify recorded evidence by date. Use current observations, not historical IDs from an earlier cleaned run. A browser resume may use the same worker.

## 20. The mechanism has deliberate limits

**On slide:**

IMPLEMENTATION BOUNDARIES + PRODUCTION DECISIONS
Single presenter
Shared demo principal · No multi-user login · Trusted Gateway callers
Operational cost
Aurora capacity + backup · Model calls + memory · Logs and hosted services
Workload limits
Bound execution + retries · Protect checkout capacity · Test business recovery

**Speaker notes:**

CORE 35:00-37:00. This sample uses a shared presenter principal bound to Alex; production needs an end-user identity and approval design. Trusted Gateway callers remain a boundary. Discuss application deadlines, bounded concurrency/retries and workload interference. Read-only queries can still consume resources; RLS does not reserve CPU. Distinguish retrieval cost per query from cost per successful answer, including model calls, retries and a quality target. Worker recovery does not establish regional recovery. Historical infrastructure/deployment findings remain in dated readiness reports; verify them before presenting as current. Do not treat local source or tests as hosted, fresh-account or room proof.

## 21. Keep these three decisions explicit

**On slide:**

IMPLEMENTED IN MERIDIAN
Context
What may the agent recall? · Scope it to the traveler.
Authority
What may the tool do? · Decide before execution.
Durability
What survives the worker? · Persist intent and receipts.

**Speaker notes:**

CORE 37:00-39:00. Return to the opening sketch. Context: what may the agent recall, and for whom? Authority: what may the tool do, and who establishes trusted inputs? Durability: which intent and receipt survive interruption? Ask the audience to place those boundaries in an application they own. Teams retain business meaning, approval requirements and recovery semantics. A useful AWS discussion asks which integration, diagnostic or outcome trace would most reduce their work. Acknowledge existing managed capabilities; make no roadmap commitments. Keep connection routing, regional guarantees, upgrades and analytics as audience-led branches.

## 22. Continue from the source

**On slide:**

SOURCE + RUNBOOK + AUTHORITATIVE SERVICE DOCUMENTATION
github.com/aws-samples/ · sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore
Presenter path
meridian/DEMO_SCRIPT.md · meridian/docs/PRESENTER_RUNBOOK_2026-09-20.md
AWS documentation: AgentCore Policy · Aurora RDS Data API

**Speaker notes:**

CORE 39:00-39:40. The source contains the implementation, demo sequence, fault exercises and presenter runbook. Point to the three inspected decisions: pinned arguments, saved intent and transaction replay. Use meridian/docs/PRESENTER_RUNBOOK_2026-09-20.md and the full notes in docs/presentation/SLIDE_NOTES.md. Validate configuration and failure behavior in the target environment. Source: https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore . Service docs: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html and https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html .

## 23. Thank you

**On slide:**

Aditya Samant
ausamant@amazon.com
       adisamant
Shayon Sanyal
shayons@amazon.com
       shayonsanyal

**Speaker notes:**

CORE 39:40-40:00. The agent can reason about a request. Our engineering makes its authority, progress and outcome inspectable. Invite a concrete failure, what the team still owns, and what they would ask AWS to simplify. Start the 20-minute discussion/flex window. The next two slides are optional references. Timing and physical back-row readability still need a human rehearsal.

## 24. Presenter preflight and recovery

**On slide:**

OPTIONAL · OPERATIONS
Before the room
Correct account + region · Durable health + four tools · Warm one live turn
If a request stalls
Wait up to 55 seconds · Read the saved receipt · Do not invent success
Before leaving
Release only owned tests · Preserve unrelated data · Check retained resources

**Speaker notes:**

OPTIONAL - OPERATOR REFERENCE. Use docs/PRESENTER_RUNBOOK_2026-09-20.md. Verify intended environment, durable saver, expected tools, policy enforcement and one warmed request. Check projector readability from the back of the room; show only the current function or evidence panel. Respect the waiting deadline, then read the saved outcome. Stop waiting ends the browser wait, not necessarily the server action. Use labelled dated evidence/source if services are unavailable and identify unexecuted steps. Never run a full seed or initialization as preflight. Release only owned demonstration records. The architecture starts open and can collapse; other briefing sections start closed.

## 25. Temporal policy is a discussion branch

**On slide:**

OPTIONAL · AVAILABLE CAPABILITY, ASSESSED INTEGRATION
Implemented
Cedar per tool invocation · Aurora ownership + expiry · Stable request replay
Available extension
Dogwood temporal policy · Session-aware conditions · Not enabled in Meridian
Verify separately
Service capability · Integration design · Deployed behavior

**Speaker notes:**

OPTIONAL - TECHNICAL DISCUSSION. Meridian deploys Cedar per-call policies. A history-dependent requirement could ask for a successful lookup of the same package within five minutes in the same authenticated policy session. AgentCore Policy currently documents Dogwood temporal conditions; this application has assessed that extension but does not enable it. Temporal policy requires explicit session/history semantics and does not establish fresh inventory or approval of exact terms. Distinguish available service capability, assessed integration and enabled behavior. No online model-judge evaluator is deployed here. Current service reference: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html . Integration assessment: meridian/docs/DOGWOOD_POLICY_ASSESSMENT.md.
