#!/usr/bin/env python3
"""Main entry point for Email Server."""

import logging
import sys

import uvicorn

from src.config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.log_file) if settings.log_file else logging.NullHandler(),
    ],
)

logger = logging.getLogger(__name__)


def run_server():
    """Run the FastAPI server."""
    logger.info("Starting Email Server on %s:%s", settings.api_host, settings.api_port)
    db_display = settings.database_url.split("@")[-1] if "@" in settings.database_url else settings.database_url
    logger.info("Database: %s", db_display)
    logger.info("HTTP API available at: /api/v1")
    logger.info("MCP endpoint available at: /llm/mcp")
    logger.info("API Documentation available at: /api/v1/docs")

    uvicorn.run(
        "server:final_app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
        log_level=settings.log_level.lower(),
    )


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Email Server")
    parser.add_argument("--host", default=settings.api_host, help=f"Server host (default: {settings.api_host})")
    parser.add_argument(
        "--port", type=int, default=settings.api_port, help=f"Server port (default: {settings.api_port})"
    )

    args = parser.parse_args()

    # Update settings if provided
    if args.host:
        settings.api_host = args.host
    if args.port:
        settings.api_port = args.port

    try:
        run_server()
    except KeyboardInterrupt:
        logger.info("Shutting down Email Server...")
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
