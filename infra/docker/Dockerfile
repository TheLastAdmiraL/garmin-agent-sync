FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_STATE_HOME=/data

WORKDIR /app
ARG SYNC_UID=10001
ARG SYNC_GID=10001
COPY functions/requirements-local.txt /app/functions/requirements-local.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /app/functions/requirements-local.txt \
    && groupadd --gid ${SYNC_GID} app && useradd --uid ${SYNC_UID} --gid ${SYNC_GID} --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data

COPY functions /app/functions
RUN chown -R root:root /app && chmod -R a-w /app

USER app
ENTRYPOINT ["python", "functions/local_run.py"]
CMD ["sync"]
