FROM python:3.13-slim AS runtime

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

RUN addgroup --system opensecdash \
    && adduser --system --ingroup opensecdash --home /app opensecdash \
    && mkdir -p /data \
    && chown -R opensecdash:opensecdash /app /data

COPY --chown=opensecdash:opensecdash backend /app/backend
COPY --chown=opensecdash:opensecdash plugins /app/plugins
COPY --chown=opensecdash:opensecdash README.md LICENSE /app/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir /app/backend \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

WORKDIR /app/backend
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"OSD_PORT\", \"8000\")}/health', timeout=3).read()"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn app.main:app --host ${OSD_HOST} --port ${OSD_PORT}"]
