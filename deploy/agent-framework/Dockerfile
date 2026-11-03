FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/ ./backend/
COPY examples/agent_framework/ ./examples/agent_framework/
ENTRYPOINT ["/app/.venv/bin/python", "-m", "backend.agent_framework"]
CMD ["--help"]
