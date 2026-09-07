# Meridian audit corrections

Validated September 6, 2026 against the existing working tree and local Aurora connection.

| Finding | Correction |
| --- | --- |
| Thread ownership | Authorize both new turns and resumes. Check the persisted journey binding even before a first checkpoint exists. Preserve HTTP 403. |
| Stale hold intent | Clear per-run hold terms. Allocate a booking identity per business intent and preserve it across checkpoint retries. Older receipts keep their original identity. |
| Party mismatch | Share the displayed traveler count with direct holds and typed recovery requests. Persist the count across filter reset, refresh, and resume. |
| Refresh loses recovery | Read the saved graph values and pending nodes into the recovery controls. Restore the original thread, shortlist, party, and resume action. |
| Browser execution evidence | Register journeys before graph execution. Claim and renew a worker lease, record terminal outcomes, and attach the execution ID to holds. The kill demonstration uses the same lifecycle. |
| Incorrect durability label | Use explicit checkpoint durability telemetry across the ladder and recovery views. Recognize legacy Aurora saver receipts. |
| Late responses | Abort and invalidate chat requests on phase changes, clear, or unmount. Stale responses cannot overwrite the current conversation. |
| Overstated restart evidence | Check lease expiry against the observed database clock. Require a successful execution and its saved resume receipt before claiming successful recovery. |

The live verification exposed an additional cancellation edge: an interrupted Data API transaction can retain locks. Scoped transactions now roll back on cancellation; takeover skips locked execution rows and returns a conflict until ownership can safely transfer. Workflow failures return HTTP 503 and direct the user to the persisted journey.

## Presentation changes

The primary Meridian mark is an M, shared by the shell, message avatars, boarding-pass header, and favicon. Aircraft icons remain for flight information.

Capability pills pair a successful query with a boundary query, label the required next capability, and carry the same question forward. Questions emphasize comparison with currency conversion, semantic trip matching, specific recalled preferences, and checkpointed recovery. These are boundaries of the configured demonstration phases. SQL and MCP are not inherently incapable of broader implementations. The fifth phase proves pause/resume; it does not issue airline tickets.

## Verification

- Frontend: 151 tests passed; production build and ESLint passed.
- Backend: 193 selected tests passed with isolated checkpoints and an outbound network guard.
- Browser: phase-switch cancellation, matching displayed/held party counts, refresh restoration, resume thread identity, expired leases, and no successful-resume claim from an unverified second attempt. Desktop and mobile layouts checked.
- Live Aurora: real HTTP handlers, checkpoints, executions, and a three-traveler hold; deterministic catalog retrieval isolated the persistence test from model variability. A competing resume returned 409. After SIGKILL, another process resumed the checkpoint. Aurora recorded abandoned/succeeded executions, exactly one booking, an unchanged booking ID and expiry, and a 900-second hold window. Temporary test journeys and bookings were removed and zero remaining rows verified.

The live test verifies the persisted 15-minute window and restart continuity; it does not wait fifteen minutes or independently validate every model response, deployed authentication configuration, or infrastructure policy.

## Final evidence check, September 7

Completed hold intents no longer appear as pending decisions. The selected package remains visible from its actual saved hold channel, and recommendation evidence cites the channel that supplied it. Eight new regression cases cover prepared versus completed intents, selection readback, and source attribution; all 24 focused hold and recovery tests passed with zero outbound network attempts.
