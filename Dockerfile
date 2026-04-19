FROM python:3.11-slim

WORKDIR /app

# Install system deps for Playwright + curl (used by HEALTHCHECK).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

# Sprint 10A §10-4: copy the full app tree.
# Previous Dockerfile shipped without batch/, scripts/, spec/, calc.js — image
# imported OK at boot (lazy batch imports) but crashed on first /api/batch-*
# call and served broken /calc.js / /spec/constants.json.
COPY app.py calc.js index.html ./
COPY batch/ ./batch/
COPY scripts/ ./scripts/
COPY spec/ ./spec/
COPY examples/ ./examples/

# Data + logs dirs need to exist and be writable by the non-root user.
# spec/profile.local.json is NOT copied (gitignored / dockerignored). The
# container boots with redacted defaults and the browser shows a yellow
# banner until the operator mounts a real profile file as a secret.
RUN mkdir -p /app/data /app/logs \
    && groupadd --system app \
    && useradd --system --gid app --home /app --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

ENV PORT=8000
EXPOSE 8000

# Sprint 10A §10-4: bind loopback by default (matches app.py's local posture).
# To expose on a LAN / Tailscale, override with --host=0.0.0.0 at run-time AND
# accept the /spec/profile.json 403 (loopback-only by design).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://127.0.0.1:8000/api/models || exit 1

CMD ["python", "-c", "import uvicorn; uvicorn.run('app:app', host='127.0.0.1', port=8000)"]
