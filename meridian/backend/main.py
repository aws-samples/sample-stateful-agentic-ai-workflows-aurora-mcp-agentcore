"""
Meridian Backend - FastAPI Application

Main entry point for the Meridian travel concierge demo backend.
Provides REST API endpoints for chat, trip catalog, and traveler memory.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from pydantic import BaseModel

from backend.agentcore.caller_credential import CallerCredentialMiddleware
from backend.agentcore.errors import CallerCredentialError
from backend.authorization import TravelerAuthorizationError
from backend.http_auth import require_http_principal
from backend.token_expiry import credential_error, error_code

# Load environment variables from .env file
load_dotenv()

from backend.logging_config import setup_logging, log_startup_banner

# Honour LOG_LEVEL / LOG_JSON from .env before other imports log anything.
setup_logging()
logger = logging.getLogger(__name__)
log_startup_banner()

# Import routers
from backend.config import EMBEDDING_MODEL_ID, bedrock_model_label, config
from backend.routers import (
    chat_router,
    products_router,
    packages_router,
    memory_router,
    diagnostics_router,
    journeys_router,
    session_router,
)


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""
    status: str
    version: str
    environment: str
    bedrock_model_id: str
    bedrock_model_label: str
    embedding_model_id: str
    checkpoint_backend: str
    checkpoint_durable: bool
    checkpoint_required: bool
    aurora_reachable: bool
    workflow_runtime_configured: bool
    degraded_component: str | None = None
    degraded_error_class: str | None = None


class ErrorResponse(BaseModel):
    """Standard error response model."""
    error: str
    request_id: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.
    
    Handles startup and shutdown events for the FastAPI application.
    """
    # Startup
    print("Starting Meridian Backend...")
    print(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
    print(f"AWS Region: {os.getenv('AWS_DEFAULT_REGION', 'us-east-1')}")
    print(f"Log level: {os.getenv('LOG_LEVEL', 'INFO')} · agent verbose: {os.getenv('LOG_AGENT_VERBOSE', 'true')}")

    try:
        yield
    finally:
        print("Shutting down Meridian Backend...")


# Create FastAPI application
app = FastAPI(
    title="Meridian Backend",
    description="Backend API for the Meridian agentic travel concierge demo",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None, redoc_url=None, openapi_url=None,
    responses={
        400: {"model": ErrorResponse, "description": "Bad Request"},
        500: {"model": ErrorResponse, "description": "Internal Server Error"},
        503: {"model": ErrorResponse, "description": "Service Unavailable"},
    }
)

def parse_cors_origins(value: str) -> list[str]:
    """Parse an explicit CORS allow-list and reject unsafe wildcard values."""
    origins = [origin.strip() for origin in value.split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError(
            "CORS_ORIGINS must name explicit origins; wildcard CORS is not supported."
        )
    return origins


# Configure CORS for frontend communication. The default accepts only local
# browser origins on arbitrary development ports. Hosted deployments must set
# an explicit allow-list rather than silently inheriting wildcard CORS.
custom_origins = parse_cors_origins(os.getenv("CORS_ORIGINS", ""))

if custom_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=custom_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

# Hand each request's verified access token to the AgentCore clients below the routes.
app.add_middleware(CallerCredentialMiddleware)

# Include routers
app.include_router(chat_router)
app.include_router(packages_router)
app.include_router(products_router)
app.include_router(memory_router)
app.include_router(diagnostics_router)
app.include_router(journeys_router)
app.include_router(session_router)


async def _health_payload() -> HealthResponse:
    """Build the health response from what is true right now.

    `status` is `healthy` only when a live Aurora query just found the
    workflow snapshot and session stop tables (see `backend.health_probe`)
    and the workflow Runtime ARN resolves; otherwise it is `degraded` and
    names the failing component and its error class. The checkpoint
    fields remain the backend's configured checkpoint state, not a
    second live probe.
    """
    from backend.agents.phase_05_workflow.service import workflow_store_status
    from backend.agentcore.cli_config import resolve_agentcore_config
    from backend.health_probe import probe_aurora

    model_id = config.bedrock.model_id
    checkpoint = workflow_store_status()
    aurora = await probe_aurora()
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    runtime_configured = bool(resolve_agentcore_config().workflow_runtime_arn)
    # App Runner always sets AGENTCORE_WORKFLOW_RUNTIME_ARN, so an unresolved ARN is a
    # broken deployment. Local development runs without a Runtime, so it stays healthy there.
    runtime_missing = not runtime_configured and environment != "development"
    degraded_component = aurora.component or ("workflow_runtime" if runtime_missing else None)
    return HealthResponse(
        status="degraded" if (not aurora.ok or runtime_missing) else "healthy",
        version="1.0.0",
        environment=environment,
        bedrock_model_id=model_id,
        bedrock_model_label=bedrock_model_label(model_id),
        embedding_model_id=EMBEDDING_MODEL_ID,
        checkpoint_backend=checkpoint["kind"],
        checkpoint_durable=checkpoint["durable"],
        checkpoint_required=checkpoint["required"],
        aurora_reachable=aurora.component != "aurora",
        workflow_runtime_configured=runtime_configured,
        degraded_component=degraded_component,
        degraded_error_class=aurora.error_class,
    )


@app.get("/", response_model=HealthResponse, dependencies=[Depends(require_http_principal)])
async def root() -> HealthResponse:
    """
    Root endpoint - returns basic service information.
    """
    return await _health_payload()


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Public process liveness only; readiness/configuration require authentication."""
    return {"status": "healthy"}


@app.get("/api/health", response_model=HealthResponse, dependencies=[Depends(require_http_principal)])
async def api_health_check() -> HealthResponse:
    """
    API health check endpoint.
    
    Returns:
        HealthResponse with service status, version, and environment
    """
    return await _health_payload()


# The schema and interactive API consoles use the same origin boundary as data.
@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_http_principal)])
async def protected_openapi():
    return app.openapi()


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_http_principal)])
async def protected_docs():
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Meridian API")


@app.get("/redoc", include_in_schema=False, dependencies=[Depends(require_http_principal)])
async def protected_redoc():
    return get_redoc_html(openapi_url="/openapi.json", title="Meridian API")


# Exception handlers for consistent error responses
@app.exception_handler(TravelerAuthorizationError)
async def traveler_authorization_exception_handler(request, exc: TravelerAuthorizationError):
    """A revoked workload grant is a refusal, including on receipt readback."""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=403, content={"error": "This traveler is not authorized for the current workload."})


@app.exception_handler(CallerCredentialError)
async def caller_credential_exception_handler(request, exc: CallerCredentialError):
    """A missing or expired caller token means sign in again, whichever route hit it.

    Expiry carries the retryable ``token_expired`` code; a missing token carries
    ``sign_in_required``. Neither body echoes the exception text.
    """
    logger.info("Caller credential refused: %s", exc.__class__.__name__)
    return await http_exception_handler(request, credential_error(exc))


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """Handle HTTP exceptions with consistent error format."""
    from fastapi.responses import JSONResponse
    content = {"error": exc.detail}
    code = error_code(exc)
    if code:
        content["code"] = code
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


@app.exception_handler(Exception)
async def general_exception_handler(request, exc: Exception):
    """Answer an unexpected error with a generic body and a short reference.

    The exception text can carry bearer tokens from the HTTPS clients, so neither
    the response nor the log holds it: the log gets the exception class and the
    reference only.
    """
    from fastapi.responses import JSONResponse
    import uuid

    reference = uuid.uuid4().hex[:8]
    logger.error("Unexpected %s (request_id=%s)", exc.__class__.__name__, reference)

    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "request_id": reference},
    )


if __name__ == "__main__":
    import uvicorn
    
    # Get configuration from environment
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("ENVIRONMENT", "development") == "development"
    
    print(f"Starting server on {host}:{port}")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload
    )
