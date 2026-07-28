FROM python:3.11-alpine

# System dependencies
# - postgresql-dev: needed for psycopg2
# - tesseract-ocr: optional OCR for image attachments
RUN apk add --no-cache \
    gcc musl-dev libffi-dev openssl-dev python3-dev curl \
    postgresql-dev \
    tesseract-ocr tesseract-ocr-data-eng leptonica py3-pillow \
    && rm -rf /var/cache/apk/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY alembic/ ./alembic/
COPY src/ ./src/

# Create non-root user
RUN adduser -D -u 1000 emailserver \
    && mkdir -p /data \
    && chown -R emailserver:emailserver /data
USER emailserver

EXPOSE 8000 2525

ENV EMAILSERVER_DATABASE_URL=postgresql://emailserver:emailserver@postgres:5432/emailserver \
    FASTMCP_HOME=/data/fastmcp \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.main"]
