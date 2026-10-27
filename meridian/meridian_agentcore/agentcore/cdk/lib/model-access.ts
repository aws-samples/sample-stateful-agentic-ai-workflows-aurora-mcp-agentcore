import { type AgentCoreApplication, type AgentCoreProjectSpec } from '@aws/agentcore-cdk';
import { Aspects, Stack, aws_iam as iam } from 'aws-cdk-lib';

/**
 * Amazon Bedrock grants for the Runtime execution roles: each role may invoke exactly the models
 * its own Runtime calls, and the AgentCore construct's grant on `foundation-model/*` and
 * `inference-profile/*` in every Region is removed.
 *
 * Inventory:
 * - MeridianConcierge calls one chat model, its own `BEDROCK_MODEL_ID`
 *   (`global.anthropic.claude-sonnet-5-5` in the template), with ConverseStream through Strands
 *   (`app/MeridianConcierge/model/load.py`). It is reached only through its global inference
 *   profile, with the three statements AWS documents under "IAM policy requirements for global
 *   cross-Region inference". ConverseStream needs `bedrock:InvokeModelWithResponseStream` and
 *   Converse needs `bedrock:InvokeModel`.
 * - MeridianWorkflow calls no chat model. Its search and memory recall call InvokeModel on the
 *   embedding model, `EMBEDDING_MODEL` (default `cohere.embed-v4:0`), and on the rerank model,
 *   `RERANK_MODEL` (default `cohere.rerank-v3-5:0`), in `backend/db/embedding_service.py`, staged
 *   into the bundle. Each is granted its Regional foundation-model ARN. The rerank call is
 *   InvokeModel on the rerank model, not the Rerank API, so `bedrock:Rerank` is not granted.
 * - A Runtime that sets its own `BEDROCK_MODEL_ID` is granted that model the same way.
 *
 * Nothing calls CountTokens (Strands counts tokens locally unless `use_native_token_count` is
 * set, and Meridian never sets it), so the construct's CountTokens grant goes too.
 *
 * AWS docs:
 * - https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html
 * - https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5-5.html
 * - https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
 * - https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModel.html
 * - https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed.html
 * - https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-cohere-rerank-3-5.html
 * - https://docs.aws.amazon.com/bedrock/latest/userguide/rerank-prereq.html (the Rerank API,
 *   which Meridian does not call)
 */

export const DEFAULT_EMBEDDING_MODEL_ID = 'cohere.embed-v4:0';
export const DEFAULT_RERANK_MODEL_ID = 'cohere.rerank-v3-5:0';
const MODEL_ENV = 'BEDROCK_MODEL_ID';
const GLOBAL_PREFIX = 'global.';
const CHAT_ACTIONS = ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'];
/** Runtimes whose code calls a chat model, so each must name it in its own BEDROCK_MODEL_ID. */
const CHAT_RUNTIMES = ['MeridianConcierge'];
/** Runtimes whose code embeds queries and reranks results. */
const RETRIEVAL_RUNTIMES = ['MeridianWorkflow'];
/** A rendered resource that ends in `foundation-model/*` or `inference-profile/*`. */
const WILDCARD_MODEL_ARN = /:(foundation-model|inference-profile)\/\*"/;

type EnvVars = Record<string, string>;

/** Where the ARNs live: the stack's own partition, Region and account. */
interface ArnScope {
  partition: string;
  region: string;
  account: string;
}

/**
 * Grant each Runtime execution role exactly the models its Runtime calls.
 *
 * @param app - The application whose Runtimes get the grants.
 * @param spec - The rendered project; each Runtime's own environment names its models.
 * @throws Error when a chat Runtime has no `BEDROCK_MODEL_ID`, the id is not a global inference
 *   profile, or a Runtime's execution role is not owned by this stack.
 */
export function grantModelAccess(app: AgentCoreApplication, spec: AgentCoreProjectSpec): void {
  for (const runtime of spec.runtimes) {
    const role = app.environments.get(runtime.name)?.runtime.role;
    if (!(role instanceof iam.Role)) {
      throw new Error(`The ${runtime.name} Runtime's execution role is not created by this stack`);
    }
    const env: EnvVars = Object.fromEntries((runtime.envVars ?? []).map(v => [v.name, v.value]));
    for (const statement of runtimeModelStatements(runtime.name, env, Stack.of(role))) {
      role.addToPolicy(statement);
    }
    dropWildcardModelGrants(role);
  }
}

function runtimeModelStatements(name: string, env: EnvVars, stack: Stack): iam.PolicyStatement[] {
  const at = { partition: stack.partition, region: stack.region, account: stack.account };
  const modelId = env[MODEL_ENV];
  if (!modelId && CHAT_RUNTIMES.includes(name)) {
    throw new Error(`Render the ${name} Runtime with ${MODEL_ENV} before deploying`);
  }
  const statements = modelId ? globalProfileStatements(modelId, at) : [];
  if (RETRIEVAL_RUNTIMES.includes(name)) {
    const embedding = env.EMBEDDING_MODEL || DEFAULT_EMBEDDING_MODEL_ID;
    const rerank = env.RERANK_MODEL || DEFAULT_RERANK_MODEL_ID;
    statements.push(inRegionModelStatement([embedding, rerank], at));
  }
  return statements;
}

/** The three statements AWS documents for a model reached only through its global profile. */
function globalProfileStatements(profileId: string, at: ArnScope): iam.PolicyStatement[] {
  if (!profileId.startsWith(GLOBAL_PREFIX)) {
    const expected = 'a global inference profile id (global.<model>)';
    throw new Error(`${MODEL_ENV} must be ${expected}, not '${profileId}'`);
  }
  const model = profileId.slice(GLOBAL_PREFIX.length);
  const bedrock = `arn:${at.partition}:bedrock`;
  const profileArn = `${bedrock}:${at.region}:${at.account}:inference-profile/${profileId}`;
  const viaProfile = { 'bedrock:InferenceProfileArn': profileArn };
  const allow = (resource: string, conditions: Record<string, string>) =>
    new iam.PolicyStatement({
      actions: CHAT_ACTIONS,
      resources: [resource],
      conditions: { StringEquals: conditions },
    });
  return [
    allow(profileArn, { 'aws:RequestedRegion': at.region }),
    allow(`${bedrock}:${at.region}::foundation-model/${model}`, {
      'aws:RequestedRegion': at.region,
      ...viaProfile,
    }),
    allow(`${bedrock}:::foundation-model/${model}`, {
      'aws:RequestedRegion': 'unspecified',
      ...viaProfile,
    }),
  ];
}

/** InvokeModel on models the code calls by their in-Region id, such as embeddings and rerank. */
function inRegionModelStatement(modelIds: string[], at: ArnScope): iam.PolicyStatement {
  const arn = (id: string) => `arn:${at.partition}:bedrock:${at.region}::foundation-model/${id}`;
  return new iam.PolicyStatement({
    actions: ['bedrock:InvokeModel'],
    resources: modelIds.map(arn),
  });
}

/**
 * Remove the construct's Bedrock grant on every model and profile from the role's default policy.
 * The construct offers no option to leave it out, so its rendered document is corrected the way
 * `policy-compatibility.ts` corrects the obsolete action.
 */
function dropWildcardModelGrants(role: iam.Role): void {
  const policy = role.node.tryFindChild('DefaultPolicy')?.node.defaultChild;
  if (!(policy instanceof iam.CfnPolicy)) {
    throw new Error(`${role.node.path} has no default policy to correct`);
  }
  Aspects.of(policy).add({
    visit(): void {
      const document = Stack.of(policy).resolve(policy.policyDocument);
      const statements = document.Statement.flatMap(withoutWildcardModels);
      policy.policyDocument = { ...document, Statement: statements };
    },
  });
}

function withoutWildcardModels(statement: Record<string, any>): Record<string, any>[] {
  if (![statement.Action].flat().some((action: string) => action.startsWith('bedrock:'))) {
    return [statement];
  }
  const resources = [statement.Resource].flat();
  const kept = resources.filter(resource => !WILDCARD_MODEL_ARN.test(JSON.stringify(resource)));
  if (kept.length === resources.length) return [statement];
  return kept.length ? [{ ...statement, Resource: kept }] : [];
}
