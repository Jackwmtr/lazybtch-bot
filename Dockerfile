FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependencies first (layer cache).
COPY pyproject.toml uv.lock README.md ./
RUN uv venv && uv sync --frozen --no-dev

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Long polling: no ports exposed, no inbound traffic.
VOLUME /app/data

CMD ["uv", "run", "lazybtch"]
