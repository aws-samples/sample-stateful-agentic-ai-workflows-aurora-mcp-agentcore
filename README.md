# Meridian: stateful agentic AI on Amazon Aurora, MCP and AgentCore

<p align="center">
  <a href="https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore/actions/workflows/application-ci.yml"><img height="20" alt="Application CI status on main" src="https://img.shields.io/github/actions/workflow/status/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore/application-ci.yml?branch=main&amp;style=flat-square&amp;labelColor=24354B&amp;label=CI"></a>
  <a href="LICENSE"><img height="20" alt="License: MIT-0" src="https://img.shields.io/badge/License-MIT--0-355E8C?style=flat-square&amp;labelColor=24354B"></a>
  <a href="#prerequisites"><img height="20" alt="Python 3.13" src="https://img.shields.io/badge/Python-3.13-355E8C?style=flat-square&amp;labelColor=24354B&amp;logo=python&amp;logoColor=white"></a>
  <a href="#prerequisites"><img height="20" alt="Node.js 22" src="https://img.shields.io/badge/Node.js-22-355E8C?style=flat-square&amp;labelColor=24354B&amp;logo=nodedotjs&amp;logoColor=white"></a>
  <br>
  <a href="https://aws.amazon.com/rds/aurora/"><img height="20" alt="Database: Amazon Aurora PostgreSQL with pgvector" src="https://img.shields.io/badge/Database-Amazon%20Aurora%20PostgreSQL%20%2B%20pgvector-355E8C?style=flat-square&amp;labelColor=24354B&amp;logo=postgresql&amp;logoColor=white"></a>
  <a href="https://aws.amazon.com/bedrock/agentcore/"><img height="20" alt="Runtime: Amazon Bedrock AgentCore" src="https://img.shields.io/badge/Runtime-Amazon%20Bedrock%20AgentCore-355E8C?style=flat-square&amp;labelColor=24354B"></a>
  <a href="https://strandsagents.com"><img height="20" alt="Agents: Strands Agents" src="https://img.shields.io/badge/Agents-Strands%20Agents-355E8C?style=flat-square&amp;labelColor=24354B"></a>
  <br>
  <a href="https://modelcontextprotocol.io"><img height="20" alt="Tools: Model Context Protocol" src="https://img.shields.io/badge/Tools-Model%20Context%20Protocol-355E8C?style=flat-square&amp;labelColor=24354B&amp;logo=modelcontextprotocol&amp;logoColor=white"></a>
  <a href="https://aws.amazon.com/cognito/"><img height="20" alt="Identity: Amazon Cognito" src="https://img.shields.io/badge/Identity-Amazon%20Cognito-355E8C?style=flat-square&amp;labelColor=24354B"></a>
  <a href="https://react.dev"><img height="20" alt="Frontend: React and TypeScript" src="https://img.shields.io/badge/Frontend-React%20%2B%20TypeScript-355E8C?style=flat-square&amp;labelColor=24354B&amp;logo=react&amp;logoColor=white"></a>
  <a href="https://aws.amazon.com/cdk/"><img height="20" alt="Infrastructure: AWS CDK" src="https://img.shields.io/badge/Infrastructure-AWS%20CDK-355E8C?style=flat-square&amp;labelColor=24354B"></a>
</p>

Reference code for an agent application that keeps its state in Amazon Aurora PostgreSQL,
reaches its tools through the Model Context Protocol (MCP), and runs as governed
workloads on Amazon Bedrock AgentCore.

<p align="center">
  <img src="meridian/docs/meridian-showcase.jpg" width="900" alt="Meridian Concierge signed in as the sample traveler, showing destination photography, sample trip prices and the traveler's saved context">
</p>

Meridian is a sample travel concierge for one fictional traveler, Jordan Morgan. Ask it for
trips, let it remember Jordan's preferences, place a 12-hour hold, then cancel a flight and watch
it rework the trip without placing a second hold. Every screen shows the SQL, tool calls,
authorization decisions and stored records behind each answer, so you can see how the pieces fit.

The code grows in five phases, and each phase keeps what came before: SQL queries, then MCP
tools, then hybrid retrieval, then a governed agent on AgentCore, then a Strands Graph workflow
in its own AgentCore Runtime that resumes after its session stops.

The data is a small catalog of sample travel packages. Holds and confirmations change rows in
Meridian's own database only. There is no supplier, airline or payment integration.

> [!IMPORTANT]
> This is sample code for learning and experimentation, not a production-ready system. It creates
> billable resources in your AWS account: read [Costs and clean up](#costs-and-clean-up) before you
> start, and delete what you create when you finish.

**Jump to:** [Architecture](#architecture) | [Quick start](#quick-start) | [Try it](#try-the-reference-app) | [Deploy](#deploy-to-your-account) | [Costs and clean up](#costs-and-clean-up) | [Tests](#tests) | [Docs](#documentation)

## What you will see

Each phase adds one capability and keeps the earlier ones. Each phase folder has a short guide
with a sample prompt and the evidence to inspect.

| Phase | Code | Adds |
| --- | --- | --- |
| 1. SQL | [phase_01_sql](meridian/backend/agents/phase_01_sql/) | Parameterized catalog queries through the RDS Data API |
| 2. MCP | [phase_02_mcp](meridian/backend/agents/phase_02_mcp/) | Named tools for search, package comparison, currency conversion, price range, region inventory and loyalty |
| 3. Retrieval | [phase_03_retrieval](meridian/backend/agents/phase_03_retrieval/) | Cohere Embed v4, pgvector and full-text candidates, Cohere Rerank 3.5 |
| 4. Production | [phase_04_production](meridian/backend/agents/phase_04_production/) | AgentCore Runtime, Gateway, Memory and Cedar policy, with traveler grants and row-level security (RLS) in Aurora |
| 5. Workflow | [phase_05_workflow](meridian/backend/agents/phase_05_workflow/) | A Strands Graph in its own AgentCore Runtime that searches, holds and releases through the Gateway, with Aurora snapshots, worker leases, idempotent holds and one audit row per run |

Strands is the open-source agent SDK that runs the agents and the workflow graph. Cedar is the
policy language that AgentCore Policy evaluates before a tool runs.

| State | Where it lives |
| --- | --- |
| **Traveler context** | Profile, preferences and conversation history in Aurora. The agent session is in AgentCore Memory. |
| **Workflow progress** | Strands Graph snapshots in the Aurora table `workflow_snapshots`, appended after every node. |
| **Business results** | Holds and bookings in Aurora. A SQL function or a guarded update handles each action, so a retried request returns the first result. |

## Architecture

Three places do the work: your machine runs the web app and the backend, your AWS account runs
the agents on Amazon Bedrock AgentCore, and Amazon Aurora PostgreSQL keeps everything that has
to survive.

```mermaid
flowchart LR
    cognito["Amazon Cognito<br/>sign-in"]
    subgraph machine["Your machine"]
        browser["Browser<br/>React app"]
        backend["FastAPI backend<br/>Phases 1 to 3"]
    end
    subgraph agentcore["Amazon Bedrock AgentCore, in your account"]
        concierge["Concierge agent<br/>Phase 4"]
        workflow["Recovery workflow<br/>Phase 5"]
        gateway["Gateway<br/>checks every tool call"]
        tools["Lambda tools<br/>catalog, holds"]
    end
    aurora[("Amazon Aurora PostgreSQL<br/>catalog, traveler data,<br/>holds, snapshots")]

    cognito -.->|token| browser
    browser --> backend
    backend --> aurora
    backend --> concierge
    backend --> workflow
    concierge --> gateway
    workflow --> gateway
    gateway --> tools
    tools --> aurora
    workflow -->|snapshots| aurora
```

- **Your machine.** The browser talks to the FastAPI backend. Phases 1 to 3 (SQL, MCP tools,
  hybrid retrieval) run inside the backend against Aurora.
- **AgentCore.** The Concierge agent (Phase 4) answers chat; the recovery workflow (Phase 5) runs
  the multi-step rebooking. Neither writes a hold itself. Every hold, confirmation and release
  goes through the Gateway, which checks the caller, evaluates the policy and then runs a Lambda
  tool. The workflow also writes its own snapshots to Aurora, so another worker can pick up a run.
- **Aurora.** The catalog, Jordan's preferences and conversation, holds and bookings, and the
  workflow's snapshots all live here. Row-level security limits each request to its own
  traveler's rows.

The app draws the same architecture in its **Solution briefing** view, with the numbered path a
Phase 4 request takes:

<p align="center">
  <img src="meridian/docs/meridian-solution-briefing.jpg" width="900" alt="Solution briefing architecture: FastAPI (1) calls the Phase 4 Strands agent and the Phase 5 Strands Graph workflow, each in its own AgentCore Runtime (2); both call AgentCore Gateway (3), where Cedar policy checks every tool call before the Lambda tools for search, details, loyalty, hold, confirm and release (4) reach Aurora PostgreSQL (5); a dashed line marks direct Data API access for the catalog, preferences, availability, saved steps and leases">
</p>

Jordan signs in with Amazon Cognito. The token travels with every request, and the backend, both
Runtimes and the Gateway each verify it.

**One hold, step by step**

1. Jordan asks the Concierge to hold a trip. The browser sends the request with Jordan's token
   to the backend, which verifies it and forwards the turn to the Concierge Runtime.
2. The agent, Claude Sonnet 5.5 on Amazon Bedrock run by Strands, decides to call the hold tool.
3. The Gateway verifies the token again. A request interceptor overwrites the `travelerId`
   argument with the traveler named in the token, and AgentCore Policy evaluates the Cedar rules:
   the traveler confirmed, at most 12 hours and 6 travelers, within the saved budget.
4. The `MeridianHolds` Lambda checks its own grant for that traveler, steps down to a restricted
   database role, and writes the hold inside one transaction, under row-level security.
5. Aurora returns the hold with its number and expiry, and the agent tells Jordan. A retry with
   the same request id returns that hold instead of a second one.

The models are Claude Sonnet 5.5 through its global cross-Region inference profile, Cohere Embed
v4 and Cohere Rerank 3.5, all on Amazon Bedrock. See
[STATEFUL_ARCHITECTURE.md](meridian/docs/STATEFUL_ARCHITECTURE.md) for the state design and
[meridian/README.md](meridian/README.md) for the API, schema and configuration.

## Prerequisites

- Python 3.13.18 and Node.js 22.23.4, the versions CI runs. They live in
  [`meridian/.python-version`](meridian/.python-version) and [`meridian/.nvmrc`](meridian/.nvmrc),
  so `uv python install` and `nvm install` in `meridian/` pick them up. Deploying the AgentCore
  resources also needs [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13.6 and
  the [AgentCore CLI](https://github.com/aws/agentcore-cli).
- An AWS account and credentials (`aws login`, `aws sso login` or `aws configure`). Use
  `us-east-1`: the defaults assume it, and another Region needs the settings in the
  [runbook prerequisites](meridian/docs/AGENTCORE_DEPLOY_RUNBOOK.md#prerequisites).
- Amazon Bedrock access, in that Region, to Claude Sonnet 5.5 (through its global cross-Region
  inference profile), Cohere Embed v4 and Cohere Rerank 3.5. Confirm it in the Amazon Bedrock
  console before you seed the database.
- An Aurora PostgreSQL cluster with the RDS Data API and a Secrets Manager secret for its database
  user. Creating one with the included CDK app needs a VPC with two subnets in different
  Availability Zones and a bootstrapped CDK environment
  ([Provision Aurora](meridian/docs/OPERATIONS.md#provision-aurora)).
- For Phases 4 and 5 and all holds: the AgentCore resources (see the
  [deployment runbook](meridian/docs/AGENTCORE_DEPLOY_RUNBOOK.md)).
- For sign-in in `jwt` mode: macOS. The user-seeding script stores passwords in the macOS Keychain
  and the release tools call `agentcore` at `/opt/homebrew/bin`, the
  Homebrew path on Apple silicon. The default `iam` mode has no such need.

## Quick start

The backend and frontend run on your machine against Aurora in your account. With Aurora and
Amazon Bedrock you can run the catalog, Phases 1 to 3, System evidence and Solution briefing. The
Concierge chat, Phase 5 and holds also need the AgentCore resources.

**0. Create the cluster**, unless you already have one: follow
[Provision Aurora](meridian/docs/OPERATIONS.md#provision-aurora). The cluster bills from the moment
it exists.

**1. Configure and prepare the database**

```bash
cd meridian
python3.13 -m venv venv && source venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
cp .env.example .env   # set AWS_DEFAULT_REGION, AURORA_CLUSTER_ARN, AURORA_SECRET_ARN, AURORA_DATABASE
python scripts/init_aurora_schema.py && python scripts/apply_migrations.py && python scripts/seed_data.py
```

`seed_data.py` calls Amazon Bedrock to embed the catalog, so the credentials need Cohere Embed v4
access. The schema and seed scripts refuse to run on a database that already has Meridian data;
there, run only `apply_migrations.py`.

> [!NOTE]
> **The Quick start uses the master login for setup only.** These steps, and a backend you run
> this way, connect as the cluster's master login, because creating the schema and seeding the
> data need it. The master login is not subject to row-level security. Anything you deploy should
> connect as the least-privilege service logins instead: see
> [Database logins](#database-logins).

**2. Start the backend**, then check it from a second terminal:

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8013
curl http://127.0.0.1:8013/api/health
```

The response has `"status": "healthy"` and `"aurora_reachable": true`. The snapshot store appears as
`"snapshot_store": "Aurora workflow_snapshots"`.
`"workflow_runtime_configured"` is `false` until you deploy the AgentCore resources and run
`sync_agentcore_env.py --write`. Local requests need no API token; read the
[governance boundary](meridian/README.md#governance-boundary) before you expose the API.

**3. Start the frontend**

```bash
cd meridian/frontend && npm ci
VITE_API_ORIGIN=http://127.0.0.1:8013 npm run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

Open <http://127.0.0.1:5176/showcase>. The header shows **Meridian live** when the backend is
healthy.

## Try the reference app

| View | Try | Needs AgentCore |
| --- | --- | --- |
| **Concierge** | Ask `Find Tokyo trips that fit my saved preferences.`, open a trip, then **Request 12-hour hold** and **Confirm this trip for Jordan** | Chat and holds |
| **Capability ladder** | Run each phase's prompts and open the activity trace for the SQL, tool calls, retrieval scores and policy decisions | Phases 4 and 5 |
| **Recovery desk** | Run `My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.`, then **Stop runtime session** and **Resume and request hold**: the run continues on a new microVM from the saved step | Yes |
| **System evidence** | Read what Aurora recorded: snapshots, leases, session stops, authorization decisions and the hold | After a Phase 5 run |
| **Solution briefing** | The architecture, phases, Gateway tools and Cedar policies | No |

Every phase's prompts are in [meridian/README.md](meridian/README.md#prompts), and
[OPERATIONS.md](meridian/docs/OPERATIONS.md#exercise-recovery-failures) has scripts that stop a
worker or discard a response on purpose.

## Deploy to your account

| Component | How | Guide |
| --- | --- | --- |
| Aurora cluster | CDK app `meridian/infra/bin/meridian-aurora.ts` | [OPERATIONS.md](meridian/docs/OPERATIONS.md#provision-aurora) |
| AgentCore Runtime, Gateway, Memory, policies | AgentCore CLI, from templates that `scripts/render_agentcore_config.py` fills in for your account | [AGENTCORE_DEPLOY_RUNBOOK.md](meridian/docs/AGENTCORE_DEPLOY_RUNBOOK.md) |
| Hosted web app (CloudFront, S3, App Runner), optional | `meridian/scripts/publish.py`, into an App Runner service you already have; publish in `jwt` mode with `--tighten` | [OPERATIONS.md](meridian/docs/OPERATIONS.md#publish-the-web-app) |

The hosted web app is optional: the repository does not create the App Runner service, and the
app runs fully on your machine without it.

## Costs and clean up

The sample creates resources that bill while they exist. The main cost drivers:

| What | Created by | Billed |
| --- | --- | --- |
| Aurora Serverless v2 cluster | [Provision Aurora](meridian/docs/OPERATIONS.md#provision-aurora) | At least 0.5 ACU for every hour the cluster exists, plus storage, backups and snapshots |
| Amazon Bedrock models | `seed_data.py` and every chat turn | Per token |
| AgentCore Runtimes, Gateway, Memory and policy engine | The [AgentCore deploy](meridian/docs/AGENTCORE_DEPLOY_RUNBOOK.md) | By use, plus stored memory |
| Lambda functions, Secrets Manager secrets, SSM parameters, CloudWatch logs | The AgentCore deploy and the provisioning scripts | By use, per secret each month, and by stored logs |
| Amazon Cognito user pool | Sign-in in `jwt` mode | By monthly active users |
| CloudFront, S3 and App Runner | The optional hosted web app | By traffic, and for the running App Runner service |

To delete everything, work from the last thing you created back to the first:

1. **Hosted web app**, if you published it: the end of
   [Publish the web app](meridian/docs/OPERATIONS.md#publish-the-web-app).
2. **Sign-in**, in `jwt` mode: the Cognito users, their secrets and the `MeridianIdentity` stack,
   steps 1 and 2 of
   [Roll back the service logins and sign-in](meridian/docs/OPERATIONS.md#roll-back-the-service-logins-and-sign-in).
3. **AgentCore**: the stack, the catalog Lambda and its role, the SSM parameters, the `jwt` mode
   interceptor, and the service logins' secrets and policies:
   [Tear down](meridian/docs/AGENTCORE_DEPLOY_RUNBOOK.md#tear-down).
4. **Aurora**: turn off termination and deletion protection, destroy the stack, then delete the
   final snapshot once you no longer need it: the end of
   [Provision Aurora](meridian/docs/OPERATIONS.md#provision-aurora).
5. **CDK bootstrap**: delete the `CDKToolkit` stack only if nothing else in the account and
   Region deploys with CDK; empty its S3 bucket and ECR repository first if the delete stops on
   them.

Log groups can outlive their resources. Delete the Meridian Lambda and AgentCore Runtime log
groups in CloudWatch Logs when you no longer need them.

## Tests

```bash
PYTHON_DOTENV_DISABLED=1 python -m pytest -m "not database"            # from meridian/
npm ci && npm run lint && npm run typecheck && npm run test:run          # from meridian/frontend/
```

Unit tests block network access and ignore `meridian/.env`. Tests marked `database` write to a
live Aurora database; run them only against a disposable one. CI runs the full set on every pull
request: see [`application-ci.yml`](.github/workflows/application-ci.yml) and the
[dependency notes](meridian/docs/DEPENDENCIES.md).

## Documentation

| Read | For |
| --- | --- |
| [meridian/README.md](meridian/README.md) | Screens, API, configuration, Aurora schema and governance boundary |
| [meridian/STRUCTURE.md](meridian/STRUCTURE.md) | A request traced through the code, and what each folder holds |
| [meridian/backend/agents/README.md](meridian/backend/agents/README.md) | The five capability guides |
| [meridian/docs/STATEFUL_ARCHITECTURE.md](meridian/docs/STATEFUL_ARCHITECTURE.md) | Snapshots, memory, transactions and recovery |
| [meridian/docs/OPERATIONS.md](meridian/docs/OPERATIONS.md) | Provision, run locally, publish, recover, troubleshoot |
| [meridian/docs/README.md](meridian/docs/README.md) | Index of all documents |

[`meridian/examples/langgraph/`](meridian/examples/langgraph/) holds a maintained second
implementation of the workflow on another graph framework; the application does not import it.

## Security

Callers sign in with Amazon Cognito. In `jwt` mode the backend, both Runtimes and the Gateway each
verify the access token and take the traveler from its `traveler_id` claim, never from a request
body or a tool argument, and Aurora row-level security is the last check. `iam` mode (the default)
signs the AgentCore hops with AWS credentials. Local development keeps an open loopback mode and
a shared API token path, which a `jwt` release refuses.

### Database logins

Use the master database login for setup only. Anything you deploy connects to Aurora as a
least-privilege login of its own. Every service login is `NOBYPASSRLS`, owns no tables and holds
only what its workload queries:

| Login | Used by | Holds directly | Reaches traveler rows |
| --- | --- | --- | --- |
| Master (`meridian_admin` in the included CDK app) | The schema, migration and seed scripts; a backend you run from the Quick start | Everything. Row-level security does not apply to it | Directly, with no row-level security. Setup only |
| `meridian_backend` | The hosted backend in `jwt` mode | SELECT on `traveler_identity_bindings` and `trip_packages`, INSERT on `traveler_access_audit`, EXECUTE on `backend_admin_count` and `package_places_left` | Only through `meridian_app` |
| `meridian_gateway` | The `MeridianHolds` Lambda (holds, confirmations, releases) | The same three tables, EXECUTE on `package_places_left` | Only through `meridian_app` |
| `meridian_workflow` | The `MeridianWorkflow` Runtime (Phase 5) | SELECT on the catalog, identity bindings and journey tables; SELECT and INSERT on `workflow_snapshots`; INSERT on `traveler_access_audit`; UPDATE on the lease column; EXECUTE on `package_places_left` | Its journey rows and snapshots under policies pinned to the traveler; everything else only through `meridian_app` |
| `meridian_catalog` | The `meridian-catalog-tools` Lambda | SELECT on `trip_packages`, EXECUTE on `package_places_left` | Never: it cannot switch to `meridian_app` |
| `meridian_identity` | The Amazon Cognito pre-token-generation Lambda | SELECT on `traveler_identity_bindings` | Never: it cannot switch to `meridian_app` |
| `meridian_app` (cannot log in) | Reached only with `SET LOCAL ROLE` from the logins above | The grants on traveler data; the row-level security policies on those tables apply to it | Only the traveler pinned in the current transaction |

A service login reads and writes traveler data (profiles, preferences, conversations, journeys,
snapshots, holds and bookings) only for the traveler pinned in its transaction. The code pins the
traveler (`set_config('app.current_traveler_id', ..., true)`) and then runs
`SET LOCAL ROLE meridian_app`; the workflow login reads its journey rows and writes its snapshots
as itself, under policies pinned to the same setting. The setting and the role both end at commit
or rollback, so nothing carries over to the next request on a pooled RDS Data API connection. The
logins that check a grant also read `traveler_identity_bindings`, the table that says which
signed-in identity or workload may act for which traveler; that table has no row-level security.

The hosted web stack refuses to deploy a `jwt` backend whose secret is the master's. A hosted
backend in `iam` mode connects as the master login, so publish the hosted backend in `jwt` mode.
[Service logins](meridian/docs/OPERATIONS.md#service-logins) shows how to create each login.

This is sample code. Review networking, monitoring, availability and data protection, and harden
it, before any production use.

To report a security issue, use the
[vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/), not a
public GitHub issue. See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

This library is licensed under the MIT-0 License; see the [LICENSE](LICENSE) file. Some images and
icons under `meridian/frontend/public/` come from third parties under their own terms; see
[NOTICE](NOTICE).
