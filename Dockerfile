# One image for api, worker, beat and migrate; the "test" target adds dev dependencies and tests.
# Code stays root-owned and read-only for the unprivileged user; the compose file also makes the root filesystem read-only with /tmp as tmpfs.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# No home directory: the unprivileged user writes only to in-memory /tmp.
RUN useradd --no-create-home --home-dir /nonexistent --uid 10001 appuser
WORKDIR /app

# Dependencies first: this layer is rebuilt only when pyproject.toml changes, not on
# every code edit. The list is read from pyproject.toml so it is declared in one place.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
        > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt


FROM base AS test
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['optional-dependencies']['dev']))" \
        > /tmp/requirements-dev.txt \
    && pip install --no-cache-dir -r /tmp/requirements-dev.txt \
    && rm /tmp/requirements-dev.txt
COPY app ./app
RUN pip install --no-cache-dir --no-deps .
COPY migrations ./migrations
COPY alembic.ini ./
COPY tests ./tests
USER appuser
# The cache provider would need to write into /app, which is read-only for appuser.
CMD ["pytest", "-q", "-p", "no:cacheprovider"]


# Last stage, so a plain "docker build" produces the production image.
FROM base AS runtime
COPY app ./app
RUN pip install --no-cache-dir --no-deps .
COPY migrations ./migrations
COPY alembic.ini ./
USER appuser
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
