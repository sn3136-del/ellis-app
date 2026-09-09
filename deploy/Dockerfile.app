# Ellis, deploy image: the FULL app the local script runs (backend + worker +
# built frontend), served on one port behind Caddy. Mirrors scripts/start-
# ellis-web.sh, for Linux.
FROM node:20-bookworm-slim AS web
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --silent
COPY . .
# Use the same React/JSX and browser configuration as the tested web release.
# A bare vite build skips plugin-react and produces a blank page at runtime.
RUN ELLIS_PUBLIC=1 npm run build:web -- --outDir ../../out/renderer \
      --emptyOutDir --logLevel error

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl openssl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
# The whole repo (app code, migrations, encrypted secrets, seed bundles).
COPY . .
# The built frontend from the web stage, where Caddy will serve it.
COPY --from=web /app/out/renderer ./out/renderer
ENV PYTHONUNBUFFERED=1 ELLIS_RUNTIME_MODE=local_real_services
EXPOSE 8000
# Unlock the sealed keys, migrate, seed the warm cache, then run the API.
CMD ["bash", "deploy/run-app.sh"]
