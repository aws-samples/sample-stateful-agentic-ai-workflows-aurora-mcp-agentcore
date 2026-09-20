# Meridian slide text and speaker notes

[Editable PowerPoint](Meridian-reInvent-chalk-talk.pptx) · [PDF](Meridian-reInvent-chalk-talk.pdf) · [Presenter runbook](../PRESENTER_RUNBOOK_2026-09-19.md)

All delivery timings are estimates. Explicit measured API timings are dated observations, not a timed human rehearsal. The runbook contains the final measured timings.

## 1. Title

Build stateful agentic workflows with Aurora, MCP, and AgentCore

re:Invent · L300

Aditya Samant

Principal DB Specialist Solutions Architect

Shayon Sanyal

Principal DB Specialist Solutions Architect

**Speaker notes:** CORE: 0:00–0:30. Updated from the user-approved DAT301-R Toronto deck. 60-minute slot: 40-minute core plus 20 minutes discussion/flex. No re:Invent session code has been assigned in this source. Demo inventory is fictional. Central takeaway: persist context, workflow progress and business records at distinct boundaries.

## 2. Alex needs a plan that can survive an interruption

Alex needs a plan that can survive an interruption

FICTIONAL TRAVEL SCENARIO · LIVE AWS IMPLEMENTATION

A canceled JFK-to-Tokyo trip.Saved preferences.A limited package hold.

What must survive when the worker disappears?

**Speaker notes:** CORE: 0:30–2:00. Describe Alex as a fictional traveler; this is not a production customer scale story. The original 500K trips/day and 10,000x claims were illustrative and have been removed. Point to the returned traveler brief. Package reservations affect Meridian Aurora rows only: no flight seat, supplier contact or payment. Screenshot is dated evidence of the local build, not a live slide or hosted parity claim.

## 3. Three questions organize the talk

Three questions organize the talk

IMPLEMENTED IN MERIDIAN

Who may act?

Browser accessWorkload-to-traveler grantCedar before a tool runs

What must persist?

Traveler factsConversation contextCheckpoint + hold intent

How do we prove it?

A new workerThe same threadOne persisted booking

**Speaker notes:** CORE: 2:00–4:00. Whiteboard three stores and three boundaries. Timing checkpoints: leave SQL/MCP/retrieval at minute 12; begin recovery at 20; evidence at 32; takeaways at 37. Reserve 20 minutes for audience discussion and service latency. Estimates are a delivery plan, not a timed rehearsal.

## 4. SQL → MCP → retrieval

SQL → MCP → retrieval

MERIDIAN CAPABILITY LADDER

Add capabilities when the question requires them.

**Speaker notes:** CORE: 4:00. These are configured teaching paths, not universal limits of SQL, MCP or Strands.

## 5. Start with grounded catalog rows

Start with grounded catalog rows

PHASE 1 · DIRECT RDS DATA API

Prompt

City trips under $2,000

→

FastAPI

Validate and parameterize

→

Aurora

Filter catalog rows

Evidence: executed SQL + returned prices

Then ask for a comparison in euros. The configured phase names its boundary.

**Speaker notes:** CORE: 4:30–6:00. Capability ladder → SQL → City trips under $2,000. Open SQL trace and point to bound parameters. This fast demo route uses deterministic parameterized SQL in backend/routers/chat.py; the separate Strands SQLAgent is reference code. SQL is capable of more than this configured exercise. Stretch: Compare trips in euros; expect the switch-to-MCP message and no invented conversions. SQL response measured 53 ms in the browser validation; timing is environment-specific.

## 6. Tools make capabilities explicit

Tools make capabilities explicit

PHASE 2 · MCP CONTRACTS

Client

List toolsCall named tool

→

meridian-concierge

compare_packagescurrency_convert

→

Aurora + rates

Catalog recordsIllustrative FX data

One server for this comparison prompt. PostgreSQL MCP is a separate catalog path.

**Speaker notes:** CORE: 6:00–8:00. MCP → Compare trips in euros. The comparison/currency prompt invokes meridian-concierge, not two servers. postgres-mcp-server 1.0.9 supports the separate SQL path. Currency conversion uses the configured demonstration rates; do not call it a live market quote. loyalty_balance now uses the workload grant and RLS scope. Evidence: named tool inputs/results. Live HTTP comparison measured 2.756 seconds in this review.

## 7. Search by meaning, then verify

Search by meaning, then verify

MERIDIAN CAPABILITY LADDER

Retrieval finds candidates. Inventory decides availability.

**Speaker notes:** CORE: approximately 8:00. Limit retrieval detail to the visible rank change and live duration check.

## 8. Words and meaning produce candidates

Words and meaning produce candidates

PHASE 3 · HYBRID RETRIEVAL + RERANK

Retrieve

Full-text searchpgvector similarity

→

Fuse

Merge + deduplicateBound candidates

→

Rerank

Bedrock relevanceReturn a shortlist

“Find a quiet, romantic wine-country retreat with a private villa.”

If reranking is unavailable, label the hybrid-order fallback.

**Speaker notes:** CORE: 8:00–10:00. Retrieval → Romantic wine-country villa. Show scores and observed order; animation is a replay of returned data, not a measured execution timeline. Do not claim a fixed precision improvement or universal top-K count. Live review: 21.659 seconds, valid rerank metadata. Safe fallback returns hybrid ordering with a visible disclosure; it must not claim reranking ran. Inspect backend/routers/chat.py and backend/search_utils.py.

## 9. Finding a trip does not authorize a reservation

Finding a trip does not authorize a reservation

IMPLEMENTED IN MERIDIAN

Search

Interpret the requestRetrieve grounded candidates

Details

Read package attributesCheck published durations

Availability

Read inventoryNo booking write in Retrieval

**Speaker notes:** CORE: 10:00–12:00. The Retrieval supervisor dispatches read-only specialists. The legacy BookingAgent name is a read-only availability role; it is not permission to book. The revised diagram removes the old process_booking_tool write claim. Ask for saved preferences: this configured phase must say to switch to Production. Booking writes require the managed Runtime/Gateway path.

## 10. Remember the traveler. Govern the action.

Remember the traveler. Govern the action.

MERIDIAN CAPABILITY LADDER

Context is useful. Authority must be explicit.

**Speaker notes:** CORE: 12:00. Transition from finding a trip to who can act on it.

## 11. Three kinds of state have different owners

Three kinds of state have different owners

IMPLEMENTED IN MERIDIAN

Traveler facts

Aurora preferencesSaved budgetRead under RLS

Conversation

AgentCore MemoryRuntime session contextAurora turn mirror

Execution

Aurora checkpointsWorker leasesStable hold intent

**Speaker notes:** CORE: 12:30–15:00. Phase 4 owns its AgentCore Memory session; backend persist_turn mirrors permitted turns to Aurora. Conversation recall is not a LangGraph checkpoint. The single-presenter sample binds its shared browser principal to Alex; it is not a multi-user sign-in product. Inspect backend/memory/store.py and runtime main.py. Recall JFK, shellfish exclusion and budget; turn memory off and verify no profile recall.

## 12. Put authority outside the model

Put authority outside the model

PHASE 4 · TRUST BOUNDARIES

Browser → API

Authenticate callerBind Alex

→

Runtime hooks

Pin travelerConfirmation + budget

→

Gateway + Cedar

Evaluate permissionThen invoke Lambda

Aurora transaction

Authorize the Lambda workload; apply RLS; validate ownership, price and inventory.

**Speaker notes:** CORE: 15:00–17:00. HTTP bearer/basic viewer access, AWS workload identity, Cedar and RLS are separate boundaries. The demo uses IAM workload grants; AgentCore Identity is not enabled as an end-user identity service. Runtime hooks overwrite model-provided identity, confirmation and budget fields. Gateway IAM callers remain a trusted boundary: Cedar cannot know whether an arbitrary privileged caller lied about application-supplied confirmation. See backend/http_auth.py, turn_trace.py and gateway Lambda _authorize.

## 13. An applicable permit is required

An applicable permit is required

IMPLEMENTED IN MERIDIAN

Read

Search packagesRead package detailsAuthenticated tools

Hold

Traveler confirmed≤6 travelers · ≤12 hoursWithin saved budget

Confirm

Traveler confirmedWithin saved budgetAurora checks owner + expiry

**Speaker notes:** CORE: 17:00–20:00. Policy mode must be ENFORCE, not LOG_ONLY. Show one unconfirmed or over-budget refusal, then use the recovery hold for the positive permit. Workflow holds last 15 minutes; direct Concierge holds last 12 hours. Confirmation books catalog inventory only. Never reduce the party size or budget merely to retry a denial secretly. Source: agentcore/agentcore.json, migrations 008 and 010. Official policy permissions: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html

## 14. The worker can disappear

The worker can disappear

MERIDIAN CAPABILITY LADDER

The journey should not.

**Speaker notes:** CORE: 20:00. The recovery and transaction boundary is the central payoff; cut optional branches before compressing this section.

## 15. Checkpoint progress; transact the business write

Checkpoint progress; transact the business write

PHASE 5 · LANGGRAPH IN FASTAPI

Search

Rank alternativesSave shortlist

→

Availability

Check durationsPrepare hold intent

→

Hold

Gateway + CedarAtomic Aurora write

AuroraDataApiSaver stores checkpoints between nodes over HTTPS Data API.

Execution lease → one active worker for a thread → saved intent reused on resume

**Speaker notes:** CORE: 20:00–23:00. Workflow runs in the FastAPI worker, not inside AgentCore Runtime. Default demo path is AuroraDataApiSaver over RDS Data API, not PostgresSaver over a live TCP session. Checkpoints use checkpoints, checkpoint_blobs and checkpoint_writes. Lease heartbeat fences stale workers; the Lambda checks an executionId before a hold. Walk source execution.py → workflow.py → hold_intent.py. Do not claim arbitrary long-lived checkpoints imply all external effects are exactly once.

## 16. Pause. Reload. Resume the same journey.

Pause. Reload. Resume the same journey.

LIVE DEMO · RECORDS, NOT A SIMULATED RESTART

1  Start recovery2  Reload the saved URL3  Resume the hold4  Open evidence

A normal pause/resume is not proof that a worker was killed.

**Speaker notes:** CORE: 23:00–27:00. Exact input: My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options. Start recovery; read the durable checkpoint; reload; resume. API measured pause 7.505 seconds and resume 17.773 seconds in this review. Preserve the thread URL. Browser run jrn_cddc6e6b1e7f resumed on the same worker; its screenshot does not prove replacement. Required browser verification: one hold receipt, original expiry and saved checkpoint.

## 17. Three failure windows, three recovery rules

Three failure windows, three recovery rules

IMPLEMENTED IN MERIDIAN

Before commit

No visible business writeRollback incomplete workRetry from saved state

Commit, reply lost

Booking may already existReplay the same request IDReturn the original receipt

After checkpoint

Read the saved threadWait for the old leaseContinue on a new worker

**Speaker notes:** CORE: 27:00–29:00. A timeout is ambiguous about whether a write committed. Do not send a fresh business request blindly. Browser Stop waiting stops the wait, not the server action. scripts/lost_response_demo.py deliberately drops a real committed reply; scripts/kill_and_resume_demo.py SIGKILLs its own worker. Both clean only their unique records. Distinguish these fault injections from the ordinary UI pause.

## 18. The transaction owns replay protection

The transaction owns replay protection

ONE HOLD PER REQUEST ID · NOT UNIVERSAL EXACTLY ONCE

Stable intent

Request IDCanonical fingerprint

→

Aurora lock

Serialize requestValidate same terms

→

Receipt

Same booking IDSame original expiry

A reused request ID with different terms is an error.

Inventory locking and lease fencing protect different races.

**Speaker notes:** CORE: 29:00–32:00. Inspect migration 008 create_courtesy_hold and hold_requests unique identity. Terms are normalized and fingerprinted; a mismatched replay fails. The inventory row lock prevents overselling and the execution lease prevents a stale worker writing. Checkpoints and the hold are separate transactions; the stable intent bridges the failure window. No payment or supplier side effect is included in this guarantee. Current live suite exercises concurrency, rollback, capacity and replay.

## 19. Follow each claim to an independent record

Follow each claim to an independent record

IMPLEMENTED IN MERIDIAN

Workflow

Thread + checkpointAttempt + worker IDSaved resume receipt

Authority

Workload binding auditCedar allow or denyPinned arguments

Business result

hold_requests + bookingsOne request, one recordOriginal expiry retained

**Speaker notes:** CORE: 32:00–35:00. System evidence has Checkpoint, Authorization and Business result tabs. Source records drive the claims; absence must say unavailable. Current separate fault proof: jrn_65911cce9cac, thread demo-d8dbd1a5b5, first worker worker-fd9a1f4e SIGKILL exit -9, new worker resumed, hold_c863973e50c2 retained exactly once and original expiry. Those rows were cleaned; the log is dated execution evidence, not a currently live saved journey. Browser handoff separately confirmed hold_276e428b4950, $3,898. Screenshots and logs are dated local-build proof, not hosted parity.

## 20. The mechanism has deliberate limits

The mechanism has deliberate limits

IMPLEMENTED IN MERIDIAN

Single presenter

Shared demo principalNo multi-user loginTrusted Gateway callers

Operational cost

Aurora capacity + backupModel calls + memoryLogs and hosted services

Release proof

Local source validatedRuntime source parity checkedHosted web still older

**Speaker notes:** CORE: 35:00–37:00. Static IAM inspection does not prove new deployment permissions. Existing Aurora was observed unencrypted with deletion protection off; this review did not migrate it. New provisioning source now requests encryption, but a fresh infrastructure deployment and end-to-end teardown were not authorized. Hosted CloudFront/App Runner remain older than this source. Cfn-lint reports nonmaterial generated dependency warnings separately. Do not state that clean local tests prove fresh environment or room readiness.

## 21. Keep these three decisions explicit

Keep these three decisions explicit

IMPLEMENTED IN MERIDIAN

Context

What may the agent recall?Scope it to the traveler.

Authority

What may the tool do?Decide before execution.

Durability

What survives the worker?Persist intent and receipts.

**Speaker notes:** CORE: 37:00–39:00. Ask the room to place a checkpoint and a business transaction in their own architecture. Repeat: state belongs to the journey; decisions stay inspectable. The next resource slide is brief, then thank the room and use the remaining 20 minutes for Q&A. Optional direct hold/confirmation and the second fault are cuttable, not required inside the 40-minute core.

## 22. Continue from the source

Continue from the source

SOURCE + RUNBOOK + AUTHORITATIVE SERVICE DOCUMENTATION

github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore

Presenter path

meridian/DEMO_SCRIPT.mdmeridian/docs/PRESENTER_RUNBOOK_2026-09-19.md

AWS documentation: AgentCore Policy · Aurora RDS Data API

**Speaker notes:** CORE: 39:00–40:00. Source: https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore . Official docs: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html and https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html . The expired June hackathon and unrelated workshop QR were removed. Refer to the tracked readiness report for tested identities and unresolved gates. No Workshop Studio publication applies to this chalk talk.

## 23. Thank you

Thank you

Aditya Samant

ausamant@amazon.com

       adisamant

Shayon Sanyal

shayons@amazon.com

       shayonsanyal

**Speaker notes:** CORE closes at minute 40. Ask: where will you put the authorization check, checkpoint and business transaction? The following two slides are optional discussion material. Automated tests do not establish timed human delivery or physical room readiness.

## 24. Presenter preflight and recovery

Presenter preflight and recovery

OPTIONAL · OPERATIONS

Before the room

Correct account + regionDurable health + four toolsWarm one live turn

If a request stalls

Wait up to 55 secondsRead the saved receiptDo not invent success

Before leaving

Release only owned testsPreserve unrelated dataCheck retained resources

**Speaker notes:** OPTIONAL Q&A. Exact commands and reset instructions are in docs/PRESENTER_RUNBOOK_2026-09-19.md. Never run initialization or a full seed against the existing demo database. A targeted release_demo_bookings.py --booking-id is available after dry-run. Fallback: label a dated capture or source walkthrough explicitly. Human checks still required: timed 40-minute delivery, 20-minute discussion reserve, projector contrast/zoom, back-row readability, keyboard/fullscreen, audio if used and network fallback.

## 25. Temporal policy is a discussion branch

Temporal policy is a discussion branch

OPTIONAL · DESIGN EXPLORATION

Implemented

Cedar per tool invocationAurora ownership + expiryStable request replay

Explore

History-dependent policyRules across multiple callsAdditional state ownership

Do not conflate

A design possibilityAn evaluated serviceAn enabled capability

**Speaker notes:** OPTIONAL Q&A. Dogwood is discussed in the source briefing as an assessment, not enabled in this application. Do not present a roadmap possibility as shipped. Keep dynamic service feature claims grounded in current official documentation at delivery time. See https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html . This source does not deploy temporal policy or online model-judge evaluators.
