# The app service: the same commands as on the host, in a container. `uv` on the host stays the primary
# path; this is the "only Docker installed" one (`make docker-test`). The repo arrives as a bind mount at
# run time (compose.yaml), so the image holds dependencies only. No secret is ever an ARG, an ENV or a
# copied file here: .dockerignore keeps .env out of the build context, and compose passes it at run time.
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/*

# The virtualenv lives outside the source mount, so the bind mount never collides with a host .venv.
# UV_NO_SYNC: the environment is fixed at build time; `uv run` inside the container never re-resolves.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_NO_SYNC=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/tmp/uv-cache \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
# Enough of the project for an editable install: the package folders are replaced by the mount.
COPY pyproject.toml uv.lock .python-version ./
COPY smalls ./smalls
COPY smalls_data ./smalls_data
RUN UV_NO_SYNC=0 uv sync --frozen --group tagging && rm -rf /tmp/uv-cache

RUN useradd --create-home --uid 1000 smalls
USER smalls
CMD ["make", "test"]
