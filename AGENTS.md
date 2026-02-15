# AGENTS.md - Email Server Development Guide

This file provides guidelines for agentic coding agents working on this codebase.

## Project Overview

- **Language**: Python 3.x
- **Framework**: FastAPI with FastMCP
- **Database**: SQLite with SQLAlchemy ORM
- **Testing**: pytest, pytest-asyncio, pytest-cov

## Build, Lint, and Test Commands

### Installation

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Running the Server

```bash
# Run from src directory
cd src
python main.py

# Or with custom host/port
python main.py --host 0.0.0.0 --port 8000

# With Docker
docker-compose up -d
```

### Running Tests

```bash
# Run all tests with verbose output
pytest -v

# Run all tests with coverage
pytest --cov=src --cov-report=html

# Run a single test file
pytest tests/test_email_handler.py -v

# Run a single test function
pytest tests/test_email_handler.py::test_list_emails -v

# Run tests matching a pattern
pytest -k "email" -v

# Run with asyncio mode
pytest -v - asyncio_mode=auto
```

### Linting and Code Quality

This project does not have a configured linter. Consider adding one of:

```bash
# Ruff (recommended - fast)
pip install ruff
ruff check src/
ruff format src/

# Flake8
pip install flake8
flake8 src/

# Pylint
pip install pylint
pylint src/
```

### Type Checking

```bash
# Using mypy
pip install mypy
mypy src/
```

## Code Style Guidelines

### General Conventions

- **Python Version**: 3.10+ (uses modern syntax like `from __future__ import annotations`)
- **Line Length**: 120 characters max
- **Indentation**: 4 spaces
- **Quotes**: Double quotes for strings, single quotes only when containing double quotes

### Import Order

Order imports strictly as shown:

1. Standard library (`import os`, `from pathlib import Path`)
2. Third-party packages (`from fastapi import ...`, `from sqlalchemy import ...`)
3. Local application imports (`from src.models import ...`)

```python
# Correct import order
import logging
import sys
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from src.database.connection import get_db
from src.models.smtp_config import SMTPConfig
from src.email.email_processor import EmailProcessor
```

### Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Variables | snake_case | `email_log_dir`, `max_attachment_size` |
| Functions | snake_case | `get_email()`, `send_email_via_config()` |
| Classes | PascalCase | `EmailLog`, `SMTPConfig`, `EmailProcessor` |
| Constants | UPPER_SNAKE_CASE | `MAX_EMAILS_PER_BATCH` |
| Files | snake_case | `email_handler.py`, `smtp_client.py` |
| Database tables | snake_case (plural) | `email_logs`, `smtp_configs` |

### Type Hints

- Use type hints for all function parameters and return values
- Use `Optional[X]` instead of `X | None`
- Use concrete types where possible (e.g., `list` instead of `List`)

```python
# Good
def process_email(email_id: int, include_content: bool = True) -> Optional[EmailResponse]:
    pass

def list_emails(skip: int = 0, limit: int = 50) -> List[EmailResponse]:
    pass
```

### Pydantic Models

- Use Pydantic v2 syntax (`model_config`, `from_attributes`)
- Use `BaseModel` for API request/response models
- Use `EmailStr` for email fields
- Set sensible defaults

```python
class SMTPConfigCreate(BaseModel):
    name: str
    host: str
    port: int = 993
    username: str
    password: str
    enabled: bool = True

    model_config = {"from_attributes": True}
```

### SQLAlchemy Models

- Use declarative base from `src/models/base.py`
- Define `__tablename__` as plural snake_case
- Use proper column types and constraints
- Define relationships using `relationship()`

```python
class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True)
    sender = Column(String(500), nullable=False)
    
    attachments = relationship("EmailAttachment", back_populates="email_log", 
                              cascade="all, delete-orphan")
```

### Async/Await Patterns

- Use `async def` for all FastAPI route handlers
- Use `asyncio` for background tasks
- Properly handle async context managers for startup/shutdown

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_database()
    processing_task = asyncio.create_task(email_processor.start_processing())
    app.state.processing_task = processing_task
    
    yield
    
    # Shutdown
    await email_processor.stop_processing()
```

### Error Handling

- Use FastAPI's `HTTPException` for HTTP errors
- Log errors with appropriate level
- Return meaningful error messages to clients (but don't expose internal details)

```python
@router.get("/emails/{email_id}")
async def get_email(email_id: int, db: Session = Depends(get_db)):
    email = db.query(EmailLog).filter(EmailLog.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email

try:
    result = await email_processor.process_server_now(config_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
except Exception as e:
    logger.error(f"Error processing email: {e}", exc_info=True)
    raise HTTPException(status_code=500, detail="Internal server error")
```

### Logging

- Use the standard `logging` module
- Get logger at module level: `logger = logging.getLogger(__name__)`
- Use appropriate log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL

```python
import logging

logger = logging.getLogger(__name__)

# Usage
logger.info(f"Starting Email Server on {settings.api_host}:{settings.api_port}")
logger.error(f"Failed to connect: {e}", exc_info=True)
```

### Configuration

- Use Pydantic `BaseSettings` with `pydantic-settings`
- Environment variables use `EMAILSERVER_` prefix
- Define sensible defaults

```python
class Settings(BaseSettings):
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    
    class Config:
        env_prefix = "EMAILSERVER_"
        env_file = ".env"

settings = Settings()
```

### Database Sessions

- Use dependency injection for database sessions
- Always close sessions properly

```python
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

## Project Structure

```
email-server/
├── src/
│   ├── main.py              # Entry point
│   ├── server.py            # FastAPI app setup
│   ├── config.py            # Settings
│   ├── models/              # SQLAlchemy models
│   │   ├── base.py
│   │   ├── email.py
│   │   ├── smtp_config.py
│   │   └── attachment.py
│   ├── database/
│   │   └── connection.py    # DB session management
│   ├── handlers/
│   │   └── email_handler.py # API route handlers
│   └── email/
│       ├── smtp_client.py
│       ├── smtp_sender.py
│       ├── email_processor.py
│       ├── email_logger.py
│       ├── attachment_handler.py
│       └── markdown_converter.py
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## Key API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/v1/smtp-configs` | List SMTP configurations |
| `POST /api/v1/smtp-configs` | Create SMTP configuration |
| `GET /api/v1/emails` | List processed emails |
| `GET /api/v1/emails/{id}` | Get email details |
| `POST /api/v1/send-email` | Send an email |
| `POST /api/v1/smtp-configs/{id}/process` | Trigger email processing |

## Development Notes

- Database is SQLite at `/app/data/emailserver.db` (in Docker)
- Email logs stored in `/app/data/emails/` (JSON or text format)
- MCP endpoint available at `/llm/mcp`
- Swagger UI at `/api/v1/docs`
