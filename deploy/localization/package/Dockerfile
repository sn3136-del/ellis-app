# Ellis visa information database, backend image for local acceptance.
# Build context is the package folder (docker compose builds it for you).
FROM python:3.12-slim
WORKDIR /opt/ellis/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY data /opt/ellis/data
ENV DATABASE_URL=sqlite:////var/lib/ellis/ellis.db
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
