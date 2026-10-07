# Meridian documentation

## Present the story

1. [Run of show](TALK_RUN_OF_SHOW.md) - 40 minutes of content, five live checkpoints, 20 minutes of discussion.
2. [Numbered capability guides](../backend/agents/README.md) - 01 SQL, 02 MCP, 03 Retrieval, 04 Production, 05 Workflow.
3. [Code walkthrough](CODE_WALKTHROUGH.md) - the precise symbols to open beside the running app.

## Understand the design

1. [Application guide](../README.md) - screens, API, configuration and governance boundaries.
2. [Source map](../STRUCTURE.md) - where requests, tools and state live.
3. [Stateful architecture](STATEFUL_ARCHITECTURE.md) - snapshots, memory, transactions and recovery.
4. [Dogwood assessment](DOGWOOD_POLICY_ASSESSMENT.md) - optional temporal-policy learning, with implemented and proposed behavior kept separate.
5. [Signed scope evaluation](SIGNED_SCOPE_EVALUATION.md) - whether Aurora should verify a signed traveler scope, evaluated and not built.
6. [Product](../../PRODUCT.md) and [design system](../../DESIGN.md) - presentation priorities and UI conventions.

## Operate and validate

1. [Operations](OPERATIONS.md) - prepare Aurora, start locally, publish, exercise recovery and clean up.
2. [AgentCore deployment](AGENTCORE_DEPLOY_RUNBOOK.md) - Runtime, Gateway, Memory and Cedar setup.
3. [AgentCore learnings](AGENTCORE_LEARNINGS.md) - deployment and runtime troubleshooting.
4. [Dependency maintenance](DEPENDENCIES.md) - audit status and safe lock updates.
5. [Script index](../scripts/README.md) - commands grouped by purpose and whether they write.

Screenshots in this directory illustrate the application. They are not runtime
receipts; current hosted release evidence belongs in the ignored `.local/`
directory, with no account-specific endpoints or credentials in public docs.
