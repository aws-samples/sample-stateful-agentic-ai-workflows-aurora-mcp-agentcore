# Meridian - Plan. Fly. Land.

Reference application for **Build stateful agentic AI workflows with Aurora, MCP, and AgentCore**.

<p align="center">
  <a href="https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore/actions/workflows/application-ci.yml"><img height="20" alt="Application CI status on main" src="https://img.shields.io/github/actions/workflow/status/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore/application-ci.yml?branch=main&amp;style=flat-square&amp;label=CI&amp;labelColor=24354B"></a>
  <a href="LICENSE"><img height="20" alt="License: MIT-0" src="https://img.shields.io/badge/License-MIT--0-355E8C?style=flat-square&amp;labelColor=24354B"></a>
  <a href="meridian/README.md#prerequisites"><img height="20" alt="Python 3.13" src="https://img.shields.io/badge/Python-3.13-355E8C?style=flat-square&amp;labelColor=24354B&amp;logo=python&amp;logoColor=white"></a>
  <a href="meridian/README.md#prerequisites"><img height="20" alt="Node.js 22.12 or later recommended" src="https://img.shields.io/badge/Node.js-22.12%2B-355E8C?style=flat-square&amp;labelColor=24354B&amp;logo=nodedotjs&amp;logoColor=white"></a>
</p>

<p align="center">
  <a href="#tech-stack"><img height="20" alt="Amazon Aurora PostgreSQL 18+" src="https://img.shields.io/badge/Amazon_Aurora-PostgreSQL_18%2B-355E8C?style=flat-square&amp;labelColor=24354B"></a>
  <a href="#tech-stack"><img height="20" alt="Amazon Bedrock AgentCore" src="https://img.shields.io/badge/Amazon_Bedrock-AgentCore-355E8C?style=flat-square&amp;labelColor=24354B"></a>
  <a href="#what-it-demonstrates"><img height="20" alt="Model Context Protocol" src="https://img.shields.io/badge/Protocol-MCP-355E8C?style=flat-square&amp;labelColor=24354B"></a>
</p>

Meridian is a working travel concierge backed by Aurora PostgreSQL. Explore
trips, recall traveler preferences, and recover an interrupted trip-planning
workflow. Then inspect the SQL, tools, authorization decisions, and persisted
state behind the experience.

The application combines SQL, pgvector, PostgreSQL full-text search, and
reranking with MCP tools, Strands Agents, Bedrock AgentCore, and LangGraph.
The catalog contains sample travel packages; it is not a live airline booking
or ticketing feed.

> **Statefulness lives in durable stores, not database connections.** The RDS
> Data API is a connectionless transport for durable Aurora reads and writes;
> LangGraph checkpoints persist execution state in Aurora through the
> `AuroraDataApiSaver` or a pooled `AsyncPostgresSaver`. AgentCore Memory adds
> managed context when configured. The app reports which checkpoint backend
> actually served the run.

![Meridian Concierge with the M brand mark, four-view navigation, destination photography, trip prices, traveler brief, and conversational composer](meridian/docs/meridian-showcase.png)

<p align="center"><sub>The current Concierge in fullscreen presentation mode, captured from the running app. Presenter controls are hidden; catalog and traveler details come from Aurora.</sub></p>

**[Quick start](#quick-start)** · **[Five-phase architecture](#what-it-demonstrates)** · **[Stateful architecture](meridian/docs/STATEFUL_ARCHITECTURE.md)** · **[Validation](#validation)** · **[Demo script](meridian/DEMO_SCRIPT.md)** · **[Presenter guide](meridian/docs/PRESENTER_GUIDE.md)**

## What It Demonstrates

Meridian walks one travel domain through five increasingly capable patterns
without hiding the implementation behind a generic chat interface:

| Phase | Adds | Live proof |
| ----- | ---- | ---------- |
| **1 · SQL** | Query | Parameterized filters over Aurora through the RDS Data API |
| **2 · MCP** | Governed tools | PostgreSQL MCP plus typed comparison, FX, loyalty, and availability tools |
| **3 · Retrieval** | Intent | Cohere Embed v4, pgvector, full-text search, and Cohere Rerank 3.5 |
| **4 · Production** | Traveler context and access controls | Recalled preferences, workload-to-traveler grants, RLS, and audit trails |
| **5 · Workflow** | Durability | Aurora checkpoint, worker restart, same-thread resume, and an unchanged package-hold receipt |

The demo traveler is **Alex Morgan** (`trv_meridian_demo`), a JFK-based
Marriott Bonvoy Platinum Elite traveler. Production and Workflow use Alex's
Aurora-backed profile, preferences, conversational memory, and RLS scope only
after the authenticated workload has an active grant to Alex's traveler record.

The four views follow the talk: **Concierge → Capability ladder → Recovery
desk → System evidence**. The ladder pairs a query that works in the selected
phase with a boundary query that introduces the next capability. These are
boundaries of this demo's configured phases, rather than limits of SQL or MCP.

## Quick Start

The runnable application lives in [`meridian/`](meridian/).

### Backend

```bash
cd meridian
python -m venv venv
source venv/bin/activate
python -m pip install --require-hashes -r requirements.txt

cp .env.example .env
# Fill in Aurora cluster ARN, secret ARN, database, and AWS region.

# Fresh or disposable database only: rebuilds the base schema.
python scripts/init_aurora_schema.py
python scripts/apply_migrations.py
python scripts/seed_data.py

# In .env, enable the Aurora Data API checkpoint path for the recovery demo:
# LANGGRAPH_CHECKPOINT_DATA_API=true
# LANGGRAPH_CHECKPOINT_REQUIRED=true

uvicorn backend.main:app --reload --port 8000
```

### Frontend

```bash
cd meridian/frontend
npm ci
npm run dev
```

Open [`http://localhost:5173/showcase`](http://localhost:5173/showcase). The
root route redirects to the showcase. For an existing database, do not rerun
`init_aurora_schema.py`; apply tracked upgrades with
`python scripts/apply_migrations.py`.

Before demonstrating recovery, check `http://localhost:8000/health` for
`checkpoint_durable: true`, then confirm live trips and Alex’s profile load in
the app. Health reports process configuration; it does not revalidate an AWS
session. The app shows a reconnect notice when catalog or profile reads fail. An explicitly configured checkpoint DSN takes
precedence over the Data API saver. `MemorySaver` is an in-process fallback
and cannot demonstrate recovery after a worker restart.

The [release review](meridian/docs/RELEASE_REVIEW.md) records the latest checks,
plain-language explanations, and live rehearsal results.

## Demo Surfaces

| Surface | Route | Purpose |
| ------- | ----- | ------- |
| Concierge | `/showcase?view=concierge` | Traveler discovery, conversation, preferences, and trip details |
| Capability ladder | `/showcase?view=ladder` | Five phases with boundary queries, architecture, and live traces |
| Recovery desk | `/showcase?view=recovery` | Canceled-trip scenario, saved shortlist, resume, and package-hold receipt |
| System evidence | `/showcase?view=proof` | Aurora readback of checkpoints, executions, authorization, and holds |
| Demo Stage | `/demo-stage`, `/stage` | Kiosk loop and presenter playback |

In windowed mode, **Presenter controls** provides an audience preview,
projector readability, and a room-check list. Select **Present fullscreen**
before sharing: the controls and service sidebar disappear, while the four
views and their evidence remain accessible. **Esc** restores the controls.
Stop sharing before returning to windowed mode.

A saved shortlist and a package hold are separate events. The recovery receipt
shows the hold's database creation time, expiry, and remaining duration. A
worker restart must preserve the same booking ID and original 15-minute expiry.

## Documentation

| Doc | Purpose |
| --- | ------- |
| [meridian/README.md](meridian/README.md) | Full setup, architecture, API, phase prompts, and validation |
| [meridian/DEMO_SCRIPT.md](meridian/DEMO_SCRIPT.md) | Extended demo script and optional code walkthrough |
| [meridian/docs/PRESENTER_GUIDE.md](meridian/docs/PRESENTER_GUIDE.md) | Concise run of show, claim boundaries, and readiness checklist |
| [meridian/docs/OPERATIONS.md](meridian/docs/OPERATIONS.md) | AgentCore deployment and day-of operations |
| [meridian/docs/STATEFUL_ARCHITECTURE.md](meridian/docs/STATEFUL_ARCHITECTURE.md) | Durable-state, transport, and checkpoint architecture |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution guidelines |

## Tech Stack

- **Frontend:** React, Vite, TypeScript
- **Backend:** FastAPI, Strands Agents, LangGraph
- **Models:** Claude Sonnet 5 on Amazon Bedrock, Cohere Embed v4, Cohere Rerank 3.5
- **Data:** Aurora PostgreSQL 18+, pgvector, RDS Data API, pooled psycopg, identity bindings, Row-Level Security
- **Protocols and services:** Model Context Protocol, Bedrock AgentCore Runtime, Gateway, Memory, and IAM or AgentCore workload identity

This sample authorizes AWS or AgentCore workload identities. A shared hosted
application must also authenticate its end users and bind the verified user
subject, such as a Cognito `sub`, to the traveler record. Apply your
organization's networking, observability, availability, and governance
requirements before production use.

## Validation

The GitHub Actions workflow runs backend, frontend, and AgentCore CDK checks on
every push to `main`. Run the same commands locally:

```bash
cd meridian
source venv/bin/activate
PYTHON_DOTENV_DISABLED=1 python -m pytest -m "not database"
ruff check backend scripts tests
python -m pip_audit -r requirements.txt
```

```bash
cd meridian/frontend
npm ci
npm run lint
npm run test:run
npm run build
npm audit --audit-level=high
```

```bash
cd meridian/meridian_agentcore/agentcore/cdk
npm ci
npm run build
npm test -- --runInBand
npm run format:check
npm audit --audit-level=high
```

Install `ruff` and `pip-audit` in the backend virtual environment before
running those checks. CI runs the offline tests; the command above also prevents
local pytest from loading the demo's `.env` configuration.

Run the live Aurora integration tests separately from `meridian/` with
`python -m pytest -m database`. This loads `.env` and requires a disposable,
migrated Aurora test database with a seeded catalog and AWS access. These tests
write checkpoints, journeys, and holds. See the
[audit validation notes](meridian/docs/AUDIT_FIXES.md) for the tested restart
scenario and its limits.
