FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # Keep the venv outside the app dir so the dev bind mount doesn't hide it
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app


# Development (compose.dev.yml): dev dependencies and Django's dev server
FROM base AS dev

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked

COPY . .

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


# Production, the default target: gunicorn on $PORT, as Cloud Run expects
FROM base AS prod

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev

COPY . .

# Settings need a secret key to load, but collectstatic never uses it
RUN DJANGO_SECRET_KEY=collectstatic python manage.py collectstatic --noinput

CMD exec gunicorn ctr.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 8 --timeout 0
