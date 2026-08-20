# Demand-Signal Workflow — app image (Python 3.11+, per PRD §6).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install pinned dependencies (cached as a separate layer).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source. Local artifacts / secrets are excluded via .dockerignore.
COPY . .

EXPOSE 8000

# Run Alembic migrations, then start uvicorn (see docker-entrypoint.sh).
ENTRYPOINT ["./docker-entrypoint.sh"]