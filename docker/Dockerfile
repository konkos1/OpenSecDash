FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv

FROM python:3.15.0rc1-slim-trixie@sha256:858b8c9b2c764d1a3076e2c2ed64fb3f76ca2ec068ec81c1c059d3a5fcc1088e AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./

RUN test "$(python -c 'import platform; print(platform.python_version())')" = "$(cat .python-version)" \
    && uv sync --python /usr/local/bin/python --frozen --no-dev --no-editable --no-install-project

FROM python:3.15.0rc1-slim-trixie@sha256:858b8c9b2c764d1a3076e2c2ed64fb3f76ca2ec068ec81c1c059d3a5fcc1088e AS runtime

ARG OPENSECDASH_VERSION=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/opensecdash.db \
    AUTO_MIGRATE=true \
    LOG_FILE_ENABLED=false \
    OPENSECDASH_VERSION=${OPENSECDASH_VERSION} \
    OSD_HOST=0.0.0.0 \
    OSD_PORT=8000

WORKDIR /app

RUN python -m pip uninstall --yes --root-user-action=ignore pip \
    && python -c 'import importlib.metadata as metadata; assert not list(metadata.distributions())' \
    && addgroup --system opensecdash \
    && adduser --system --ingroup opensecdash --home /app opensecdash \
    && mkdir -p /data \
    && chown -R opensecdash:opensecdash /app /data

COPY --from=builder --chown=opensecdash:opensecdash /app/backend/.venv /app/backend/.venv
COPY --chown=opensecdash:opensecdash backend /app/backend
COPY --chown=opensecdash:opensecdash plugins /app/plugins
COPY --chown=opensecdash:opensecdash README.md LICENSE THIRD_PARTY_NOTICES.md THIRD_PARTY_SOURCE.md /app/
COPY --chown=opensecdash:opensecdash third_party /app/third_party
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PATH="/app/backend/.venv/bin:$PATH"

WORKDIR /app/backend
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"OSD_PORT\", \"8000\")}/health', timeout=3).read()"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["opensecdash"]
