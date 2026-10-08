// Implementation details shared by the compact briefing and its disclosures.
export const PHASES: [string, string, string][] = [
  ['SQL', 'Ground the assistant in live rows.', 'Parameterised filters over trip_packages in Aurora PostgreSQL through the RDS Data API. The trace shows the SQL that ran and the rows it returned.'],
  ['MCP', 'Give the agent tools it can reuse.', 'Search, compare and currency conversion behind named MCP tool contracts. The trace shows each tool name, its inputs and its result.'],
  ['Retrieval', 'Find trips by meaning.', 'pgvector similarity and full-text search fused into one candidate list, then reranked by Cohere Rerank 3.5 on Bedrock. The trace shows candidate scores and the rerank order.'],
  ['Production', 'Run the concierge on managed infrastructure under policy.', 'A Strands agent in Bedrock AgentCore Runtime calls its tools through AgentCore Gateway over MCP; a Cedar policy engine decides every call; AgentCore Memory carries the conversation. A courtesy hold and its confirmation are governed writes.'],
  ['Workflow', 'Recover the intent and verify the outcome.', 'A Strands Graph on its own AgentCore Runtime saves each step as a snapshot in AWS Aurora, holds a worker lease, and places its hold through the same gateway tool. If a committed reply is lost, a replacement worker replays the saved intent and reads the original booking and expiry.'],
];

export const TOOLS: [string, string, string][] = [
  ['SemanticTripSearchLambda___semantic_trip_search', 'Read', 'Embeds the query with Cohere Embed v4, searches pgvector and full-text indexes in Aurora, and returns ranked packages with scores.'],
  ['MeridianHolds___get_package_details', 'Read', 'One package with its live durations, availability and highlights, read under the traveler’s RLS scope.'],
  ['MeridianHolds___create_courtesy_hold', 'Write', 'Places a courtesy hold on one package duration. Requires the traveler id, confirmation flag, budget ceiling and journey reference the platform pinned; accepts a saved request id, booking id and worker execution id for replay.'],
  ['MeridianHolds___confirm_booking', 'Write', 'Confirms a held booking for the authorized traveler: catalog inventory in Meridian’s database only, no supplier and no payment. Requires the booking id and the total Aurora holds, plus the traveler id, confirmation flag, budget ceiling and journey reference the platform pinned. A retry returns the original confirmation.'],
];

export const POLICIES: [string, string, string][] = [
  ['meridian_read_tools', 'Any authenticated caller may search packages and read package details.',
   'permit(principal,\n  action in [AgentCore::Action::"SemanticTripSearchLambda___semantic_trip_search",\n             AgentCore::Action::"MeridianHolds___get_package_details"],\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:<region>:<account-id>:gateway/<gateway-id>");'],
  ['meridian_hold_governance', 'A courtesy hold runs only after the traveler confirmed it, for at most 12 hours, for at most 6 travelers, and within the traveler’s saved budget ceiling. Nothing else permits the hold, so any other call is denied by default.',
   'permit(principal,\n  action == AgentCore::Action::"MeridianHolds___create_courtesy_hold",\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:<region>:<account-id>:gateway/<gateway-id>")\nwhen {\n  context.input.travelerConfirmed == true &&\n  context.input.holdMinutes <= 720 &&\n  context.input.travelers <= 6 &&\n  context.input.totalCents <= context.input.budgetCeilingCents\n};'],
  ['meridian_booking_governance', 'A held booking is confirmed only after the traveler confirmed it and only when its total is within the traveler’s saved budget ceiling. Aurora enforces that the booking is held, unexpired and owned by the traveler. Nothing else permits confirm_booking, so any other call is denied by default.',
   'permit(principal,\n  action == AgentCore::Action::"MeridianHolds___confirm_booking",\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:<region>:<account-id>:gateway/<gateway-id>")\nwhen {\n  context.input.travelerConfirmed == true &&\n  context.input.totalCents <= context.input.budgetCeilingCents\n};'],
];

export const CONTROLS: [string, string][] = [
  ['Check access at every hop', 'The backend, both Runtimes and the Gateway each check the caller before acting, and the database applies its own row-level security again.'],
  ['Authorize the traveler', 'traveler_identity_bindings in Aurora grants that subject a traveler. A missing grant fails before any row-level scope is set, and both allow and deny land in traveler_access_audit.'],
  ['Scope every row', 'Row-Level Security filters rows to the authorized traveler under the least-privilege meridian_app role, inside one Data API transaction.'],
  ['Decide every tool call', 'AgentCore Gateway serves the tools over MCP; its Cedar policy engine, MeridianGovernance in ENFORCE mode, decides each call on the arguments before any Lambda runs.'],
  ['Make the writer a workload too', 'The MeridianHolds Lambda holds its own grant, sets the traveler scope, steps down to meridian_app and calls create_courtesy_hold or confirm_booking, so a retried call replays the same booking or the same confirmation.'],
];
