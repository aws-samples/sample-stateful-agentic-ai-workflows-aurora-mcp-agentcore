# Meridian — Plan. Fly. Land.

Agentic travel concierge workshop for **DAT309** — *Build agentic workflows with Aurora and MCP*.

The application lives entirely in **[`meridian/`](meridian/)**. Everything else in this repo supports that demo.

## What it demonstrates

A five-phase ladder on one Aurora catalog (`trip_packages`) — each phase composes a new capability onto the last:

| Phase | Capability |
| ----- | ------------ |
| 1 · SQL | Direct SQL via RDS Data API |
| 2 · MCP | Same queries through postgres-mcp-server + a custom domain MCP server |
| 3 · Retrieval | Cohere Embed v4 hybrid pgvector / full-text search, reranked by Cohere Rerank 3.5 |
| 4 · Production | AgentCore Runtime + Gateway + Memory + Aurora RLS · Strands concierge |
| 5 · Workflow | LangGraph `StateGraph` with `PostgresSaver` checkpoints in Aurora |

Demo traveler: **Alex Morgan** (`trv_meridian_demo`) — profile and preferences load from Aurora before every Phase 4–5 turn.

## Quick start

```bash
cd meridian
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Aurora ARNs + region

python scripts/init_aurora_schema.py
python scripts/seed_data.py
uvicorn backend.main:app --reload --port 8000
```

```bash
cd meridian/frontend
npm install && npm run dev
```

Open http://localhost:5173

Or run the full setup script: `meridian/scripts/setup.sh`

## Documentation

| Doc | Purpose |
| --- | ------- |
| [meridian/README.md](meridian/README.md) | Setup, API, schema, tech stack |
| [meridian/DEMO_SCRIPT.md](meridian/DEMO_SCRIPT.md) | 60-minute workshop script |
| [meridian/STRUCTURE.md](meridian/STRUCTURE.md) | Live code vs reference agents |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution guidelines |

## Tech stack

- **Frontend:** React, Vite, TypeScript
- **Backend:** FastAPI, Strands Agents, Amazon Bedrock (Claude Opus 4.8 + Cohere Embed v4 / Rerank 3.5)
- **Data:** Aurora PostgreSQL 17, pgvector, RDS Data API
- **Protocol:** Model Context Protocol (Phase 2)
- **Orchestration:** Strands (Phases 1–4) · LangGraph `StateGraph` (Phase 5)

> Educational demo — not production-hardened.
