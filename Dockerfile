FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12.10-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY chargeops ./chargeops
# Committed offline evaluation summaries served read-only by GET /api/v1/evaluations.
COPY data/evaluation ./data/evaluation
ARG APP_VERSION=0.1.0
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DD_VERSION=$APP_VERSION
# Run under ddtrace-run only when DD_TRACE_ENABLED=true; otherwise exec the command unchanged.
ENTRYPOINT ["sh", "-c", "if [ \"$DD_TRACE_ENABLED\" = true ]; then exec ddtrace-run \"$@\"; fi; exec \"$@\"", "--"]
CMD ["uvicorn", "chargeops.api:app", "--host", "0.0.0.0", "--port", "8000"]
