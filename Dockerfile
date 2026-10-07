FROM ghcr.io/astral-sh/uv:0.9.8 AS uv
FROM python:3.12.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
COPY src ./src
COPY dbt ./dbt
COPY migrations ./migrations
COPY config ./config
COPY tests ./tests
COPY ops ./ops
RUN uv sync --frozen && groupadd --gid 10001 dqflow && useradd --uid 10001 --gid 10001 --create-home dqflow \
    && mkdir -p /data/artifacts /data/raw /data/demo /data/mock \
    && chown -R dqflow:dqflow /app /data
USER 10001:10001
ENTRYPOINT ["dqflow"]
CMD ["--help"]

