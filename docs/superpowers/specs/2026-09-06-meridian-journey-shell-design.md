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

## Constraints verified on 2026-09-06

- `meridian-demo-instance` is `PubliclyAccessible: false`; the cluster has
  `HttpEndpointEnabled: true`. From a laptop, only the RDS Data API reaches
  Aurora.
- `AsyncPostgresSaver` requires psycopg over TCP 5432, so it runs only through
  `scripts/start_checkpoint_tunnel.sh` or from inside the VPC.
- `_resolve_checkpoint_dsn()` therefore returns `None` on the laptop and the
  process falls back to `MemorySaver (in-process)`.
- The UI has no evidence contract. `ChatResponse` carries only
  `workflow_status` and `workflow_resumed_after_restart`
  (`meridian/backend/routers/chat.py:161`). Everything else is regex-scraped
  from trace prose in `deriveAuroraEvidence`
  (`meridian/frontend/src/showcase/lib/showcaseProof.ts:110`).
- Nothing rehydrates. A refresh loses the journey.
- `setSelectedPhase` deliberately clears the run on phase change
  (`useMeridianShowcase.ts:585-611`). Correct for the ladder, incompatible
  with view switching.

**Data API limits, confirmed against AWS documentation:**

| Limit | Value |
| --- | --- |
| Size of a single row in a returned result set | 64 KB |
| Total response size | 1 MiB |
| Total HTTP request size, including headers and JSON | 4 MiB |

The 64 KB per-row limit is the binding constraint on checkpoint reads. It is
handled by windowed `substring` reads over LangGraph's own `checkpoint_blobs`
rather than by a schema change, so the two backends share one table layout.

**Pinned versions.** `langgraph==1.2.9`, `langgraph-checkpoint==4.1.1`,
`langgraph-checkpoint-postgres==3.1.2`, `psycopg==3.3.4`, `boto3==1.43.51`.
The checkpointer contract is version-sensitive, so these are pinned and the
conformance run is tied to them.

## Decisions

1. **Server-owned journey with a thin persistent identity and an aggregated
   read API.** The journey document assembles existing persisted state.
   Checkpoints, bookings, audit rows, and conversation records keep authority
   over their own facts. No second copy, no separate evidence ledger.
2. **Hybrid checkpoint deployment.** Aurora Data API checkpointing for the
   demo; `AsyncPostgresSaver` retained where direct connectivity exists. Both
   behind the existing `CheckpointBackend` interface. The selected backend is
   persisted with the journey and never silently switched. Cross-backend
   resume is out of scope.
3. **Hold intent is checkpointed before the hold executes.** A `prepare_hold`
   node durably persists `hold_request_id` and the canonical parameters, and
   the `hold` node consumes them. The database constraint is the second line
   of defence, not the only one.
4. **Durable vertical slice before the new UI.** Kill and resume must work
   before Recovery desk and Presenter proof are built on it.

## 1. Identity, ownership, and the resume target

```sql
CREATE TABLE journeys (
    journey_id         VARCHAR(64) PRIMARY KEY,
    traveler_id        VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    checkpoint_backend VARCHAR(32) NOT NULL,
    active_thread_id   VARCHAR(200),
    status             VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE journey_threads (
    thread_id  VARCHAR(200) PRIMARY KEY,
    journey_id VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE journey_executions (
    execution_id     VARCHAR(64) PRIMARY KEY,
    journey_id       VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    thread_id        VARCHAR(200) NOT NULL REFERENCES journey_threads(thread_id),
    attempt          INTEGER NOT NULL,
    phase            SMALLINT,
    worker_id        VARCHAR(128) NOT NULL,
    status           VARCHAR(16) NOT NULL,
    lease_expires_at TIMESTAMPTZ,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at         TIMESTAMPTZ
);

CREATE UNIQUE INDEX journey_executions_one_running
    ON journey_executions (thread_id) WHERE status = 'running';
CREATE INDEX ON journey_executions (journey_id, started_at DESC);
```

`journey_threads` makes journey-to-thread ownership an enforced relationship
rather than a convention: a thread belongs to exactly one journey, by primary
key. `journeys.active_thread_id` names the resume target explicitly, so
"resume the journey" is never a search.

**Why not reuse `conversations`.** It is the Phase-4 memory record, carrying
`summary` and owning the embedded `conversation_messages` child, and its
primary key is already bound one-to-one to the LangGraph thread
(`workflow.py:1236` sets `thread_id = conversation_id`). A journey must outlive
a thread and span several.

### Resume semantics

Resume creates a **new execution attempt on the existing thread**. It never
creates a thread, never starts a second workflow, and never repeats a
business action.

`POST /journeys/{journey_id}/resume` runs one transaction:

1. Authorize the caller against `journeys.traveler_id`.
2. Insert a `journey_executions` row with `status='running'`, a fresh
   `execution_id`, `attempt = max(attempt) + 1`, this worker's id, and
   `lease_expires_at = now() + lease_ttl`.
3. The partial unique index `journey_executions_one_running` admits exactly
   one running execution per thread. A concurrent second request violates it.

Concurrency resolves as follows:

- **Live owner.** The unique violation is caught and the endpoint returns
  `409` naming the existing `execution_id`, its `worker_id`, and its lease
  expiry. Duplicate resume requests are answered, not queued.
- **Dead owner.** The claim transaction first runs a takeover step: any
  `running` row for the thread whose `lease_expires_at` is in the past is
  transitioned to `abandoned` in the same transaction, after which the insert
  succeeds. Expiry is a state transition, never an index predicate, because
  PostgreSQL requires index predicates to be immutable and `now()` is not.
- **Heartbeat.** A running execution renews `lease_expires_at` on a timer.
  Losing the heartbeat is what makes a killed worker recoverable within one
  lease TTL instead of forever.

The same partial-index rule is why a simultaneous-active-holds constraint, if
one is ever wanted, must also be expressed on a maintained status column
rather than on `hold_expires_at > now()`.

## 2. Checkpoint backends

`CheckpointBackend(saver, kind, durable, pool, error)` at `workflow.py:157` is
the workflow-facing interface, and `initialize_checkpoint_backend()` is the
single place a backend is chosen. `AuroraDataApiSaver` becomes a third branch:

1. Resolvable DSN plus psycopg extras gives `AsyncPostgresSaver`.
2. Otherwise a usable Data API client gives `AuroraDataApiSaver`.
3. Otherwise `MemorySaver`, unless `LANGGRAPH_CHECKPOINT_REQUIRED` is set,
   which raises.

**The demo configuration sets `LANGGRAPH_CHECKPOINT_REQUIRED=true`.** A silent
degrade to MemorySaver during a talk is the failure this whole design exists
to prevent, so it fails at startup instead.

### Durability capability versus commit evidence

These are two different facts and the UI must never substitute one for the
other:

- **Capability**: `CheckpointBackend.durable` — this backend *can* persist.
- **Evidence**: a specific `(thread_id, checkpoint_ns, checkpoint_id)` row
  observed in Aurora with its commit timestamp — this checkpoint *did* persist.

Presenter proof renders commit evidence. A durable backend with no committed
row for the current thread reads as "backend durable, no checkpoint committed
yet", not as a checkpoint.

### Synchronous persistence at the recovery boundary

The checkpoint at the demonstrated interrupt point is committed before the
request that produced it is acknowledged. LangGraph's durability mode is set
so the boundary write is synchronous rather than deferred. A resume beat whose
checkpoint was still in flight when the worker died is not a recovery
demonstration.

### Semantics the saver must match, not just table shape

Mirroring `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` is
necessary and not sufficient. The implementation must match
`langgraph-checkpoint==4.1.1` on:

- **Pending-write identity.** `(thread_id, checkpoint_ns, checkpoint_id,
  task_id, idx)`, including `task_path`.
- **Reserved write indices.** Negative and special indices carry meaning;
  they are stored and returned verbatim, not renumbered.
- **Parent relationships.** `parent_checkpoint_id` is written and used for
  history, so `alist` returns a correctly ordered lineage.
- **Channel versions.** The version strings the saver emits and compares must
  round-trip exactly; `new_versions` handling decides what a resumed graph
  recomputes.
- **Conflict behaviour.** Writes upsert on the natural key so a retried Data
  API call is idempotent.

Validation uses the checkpointer conformance suite from the upstream
`langgraph` repository. It is **not** part of the installed distribution — the
installed `langgraph/checkpoint/` tree contains only `base`, `memory`,
`postgres`, and `serde` — so it is vendored into `meridian/tests/` at the
pinned version and run alongside real Aurora integration tests. Neither
substitutes for the other: conformance catches contract violations, Aurora
integration catches transport and size failures.

### Data API storage contract

Serialization uses the same `JsonPlusSerializer` as the Postgres saver.

**Binary handling.** boto3 exposes `blobValue` as Python `bytes` in both
directions; it performs base64 transcoding itself. Any additional base64 in
application code is a double-encoding bug. The existing client cannot round-trip
binary at all today:

- `_format_parameters` (`backend/db/rds_data_client.py:69-95`) has no `bytes`
  branch. Bytes fall through to `else: {"stringValue": str(value)}`, which
  stores the Python repr `"b'\\x80\\x03...'"`. Silent corruption.
- `_parse_value` (`:105-119`) has no `blobValue` branch and returns `None` for
  any blob column.

Both gain binary handling, with a test asserting byte-identical round trips
across the full byte range including embedded nulls.

**Storage uses LangGraph's own tables unchanged.** The saver writes
`checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` with the exact DDL
from `langgraph-checkpoint-postgres==3.1.2`, including
`checkpoint_writes.task_path`. No parallel chunk table, so the two backends
never diverge on schema and the `checkpoint_migrations` bookkeeping stays
consistent.

The 64 KB per-row limit is handled entirely on the **read** path by ranged
reads, which is why no schema change is needed:

```sql
-- size first
SELECT octet_length(blob) AS n FROM checkpoint_blobs
 WHERE thread_id = :p0 AND checkpoint_ns = :p1
   AND channel = :p2 AND version = :p3;

-- then fixed-size windows, each safely under 64 KB
SELECT substring(blob FROM :p4 FOR :p5) AS part FROM checkpoint_blobs
 WHERE thread_id = :p0 AND checkpoint_ns = :p1
   AND channel = :p2 AND version = :p3;
```

Writes stay under the 4 MiB request limit the same way: the first segment is
inserted, and subsequent segments append with
`UPDATE ... SET blob = blob || :chunk`, so no single HTTP request carries the
whole value.

**Size enforcement before acknowledgement.** A checkpoint that cannot be read
back must never be reported as persisted. `aput` writes all blob segments
first and writes the `checkpoints` row that makes the checkpoint visible only
after they succeed, so a partial write is never observable as a committed
checkpoint.

**Paginated reads.** `alist` pages history with keyset pagination on
`checkpoint_id` so no single response approaches the 1 MiB ceiling. A read
that encounters a value it cannot window fails loudly, naming the thread,
checkpoint, and channel.

**Bug this exposes.** `_uses_postgres_saver` (`workflow.py:533`) tests
`checkpointer_kind.startswith("PostgresSaver")`. A Data API saver fails that
string test and silently renders the MemorySaver trace, reintroducing the
mislabelling fixed in `e78d68f`. It becomes a check on the `durable`
capability flag, and the trace reports the real backend name.

## 3. Business-action identity

The invariant: **one business effect per intended hold request**, across
retries, executions, and workers. A journey may legitimately contain several
holds, so every surface labels this as *one hold for this request*, never
"one hold per journey".

### Intent is persisted before the action

A `prepare_hold` node runs before the `hold` node. It allocates
`hold_request_id`, normalizes and validates the hold terms, computes the
fingerprint, writes all of it into the graph state, and that state is
checkpointed synchronously before the `hold` node is entered. Every retry and
every later execution reads the id and the canonical parameters back from the
checkpoint rather than regenerating them.

This is what makes the identity independently persisted. The `hold_requests`
row commits with the booking and therefore cannot serve as pre-action intent:
if the transaction rolls back, or the worker dies before it, the row does not
exist. The checkpoint does.

### The database constraint

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

`thread_id` and `execution_id` are provenance. `fingerprint` is a digest of
**validated, normalized** terms — package, duration, quantity, unit price,
total — computed in `prepare_hold` from values that have already been checked,
so a fingerprint never encodes unvalidated input.

The foreign key is deferred because the identity row is written before the
booking it names exists. `create_courtesy_hold` already receives
`p_booking_id` from the caller, so the id is known at the top of the
transaction.

`create_courtesy_hold` gains `p_journey_id`, `p_hold_request_id`, and
`p_fingerprint`, and its body runs, inside the existing transaction and before
the advisory lock:

1. **Authorize the journey.** `journeys.journey_id = p_journey_id` must exist
   with `traveler_id = p_traveler_id`, in addition to the existing
   `app.current_traveler_id` scope check. This runs before creation **and**
   before replay, so a known request id never returns another traveler's
   booking.
2. `INSERT INTO hold_requests ... ON CONFLICT (journey_id, hold_request_id)
   DO NOTHING`.
3. **Inserted**: take the advisory lock, run the existing capacity check,
   insert `bookings` and `booking_lines`, return the new hold.
4. **Conflicted**: select the existing row. On fingerprint mismatch raise
   `hold_request_parameter_mismatch`. Otherwise return the existing booking
   with its current status, taking no inventory action — no re-reservation, no
   extended expiry, no reactivation of a cancelled or expired hold.

Concurrent identical requests serialize on the primary key: one inserts, the
other blocks on the uncommitted key, then takes the replay path. An
application-level check-then-insert is insufficient and is not used.

The return signature extends to carry `booking_id`, `status`, and a replay
flag, so callers render what happened rather than assuming a fresh
reservation.

A deliberate replacement hold allocates a new `hold_request_id` through
`prepare_hold`, which means a new checkpointed intent.

### Migration

A forward migration, `007_journey_scoped_hold_identity.sql`:

- Creates `journeys`, `journey_threads`, `journey_executions`, and
  `hold_requests`, plus LangGraph's `checkpoint_migrations`, `checkpoints`,
  `checkpoint_blobs`, and `checkpoint_writes` using the exact DDL from
  `langgraph-checkpoint-postgres==3.1.2`.
- Creates the new `create_courtesy_hold` with the expanded signature, and
  **drops the old eight-argument function**. `CREATE OR REPLACE FUNCTION` with
  a changed parameter list creates an overload rather than replacing, so
  leaving it in place would leave a callable path that bypasses idempotency
  entirely. The old signature is dropped and its grant revoked.
- Updates every caller to the new signature.
- Grants `EXECUTE` on the new function to `meridian_app` only, and applies the
  same traveler-scoped RLS policy pattern the existing traveler tables use to
  `journeys`, `journey_threads`, `journey_executions`, and `hold_requests`,
  so a scoped session cannot read another traveler's journey rows.
  The LangGraph checkpoint tables are workload-owned rather than
  traveler-scoped and are granted to the application role without an RLS
  policy; thread ownership is enforced above them by `journey_threads` and the
  resume authorization path.
- Preserves existing `booking_id` values. Legacy holds are linked to journeys
  and request identities only where the mapping is unambiguous.

**Legacy holds that cannot be linked** are readable and cancellable but are
never resumed into an idempotent flow. A resume that encounters an unlinked
legacy hold stops and asks for an explicit decision rather than creating a
second hold or adopting one it cannot prove belongs to the request.

## 4. Read API and evidence

`GET /journeys/{journey_id}` assembles a typed document from the sources that
already hold the facts. It has no workflow side effects.

| Section | Source |
| --- | --- |
| Journey, owner, backend, active thread | `journeys`, `journey_threads` |
| Executions, workers, attempts, leases | `journey_executions` |
| Checkpoint and thread | active backend, `checkpoints`, `checkpoint_blobs` |
| Conversation and recommendations | `conversation_messages`, checkpointed state |
| Business record | `bookings`, `booking_lines`, `hold_requests` |
| Authorization history | `traveler_access_audit` |
| Session totals | existing `/diagnostics/session-receipt` logic |

Every claim carries `source`, `id`, and `observed_at`, and every claim is
correlated to the actual `journey_id`, `thread_id`, `execution_id`, and
`hold_request_id` it describes. Unavailable evidence is explicit:
`{"status": "unavailable", "reason": "..."}`. It is never omitted and never
inferred from prose.

**Historical authorization comes from `traveler_access_audit` rows, not from
the current identity context.** Who is authenticated now is a fact about now.
Presenter proof asserts that a past action was authorized, which only the
audit row recorded at the time can establish.

Authorization runs on every read and every action, reusing
`require_http_principal` and `authorize_traveler` plus the persisted-owner
check `_authorize_thread` performs (`workflow.py:1210`). A journey id or a
thread id grants nothing on its own.

### Response example

```jsonc
{
  "journey_id": "jrn_7f3a91",
  "traveler_id": "TRV-001",
  "status": "awaiting_decision",
  "checkpoint_backend": { "kind": "AuroraDataApiSaver", "durable": true },
  "active_thread_id": "tokyo_1042",

  "conversation": {
    "source": "conversation_messages",
    "observed_at": "2026-09-06T02:14:07Z",
    "messages": [
      { "message_id": "msg_01", "role": "user",
        "content": "My plans changed. Can we keep the trip in Tokyo and stay within my budget?",
        "created_at": "2026-09-06T02:11:52Z" }
    ]
  },

  "recommendations": {
    "source": "checkpoint:tokyo_1042/cp_1042_03#channel:recommendations",
    "observed_at": "2026-09-06T02:13:40Z",
    "items": [
      { "package_id": "TKY-003", "name": "Tokyo Executive Stopover",
        "price_per_person": 1949, "nights": 3 },
      { "package_id": "TKY-006", "name": "Tokyo Indie Neighborhood Walk",
        "price_per_person": 1599, "nights": 4 }
    ]
  },

  "selected_plan": {
    "source": "checkpoint:tokyo_1042/cp_1042_03#channel:selected_package",
    "package_id": "TKY-003"
  },

  "pending_decision": {
    "source": "checkpoint:tokyo_1042/cp_1042_03#next",
    "next_nodes": ["confirm_plan"],
    "prompt": "Confirm the Tokyo Executive Stopover hold"
  },

  "checkpoint": {
    "status": "committed",
    "source": "checkpoints",
    "thread_id": "tokyo_1042",
    "checkpoint_id": "cp_1042_03",
    "parent_checkpoint_id": "cp_1042_02",
    "checkpoint_ns": "",
    "committed_at": "2026-09-06T02:13:41Z"
  },

  "hold": {
    "status": "held",
    "label": "one hold for this request",
    "source": "hold_requests + bookings",
    "hold_request_id": "hrq_5c1d0e",
    "booking_id": "BKG-4471",
    "created_by_execution_id": "exe_02",
    "hold_expires_at": "2026-09-06T14:13:44Z",
    "replayed": false
  },

  "executions": [
    { "execution_id": "exe_01", "attempt": 1, "worker_id": "worker_01",
      "status": "abandoned", "started_at": "2026-09-06T02:11:50Z",
      "ended_at": "2026-09-06T02:13:58Z" },
    { "execution_id": "exe_02", "attempt": 2, "worker_id": "worker_02",
      "status": "running", "started_at": "2026-09-06T02:14:02Z",
      "lease_expires_at": "2026-09-06T02:14:32Z" }
  ],

  "authorization": {
    "status": "observed",
    "source": "traveler_access_audit",
    "subject": "arn:aws:sts::…:assumed-role/meridian-workload/…",
    "decision": "ALLOW",
    "observed_at": "2026-09-06T02:11:49Z"
  },

  "rls": { "status": "unavailable", "reason": "no scoped probe run this session" }
}
```

A refresh restores the conversation, the recommendations, the selected plan,
the pending decision, the hold and its status, and both executions — including
the fact that `worker_01` was abandoned and `worker_02` holds the lease. That
is the whole continuity claim, served from persisted state.

## 5. Frontend shell

`MeridianJourneyProvider` owns journey identity and the assembled document,
caching it and coordinating the views. `useMeridianShowcase` keeps ladder and
turn state, including clear-on-phase-change, which now applies only inside the
Capability ladder.

`view` and `journey_id` live in the URL so refresh and reconnect reload backend
state. Capability demonstrations keep their own operation ids; not every SQL or
retrieval demo is forced into the workflow.

`deriveAuroraEvidence` stops regex-scraping and becomes a renderer over the
typed document, retiring the class of bug fixed in `98bbac2`.

Presenter proof shows the actual graph host, the actual checkpoint backend, and
committed evidence. A local graph writing Aurora checkpoints is labelled as
exactly that.

The design prototypes are visual references; their simulated transitions are
replaced by real integrations.

## 6. Sequencing

The existing five-phase path stays runnable throughout.

1. Data API binary support and size enforcement in `rds_data_client`.
2. `AuroraDataApiSaver` behind `CheckpointBackend`, with the vendored
   conformance suite and Aurora integration tests.
3. `journeys`, `journey_threads`, `journey_executions`, lease-based resume,
   read API, authorization.
4. `prepare_hold` intent checkpointing and journey-scoped hold identity,
   including the forward migration and the dropped old signature.
5. The vertical slice, verified end to end.
6. Shell and view axis. Capability ladder untouched.
7. Recovery desk, then Presenter proof.
8. Concierge visual pass.

## 7. Verification

Vertical slice, against real Aurora:

1. Save a checkpoint and confirm persistence by reading the committed row.
2. Terminate the actual graph worker.
3. Resume the authorized thread from a fresh worker.
4. Verify restored state and changed worker identity.
5. Interrupt after a hold commits but before its following checkpoint, retry,
   and verify exactly one hold exists.
6. Interrupt **between intent persistence and hold creation**, resume, and
   verify one hold created under the checkpointed `hold_request_id`.

Focused tests: serialization round trips, pending writes and reserved indices,
retry idempotency, ranged blob reads, the 64 KB row boundary, the 1 MiB
paginated read boundary, and byte-identical binary round trips with no double
encoding.

Business identity: concurrent execution of the same request, retry after
commit but before checkpoint, parameter mismatch returning a conflict, a
deliberate new request after expiry, and ownership rejection on both the
creation and replay paths.

Shell: switching views preserves the active run; refresh restores conversation,
recommendations, selected plan, pending decision and hold; interruption and
resume retain the correct checkpoint; another traveler cannot resume the
thread; simultaneous resume yields one execution and a `409` naming the other;
repeated resume does not duplicate the business action.

## 8. Out of scope

- AgentCore-hosted orchestration with VPC connectivity. Separate milestone.
- Cross-backend resume between `AsyncPostgresSaver` and `AuroraDataApiSaver`.
- A separate evidence ledger table.
- Remaining lower-priority audit items: profile-table RLS, the
  `loyalty_balance` scoped-session check, the `memory_server` preference
  insert missing `preference_id`, and the health endpoint conflating liveness
  with readiness.

## 9. Risks

- **Checkpointer correctness.** Pending writes, reserved indices, and channel
  versions are where custom savers fail. Mitigated by the vendored conformance
  suite at pinned versions plus interrupt-and-resume integration tests.
- **Segmented blob correctness.** Windowing bugs surface as corrupted resumes,
  not as errors. Mitigated by round-trip tests at and above the 64 KB
  boundary, including values spanning many windows.
- **Lease tuning.** Too long delays recovery after a kill; too short risks
  stealing a live execution. The TTL and heartbeat interval are configuration
  with a documented default and a test at both edges.
- **Backend gaps found during UI work** are recorded separately from UI tasks.
