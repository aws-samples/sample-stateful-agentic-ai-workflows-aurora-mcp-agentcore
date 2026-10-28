import { Stack, Token, aws_iam as iam } from 'aws-cdk-lib';

/**
 * Amazon Bedrock grants for the App Runner backend: exactly the models it calls, and no wildcard.
 *
 * Inventory (each id is read from the App Runner environment, with the code's default):
 * - Chat, `BEDROCK_MODEL_ID` (default `global.anthropic.claude-sonnet-5-5`): ConverseStream from
 *   the Strands agents (`backend/bedrock_model.py`). Reached only through its global inference
 *   profile, with the three statements AWS documents under "IAM policy requirements for
 *   global cross-Region inference". Converse needs `bedrock:InvokeModel` and ConverseStream needs
 *   `bedrock:InvokeModelWithResponseStream`.
 * - Embeddings, `EMBEDDING_MODEL` (default `cohere.embed-v4:0`), and rerank, `RERANK_MODEL`
 *   (default `cohere.rerank-v3-5:0`): InvokeModel in `backend/db/embedding_service.py`, by the
 *   in-Region model id, so each is granted its Regional foundation-model ARN. The rerank call is
 *   InvokeModel on the rerank model, not the Rerank API, so `bedrock:Rerank` is not granted.
 *
 * Nothing calls CountTokens (Strands counts tokens locally unless `use_native_token_count` is
 * set, and Meridian never sets it), so it is not granted either.
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

export const DEFAULT_BEDROCK_MODEL_ID = 'global.anthropic.claude-sonnet-5-5';
export const DEFAULT_EMBEDDING_MODEL_ID = 'cohere.embed-v4:0';
export const DEFAULT_RERANK_MODEL_ID = 'cohere.rerank-v3-5:0';
const GLOBAL_PREFIX = 'global.';
const CHAT_ACTIONS = ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'];

/** Where the ARNs live: the roles stack's own partition, Region and account. */
interface ArnScope {
  partition: string;
  region: string;
  account: string;
}

/**
 * Invoke one model only through its global inference profile, pinned to the stack's Region.
 *
 * @param profileId - The global inference profile id, `global.<foundation model id>`.
 * @param at - The partition, Region and account of the role.
 * @returns The profile, Regional foundation model and Region-agnostic foundation model
 *   statements, each with the conditions AWS documents.
 * @throws Error when `profileId` is not a global inference profile id.
 */
export function globalProfileStatements(
  profileId: string,
  at: ArnScope,
): iam.PolicyStatement[] {
  if (!profileId.startsWith(GLOBAL_PREFIX)) {
    throw new Error(
      `BEDROCK_MODEL_ID must be a global inference profile id (global.<model>), not ` +
        `'${profileId}'; set BEDROCK_MODEL_ID=${DEFAULT_BEDROCK_MODEL_ID} in meridian/.env`,
    );
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
export function inRegionModelStatement(modelIds: string[], at: ArnScope): iam.PolicyStatement {
  const arn = (id: string) => `arn:${at.partition}:bedrock:${at.region}::foundation-model/${id}`;
  return new iam.PolicyStatement({
    actions: ['bedrock:InvokeModel'],
    resources: modelIds.map(arn),
  });
}

/**
 * Every Amazon Bedrock statement the App Runner backend needs.
 *
 * @param environment - The App Runner environment from serviceEnvironment().
 * @param stack - The roles stack; its Region pins every ARN and condition.
 * @returns The global profile statements for the chat model and one statement for the
 *   in-Region embedding and rerank models.
 * @throws Error when `BEDROCK_REGION` names another Region than the stack's.
 */
export function backendModelStatements(
  environment: Record<string, string>,
  stack: Stack,
): iam.PolicyStatement[] {
  const { partition, region, account } = stack;
  const bedrockRegion = environment.BEDROCK_REGION;
  if (bedrockRegion && !Token.isUnresolved(region) && bedrockRegion !== region) {
    throw new Error(
      `BEDROCK_REGION '${bedrockRegion}' differs from the stack Region ${region}; the grants are ` +
        'pinned to the stack Region, so unset BEDROCK_REGION or deploy to that Region',
    );
  }
  const at = { partition, region, account };
  return [
    ...globalProfileStatements(environment.BEDROCK_MODEL_ID || DEFAULT_BEDROCK_MODEL_ID, at),
    inRegionModelStatement(
      [
        environment.EMBEDDING_MODEL || DEFAULT_EMBEDDING_MODEL_ID,
        environment.RERANK_MODEL || DEFAULT_RERANK_MODEL_ID,
      ],
      at,
    ),
  ];
}
