import { Aspects, Stack, aws_iam as iam } from 'aws-cdk-lib';
import { IConstruct } from 'constructs';

/**
 * alpha.47 (and alpha.53) emit a nonexistent IAM action. The documented Policy
 * contract is AuthorizeAction, PartiallyAuthorizeActions and GetPolicyEngine.
 * Remove only the obsolete action, preserving every real grant and resource.
 * This is an infrastructure compatibility fix, not agent configuration.
 * https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html
 */
export function correctPolicyPermissions(scope: IConstruct): void {
  function clean(document: Record<string, unknown>): Record<string, unknown> {
    const statements = document.Statement as Record<string, unknown>[] | undefined;
    if (!statements) return document;
    return {
      ...document,
      Statement: statements.map(statement => ({
        ...statement,
        ...(Array.isArray(statement.Action)
          ? { Action: statement.Action.filter(action => action !== 'bedrock-agentcore:CheckAuthorizePermissions') }
          : {}),
      })),
    };
  }
  Aspects.of(scope).add({
    visit(node: IConstruct): void {
      const stack = Stack.of(node);
      if (node instanceof iam.CfnPolicy) {
        node.policyDocument = clean(stack.resolve(node.policyDocument));
      } else if (node instanceof iam.CfnRole && node.policies) {
        const policies = stack.resolve(node.policies) as iam.CfnRole.PolicyProperty[];
        node.policies = policies.map(policy => ({ ...policy, policyDocument: clean(policy.policyDocument) }));
      }
    },
  });
}
