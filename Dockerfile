FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --system --home /app uhura && mkdir /data && chown uhura /data
USER uhura
ENV PATH="/app/.venv/bin:$PATH" UHURA_DB=/data/uhura.db
VOLUME /data
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health')"]
CMD ["uhura", "serve", "--host", "0.0.0.0", "--port", "8787"]
