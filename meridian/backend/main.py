"""
Meridian Backend - FastAPI Application

Main entry point for the Meridian travel concierge demo backend.
Provides REST API endpoints for chat, trip catalog, and traveler memory.
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Load environment variables from .env file
load_dotenv()

from backend.logging_config import setup_logging, log_startup_banner

# Honour LOG_LEVEL / LOG_JSON from .env before other imports log anything.
setup_logging()
log_startup_banner()

# Import routers
from backend.config import EMBEDDING_MODEL_ID, bedrock_model_label, config
from backend.routers import (
    chat_router,
    products_router,
    packages_router,
    memory_router,
    diagnostics_router,
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

    checkpoint_required = os.getenv(
        "LANGGRAPH_CHECKPOINT_REQUIRED", "false"
    ).lower() in {"1", "true", "yes", "on"}
    checkpoint_startup = os.getenv(
        "LANGGRAPH_CHECKPOINT_INIT_ON_STARTUP", "false"
    ).lower() in {"1", "true", "yes", "on"}
    if checkpoint_required or checkpoint_startup:
        from backend.agents.orchestration_05.workflow import (
            initialize_checkpoint_backend,
        )

        await initialize_checkpoint_backend()

    try:
        yield
    finally:
        from backend.agents.orchestration_05.workflow import (
            close_checkpoint_backend,
        )

        await close_checkpoint_backend()
        print("Shutting down Meridian Backend...")


# Create FastAPI application
app = FastAPI(
    title="Meridian Backend",
    description="Backend API for the Meridian agentic travel concierge demo",
    version="1.0.0",
    lifespan=lifespan,
    responses={
        400: {"model": ErrorResponse, "description": "Bad Request"},
        500: {"model": ErrorResponse, "description": "Internal Server Error"},
        503: {"model": ErrorResponse, "description": "Service Unavailable"},
    }
)

# Configure CORS for frontend communication.
#
# This API has no cookie/session auth — the frontend calls it with plain JSON
# and never sends credentials. For local/demo use we therefore accept ANY
# origin so CORS preflights never 400. This matters because the app is opened
# from several contexts during the workshop: localhost:5173, 127.0.0.1, ad-hoc
# Vite ports, and embedded/preview browsers that send `Origin: null` (which is
# not matchable by an explicit allow-list).
#
# To lock this down (e.g. a hosted deployment), set CORS_ORIGINS to a comma-
# separated allow-list; that path re-enables credentialed, origin-scoped CORS.
custom_origins = os.getenv("CORS_ORIGINS", "").strip()

if custom_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in custom_origins.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        # Must be False when allow_origins=["*"]; the API uses no cookies anyway.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Include routers
app.include_router(chat_router)
app.include_router(packages_router)
app.include_router(products_router)
app.include_router(memory_router)
app.include_router(diagnostics_router)


def _health_payload() -> HealthResponse:
    from backend.agents.orchestration_05.workflow import checkpoint_backend_status

    model_id = config.bedrock.model_id
    checkpoint = checkpoint_backend_status()
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        environment=os.getenv("ENVIRONMENT", "development"),
        bedrock_model_id=model_id,
        bedrock_model_label=bedrock_model_label(model_id),
        embedding_model_id=EMBEDDING_MODEL_ID,
        checkpoint_backend=checkpoint["kind"],
        checkpoint_durable=checkpoint["durable"],
        checkpoint_required=checkpoint["required"],
    )


@app.get("/", response_model=HealthResponse)
async def root() -> HealthResponse:
    """
    Root endpoint - returns basic service information.
    """
    return _health_payload()


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint for monitoring and load balancers.
    
    Returns:
        HealthResponse with service status, version, and environment
    """
    return _health_payload()


@app.get("/api/health", response_model=HealthResponse)
async def api_health_check() -> HealthResponse:
    """
    API health check endpoint.
    
    Returns:
        HealthResponse with service status, version, and environment
    """
    return _health_payload()


# Exception handlers for consistent error responses
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """Handle HTTP exceptions with consistent error format."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc: Exception):
    """Handle unexpected exceptions with consistent error format."""
    from fastapi.responses import JSONResponse
    import uuid
    
    request_id = str(uuid.uuid4())
    print(f"Unexpected error (request_id={request_id}): {exc}")
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "request_id": request_id
        }
    )


if __name__ == "__main__":
    import uvicorn
    
    # Get configuration from environment
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("ENVIRONMENT", "development") == "development"
    
    print(f"Starting server on {host}:{port}")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload
    )
