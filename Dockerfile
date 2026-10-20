FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12.10-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY chargeops ./chargeops
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn", "chargeops.api:app", "--host", "0.0.0.0", "--port", "8000"]
