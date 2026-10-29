# Serving image: FastAPI /ask + chat UI. The LLM backend is chosen with AA_MODEL.
# In production, replace the Claude-CLI backend with an API client behind the company LLM gateway
# (same LLMClient interface); AA_MODEL=mock runs the container offline for smoke tests.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev
COPY eval_sets ./eval_sets
# The warehouse file is mounted at runtime (it is built from public data by `make olist`):
#   docker run -p 8000:8000 -v $PWD/data:/app/data -e AA_MODEL=mock analytics-agent
ENV AA_MODEL=haiku
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "analytics_agent.serving.api:app", "--host", "0.0.0.0", "--port", "8000"]
