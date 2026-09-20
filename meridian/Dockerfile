# Meridian backend for AWS App Runner.
#
# Built by the CDK stack in infra/ with Finch or Docker. The image carries the
# FastAPI app, the hash-pinned Python dependencies, and the pinned Phase 2 MCP
# server in the same hash lock so a Phase 2 turn never downloads at
# request time. Configuration arrives as App Runner environment variables and
# the configured origin-access token; nothing from .env is copied in.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 meridian

WORKDIR /app

# Include the Phase 2 server and compatible MCP SDK in the same hash lock.
COPY requirements.txt ./
RUN pip install --upgrade pip==26.2.1 \
    && pip install --require-hashes -r requirements.txt

# Both launchers use this module. Fail the build if it cannot load.
RUN python -m awslabs.postgres_mcp_server.server --help >/dev/null

COPY backend ./backend
COPY examples ./examples

USER meridian

ENV HOST=0.0.0.0 \
    PORT=8000 \
    ENVIRONMENT=production \
    AGENTCORE_SKIP_CLI_SYNC=1

EXPOSE 8000

# backend/launch.py opens port 8000 at once and hands the socket to uvicorn;
# App Runner refused deployments whose port stayed closed while the app loaded.
CMD ["python", "-m", "backend.launch"]
