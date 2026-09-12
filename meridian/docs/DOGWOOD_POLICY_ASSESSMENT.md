# Meridian: Cedar and Dogwood

Reviewed against the repository and current AWS documentation on 12 September 2026.
This is an integration assessment and a candidate policy, not a deployment record.
No gateway, policy, permission or inventory was changed for this assessment.

## Recommendation

Keep Meridian's existing Cedar controls. Add Dogwood for a specific temporal
requirement: **a hold must follow a successful package lookup for the same
package within five minutes, in the same authenticated policy session**.

Dogwood is the language AgentCore Policy uses for temporal rules. It is compatible
with Cedar; this is an extension of the existing policy engine, not a separate
policy service. Cedar judges the current call. The temporal condition adds
evidence about a prior call.

This first rule proves a recent successful lookup, not the freshness of a
particular duration's inventory or human approval. Aurora must still validate
availability, ownership, expiry, capacity and replay inside its transaction.
Traveler confirmation must continue to come from the application.

## What Meridian already implements

| Control | Source |
| --- | --- |
| `MeridianGovernance`, configured in `ENFORCE` | `meridian_agentcore/agentcore/agentcore.json` |
| Read, hold and confirmation Cedar policies | `policyEngines` in the same configuration |
| Platform-pinned traveler, confirmation, ceiling and journey | `meridian_agentcore/app/MeridianConcierge/turn_trace.py` |
| Confirmed writes executed before model narration | `meridian_agentcore/app/MeridianConcierge/hold_execution.py` |
| Phase 5 hold through the same gateway | `backend/agents/orchestration_05/workflow.py`, `_node_hold` |
| Atomic, replay-safe hold and lease validation | `meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py` and Aurora functions |
| Structured allow/deny fields for recovery | `cedar_decision`, `cedar_policy`, `policy_mode`, `gateway_tool` in workflow telemetry |

The new Recovery checks UI exposes these structured decisions separately from
persisted checkpoint and booking records. It does not infer an allow from prose,
claim that an allowed call created a hold, or describe Dogwood as enabled.

## Integration work required

1. **Persist a policy session per recovery thread.** Store a server-generated UUID
   with the journey before its first gateway call. Reuse it across worker restarts.
   Never generate a new ID for every tool call or automatically rotate it to
   bypass a denial. Bind it to the authorized traveler and logical journey.
2. **Carry the ID on every gateway request.** Add
   `x-amzn-bedrock-agentcore-policy-session-id` to the backend adapter and the
   runtime MCP transport, including initialization and discovery requests. Include
   it in the headers signed by the runtime's `GatewaySigV4` implementation.
   Neither client currently sends this header.
3. **Make the prerequisite observable to the gateway.** Phase 5 currently checks
   availability through its own tools and then calls the gateway hold tool.
   Immediately before the hold, complete a gateway
   `MeridianHolds___get_package_details` call for that package in the same session.
   The direct Phase 4 confirmed-hold path needs the same prerequisite.
   Check for a successful result, then await it before attempting the hold.
4. **Keep caller identity boundaries explicit.** The Phase 5 backend and Phase 4
   runtime sign with different principals. Identical session strings do not merge
   those histories. For the first slice, perform the lookup and hold under the
   same caller. Keep Aurora's booking record as the authority across the recovery
   to Concierge handoff. A later confirmation prerequisite should use a scoped
   booking-read tool in the confirming caller's session, or a deliberately
   designed Gateway/Runtime identity chain.
5. **Update IAM through infrastructure code.** The Gateway role requires
   `bedrock-agentcore:GetWorkloadAccessToken`, scoped as described in the AWS
   policy permissions guide. Verify existing permissions and regional support.
   Do not fetch or manually construct the service-managed WAT.
6. **Use the temporal policy definition.** The control-plane definition is
   `definition.policy.statement`, rather than `definition.cedar.statement`.
   Verify that the installed AgentCore CLI schema supports it before modifying
   `agentcore.json`; a supported IaC/control-plane path may be necessary.
7. **Replace the hold permit deliberately.** A second, stricter permit does not
   constrain the existing looser permit. Replace the existing hold permit with
   one combining its Cedar checks and the temporal prerequisite, or design an
   explicit temporal forbid. Keep read and booking policies enforced.
8. **Handle session expiry and invalidation.** Sessions idle out after 24 hours.
   Changing temporal policies invalidates active sessions; their next request can
   return HTTP 409. On a verified invalidation, reconcile the journey and hold,
   establish a new persisted session, and rerun required reads. Preserve the
   original hold request and booking IDs. Do not repeat uncertain writes blindly.

## Candidate replacement hold policy

Design candidate only. It has not been validated by AgentCore or deployed.
Substitute the intended gateway ARN and validate against that gateway's actual
schema using `FAIL_ON_ANY_FINDINGS` in an isolated test engine.

```text
permit (
    principal,
    action == AgentCore::Action::"MeridianHolds___create_courtesy_hold",
    resource == AgentCore::Gateway::"<gateway-arn>"
)
when temporal {
    formerly within 5m
        AgentCore::Action::"MeridianHolds___get_package_details"::response{
            eventResource: resource,
            input.packageId: context.input.packageId
        }
}
when {
    context.input.travelerConfirmed == true &&
    context.input.holdMinutes <= 720 &&
    context.input.travelers <= 6 &&
    context.input.totalCents <= context.input.budgetCeilingCents
};
```

This uses an existing declared input field to correlate the successful lookup
with the hold. Output-to-input rules need explicit output schemas; the current
Lambda tool manifest declares input schemas only. A lookup is not a surrogate
approval, and a prior response is not a replacement for transactional capacity
validation.

Avoid a “one hold call per session” rule initially. Counting requests also counts
retries; it can block the very idempotent recovery the workshop demonstrates.
Keep duplicate prevention and capacity locking in Aurora. Session limits also
reset with a new session, so they are not account-wide spending limits.

## Concrete rehearsal

Use an isolated Gateway/policy engine with the same schemas and controlled test
targets. Do not set the current gateway to `LOG_ONLY`: enforcement mode applies
to the gateway and would stop enforcing its existing Cedar policies too.

| Scenario | Required result |
| --- | --- |
| Hold before package lookup | Denied; writer not invoked |
| Successful lookup of A, then hold A within five minutes | Allowed only if existing Cedar checks also pass |
| Lookup A, then hold B | Denied |
| Failed lookup, then hold | Denied |
| Lookup in a different policy session or principal | Denied |
| Missing session ID with temporal policy configured | Explicit validation failure |
| Resume worker after lookup window expires | Re-read package before attempting hold |
| Resume after committed hold, with lost response | Reuse hold identity; exactly one booking and original expiry |
| Change temporal policy during active session | Surface conflict; reconcile before establishing new history |
| Hand back to Concierge under a different principal | Read the authorized Aurora booking; do not assume shared history |

Capture the actual policy decision, policy session ID, gateway trace ID, temporal
evaluation attributes, checkpoint and booking identity for the workshop proof.
The temporal `evaluation_invoked` span attribute alone does not establish that a
temporal condition matched or determined the decision.

## Sources

- [Temporal policies and prerequisites](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal.html)
- [Session lifecycle, principal binding and header requirements](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-session-based-temporal.html)
- [Authoring, event schemas and combined conditions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal-authoring.html)
- [Gateway enforcement modes](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-enforcement-modes.html)
- [IAM permissions for temporal policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html#policy-permissions-session-temporal)
