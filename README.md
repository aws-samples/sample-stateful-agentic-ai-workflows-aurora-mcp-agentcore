# Meridian - Plan. Fly. Land.

Reference application for **Build stateful agentic AI workflows with Aurora, MCP, and AgentCore**.

[![License: MIT-0](https://img.shields.io/badge/License-MIT--0-2EA44F?style=flat-square)](LICENSE)
![Amazon Aurora PostgreSQL 18+](https://img.shields.io/badge/Amazon_Aurora-PostgreSQL_18%2B-527FFF?style=flat-square&labelColor=232F3E)
![Amazon Bedrock AgentCore](https://img.shields.io/badge/Amazon_Bedrock-AgentCore-FF9900?style=flat-square&labelColor=232F3E)
![Claude Sonnet 5](https://img.shields.io/badge/Claude-Sonnet_5-191919?style=flat-square&logo=anthropic&logoColor=white)
![Model Context Protocol](https://img.shields.io/badge/MCP-Model_Context_Protocol-000000?style=flat-square&logo=modelcontextprotocol&logoColor=white)
![LangGraph StateGraph](https://img.shields.io/badge/LangGraph-StateGraph-1C3C3C?style=flat-square&logo=langchain&logoColor=white)

Meridian is a realistic agentic travel concierge operating on live relational
data. It combines structured SQL, pgvector semantic retrieval, PostgreSQL
full-text search, and reranking with MCP tools, Strands Agents, Bedrock
AgentCore, and durable LangGraph workflows. Aurora-backed memory, row-level
security, audit trails, and checkpoints keep every turn governed and resilient.

![Meridian showcase displaying grounded trip results, SQL execution proof, and Alex Morgan's governed traveler context](meridian/docs/meridian-showcase.jpg)

<p align="center"><sub>The live showcase pairs the traveler experience with inspectable SQL, retrieval, memory, RLS, and workflow proof.</sub></p>

**[Quick start](#quick-start)** · **[Five-phase architecture](#what-it-demonstrates)** · **[Demo script](meridian/DEMO_SCRIPT.md)** · **[Presenter guide](meridian/docs/PRESENTER_GUIDE.md)**

## What It Demonstrates

Meridian walks one travel domain through five increasingly capable patterns
without hiding the implementation behind a generic chat interface:

| Phase | Adds | Live proof |
| ----- | ---- | ---------- |
| **1 · SQL** | Query | Parameterized filters over Aurora through the RDS Data API |
| **2 · MCP** | Governed tools | PostgreSQL MCP plus typed comparison, FX, loyalty, and availability tools |
| **3 · Retrieval** | Intent | Cohere Embed v4, pgvector, full-text search, and Cohere Rerank 3.5 |
| **4 · Production** | Trust | Workload identity, workload-to-traveler grants, RLS, and audit trails |
| **5 · Workflow** | Durability | Explicit LangGraph routing with Aurora-backed checkpoints and resume |

The demo traveler is **Alex Morgan** (`trv_meridian_demo`), a JFK-based
Marriott Bonvoy Platinum Elite traveler. Production and Workflow use Alex's
Aurora-backed profile, preferences, conversational memory, and RLS scope only
after the authenticated workload has an active grant to Alex's traveler record.

The showcase exposes two synchronized views:

- **Experience** presents the personalized concierge, realistic recommendations,
  comparison, holds, saved trips, and a persistent journey workspace.
- **System proof** exposes tool spans, generated SQL, hybrid retrieval,
  memory reads and writes, authorization ALLOW/DENY decisions, RLS evidence,
  audit records, and checkpoints.

## Quick Start

The runnable application lives in [`meridian/`](meridian/).

### Backend

```bash
cd meridian
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in Aurora cluster ARN, secret ARN, database, and AWS region.

python scripts/init_aurora_schema.py
python scripts/seed_data.py

uvicorn backend.main:app --reload --port 8000
```

### Frontend

```bash
cd meridian/frontend
npm install
npm run dev
```

Open [`http://localhost:5173/showcase`](http://localhost:5173/showcase). The
root route redirects to the showcase.

## Demo Surfaces

| Surface | Route | Purpose |
| ------- | ----- | ------- |
| Meridian Showcase | `/showcase` | Primary live experience and system-proof surface |
| Meridian Pro | `/pro` | Supporting architecture and builder walkthrough |

## Documentation

| Doc | Purpose |
| --- | ------- |
| [meridian/README.md](meridian/README.md) | Full setup, architecture, API, phase prompts, and validation |
| [meridian/DEMO_SCRIPT.md](meridian/DEMO_SCRIPT.md) | Presenter flow and live-demo talk track |
| [meridian/docs/PRESENTER_GUIDE.md](meridian/docs/PRESENTER_GUIDE.md) | Detailed narration, code references, and dry-run checklist |
| [meridian/docs/OPERATIONS.md](meridian/docs/OPERATIONS.md) | AgentCore deployment and day-of operations |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution guidelines |

## Tech Stack

- **Frontend:** React, Vite, TypeScript
- **Backend:** FastAPI, Strands Agents, LangGraph
- **Models:** Claude Sonnet 5 on Amazon Bedrock, Cohere Embed v4, Cohere Rerank 3.5
- **Data:** Aurora PostgreSQL 18+, pgvector, RDS Data API, identity bindings, Row-Level Security
- **Protocols and services:** Model Context Protocol, Bedrock AgentCore Runtime, Gateway, Memory, and Identity

This sample authorizes AWS or AgentCore workload identities. A shared hosted
application must also authenticate its end users and bind the verified user
subject, such as a Cognito `sub`, to the traveler record. Apply your
organization's networking, observability, availability, and governance
requirements before production use.
