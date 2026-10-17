# Stage 1: Build stage – install deps with uv into a venv
FROM python:3.15-slim@sha256:d802f03223b9e00604cd54574bcac1a4638914c43683d3f0afd6ebee35f25313 AS builder

WORKDIR /app

# Copy the uv binary from the official uv image
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv

# git: noetig, damit uv die schalti-ui-Git-Dependency klonen/bauen kann
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

ENV UV_PROJECT_ENVIRONMENT=/opt/venv
RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: Final runtime image
FROM python:3.15-slim@sha256:d802f03223b9e00604cd54574bcac1a4638914c43683d3f0afd6ebee35f25313

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Zeitzonendaten werden für die korrekte Sommerzeit-Umstellung gebraucht.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

COPY . .

# Der Build-Schritt braucht nur irgendeinen Schlüssel – der echte kommt zur Laufzeit.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --create-home --uid 1000 termine \
    && mkdir -p /app/data \
    && chown -R termine:termine /app
USER termine

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-"]
