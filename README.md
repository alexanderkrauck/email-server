# Email Server

A Python-based multi-SMTP email server that connects to multiple email accounts, retrieves emails, and logs their content to files. Built with FastAPI and FastMCP, following the chronotask project architecture pattern.

## Features

- **Multi-SMTP Support**: Connect to multiple email servers simultaneously
- **Email Content Logging**: Save email content to structured files (JSON or text format)
- **Attachment Handling**: Extract, store, and serve email attachments with metadata
- **Email Sending**: Full SMTP sending capabilities with templates and attachments
- **REST API**: Full RESTful API for managing SMTP configurations and viewing emails
- **MCP Integration**: Model Context Protocol support for AI agent interactions
- **Database Storage**: SQLite database for email metadata and server configurations
- **Background Processing**: Continuous email checking and processing
- **Docker Support**: Containerized deployment with Docker and docker-compose
- **File Management**: Automatic cleanup of old log files

## Quick Start

### Using Docker Compose (Recommended)

```bash
# Clone and navigate to the project
cd email-server

# Start the service
docker-compose up -d

# View logs
docker-compose logs -f email-server
```

### Manual Docker Build

```bash
# Build the image
docker build -t email-server .

# Run the container
docker run -d \
  --name email-server \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  email-server
```

### Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the server
cd src
python main.py
```

## Configuration

The server uses environment variables with the `EMAILSERVER_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAILSERVER_DATABASE_URL` | `sqlite:////app/data/emailserver.db` | Database connection string |
| `EMAILSERVER_API_HOST` | `0.0.0.0` | API server host |
| `EMAILSERVER_API_PORT` | `8000` | API server port |
| `EMAILSERVER_EMAIL_LOG_DIR` | `/app/data/emails` | Directory for email log files |
| `EMAILSERVER_LOG_LEVEL` | `INFO` | Logging level |
| `EMAILSERVER_LOG_FORMAT` | `json` | Email log format (json/text) |
| `EMAILSERVER_EMAIL_CHECK_INTERVAL` | `30` | Email check interval in seconds |
| `EMAILSERVER_MAX_ATTACHMENT_SIZE` | `10485760` | Max attachment size (10MB) |
| `EMAILSERVER_INLINE_ATTACHMENT_SIZE` | `1048576` | Size limit for DB storage (1MB) |
| `EMAILSERVER_MCP_ENABLED` | `true` | Enable MCP support |
| `EMAILSERVER_MCP_PORT` | `8001` | MCP server port |

## API Usage

Once running, the server provides both HTTP API and MCP endpoints:

### API Documentation
- **HTTP API**: http://localhost:8000/api/v1
- **Swagger UI**: http://localhost:8000/api/v1/docs
- **ReDoc**: http://localhost:8000/api/v1/redoc
- **MCP Endpoint**: http://localhost:8000/llm/mcp

### Key Endpoints

#### SMTP Configuration Management
```bash
# List all SMTP configurations
curl http://localhost:8000/api/v1/smtp-configs

# Create new SMTP configuration
curl -X POST http://localhost:8000/api/v1/smtp-configs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Gmail Account",
    "host": "imap.gmail.com",
    "port": 993,
    "username": "your-email@gmail.com",
    "password": "your-app-password",
    "use_tls": true,
    "use_ssl": true,
    "enabled": true
  }'

# Manually trigger processing for a server
curl -X POST http://localhost:8000/api/v1/smtp-configs/1/process
```

#### Email Management
```bash
# List processed emails
curl http://localhost:8000/api/v1/emails

# Get specific email content
curl http://localhost:8000/api/v1/emails/1/content

# List email attachments
curl http://localhost:8000/api/v1/emails/1/attachments

# Download attachment
curl http://localhost:8000/api/v1/attachments/1 --output attachment.pdf
```

#### Email Sending
```bash
# Send simple email
curl -X POST http://localhost:8000/api/v1/send-email \
  -H "Content-Type: application/json" \
  -d '{
    "smtp_config_id": 1,
    "to_addresses": ["recipient@example.com"],
    "subject": "Test Email",
    "body_text": "Hello from Email Server!",
    "body_html": "<h1>Hello from Email Server!</h1>"
  }'

# Test SMTP connection
curl http://localhost:8000/api/v1/smtp-configs/1/test-connection
```

#### System Status
```bash
# Check system status
curl http://localhost:8000/api/v1/status

# Health check
curl http://localhost:8000/health
```

## MCP Integration

The Email Server provides MCP (Model Context Protocol) support for AI agent interactions. This allows AI assistants like Claude to directly interact with the email server.

### MCP Endpoint
- **MCP Server**: http://localhost:8000/llm/mcp

### Available MCP Functions
All HTTP API endpoints are automatically exposed as MCP functions, including:
- `get_api_v1_smtp_configs` - List SMTP configurations
- `post_api_v1_smtp_configs` - Create SMTP configuration
- `get_api_v1_emails` - List processed emails
- `get_api_v1_emails_email_id_content` - Get email content
- `get_api_v1_emails_email_id_attachments` - List email attachments
- `get_api_v1_attachments_attachment_id` - Download attachment
- `post_api_v1_send_email` - Send email
- `post_api_v1_send_email_with_attachments` - Send email with attachments
- `get_api_v1_status` - Get system status

### Using with Claude Code
Add the MCP server to your Claude Code configuration:

```json
{
  "mcpServers": {
    "email-server": {
      "command": "curl",
      "args": ["-X", "POST", "http://localhost:8000/llm/mcp"]
    }
  }
}
```

## Email Server Configuration

### Gmail Setup
1. Enable 2-factor authentication
2. Generate an app password
3. Use these settings:
   - Host: `imap.gmail.com`
   - Port: `993`
   - Use SSL: `true`
   - Use TLS: `false`

### Outlook/Hotmail Setup
- Host: `outlook.office365.com`
- Port: `993`
- Use SSL: `true`

### Yahoo Mail Setup
- Host: `imap.mail.yahoo.com`
- Port: `993`
- Use SSL: `true`

## File Structure

```
email-server/
├── docker-compose.yml          # Docker Compose configuration
├── Dockerfile                  # Docker build instructions
├── requirements.txt            # Python dependencies
├── README.md                  # This file
├── src/                       # Source code
│   ├── main.py               # Application entry point
│   ├── server.py             # FastAPI server setup
│   ├── config.py             # Configuration settings
│   ├── models/               # Database models
│   │   ├── smtp_config.py    # SMTP configuration model
│   │   └── email.py          # Email log model
│   ├── database/             # Database connection
│   │   └── connection.py     # SQLAlchemy setup
│   ├── email/                # Email processing
│   │   ├── smtp_client.py    # IMAP client for fetching emails
│   │   ├── email_processor.py # Email processing orchestrator
│   │   └── email_logger.py   # File logging functionality
│   └── handlers/             # API handlers
│       └── email_handler.py  # FastAPI route handlers
└── data/                     # Data directory (mounted volume)
    ├── emailserver.db        # SQLite database
    ├── emailserver.log       # Application logs
    └── emails/               # Email content files
```

## Email Log Files

Emails are logged to files in two formats:

### JSON Format (default)
```json
{
  "email_id": 1,
  "timestamp": "2023-12-07T10:30:00",
  "sender": "sender@example.com",
  "recipient": "recipient@example.com",
  "subject": "Test Email",
  "body": {
    "plain": "Plain text content",
    "html": "<html>...</html>"
  }
}
```

### Text Format
```
================================================================================
Email ID: 1
From: sender@example.com
To: recipient@example.com
Subject: Test Email
================================================================================
PLAIN TEXT BODY:
Hello, this is a test email.
--------------------------------------------------------------------------------
```

## Monitoring

The server provides several monitoring endpoints:

- `/health` - Basic health check
- `/api/v1/status` - Detailed system status
- `/api/v1/log-files` - List recent log files

## Security Considerations

- **Password Storage**: Passwords are stored in plain text in the database. For production use, implement encryption.
- **API Security**: No authentication is implemented. Add API keys or OAuth for production.
- **Network Security**: Use HTTPS in production and secure your email credentials.

## Troubleshooting

### Common Issues

1. **Connection Failed**: Check email server settings and credentials
2. **Permission Denied**: Ensure proper file permissions for data directory
3. **SSL Errors**: Verify SSL/TLS settings match your email provider

### Logs
Check application logs for detailed error information:
```bash
# Docker logs
docker-compose logs -f email-server

# Local file logs
tail -f data/emailserver.log
```

## License

This project follows the same license as the parent goedlike project.