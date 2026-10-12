FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app
COPY . /app

RUN uv sync --locked --all-packages --no-dev --no-editable \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin mrq \
    && mkdir -p /var/lib/mrq \
    && chown -R 10001:10001 /var/lib/mrq /home/mrq

ENV HOME=/home/mrq \
    XDG_CACHE_HOME=/tmp/.cache \
    MRQ_PROJECT_ROOT=/app \
    MRQ_STATE_DIR=/var/lib/mrq

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["macro-regime-quant", "serve-backtest-api", "--host", "0.0.0.0", "--port", "8000"]
