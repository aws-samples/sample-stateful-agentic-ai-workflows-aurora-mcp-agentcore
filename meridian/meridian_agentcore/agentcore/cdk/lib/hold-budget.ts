import { type AgentCoreProjectSpec } from '@aws/agentcore-cdk';
import { CfnFunction } from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

const BUDGET = 'MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS';

/** The CLI's Lambda compute schema has no environment field. Bind the rendered value here. */
export function bindHoldBudget(scope: Construct, spec: AgentCoreProjectSpec): void {
  const ceilings = spec.runtimes.map(runtime => runtime.envVars?.find(v => v.name === BUDGET)?.value);
  const ceiling = ceilings[0];
  if (
    !ceiling ||
    !/^[0-9]+$/.test(ceiling) ||
    !Number.isSafeInteger(Number(ceiling)) ||
    Number(ceiling) <= 0 ||
    ceilings.some(value => value !== ceiling)
  ) {
    throw new Error(`Render both Runtimes with the same positive ${BUDGET} before deploying`);
  }
  for (const node of scope.node.findAll()) {
    if (node instanceof CfnFunction && node.functionName === `${spec.name}-MeridianHolds`) {
      node.addPropertyOverride(`Environment.Variables.${BUDGET}`, ceiling);
    }
  }
}
