# Meridian presenter runbook - 19 September 2026

L300, 60 minutes: a **planned 40-minute core** plus **20 minutes discussion/flex**. This is a chalk talk, not a participant workshop. Use the [editable deck](presentation/Meridian-reInvent-chalk-talk.pptx), [slide notes](presentation/SLIDE_NOTES.md), [technical presenter guide](PRESENTER_GUIDE.md), and [readiness report](READINESS_2026-09-19.md). No timed human rehearsal or physical room check is claimed.

## Preflight, 30 minutes before the session

Use the established demo account and region, Python 3.13, Node 22.12+, AWS CLI, and the repository's hash-locked dependencies. Never run initialization or a full seed against the existing demo database. The app uses Aurora RDS Data API; it does not need a local PostgreSQL substitute or checkpoint tunnel.

From the repository root:

```bash
git status --short
git rev-parse HEAD
git ls-remote origin refs/heads/main
cd meridian
source venv/bin/activate
aws sts get-caller-identity
python scripts/verify_installation.py
python scripts/test_aurora_connection.py
python scripts/verify_agentcore.py
```

Expected: intended source SHA, intended account, Aurora reachable, Runtime READY, Gateway READY with four tools, Memory ACTIVE, policy ACTIVE in ENFORCE mode, and observability READY. Confirm the locally configured Aurora/runtime references match the established demo environment. Do not print `.env` or credential files.

Start the backend in terminal A, from `meridian/`:

```bash
LANGGRAPH_CHECKPOINT_DATA_API=true LANGGRAPH_CHECKPOINT_REQUIRED=true \
  uvicorn backend.main:app --host 127.0.0.1 --port 8013
```

In terminal B, from `meridian/`:

```bash
curl --fail --silent --show-error http://127.0.0.1:8013/api/health | python -m json.tool
cd frontend
npm ci
VITE_API_ORIGIN=http://127.0.0.1:8013 npm run build
npm run preview -- --host 127.0.0.1 --port 5176 --strictPort
```

Expected health: `AuroraDataApiSaver`, durable and required checkpoint flags both true. `/health` is process liveness only. Open `http://127.0.0.1:5176/showcase`. Expect **Meridian live**. If ports are occupied, identify the owner and choose unused ports; do not stop another session. Update `VITE_API_ORIGIN` and rebuild when the API port changes. Restart the backend after pulling new Python code.

Before the audience arrives, from `meridian/`:

```bash
python scripts/validate_demo.py --base-url http://127.0.0.1:8013
```

This runs real AWS calls, creates uniquely scoped test records, checks 31 contracts, and removes its own conversations/holds by default. Expect no failures, no request above the 55-second browser deadline, and a successful cleanup line. Cloud service audit events retain their configured lifetime. It does not certify hosted parity. If a failure interrupts cleanup, use the recorded IDs to inspect and release only those records.

Enable **Projector readability** and use **Preview audience layout** before fullscreen. The five surface tabs are Concierge, Capability ladder, Recovery desk, System evidence, and Solution briefing. Use focused architecture views rather than reading the entire diagram at once. Browser access is a single shared demo principal bound to Alex; there is no end-user account-switch demonstration.

## Required sequence and observable results

All talk timings are estimates. API timings below were measured in the final September 19 local run against live AWS; they vary with model/network conditions. The 31-call automated run spent **171.6 seconds in HTTP requests**, excluding cleanup and narration.

| Beat | Presenter action | Observable result | Measured request time |
| --- | --- | --- | --- |
| Problem, 0-4 min | Concierge: introduce Alex's canceled JFK-to-Tokyo trip | Fictional traveler/catalog; distinguish a package hold from a flight reservation | Static setup |
| Capabilities, 4-12 min | Ladder, SQL: **City trips under $2,000** | Grounded rows, prices and SQL activity | 0.089 s |
| SQL boundary | **Compare trips in euros** | Explains the phase boundary; no invented conversion | 0.001 s |
| MCP | Switch to MCP, same prompt | Named comparison/conversion calls through `meridian-concierge`; configured demo FX rates | 2.326 s |
| Retrieval | Switch to Retrieval, **Romantic wine-country villa** | Hybrid candidates, actual rerank metadata, shortlist | 17.907 s |
| Retrieval boundary | **Recall my plan & preferences** | Explains that saved context requires Production | 0.002 s |
| Authority, 12-20 min | Production, **Use traveler context** on, **Tokyo with my preferences** | Workload grant, Runtime turn, saved preferences; inspect Activity and RLS | 30.349 s first turn; 9.293 s recall |
| Bridge | **Canceled flight replan**, then **Run this in Workflow** | Explicit handoff to durable Workflow | 23.293 s |
| Durability, 20-32 min | Recovery desk: **Start recovery** | Shortlist saved, durable checkpoint, resume action | 8.186 s |
| Reload | Reload the saved URL with its thread/journey parameters | Same paused journey read from Aurora | Readback, not a new run |
| Resume | **Resume and request hold** | Same thread; availability checked; Cedar allowed; one 15-minute hold | 21.912 s |
| Evidence, 32-37 min | System evidence: Checkpoint, Authorization, Business result | Saved checkpoint, attempts/worker IDs, authorization audit, same booking and expiry | 1.824 s journey read |
| Close, 37-40 min | Three takeaways and source link | Separate context, authority and durability; invite discussion | Planned timing |

A normal pause/reload/resume can use the same worker. Call it a **resumed execution**. It is not proof of worker death. For the actual worker-replacement demonstration, run the following from `meridian/`, preferably prepared in a separate terminal before the talk:

```bash
python scripts/kill_and_resume_demo.py
```

It kills only its own worker after a committed checkpoint, verifies the old lease prevents premature takeover, resumes with a different worker, reads one hold with its original expiry from Aurora, then removes its uniquely scoped records. The September 19 review observed exit `-9`, a new worker, one retained hold and successful cleanup. Allow roughly one minute as a planning estimate; do not call that a measured human delivery time. If short on time, show the dated evidence in the report and explicitly identify it as an earlier execution.

## Optional branches

- **Handoff and confirmation:** Recovery desk → **Take it back to Alex** → review the trip → **Confirm this trip for Alex**. Expect the same booking to become confirmed in Aurora. Final direct confirmation measured 9.571 s. No supplier was contacted and no payment was taken.
- **Lost response:** `python scripts/lost_response_demo.py` deliberately discards an actual committed Gateway response, retries the same intent, verifies one hold and unchanged expiry, then cleans its records. A timeout alone does not prove rollback.
- **Denial:** use `python scripts/smoke_production_turn.py` for unconfirmed and over-budget policy checks. Do not secretly relax input constraints to turn a denial into a success. The automated suite also verifies an eight-traveler request is denied and creates no hold.
- **Solution briefing:** architecture focus controls and temporal-policy discussion. Dogwood is assessed, not enabled. Online model-judge evaluation is not deployed.

## Reset, interruption and recovery

A completed or paused thread cannot be restarted as a new workflow (HTTP 409). Use **Clear** or open `/showcase?view=recovery` without a thread to start a new journey. Preserve the old URL when its result matters. The new desk must not show the previous Concierge booking receipt.

For an unknown request outcome, wait for the UI deadline, then re-read the journey or booking. **Stop waiting** cancels the browser wait; the server may still complete. Do not clear browser storage to lose a stable hold request identity. Wait for an active execution lease before retrying a recovery. Restarting the backend is safe after a durable checkpoint; refresh and resume the saved thread.

Workflow holds expire after 15 minutes; direct Concierge holds expire after 12 hours. Confirmed bookings persist and consume catalog inventory. Release only a booking you created and reviewed:

```bash
python scripts/release_demo_bookings.py --booking-id BOOKING_ID --dry-run
python scripts/release_demo_bookings.py --booking-id BOOKING_ID
```

Replace `BOOKING_ID` with the exact owned receipt; never use a broad reset during a shared session. A dry-run and real release were checked in this review. Schema initialization refuses nonempty databases; full seeding refuses existing traveler/catalog data. `seed_data.py --catalog-only` refreshes catalog data while preserving traveler history, but changes shared inventory and is not a routine on-stage reset.

If the API is offline, check the process/port, `VITE_API_ORIGIN`, AWS session expiry, `/api/health`, and sanitized backend logs. For hosted 401/403, use the established access mechanism and workload binding; a public liveness response does not establish access. If Bedrock/model availability or rerank fails, follow the visible failure/fallback message. Do not say a model, reranker, or tool ran when its trace says otherwise.

## Fallback and remaining human checks

Use the [PDF](presentation/Meridian-reInvent-chalk-talk.pdf), [notes](presentation/SLIDE_NOTES.md), and dated evidence summary in the readiness report. Say “recorded during the September 19 local validation.” The embedded screenshots are still images, not a fresh live run. No new video recording was created. A source walkthrough is a fallback explanation, not proof of live service health.

The hosted CloudFront/App Runner deployment remains older than this source. Use the validated local application until an authorized hosted deployment is updated and authenticated parity is checked. Do not run provisioning or deployment scripts as preflight.

Before delivery, complete a timed 40-minute human run, reserve 20 minutes for questions, check actual projector contrast and back-row text size, fullscreen/Escape, keyboard focus, browser zoom, network and AWS session duration, and the fallback files offline. Confirm speaker names/title/session code with the event owner. Test screen-reader navigation if required and verify that the print PDF meets the event's accessibility delivery requirements. End by releasing only owned demo bookings and documenting retained billable AWS resources.
