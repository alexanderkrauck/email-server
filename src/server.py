"""FastAPI + MCP server for Email Server using FastMCP."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP

from src.database.connection import init_database
from src.handlers.email_handler import email_processor
from src.handlers.email_handler import router as email_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    # Startup
    logger.info("Starting Email Server...")

    # Initialize database
    init_database()

    # Start email processing in background
    processing_task = asyncio.create_task(email_processor.start_processing())
    app.state.processing_task = processing_task

    logger.info("Email Server started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Email Server...")

    # Stop email processing
    await email_processor.stop_processing()

    # Cancel processing task
    if hasattr(app.state, "processing_task"):
        app.state.processing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.processing_task

    logger.info("Email Server shut down complete")


# 1. Create normal FastAPI app
app = FastAPI(
    title="Email Server API", description="Multi-SMTP Email Server with PostgreSQL", version="1.0.0", lifespan=lifespan
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "email-server", "processor_active": email_processor.processing}


# Include routers
app.include_router(email_router)


# Root endpoint for API
@app.get("/")
async def api_root():
    """API root endpoint with basic info."""
    return {
        "service": "Email Server API",
        "version": "1.0.0",
        "description": "Multi-SMTP Email Server with PostgreSQL",
        "api_docs": "/docs",
        "health": "/health",
    }


# 2. Convert to MCP
logger.info("Converting FastAPI app to MCP...")
mcp = FastMCP.from_fastapi(app, name="EmailServer MCP")

# 3. Create MCP's ASGI app
mcp_app = mcp.http_app(path="/mcp")


@asynccontextmanager
async def combined_lifespan(final_app: FastAPI):
    """Combined lifespan for both Email Server and MCP."""
    # Start our database and email processing services
    logger.info("Starting Email Server...")
    init_database()

    # Start email processing in background
    processing_task = asyncio.create_task(email_processor.start_processing())
    final_app.state.processing_task = processing_task
    logger.info("Email Server started successfully")

    # Start MCP services
    async with mcp_app.lifespan(final_app):
        yield

    # Shutdown
    logger.info("Shutting down Email Server...")
    await email_processor.stop_processing()

    # Cleanup email senders
    from src.handlers.email_handler import email_sender_manager

    email_sender_manager.cleanup()

    # Cancel processing task
    if hasattr(final_app.state, "processing_task"):
        final_app.state.processing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await final_app.state.processing_task
    logger.info("Email Server shut down complete")


# 4. Create the final app with combined lifespan
final_app = FastAPI(
    title="Email Server Service",
    description="Multi-SMTP Email Server with HTTP API and MCP support",
    version="1.0.0",
    lifespan=combined_lifespan,
)

# Mount the original API
final_app.mount("/api/v1", app)

# Mount the MCP app
final_app.mount("/llm", mcp_app)


# Add a root endpoint
@final_app.get("/")
async def root():
    """Root endpoint showing available APIs."""
    return {
        "service": "Email Server",
        "version": "1.0.0",
        "description": "Multi-SMTP Email Server with PostgreSQL",
        "apis": {"http": "/api/v1", "mcp": "/llm/mcp", "health": "/api/v1/health", "docs": "/api/v1/docs"},
    }
