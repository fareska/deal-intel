FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.12.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

COPY --from=uv /uv /usr/local/bin/uv

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY deal_intel ./deal_intel
RUN uv sync --frozen --no-dev --no-editable

USER app
EXPOSE 8000
CMD ["uvicorn", "deal_intel.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
