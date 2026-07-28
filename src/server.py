"""Authenticated FastAPI and explicit FastMCP service."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.database.connection import init_database
from src.handlers.email_handler import (
    email_processor,
    email_sender_manager,
    router as email_router,
)
from src.mcp_tools import register_mcp_tools
from src.security.auth import build_mcp_auth_provider
from src.security.crypto import persistent_secret

logger = logging.getLogger(__name__)


@asynccontextmanager
async def service_lifespan(app: FastAPI):
    logger.info("Starting Email Server")
    init_database()
    processing_task = asyncio.create_task(email_processor.start_processing())
    app.state.processing_task = processing_task
    try:
        yield
    finally:
        logger.info("Shutting down Email Server")
        await email_processor.stop_processing()
        email_sender_manager.cleanup()
        processing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await processing_task


api_app = FastAPI(
    title="Email Server API",
    description="Authenticated multi-user mail account management",
    version="2.0.0",
)


@api_app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled API exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@api_app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "email-server", "processor_active": email_processor.processing}


api_app.include_router(email_router)

mcp = FastMCP(
    "Email Server",
    instructions=(
        "Manage, search, retrieve, and send mail owned by the authenticated user. "
        "Mailbox passwords are write-only and are never returned."
    ),
    auth=build_mcp_auth_provider(),
    mask_error_details=True,
)
register_mcp_tools(mcp)
mcp_app = mcp.http_app(path="/mcp", stateless_http=True)


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with service_lifespan(app):
        async with mcp_app.lifespan(app):
            yield


final_app = FastAPI(
    title="Email Server",
    description="Multi-user email service with OAuth-protected MCP",
    version="2.0.0",
    lifespan=combined_lifespan,
)
final_app.add_middleware(
    SessionMiddleware,
    secret_key=persistent_secret(settings.session_secret, "session.key"),
    https_only=settings.auth_mode == "google",
    same_site="lax",
)
final_app.mount("/api/v1", api_app)


@final_app.get("/")
async def root():
    return {
        "service": "Email Server",
        "version": "2.0.0",
        "auth_mode": settings.auth_mode,
        "apis": {"http": "/api/v1", "mcp": "/mcp", "health": "/api/v1/health"},
    }


# The MCP ASGI app owns /mcp and root-level OAuth discovery/callback routes.
final_app.mount("/", mcp_app)
