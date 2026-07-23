FROM python:3.12-slim

# curl is used by the compose healthchecks (minio); libpq is bundled in
# psycopg[binary] so no system Postgres client libs are needed.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the application package is copied — never tests, .env, the local db, or
# any credential (enforced by .dockerignore).
COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# API by default; the worker + mock-portal override the command in compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
