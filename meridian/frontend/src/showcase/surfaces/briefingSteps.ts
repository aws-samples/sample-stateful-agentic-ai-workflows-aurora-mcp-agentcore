// What each step of the Solution briefing's paths says. One idea per step, true of the code:
// Phases 1 and 2 in backend/routers/chat.py, Phase 3 in backend/retrieval/hybrid.py, Phase 4 in the
// Concierge Runtime, Gateway and holds Lambda, Phase 5 in backend/agents/phase_05_workflow
// (graph.py for the route and the review pause, runner.py for the lease each execution releases).
import type { ServiceMarkName } from '../components/ServiceMark';

export type Hop = {
  title: string;
  detail: string;
  service?: ServiceMarkName;
  glyph?: string;
  branches?: [string, string][];
  image?: { src: string; alt: string };
};
export type PathStep = { hop: number; chip: string; caption: string };
export type StepPathData = { label: string; hops: Hop[]; steps: PathStep[] };

export const PHASE_PATHS: Record<1 | 2 | 3 | 4, StepPathData> = {
  1: {
    label: 'Phase 1 SQL path',
    hops: [
      { glyph: '?', title: 'Question', detail: 'Destination + budget' },
      { service: 'app-runner', title: 'FastAPI', detail: 'Parameterized query' },
      { service: 'aurora', title: 'Amazon Aurora', detail: 'Data API → rows' },
    ],
    steps: [
      { hop: 0, chip: 'question', caption: 'The traveler asks for trips by destination and budget.' },
      { hop: 1, chip: 'keywords + price', caption: 'FastAPI reads the keywords and the price. No model runs here.' },
      { hop: 1, chip: 'bound parameters', caption: 'It runs one parameterized query. Every value is a bound parameter, never SQL text.' },
      { hop: 2, chip: 'rows', caption: 'Aurora answers over the RDS Data API with exact rows.' },
    ],
  },
  2: {
    label: 'Phase 2 MCP path',
    hops: [
      { service: 'app-runner', title: 'FastAPI', detail: 'MCP client' },
      { glyph: '{ }', title: 'Concierge MCP', detail: 'Compare + convert' },
      { service: 'aurora', title: 'Catalog + rates', detail: 'Aurora + sample FX' },
    ],
    steps: [
      { hop: 0, chip: 'tool call', caption: 'FastAPI calls the search as a named tool, search_trips, in code, with no model choosing.' },
      { hop: 1, chip: 'typed inputs', caption: 'The Concierge MCP server checks the tool’s typed inputs.' },
      { hop: 1, chip: 'more tools', caption: 'Compare and currency conversion are tools on the same server.' },
      { hop: 2, chip: 'records', caption: 'Aurora returns the catalog records. The server converts currency from a fixed table of sample rates.' },
    ],
  },
  3: {
    label: 'Phase 3 hybrid retrieval path',
    hops: [
      { service: 'bedrock', title: 'Embed the query', detail: 'Cohere Embed v4' },
      {
        service: 'aurora', title: 'Search both ways', detail: 'Merge candidates by package ID',
        branches: [['Meaning', 'Query vector → pgvector'], ['Words', 'Query text → tsvector']],
      },
      { service: 'bedrock', title: 'Rerank', detail: 'Cohere Rerank 3.5' },
      {
        title: 'Bali Rice Terrace Retreat', detail: 'Example candidate BCH-003',
        image: { src: '/travel/catalog/BCH-003.jpg', alt: 'Green rice terraces and palms in Bali' },
      },
    ],
    steps: [
      { hop: 0, chip: 'query vector', caption: 'Cohere Embed v4 on Amazon Bedrock turns the meaning of the query into a vector.' },
      { hop: 1, chip: 'by meaning', caption: 'Aurora finds candidates by meaning, comparing the query vector with pgvector.' },
      { hop: 1, chip: 'by words', caption: 'It also finds them by words, matching the query text with tsvector.' },
      { hop: 1, chip: 'merged', caption: 'The two lists merge by package ID.' },
      { hop: 2, chip: 'eligible', caption: 'Before reranking, trips over a price named in the query or with no places left are dropped.' },
      { hop: 2, chip: 'ranked', caption: 'Cohere Rerank 3.5 reads the query and the candidates together to order them.' },
      { hop: 3, chip: 'top match', caption: 'In this example, BCH-003 ranks first: an Ubud villa, yoga mornings and a beach club day.' },
    ],
  },
  4: {
    label: 'Phase 4 governed action path',
    hops: [
      { service: 'cognito', title: 'Amazon Cognito', detail: 'Traveler sign-in' },
      { service: 'agentcore-runtime', title: 'AgentCore Runtime', detail: 'Strands agent + Memory' },
      { service: 'agentcore-gateway', title: 'AgentCore Gateway', detail: 'Token, traveler pin, Cedar' },
      { service: 'lambda', title: 'AWS Lambda', detail: 'Holds tool' },
      { service: 'aurora', title: 'Amazon Aurora', detail: 'Hold + action audit' },
    ],
    steps: [
      { hop: 0, chip: 'access token', caption: 'The traveler signs in, and Amazon Cognito issues an access token.' },
      { hop: 1, chip: 'confirmed', caption: 'The traveler clicks Hold to confirm the trip shown: package, nights, party size and price.' },
      { hop: 1, chip: 'pinned call', caption: 'The Runtime, not the model, calls the hold tool with exactly those values, and pins the traveler, the confirmation and the saved budget.' },
      { hop: 2, chip: 'token checked', caption: 'The Gateway checks the token and pins the traveler.' },
      { hop: 2, chip: 'Cedar decision', caption: 'Cedar decides whether the hold may run.' },
      { hop: 3, chip: 'grant checked', caption: 'The holds Lambda checks its grant for this traveler.' },
      { hop: 4, chip: 'inventory', caption: 'Aurora checks inventory, ownership and expiry.' },
      { hop: 4, chip: 'audit row', caption: 'Aurora commits the hold with its action audit row.' },
    ],
  },
};

// The recovery route through the graph; memory_recall, the seventh node, serves other requests.
// Plain step names; the small line names the graph node in code.
export type WorkflowNode = { title: string; code: string; note?: string; worker: 1 | 2 };
export const WORKFLOW_NODES: WorkflowNode[] = [
  { title: 'Classify', code: 'classify', worker: 1 },
  { title: 'Find alternatives', code: 'search', note: 'may pause', worker: 1 },
  { title: 'Check places', code: 'availability', worker: 2 },
  { title: 'Save hold IDs', code: 'prepare_hold', worker: 2 },
  { title: 'Place hold', code: 'hold', worker: 2 },
  { title: 'Show results', code: 'synthesize', worker: 2 },
];

export type WorkflowStep = {
  caption: string;
  saved: number;
  next?: number;
  paused?: boolean;
  session1: 'live' | 'stopped';
  session2: boolean;
  lease: string;
  restored?: boolean;
};

const live = { session1: 'live', session2: false } as const;
const resumed = { session1: 'stopped', session2: true } as const;
const stopped = { session1: 'stopped', session2: false } as const;

// LEASE_SECONDS and HEARTBEAT_SECONDS in backend/agents/phase_05_workflow/runner.py: 60 and 10.
export const LEASE_NOTE = 'The lease lets one worker at a time run this recovery. It lapses after 60 seconds without a heartbeat.';

export const WORKFLOW_STEPS: WorkflowStep[] = [
  { ...live, saved: 0, lease: 'Not taken yet', caption: 'The traveler starts recovery for the canceled flight.' },
  { ...live, saved: 0, next: 0, lease: 'Held by worker 1', caption: 'Worker 1 takes the lease, so no other worker can run this recovery at the same time.' },
  { ...live, saved: 1, next: 1, lease: 'Held by worker 1', caption: 'Classify picks the recovery route, and the step is saved.' },
  { ...live, saved: 2, next: 2, lease: 'Held by worker 1', caption: 'Find alternatives searches through the Gateway, and the step is saved.' },
  { ...live, saved: 2, paused: true, lease: 'Held by worker 1', caption: 'The run pauses so the traveler can review and confirm the shortlist.' },
  { ...live, saved: 2, paused: true, lease: 'Free while paused', caption: 'While it waits, the run gives up its lease.' },
  { ...stopped, saved: 2, paused: true, lease: 'Free while paused', caption: 'The Runtime session is stopped: worker 1 and its microVM are gone.' },
  { ...stopped, saved: 2, paused: true, lease: 'Free while paused', caption: 'The saved steps are still in Aurora.' },
  { ...resumed, saved: 2, lease: 'Held by worker 2', caption: 'Resume: worker 2 starts in a new microVM and takes the lease.' },
  { ...resumed, saved: 2, next: 2, restored: true, lease: 'Held by worker 2', caption: 'It loads the newest saved step and continues at Check places.' },
  { ...resumed, saved: 3, next: 3, lease: 'Held by worker 2', caption: 'Check places rechecks stock in Aurora, and the step is saved.' },
  { ...resumed, saved: 4, next: 4, lease: 'Held by worker 2', caption: 'Save hold IDs stores the request and booking IDs before the write, so a retry finds the same hold instead of making a second one.' },
  { ...resumed, saved: 5, next: 5, lease: 'Held by worker 2', caption: 'Place hold goes through the Gateway to Lambda, which writes the hold only if worker 2 still holds the lease.' },
  { ...resumed, saved: 6, lease: 'Released, run finished', caption: 'Show results reads the hold back from Aurora, and the run ends with one hold under the booking ID saved before the write.' },
];

// The five checks, one row each, with the control that makes the decision.
export type BoundaryCheck = {
  verb: string; service: string; mark: ServiceMarkName; question: string; line: string; pill: string;
};
export const BOUNDARY_CHECKS: BoundaryCheck[] = [
  { verb: 'Verify', service: 'Application', mark: 'app-runner', question: 'Who is asking?', line: 'Validate the token and identify the user', pill: 'Cognito JWT' },
  { verb: 'Bind', service: 'AgentCore Runtime', mark: 'agentcore-runtime', question: 'Whose request is this?', line: 'Bind the request to the verified user', pill: 'JWT authorizer' },
  { verb: 'Decide', service: 'AgentCore Gateway', mark: 'agentcore-gateway', question: 'Is this action allowed?', line: 'Check identity and action limits with Cedar', pill: 'Cedar policy' },
  { verb: 'Execute', service: 'AWS Lambda', mark: 'lambda', question: 'May this service act for the user?', line: 'Check the service\u2019s grant before acting', pill: 'Grant table' },
  { verb: 'Enforce', service: 'Amazon Aurora', mark: 'aurora', question: 'Which rows may it touch?', line: 'Apply a least-privilege role and row-level security', pill: 'RLS policies' },
];

// Where each staged refusal stops, read from Aurora on 22 Nov 2026 (dbstage R): the over-budget hold
// is refused by policy and its turn records it; the second traveler's call passes the Gateway and
// the holds Lambda writes the deny (no active identity binding) to traveler_access_audit.
export type ScenarioId = 'within' | 'budget' | 'other';
export type Scenario = {
  id: ScenarioId;
  label: string;
  stopAt: number | null;
  chip: string;
  title: string;
  text: string;
};

export const SCENARIOS: Scenario[] = [
  {
    id: 'within', label: 'Hold within budget', stopAt: null, chip: 'hold request', title: 'Committed',
    text: 'Signed in and within the saved budget, the hold passes all five checks, and Aurora commits it with its action audit row.',
  },
  {
    id: 'budget', label: 'Over the saved budget', stopAt: 2, chip: 'over budget', title: 'Refused by Cedar',
    text: 'The total is over the traveler’s saved budget, so no Cedar permit matches and the call is denied by default. The Lambda never runs, and the traveler’s turn records the refusal.',
  },
  {
    id: 'other', label: 'Second traveler', stopAt: 3, chip: 'second traveler', title: 'Refused by the grant check',
    text: 'A second traveler signs in as themselves and asks for a hold. The call passes Cedar, but the holds Lambda has no grant for that traveler, so it refuses before writing, and the deny is recorded in traveler_access_audit.',
  },
];
