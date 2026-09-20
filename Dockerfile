# syntax=docker/dockerfile:1
# Football Market AI Trader - container image.
# Secrets are NEVER baked in: everything comes from environment variables.
# Live trading is disabled by default (the image sets TRADING_MODE=backtest).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TRADING_MODE=backtest \
    POLYMARKET_ALLOW_LIVE_TRADING=false

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Run as an unprivileged user; the data volume is the only writable location.
RUN useradd --create-home --uid 10001 trader \
 && mkdir -p /app/data /app/logs /app/artifacts \
 && chown -R trader:trader /app
USER trader

VOLUME ["/app/data", "/app/logs", "/app/artifacts"]

HEALTHCHECK --interval=60s --timeout=20s --start-period=20s --retries=3 \
  CMD python -m app.cli version || exit 1

ENTRYPOINT ["python", "-m", "app.cli"]
CMD ["check"]
