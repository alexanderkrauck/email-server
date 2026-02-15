# Use Python 3.11 Alpine for minimal size (like chronotask)
FROM python:3.11-alpine

# Install system dependencies
# - ripgrep: for regex search
# - tesseract-ocr: for image text extraction (OCR)
# - leptonica: image processing library for OCR
# - gcc, musl-dev, libffi-dev, etc.: build dependencies for Python packages
# - pillow dependencies: for image handling
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    python3-dev \
    curl \
    ripgrep \
    tesseract-ocr \
    leptonica \
    tesseract-ocr-eng \
    pillow \
    && rm -rf /var/cache/apk/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Create data directory for database and email logs
RUN mkdir -p /app/data/emails

# Create non-root user for security
RUN adduser -D -u 1000 emailserver && \
    chmod -R 777 /app/data

# For testing, run as root (like chronotask pattern)
# USER emailserver

# Expose HTTP API port and SMTP port
EXPOSE 8000 2525

# Volume for persistent data
VOLUME ["/app/data"]

# Environment variables
ENV EMAILSERVER_DATABASE_URL=sqlite:////app/data/emailserver.db \
    EMAILSERVER_LOG_FILE=/app/data/emailserver.log \
    EMAILSERVER_EMAIL_LOG_DIR=/app/data/emails \
    PYTHONUNBUFFERED=1

# Default command - run HTTP server
WORKDIR /app
CMD ["python", "-m", "src.main"]