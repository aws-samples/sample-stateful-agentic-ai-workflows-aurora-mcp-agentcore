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
      { hop: 1, chip: 'approved terms', caption: 'The traveler approves the hold terms.' },
      { hop: 1, chip: 'pinned terms', caption: 'The Runtime sends exactly those terms, with the traveler, confirmation and saved budget pinned.' },
      { hop: 2, chip: 'token checked', caption: 'The Gateway checks the token and pins the traveler.' },
      { hop: 2, chip: 'Cedar decision', caption: 'Cedar decides whether the hold may run.' },
      { hop: 3, chip: 'grant checked', caption: 'The holds Lambda checks its grant for this traveler.' },
      { hop: 4, chip: 'inventory', caption: 'Aurora checks inventory, ownership and expiry.' },
      { hop: 4, chip: 'receipt', caption: 'Aurora commits the hold with its action audit row.' },
    ],
  },
};

// The recovery route through the graph; memory_recall, the seventh node, serves other requests.
export const WORKFLOW_NODES: [title: string, detail: string][] = [
  ['Classify', 'Keyword rules, no model'],
  ['Search', 'Gateway search, may pause'],
  ['Availability', 'Recheck stock in Aurora'],
  ['Prepare hold', 'Confirm, then save intent'],
  ['Hold', 'Gateway to Lambda'],
  ['Synthesize', 'Read the saved result'],
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

export const WORKFLOW_STEPS: WorkflowStep[] = [
  { ...live, saved: 0, lease: 'Not claimed yet', caption: 'The traveler starts recovery for the canceled flight.' },
  { ...live, saved: 0, next: 0, lease: 'Held by session 1', caption: 'Runtime session 1 claims the worker lease.' },
  { ...live, saved: 1, next: 1, lease: 'Held by session 1', caption: 'Classify runs with keyword rules, and the step is saved.' },
  { ...live, saved: 2, next: 2, lease: 'Held by session 1', caption: 'Search runs through the Gateway, and the step is saved.' },
  { ...live, saved: 2, paused: true, lease: 'Held by session 1', caption: 'The run pauses so the traveler can review the shortlist.' },
  { ...live, saved: 2, paused: true, lease: 'Free, run paused', caption: 'The paused execution releases its lease.' },
  { session1: 'stopped', session2: false, saved: 2, paused: true, lease: 'Free, run paused', caption: 'Stop runtime session: the microVM is gone.' },
  { session1: 'stopped', session2: false, saved: 2, paused: true, lease: 'Free, run paused', caption: 'The saved steps are still in Aurora.' },
  { ...resumed, saved: 2, lease: 'Held by session 2', caption: 'Resume: a new microVM claims the next attempt.' },
  { ...resumed, saved: 2, next: 2, restored: true, lease: 'Held by session 2', caption: 'It restores the newest saved step and continues at Availability.' },
  { ...resumed, saved: 3, next: 3, lease: 'Held by session 2', caption: 'Availability rechecks stock in Aurora, and the step is saved.' },
  { ...resumed, saved: 4, next: 4, lease: 'Held by session 2', caption: 'Prepare hold saves the request and booking IDs before the write.' },
  { ...resumed, saved: 5, next: 5, lease: 'Held by session 2', caption: 'Hold goes through the Gateway to Lambda, which checks the lease inside the transaction.' },
  { ...resumed, saved: 6, lease: 'Released, run succeeded', caption: 'Synthesize reads the hold back from Aurora, and the run ends with one hold under the booking ID saved before the write.' },
];

export const BOUNDARY_LABELS = ['Identity', 'Permission', 'Grant', 'Outcome'];

export const BOUNDARY_HOPS: Hop[] = [
  { service: 'cognito', title: 'Sign-in', detail: 'Token names the traveler' },
  { service: 'agentcore-policy', title: 'Gateway and Cedar', detail: 'May this tool run?' },
  { service: 'lambda', title: 'Holds Lambda', detail: 'Grant for this traveler' },
  { service: 'aurora', title: 'Amazon Aurora', detail: 'What actually committed' },
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
    text: 'Signed in and within the saved budget, the hold passes Cedar and the Lambda’s grant check, and Aurora commits it with its action audit row.',
  },
  {
    id: 'budget', label: 'Over the saved budget', stopAt: 1, chip: 'over budget', title: 'Refused by Cedar',
    text: 'The total is over the traveler’s saved budget, so no Cedar permit matches and the call is denied by default. The Lambda never runs, and the traveler’s turn records the refusal.',
  },
  {
    id: 'other', label: 'Second traveler', stopAt: 2, chip: 'second traveler', title: 'Refused by the grant check',
    text: 'A second traveler signs in as themselves and asks for a hold. The holds Lambda has no grant for that traveler, so it refuses before writing, and the deny is recorded in traveler_access_audit.',
  },
];
