FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/* \
    # The Root is mounted read-only and owned by someone else: git must still read its history.
    && git config --system --add safe.directory '*'

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY evaluation ./evaluation
RUN uv sync --frozen --no-dev

RUN useradd --system --create-home stc
USER stc

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://localhost:8000/api/health || exit 1
CMD ["uvicorn", "stc.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
