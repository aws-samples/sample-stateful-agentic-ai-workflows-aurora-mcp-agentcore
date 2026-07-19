# Meridian — Plan. Fly. Land.

Agentic travel concierge sample for **DAT309: Build agentic workflows with Aurora and MCP**.

[![License: MIT-0](https://img.shields.io/badge/License-MIT--0-blue?style=flat-square)](LICENSE)
![AWS Aurora PostgreSQL](https://img.shields.io/badge/AWS-Aurora%20PostgreSQL-232F3E?style=flat-square&logo=amazonaws&logoColor=white)
![Amazon Bedrock](https://img.shields.io/badge/Amazon%20Bedrock-Claude%20Sonnet%204.6-232F3E?style=flat-square&logo=amazonaws&logoColor=white)
![Model Context Protocol](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-5B5FC7?style=flat-square)
![LangGraph](https://img.shields.io/badge/LangGraph-StateGraph-1F6F8B?style=flat-square)

The runnable application lives in [`meridian/`](meridian/). The primary live-demo URL is:

```text
http://localhost:5173/showcase
```

## What It Demonstrates

Meridian walks one travel domain through five increasingly capable agent patterns on Aurora PostgreSQL:

| Phase | Capability | Implementation proof |
| ----- | ---------- | -------------------- |
| Phase 1: SQL | Query | Direct SQL filters through the RDS Data API |
| Phase 2: MCP | Tool | Generic PostgreSQL MCP plus custom `meridian-concierge` domain tools |
| Phase 3: Retrieval | Intent | Cohere Embed v4, pgvector, full-text search, and Cohere Rerank 3.5 |
| Phase 4: Production | Trust | Bedrock AgentCore, traveler memory, Aurora RLS, and audit logging |
| Phase 5: Workflow | Durable Workflow | LangGraph `StateGraph` with Aurora-backed checkpoints |

The demo traveler is **Alex Morgan** (`trv_meridian_demo`). Phase 4 and Phase 5 use Alex's Aurora-backed profile, preferences, memory, and RLS scope.

## Quick Start

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

Open [`http://localhost:5173/showcase`](http://localhost:5173/showcase). The root route redirects to `/showcase`.

## Demo Surfaces

| Surface | Route | Purpose |
| ------- | ----- | ------- |
| Device Showcase | `/showcase` | Primary AWS Summit chalk-talk surface |

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
- **Data:** Aurora PostgreSQL 17, pgvector, RDS Data API, Row-Level Security
- **Protocols and services:** Model Context Protocol, Bedrock AgentCore Runtime, Gateway, Memory, and Identity

This is an educational sample, not production-hardened application code.
