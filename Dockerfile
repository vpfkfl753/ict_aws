FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY tacit tacit
COPY tacit_runtime tacit_runtime
RUN uv sync --locked --no-dev
COPY deploy/demo-workspaces workspaces

# UID 1000 matches the default EC2 login user so it can read the 0600 files in deploy/config.
RUN useradd --create-home --uid 1000 tacit && mkdir /data && chown tacit:tacit /data
USER tacit
ENTRYPOINT ["/app/.venv/bin/tacit"]
