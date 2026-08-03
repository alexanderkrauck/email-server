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
)
from src.handlers.email_handler import (
    router as email_router,
)
from src.mcp_tools import register_mcp_tools
from src.security.auth import build_mcp_auth_provider
from src.security.crypto import persistent_secret
from src.web_pages import service_page

logger = logging.getLogger(__name__)


# Module level rather than app.state: /health lives on api_app, which is mounted
# inside final_app, and a mounted sub-app gets its own state object. Reading the
# flag off request.app there would always find it missing.
DATABASE_READY = False


async def _prepare_database(app: FastAPI) -> None:
    """Migrate, then start syncing. Keep trying rather than dying.

    A database that is briefly unreachable -- restarting alongside this
    container, or a few seconds slower to accept connections -- used to abort
    startup and leave Docker restarting the process in a loop. Nothing about the
    MCP surface needs the database to be described, only to be used, so the
    server now comes up either way and reports itself unhealthy until it can
    actually serve.
    """
    delay = 2
    while True:
        try:
            init_database()
            break
        except Exception as exc:
            logger.error(
                "Database is not ready (%s: %s); retrying in %ss",
                type(exc).__name__,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    global DATABASE_READY
    DATABASE_READY = True
    app.state.processing_task = asyncio.create_task(email_processor.start_processing())


@asynccontextmanager
async def service_lifespan(app: FastAPI):
    logger.info("Starting Email Server")
    app.state.processing_task = None
    preparation = asyncio.create_task(_prepare_database(app))
    try:
        yield
    finally:
        logger.info("Shutting down Email Server")
        preparation.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await preparation
        await email_processor.stop_processing()
        email_sender_manager.cleanup()
        processing_task = getattr(app.state, "processing_task", None)
        if processing_task:
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
    # 503 while the database is unreachable: the process is up and can describe
    # itself, but it cannot answer a question about anyone's mail, and a health
    # check that says otherwise is worse than no health check.
    ready = DATABASE_READY
    body = {
        "status": "healthy" if ready else "degraded",
        "service": "email-server",
        "database": "ready" if ready else "unavailable",
        "processor_active": email_processor.processing,
    }
    return JSONResponse(status_code=200 if ready else 503, content=body)


api_app.include_router(email_router)

mcp = FastMCP(
    "Email Server",
    instructions=(
        "Manage, search, retrieve, and send mail owned by the authenticated user. "
        "Mailbox passwords are write-only and are never returned. Passwords may be "
        "supplied directly or entered through a short-lived password-only browser link."
    ),
    auth=build_mcp_auth_provider(),
    mask_error_details=True,
)
register_mcp_tools(mcp)
mcp_app = mcp.http_app(path="/mcp", stateless_http=True)


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with service_lifespan(app), mcp_app.lifespan(app):
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
    https_only=settings.auth_mode != "development",
    same_site="lax",
)
final_app.mount("/api/v1", api_app)


@final_app.get("/")
async def root(connected: str | None = None):
    return service_page(connected)


# The MCP ASGI app owns /mcp and root-level OAuth discovery/callback routes.
final_app.mount("/", mcp_app)
