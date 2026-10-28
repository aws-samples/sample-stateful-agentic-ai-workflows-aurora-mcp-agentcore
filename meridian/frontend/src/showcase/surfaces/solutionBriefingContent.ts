// Implementation details shared by the compact briefing and its disclosures.
export const PHASES: [string, string, string][] = [
  ['SQL', 'Ground the assistant in live rows.', 'Parameterised filters over trip_packages in Aurora PostgreSQL through the RDS Data API. The trace shows the SQL that ran and the rows it returned.'],
  ['MCP', 'Give the agent tools it can reuse.', 'Search, compare and currency conversion behind named MCP tool contracts. The trace shows each tool name, its inputs and its result.'],
  ['Retrieval', 'Find trips by meaning.', 'pgvector similarity and full-text search fused into one candidate list, then reranked by Cohere Rerank 3.5 on Bedrock. One Strands agent chooses among the earlier tools and these, and writes the reply. The trace shows each tool call, the candidate scores and the rerank order.'],
  ['Production', 'Run the concierge on managed infrastructure under policy.', 'The same Strands agent definition, with the same tool names, runs in Bedrock AgentCore Runtime and calls its tools through AgentCore Gateway over MCP; a Cedar policy engine decides every call; AgentCore Memory carries the conversation. A courtesy hold and its confirmation are governed writes.'],
  ['Workflow', 'Recover the intent and verify the outcome.', 'A Strands Graph on its own AgentCore Runtime saves each step as a snapshot in Amazon Aurora, holds a worker lease, and searches, places its hold and, when a later step fails, releases that hold through the Gateway under Cedar. The release is fenced by the run\u2019s lease and journey. If a committed reply is lost, a replacement worker replays the saved intent and reads the original booking and expiry.'],
];

export const TOOLS: [string, string, string][] = [
  ['MeridianCatalog___hybrid_trip_search', 'Read', 'Embeds the query with Cohere Embed v4, takes pgvector and full-text candidates from Amazon Aurora, drops packages over a price ceiling named in the query or with no places left, and reranks with Cohere Rerank 3.5.'],
  ['MeridianCatalog___search_trips', 'Read', 'Filters the catalog by trip type, price ceiling and words, cheapest first. Every value is a bound parameter, never SQL text.'],
  ['MeridianCatalog___compare_packages', 'Read', 'Side-by-side rows for up to four packages.'],
  ['MeridianCatalog___price_range', 'Read', 'Low, average and high price for a destination, from the catalog’s one price per package.'],
  ['MeridianCatalog___region_inventory', 'Read', 'Package count, departure slots left across every duration, and a count by trip type for a region.'],
  ['MeridianCatalog___currency_convert', 'Read', 'Indicative currency conversion, not for settlement.'],
  ['MeridianHolds___get_package_details', 'Read', 'One package with its live durations, availability and highlights, read under the traveler’s RLS scope.'],
  ['MeridianHolds___loyalty_balance', 'Read', 'The signed-in traveler’s saved points balance and tier for a loyalty program, read under the workload grant and row-level security. The platform pins the traveler.'],
  ['MeridianHolds___create_courtesy_hold', 'Write', 'Places a courtesy hold on one package duration. Requires the traveler id, confirmation flag, budget ceiling and journey reference the platform pinned; accepts a saved request id, booking id and worker execution id for replay.'],
  ['MeridianHolds___confirm_booking', 'Write', 'Confirms a held booking for the authorized traveler: catalog inventory in Meridian’s database only, no supplier and no payment. Requires the booking id and the total Aurora holds, plus the traveler id, confirmation flag, budget ceiling and journey reference the platform pinned. A retry returns the original confirmation.'],
  ['MeridianHolds___release_hold', 'Write', 'Gives back one hold of the authorized traveler so its places return to the catalog. Needs the hold request id and the journey id from the hold receipt, plus the traveler id the platform pinned. The Phase 5 workflow calls it to compensate a failed run, and the Lambda checks the run\u2019s lease and journey in the same transaction; the Phase 4 agent is not offered it. A retry of an already released hold answers that it was released.'],
];

const COUNT_WORDS = ['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten',
  'Eleven', 'Twelve'];

/** The tools listed and the Gateway targets they belong to, counted from TOOLS itself. */
export function toolCounts(tools: readonly (readonly string[])[] = TOOLS): { tools: number; targets: number } {
  const targets = new Set(tools.map(([name]) => name.split('___')[0]));
  return { tools: tools.length, targets: targets.size };
}

export function toolsHeading(): string {
  const { tools, targets } = toolCounts();
  const word = (count: number) => COUNT_WORDS[count] ?? String(count);
  return `${word(tools)} MCP tools, ${word(targets).toLowerCase()} Lambda targets`;
}

export const POLICIES: [string, string, string][] = [
  ['meridian_read_tools', 'Any authenticated caller may search packages, read package details and read a loyalty balance. In the signed-in release the traveler binding below limits the loyalty read to the caller’s own traveler.',
   'permit(principal,\n  action in [AgentCore::Action::"MeridianCatalog___search_trips",\n             AgentCore::Action::"MeridianCatalog___compare_packages",\n             AgentCore::Action::"MeridianCatalog___price_range",\n             AgentCore::Action::"MeridianCatalog___region_inventory",\n             AgentCore::Action::"MeridianCatalog___currency_convert",\n             AgentCore::Action::"MeridianCatalog___hybrid_trip_search",\n             AgentCore::Action::"MeridianHolds___get_package_details",\n             AgentCore::Action::"MeridianHolds___loyalty_balance"],\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:<region>:<account-id>:gateway/<gateway-id>");'],
  ['meridian_hold_governance', 'A courtesy hold runs only after the traveler confirmed it, for at most 12 hours, for at most 6 travelers, and within the traveler’s saved budget ceiling. Nothing else permits the hold, so any other call is denied by default.',
   'permit(principal,\n  action == AgentCore::Action::"MeridianHolds___create_courtesy_hold",\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:<region>:<account-id>:gateway/<gateway-id>")\nwhen {\n  context.input.travelerConfirmed == true &&\n  context.input.holdMinutes <= 720 &&\n  context.input.travelers <= 6 &&\n  context.input.totalCents <= context.input.budgetCeilingCents\n};'],
  ['meridian_booking_governance', 'A held booking is confirmed only after the traveler confirmed it and only when its total is within the traveler’s saved budget ceiling. Aurora enforces that the booking is held, unexpired and owned by the traveler. Nothing else permits confirm_booking, so any other call is denied by default.',
   'permit(principal,\n  action == AgentCore::Action::"MeridianHolds___confirm_booking",\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:<region>:<account-id>:gateway/<gateway-id>")\nwhen {\n  context.input.travelerConfirmed == true &&\n  context.input.totalCents <= context.input.budgetCeilingCents\n};'],
  ['meridian_release_governance', 'A hold is released only when the call names a hold request and a journey. The holds Lambda checks that the hold exists and belongs to the pinned traveler, because Cedar sees arguments, not rows. Nothing else permits release_hold, so any other call is denied by default.',
   'permit(principal, action == AgentCore::Action::"MeridianHolds___release_hold", resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:<region>:<account-id>:gateway/<gateway-id>") when { context.input.holdRequestId != "" && context.input.journeyId != "" };'],
  ['meridian_traveler_binding', 'Used only in the signed-in release, and left out if the interceptor design is chosen. A hold, a booking, a release or a loyalty read runs only when its travelerId equals the traveler_id claim of the caller’s access token. A token without that claim, or a call without that argument, is denied. The permits above still decide everything else.',
   'forbid(principal is AgentCore::OAuthUser,\n  action in [AgentCore::Action::"MeridianHolds___loyalty_balance",\n             AgentCore::Action::"MeridianHolds___create_courtesy_hold",\n             AgentCore::Action::"MeridianHolds___confirm_booking",\n             AgentCore::Action::"MeridianHolds___release_hold"],\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:<region>:<account-id>:gateway/<gateway-id>")\nunless {\n  principal.hasTag("traveler_id") &&\n  context.input has travelerId &&\n  principal.getTag("traveler_id") != "" &&\n  principal.getTag("traveler_id") == context.input.travelerId\n};'],
];

export const CONTROLS: [string, string][] = [
  ['Check access at every hop', 'The backend, both Runtimes and the Gateway each check the caller before acting, and the database applies its own row-level security again.'],
  ['Authorize the traveler', 'traveler_identity_bindings in Aurora grants that subject a traveler. A missing grant fails before any row-level scope is set, and both allow and deny land in traveler_access_audit.'],
  ['Scope every row', 'Row-Level Security filters rows to the authorized traveler under the least-privilege meridian_app role, inside one Data API transaction.'],
  ['Decide every tool call', 'AgentCore Gateway serves the tools over MCP; its Cedar policy engine, MeridianGovernance in ENFORCE mode, decides each call on the arguments before any Lambda runs.'],
  ['Make the writer a workload too', 'The MeridianHolds Lambda holds its own grant, sets the traveler scope, steps down to meridian_app and calls create_courtesy_hold or confirm_booking, so a retried call replays the same booking or the same confirmation.'],
];
