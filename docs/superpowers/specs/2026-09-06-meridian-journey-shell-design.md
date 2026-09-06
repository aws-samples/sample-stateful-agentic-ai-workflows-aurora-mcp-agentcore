# Meridian journey shell and durable recovery

Date: 2026-09-06
Status: design, approved for planning
Branch: `meridian-audit-round-2`

## Goal

Restructure the Meridian showcase around a server-owned journey, so one live
run can be viewed four ways without losing continuity, and make the workflow
genuinely survive worker replacement. The chalk talk's centerpiece is the
resilient workflow. The Data API checkpointer is the supporting implementation
that makes the claim true on the demo machine.

Four surfaces, named descriptively in the interface. A/B/C/D are design-review
labels only.

| Surface | Responsibility |
| --- | --- |
| Concierge | Traveler-facing. Request, preferences, recommendations, next action. No infrastructure terminology outside presenter controls. |
| Capability ladder | The existing five-phase progression, preserved as the guided technical walkthrough with its working actions and evidence. |
| Recovery desk | Centerpiece. Selected plan, the interruption, the saved progress, the next action to resume. One dominant decision, one primary action. |
| Presenter proof | Backend-sourced evidence at projector scale: authenticated identity, authorized scope, retrieval results, checkpoint and thread identifiers, worker execution, resulting business record. |

## Constraints that shaped the design

Verified against the account and the code on 2026-09-06.

- `meridian-demo-instance` is `PubliclyAccessible: false`. The cluster has
  `HttpEndpointEnabled: true`. From a laptop, only the RDS Data API reaches
  Aurora.
- `AsyncPostgresSaver` requires psycopg over TCP 5432, so it cannot run from
  the laptop without the tunnel in `scripts/start_checkpoint_tunnel.sh`.
- Consequently `_resolve_checkpoint_dsn()` returns `None` and the process falls
  back to `MemorySaver (in-process)`. Every durability claim on the current UI
  is false on the demo machine unless this changes.
- The UI has no evidence contract. `ChatResponse` carries only
  `workflow_status` and `workflow_resumed_after_restart`
  (`meridian/backend/routers/chat.py:161`). Everything else is regex-scraped
  from trace prose in `deriveAuroraEvidence`
  (`meridian/frontend/src/showcase/lib/showcaseProof.ts:110`), including
  `thread_id` at line 294.
- Nothing can rehydrate. Eight endpoints, all turn-scoped POSTs plus two
  catalog GETs. A refresh loses the journey.
- `setSelectedPhase` deliberately clears the run on every phase change
  (`meridian/frontend/src/showcase/hooks/useMeridianShowcase.ts:585-611`).
  Correct for the ladder, incompatible with view switching.

## Decisions

1. **Server-owned journey with a thin persistent identity and an aggregated
   read API.** The journey document is assembled from existing persisted
   state. Checkpoints, bookings, audit rows, and conversation records keep
   authority over their own facts. No copying into a second document, no
   separate evidence ledger.
2. **Hybrid checkpoint deployment.** Aurora Data API checkpointing for the
   current demo. `AsyncPostgresSaver` retained as the native option where
   direct database connectivity exists. Both behind the same workflow-facing
   interface.
3. **Journey-scoped business-request identity.** A persisted
   `hold_request_id` allocated once per intended hold, enforced by a database
   constraint inside the existing inventory transaction.
4. **Durable vertical slice before the new UI.** Kill and resume must work
   before Recovery desk and Presenter proof are built on top of it.

## 1. Identity model

Two new tables. Everything else is read where it already lives.

```sql
CREATE TABLE journeys (
    journey_id         VARCHAR(64) PRIMARY KEY,
    traveler_id        VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    checkpoint_backend VARCHAR(32) NOT NULL,
    status             VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE journey_executions (
    execution_id VARCHAR(64) PRIMARY KEY,
    journey_id   VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    thread_id    VARCHAR(200) NOT NULL,
    phase        SMALLINT,
    worker_id    VARCHAR(128) NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at     TIMESTAMPTZ
);
CREATE INDEX ON journey_executions (journey_id, started_at DESC);
```

`journey_id` is stable across worker replacement. A journey contains many
executions; each execution records the worker that ran it, which is what lets
Presenter proof show a changed worker identity as a fact rather than a caption.

**Why not reuse `conversations`.** It is the Phase-4 memory record: it carries
`summary` and owns the embedded `conversation_messages` child table, and its
primary key is already bound one-to-one to the LangGraph thread
(`meridian/backend/agents/orchestration_05/workflow.py:1236` sets
`thread_id = conversation_id`). A journey must outlive a thread and span
several. Overloading that row would break both jobs.

`checkpoint_backend` is written when the journey is created and is never
silently changed. Resume against a different active backend is refused with a
clear error. Cross-backend resume is out of scope until it is implemented and
tested deliberately.

## 2. Checkpoint backends

The seam already exists. `CheckpointBackend(saver, kind, durable, pool, error)`
at `workflow.py:157` is the workflow-facing interface, and
`initialize_checkpoint_backend()` is the single place backends are chosen.

Add `AuroraDataApiSaver`, a `BaseCheckpointSaver` implemented over
`get_rds_data_client()`. Selection order:

1. Explicit `LANGGRAPH_CHECKPOINT_DSN` or resolvable credentials, and the
   psycopg extras installed, gives `AsyncPostgresSaver` (`durable=True`).
2. Otherwise, a usable Data API client gives `AuroraDataApiSaver`
   (`durable=True`).
3. Otherwise `MemorySaver` (`durable=False`), unless
   `LANGGRAPH_CHECKPOINT_REQUIRED` is set, which raises instead.

The saver mirrors LangGraph's own table layout, `checkpoints`,
`checkpoint_blobs`, and `checkpoint_writes`, so the two backends do not invent
separate storage. This keeps a future cross-backend resume cheap without
claiming it works today.

Implementation notes that the tests must pin:

- Serialize through the same `JsonPlusSerializer` the Postgres saver uses.
  Pass serialized values as Data API `blobValue` parameters; they return
  base64-encoded and must round-trip byte-identically.
- `aput_writes` carries pending writes with their `task_id` and `task_path`.
  This is where checkpointer implementations usually break, and it is what
  makes interrupt-and-resume correct rather than approximately correct.
- The Data API caps a result set at 1 MiB. `aget_tuple` and `alist` must fail
  loudly with an actionable message when a checkpoint exceeds it, not truncate.
  A test asserts the ceiling rather than assuming this workflow stays small.
- Data API calls are retried on throttling and transient faults. Retries must
  be safe: writes are keyed on
  `(thread_id, checkpoint_ns, checkpoint_id)` and upsert.

**Bug to fix as part of this work.** `_uses_postgres_saver` (`workflow.py:533`)
tests `checkpointer_kind.startswith("PostgresSaver")`. A Data API saver would
fail that string test and silently render the MemorySaver trace, reintroducing
the mislabelling fixed in commit `e78d68f`. It becomes a check on the `durable`
capability flag, and the trace reports the actual backend name.

## 3. Business-action identity

The invariant: **one business effect per intended hold request, across
retries, executions, and workers.**

A `hold_request_id` is allocated and persisted once, when the intended hold is
established and before the business action is scheduled. Every execution and
retry for that intent reuses it.

```sql
CREATE TABLE hold_requests (
    journey_id       VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    hold_request_id  VARCHAR(64) NOT NULL,
    booking_id       VARCHAR(50) NOT NULL,
    fingerprint      TEXT NOT NULL,
    thread_id        VARCHAR(200) NOT NULL,
    execution_id     VARCHAR(64),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (journey_id, hold_request_id),
    CONSTRAINT hold_requests_booking_fk
        FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
        DEFERRABLE INITIALLY DEFERRED
);
```

`thread_id` and `execution_id` are provenance only. `fingerprint` is a
normalized digest of the material terms: package, duration, quantity, unit
price, and total.

The foreign key is deferred because the identity row is written before the
booking it names. `create_courtesy_hold` already receives `p_booking_id` from
the caller, so the id is known at the top of the transaction, but the
`bookings` row does not exist until after the inventory check passes. Deferring
to commit keeps both facts in one transaction without ordering them wrongly.

The identity record and the hold are created **inside** the existing
inventory-protection transaction. `create_courtesy_hold`
(`scripts/migrations/006_add_courtesy_hold_function.sql`) gains
`p_journey_id`, `p_hold_request_id`, and `p_fingerprint`. Its body runs:

1. `INSERT INTO hold_requests ... ON CONFLICT (journey_id, hold_request_id)
   DO NOTHING`.
2. If a row was inserted, take the advisory lock, run the existing capacity
   check, insert `bookings` and `booking_lines`, and return the new hold.
3. If the insert conflicted, select the existing row. On a fingerprint
   mismatch, raise `hold_request_parameter_mismatch`. Otherwise return the
   existing booking with its current status and take **no** inventory action.

An application-level check-then-insert is insufficient and is not used.
Concurrent identical requests serialize on the primary key: one inserts, and
the other blocks on the uncommitted key until the first transaction commits,
then observes the committed row and takes the replay path.

A failed attempt rolls the identity row back with the rest of the transaction.
If the capacity check raises `insufficient_inventory`, the `hold_request_id` is
not consumed and a later retry is a fresh attempt rather than a permanent
replay of a failure.

Behaviour:

- **Duplicate request, same fingerprint.** Returns the existing hold and its
  current status. Does not reserve inventory again, does not extend expiry,
  does not reactivate a cancelled or expired hold.
- **Same request id, different fingerprint.** Raises a conflict.
- **Deliberate replacement.** A new `hold_request_id` is allocated and
  persisted. The old hold is unaffected by the identity mechanism.

The function's return signature extends to carry `booking_id`, `status`, and
whether the call was a replay, so callers can render the outcome truthfully
instead of assuming a fresh reservation.

**Constraint worth stating explicitly.** A rule about simultaneous active
holds cannot be expressed as a partial unique index predicated on
`hold_expires_at > now()`, because PostgreSQL requires index predicates to be
immutable and `now()` is stable. If that rule is wanted, it is enforced on an
actively maintained status column, with expiry moved by a transition rather
than inferred from a timestamp comparison at read time.

**Migration.** Existing `booking_id` values are preserved. Legacy holds are
linked to journeys and request identities only where the mapping is reliable;
where it is not, the row is left unlinked rather than guessed.

## 4. Read API and evidence

`GET /journeys/{journey_id}` assembles a typed document from the sources that
already hold the facts:

| Section | Source |
| --- | --- |
| Journey, owner, backend | `journeys` |
| Executions, workers | `journey_executions` |
| Checkpoint and thread | active checkpoint backend, `checkpoints` |
| Business record | `bookings`, `booking_lines`, `hold_requests` |
| Authorization | `traveler_access_audit`, AgentCore identity context |
| Session totals | the existing `/diagnostics/session-receipt` logic |

Every claim carries `source`, `id`, and `observed_at`. Evidence that is not
available is represented explicitly as
`{"status": "unavailable", "reason": "..."}`. It is never omitted and never
inferred from prose. Success, authorization, persistence, and no-duplicate-hold
claims render only when the corresponding record supports them.

Authorization runs on every read and every action, reusing
`require_http_principal` and `authorize_traveler`, plus the persisted-owner
check that `_authorize_thread` already performs (`workflow.py:1210`). Knowing a
journey id or a thread id grants nothing.

Reads and view changes have no workflow side effects. Resume is an explicit
command, `POST /journeys/{journey_id}/resume`, and it does not start a second
run or repeat a business action.

## 5. Frontend shell

A `MeridianJourneyProvider` owns journey identity and the assembled document,
caching it and coordinating the four views. `useMeridianShowcase` keeps owning
ladder and turn state, including its clear-on-phase-change behaviour, which now
applies only inside the Capability ladder.

`view` and `journey_id` live in the URL, so refresh and reconnect reload
backend state rather than restarting. Capability demonstrations keep their own
operation ids within the session; not every SQL or retrieval demo is forced
into the workflow.

`deriveAuroraEvidence` stops regex-scraping trace spans and becomes a renderer
over the typed document. That is the change that makes the proof surfaces
honest by construction rather than by vigilance, and it retires the class of
bug fixed in `98bbac2`.

Presenter proof shows the actual graph host, the actual checkpoint backend, and
persisted evidence. A local graph writing Aurora checkpoints is labelled as
exactly that.

The design prototypes are visual references. Their simulated state transitions
are replaced by real integrations.

## 6. Sequencing

The durable slice lands before the new UI. The existing five-phase path stays
runnable throughout.

1. `AuroraDataApiSaver` behind the existing `CheckpointBackend` interface,
   with focused integration tests.
2. `journeys` and `journey_executions`, the read API, and authorization.
3. Journey-scoped hold request identity, inside the inventory transaction.
4. The five-step vertical slice, verified end to end.
5. Shell and view axis. Capability ladder untouched.
6. Recovery desk, then Presenter proof.
7. Concierge visual pass, after continuity works.

## 7. Verification

The vertical slice, run against real Aurora:

1. Save a checkpoint and confirm persistence.
2. Terminate the actual graph worker.
3. Resume the authorized thread from a fresh worker.
4. Verify restored state and changed worker identity.
5. Interrupt after a hold commits but before its following checkpoint, retry,
   and verify exactly one hold exists.

Focused tests covering serialization, pending writes, retries, and Data API
response limits. Business-identity tests covering concurrent execution of the
same request, retry after commit but before checkpoint, parameter mismatch,
and a deliberate new request after expiry.

Shell tests covering: switching views preserves the active run; interruption
and resume retain the correct checkpoint; another traveler cannot resume the
thread; repeated resume does not duplicate the business action.

## 8. Out of scope

- AgentCore-hosted orchestration with VPC connectivity. Separate deployment
  milestone.
- Cross-backend resume between `AsyncPostgresSaver` and `AuroraDataApiSaver`.
- A separate evidence ledger table.
- The remaining lower-priority audit items: profile-table RLS, the
  `loyalty_balance` scoped-session check, the `memory_server` preference
  insert missing `preference_id`, and the health endpoint conflating liveness
  with readiness. Tracked, not part of this work.

## 9. Risks

- **Checkpointer correctness.** Pending writes and channel-value serialization
  are where `BaseCheckpointSaver` implementations fail. Mitigated by mirroring
  LangGraph's schema and by testing interrupt-and-resume rather than only
  put-and-get.
- **Data API size ceiling.** 1 MiB per result set. Mitigated by an explicit
  failure and a test that asserts the boundary.
- **Backend gaps found during UI work.** Recorded separately from the UI
  tasks so the two do not blur.
