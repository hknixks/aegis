# Aegis dashboard backend (aegis.api). Not the CLI, not a second
# execution path — this container only ever runs `uvicorn aegis.api:app`,
# the same read-only visualization backend described in that module's
# own docstring.
FROM python:3.12-slim

WORKDIR /app

# Install the project with the [api] extra (fastapi + uvicorn). The
# [hermes] extra is intentionally NOT installed here — Hermes stays
# opt-in (AEGIS_HERMES_ENABLED) and this image doesn't need anthropic/mcp
# just to serve the dashboard.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[api]"

# AEGIS_AUDIT_LOG_PATH's file writes need a real, writable, persistent
# path — see README's Deployment section. /data is where the Fly volume
# in fly.toml is mounted.
ENV AEGIS_AUDIT_LOG_PATH=/data/aegis_runs.jsonl
RUN mkdir -p /data

EXPOSE 8000
CMD ["uvicorn", "aegis.api:app", "--host", "0.0.0.0", "--port", "8000"]
