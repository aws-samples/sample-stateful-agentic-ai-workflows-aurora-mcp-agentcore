# Meridian backend for AWS App Runner.
#
# Built by the CDK stack in infra/ with Finch or Docker. The image carries the
# FastAPI app, the hash-pinned Python dependencies, and the pinned Phase 2 MCP
# server pre-resolved into the uv cache so a Phase 2 turn never downloads at
# request time. Configuration arrives as App Runner environment variables and
# one Secrets Manager secret; nothing from .env is copied in.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_CACHE_DIR=/opt/uv/cache \
    UV_TOOL_DIR=/opt/uv/tools \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 meridian \
    && mkdir -p /opt/uv \
    && chown -R meridian:meridian /opt/uv

WORKDIR /app

# uv from PyPI (the same index the requirements come from) supplies the uvx
# launcher Phase 2 uses to start the pinned postgres MCP server.
COPY requirements.txt ./
RUN pip install uv==0.11.21 \
    && pip install --require-hashes -r requirements.txt

# Phase 2 spawns `uvx awslabs.postgres-mcp-server@1.0.9`; resolve it now so the
# cache is warm inside the image. The server exits immediately without a
# database, which is all this step needs.
RUN uvx --python /usr/local/bin/python awslabs.postgres-mcp-server@1.0.9 --help >/dev/null 2>&1 || true \
    && chown -R meridian:meridian /opt/uv

COPY backend ./backend
COPY examples ./examples

USER meridian

ENV HOST=0.0.0.0 \
    PORT=8000 \
    ENVIRONMENT=production \
    AGENTCORE_SKIP_CLI_SYNC=1

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-keep-alive", "75"]
