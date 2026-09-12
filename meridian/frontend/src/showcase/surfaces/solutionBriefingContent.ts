import type { ServiceMarkName } from '../components/ServiceMark';

// Implementation details shared by the compact briefing and its disclosures.
export const PHASES: [string, string, string][] = [
  ['SQL', 'Ground the assistant in live rows.', 'Parameterised filters over trip_packages in Aurora PostgreSQL through the RDS Data API. The trace shows the SQL that ran and the rows it returned.'],
  ['MCP', 'Give the agent tools it can reuse.', 'Search, compare and currency conversion behind named MCP tool contracts. The trace shows each tool name, its inputs and its result.'],
  ['Retrieval', 'Find trips by meaning.', 'pgvector similarity and full-text search fused into one candidate list, then reranked by Cohere Rerank 3.5 on Bedrock. The trace shows candidate scores and the rerank order.'],
  ['Production', 'Run the concierge on managed infrastructure under policy.', 'A Strands agent in Bedrock AgentCore Runtime calls its tools through AgentCore Gateway over MCP; a Cedar policy engine decides every call; AgentCore Memory carries the conversation. A courtesy hold and its confirmation are governed writes.'],
  ['Workflow', 'Make multi-step work survive a dead worker.', 'A LangGraph state graph checkpoints every node into Aurora, holds a worker lease, and places its hold through the same gateway tool. Kill the worker; a second one resumes the same thread and finds one hold.'],
];

export const MODEL_DECIDES: [string, string][] = [
  ['Interpretation', 'What the traveler is asking for: destination, duration, party size, the preferences worth recalling.'],
  ['Sequencing', 'Which gateway tool to call next, with which arguments drawn from the search results.'],
  ['Prose', 'The reply the traveler reads, written from the tool results and the recalled facts.'],
];

export const CODE_DECIDES: [string, string][] = [
  ['Who the caller is', 'STS or AgentCore Identity names the workload; Aurora binds that subject to a traveler before any row is read.'],
  ['What a hold may cost', 'The budget ceiling comes from the traveler’s saved budget fact, read under RLS; the runtime pins it onto the hold call. The model never chooses it. The travel brief and the confirmation dialog show that same saved cap and the party ceiling derived from it, so what a traveler reads is the basis Cedar judged.'],
  ['Whether a hold runs', 'The traveler’s confirmation flag and the ceiling travel as tool arguments; Cedar evaluates them before the Lambda runs.'],
  ['Whether a booking is confirmed', 'The traveler confirms the held trip in the concierge. The backend reads the booking total under RLS, the runtime pins the confirmation and the ceiling, Cedar decides, and Aurora flips the same booking row from held to confirmed. No supplier, no payment.'],
  ['Inventory and replay', 'create_courtesy_hold in Aurora takes the capacity lock, decrements seats and replays an identical request instead of holding twice.'],
];

export const TOOLS: [string, string, string][] = [
  ['SemanticTripSearchLambda___semantic_trip_search', 'Read', 'Embeds the query with Cohere Embed v4, searches pgvector and full-text indexes in Aurora, and returns ranked packages with scores.'],
  ['MeridianHolds___get_package_details', 'Read', 'One package with its live durations, availability and highlights, read under the traveler’s RLS scope.'],
  ['MeridianHolds___create_courtesy_hold', 'Write', 'Places a courtesy hold on one package duration. Requires the traveler id, confirmation flag, budget ceiling and journey reference the platform pinned; accepts a checkpointed request id, booking id and worker execution id for replay.'],
  ['MeridianHolds___confirm_booking', 'Write', 'Confirms a held booking for the authorized traveler: catalog inventory in Meridian’s database only, no supplier and no payment. Requires the booking id and the total Aurora holds, plus the traveler id, confirmation flag, budget ceiling and journey reference the platform pinned. A retry returns the original confirmation.'],
];

export const POLICIES: [string, string, string][] = [
  ['meridian_read_tools', 'Any authenticated caller may search packages and read package details.',
   'permit(principal,\n  action in [AgentCore::Action::"SemanticTripSearchLambda___semantic_trip_search",\n             AgentCore::Action::"MeridianHolds___get_package_details"],\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:...:gateway/meridianv2-meridian-aurora-temzt21jg0");'],
  ['meridian_hold_governance', 'A courtesy hold runs only after the traveler confirmed it, for at most 12 hours, for at most 6 travelers, and within the traveler’s saved budget ceiling. Nothing else permits the hold, so any other call is denied by default.',
   'permit(principal,\n  action == AgentCore::Action::"MeridianHolds___create_courtesy_hold",\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:...:gateway/meridianv2-meridian-aurora-temzt21jg0")\nwhen {\n  context.input.travelerConfirmed == true &&\n  context.input.holdMinutes <= 720 &&\n  context.input.travelers <= 6 &&\n  context.input.totalCents <= context.input.budgetCeilingCents\n};'],
  ['meridian_booking_governance', 'A held booking is confirmed only after the traveler confirmed it and only when its total is within the traveler’s saved budget ceiling. Aurora enforces that the booking is held, unexpired and owned by the traveler. Nothing else permits confirm_booking, so any other call is denied by default.',
   'permit(principal,\n  action == AgentCore::Action::"MeridianHolds___confirm_booking",\n  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-east-1:...:gateway/meridianv2-meridian-aurora-temzt21jg0")\nwhen {\n  context.input.travelerConfirmed == true &&\n  context.input.totalCents <= context.input.budgetCeilingCents\n};'],
];

export const CONTROLS: [string, string][] = [
  ['Authenticate the workload', 'AgentCore Identity or AWS STS names the caller: the backend, the runtime, or the holds Lambda, each with its own role.'],
  ['Authorize the traveler', 'traveler_identity_bindings in Aurora grants that subject a traveler. A missing grant fails before any row-level scope is set, and both allow and deny land in traveler_access_audit.'],
  ['Scope every row', 'Row-Level Security filters rows to the authorized traveler under the least-privilege meridian_app role, inside one Data API transaction.'],
  ['Decide every tool call', 'AgentCore Gateway serves the tools over MCP with SigV4; its Cedar policy engine, MeridianGovernance in ENFORCE mode, decides each call on the arguments before any Lambda runs.'],
  ['Make the writer a workload too', 'The MeridianHolds Lambda holds its own grant, sets the traveler scope, steps down to meridian_app and calls create_courtesy_hold or confirm_booking, so a retried call replays the same booking or the same confirmation.'],
];

export const SERVICE_MARKS: Record<string, ServiceMarkName> = {
  'Amazon Aurora PostgreSQL': 'aurora',
  'Amazon Bedrock AgentCore Runtime': 'agentcore',
  'Amazon Bedrock AgentCore Gateway': 'agentcore',
  'Amazon Bedrock AgentCore Policy': 'agentcore',
  'Amazon Bedrock AgentCore Memory': 'agentcore',
  'Amazon Bedrock': 'bedrock',
};

export const SERVICES: [string, string][] = [
  ['Amazon Aurora PostgreSQL', 'Catalog, traveler profile and preferences, identity bindings and audit, LangGraph checkpoints, journeys, leases and holds. pgvector HNSW for retrieval; RLS for scope; the RDS Data API as the connectionless transport.'],
  ['Amazon Bedrock AgentCore Runtime', 'Hosts the Phase 4 Strands agent in its own microVM with the AWS Distro for OpenTelemetry attached.'],
  ['Amazon Bedrock AgentCore Gateway', 'Serves the four tools over MCP with IAM authorization and names them Target___tool.'],
  ['Amazon Bedrock AgentCore Policy', 'Cedar policy engine attached to the gateway in ENFORCE mode; default deny.'],
  ['Amazon Bedrock AgentCore Memory', 'Semantic memory strategy over the concierge session, namespaced per traveler and conversation.'],
  ['Amazon Bedrock', 'Configured Bedrock models for the agents; Cohere Embed v4 and Cohere Rerank 3.5 for retrieval.'],
  ['AWS Lambda', 'The semantic search target and the MeridianHolds target behind the gateway.'],
  ['Amazon CloudWatch', 'ADOT spans and structured logs in the runtime’s log group; every Phase 4 span in the trace panel links to its trace id.'],
  ['AWS App Runner and Amazon CloudFront', 'The published site: the FastAPI backend as a container, the Vite build in S3, basic authentication and the API bearer token at the edge.'],
];
