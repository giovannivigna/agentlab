# uv's image: one tool resolves, locks and installs, and the lock
# (agent/uv.lock) is what makes "it worked last week" reproducible next month.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ARG APP_UID=1000
ARG APP_GID=1000

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

# Dependencies in their own layer: editing app/ does not reinstall LangGraph.
# uv.lock pins every transitive version; `uv lock --upgrade` in agent/ moves
# them forward deliberately rather than whenever the image happens to rebuild.
COPY pyproject.toml ./
COPY uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project

COPY app ./app

# The agent runs as a user who owns nothing. It cannot write to /app, cannot
# install packages, and cannot escalate - the only writable path it has is the
# output/ bind mount. If a tool is talked into `rm -rf`, this is what is
# between the model's suggestion and your afternoon.
RUN groupadd -g "${APP_GID}" agent 2>/dev/null || true; \
    useradd -u "${APP_UID}" -g "${APP_GID}" -M -s /bin/bash agent 2>/dev/null || true
USER ${APP_UID}:${APP_GID}

ENTRYPOINT ["python", "-m", "app.run"]
