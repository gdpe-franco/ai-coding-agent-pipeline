FROM node:22-slim AS frontend
WORKDIR /src
COPY client/package.json client/package-lock.json /src/client/
RUN cd /src/client && npm ci
COPY client/ /src/client/
RUN cd /src/client && npm run build \
    && mkdir -p /client && cp -a dist /client/dist

FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libpq5 nodejs \
    && rm -rf /var/lib/apt/lists/*
COPY server/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir .
COPY server/ /app/
COPY --from=frontend /client/dist/ /app/static/
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["gunicorn", "app.main:create_app()", "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-class", "uvicorn.workers.UvicornWorker"]
