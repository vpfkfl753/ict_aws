FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1

WORKDIR /app

# Kiro CLI for TACIT_BACKEND=kiro. The musl build runs on this image's glibc on both
# architectures; checksums are pinned to the release in the URL.
RUN arch="$(uname -m)" \
    && case "$arch" in \
        aarch64) sum=fa763c0aee17abcc8139b37650f5f84bf5593750d16350d5364703d7110bb5ef ;; \
        x86_64) sum=3f6cb3be9959799f4506a152508dc6e9b17b1aa6369f84e43d94b36711c06311 ;; \
        *) echo "unsupported architecture $arch" >&2; exit 1 ;; \
    esac \
    && python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/kiro.tgz')" \
        "https://prod.download.cli.kiro.dev/stable/2.25.0/kirocli-$arch-linux-musl.tar.gz" \
    && echo "$sum  /tmp/kiro.tgz" | sha256sum -c - \
    && mkdir .tools && tar xzf /tmp/kiro.tgz -C .tools && rm /tmp/kiro.tgz

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY tacit tacit
COPY tacit_runtime tacit_runtime
RUN uv sync --locked --no-dev
COPY deploy/demo-workspaces workspaces

# UID 1000 matches the default EC2 login user so it can read the 0600 files in deploy/config.
RUN useradd --create-home --uid 1000 tacit && mkdir /data && chown tacit:tacit /data \
    && install -d -o tacit -g tacit /home/tacit/.local /home/tacit/.local/share \
        /home/tacit/.local/share/kiro-cli
USER tacit
ENTRYPOINT ["/app/.venv/bin/tacit"]
