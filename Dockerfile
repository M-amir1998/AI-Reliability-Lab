# Lightweight, non-root, CPU-only image for the CLI, test runner and reporting.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra observability

FROM python:3.12-slim
RUN groupadd --system lab && useradd --system --gid lab --create-home --home-dir /home/lab lab
WORKDIR /workspace
COPY --from=build --chown=lab:lab /app /app
COPY --chown=lab:lab configs /workspace/configs
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER lab
ENTRYPOINT ["aireliability"]
CMD ["--help"]
