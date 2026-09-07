# botc-scripts, packaged for Docker Compose.
#
# Upstream ships no production Dockerfile — it deploys to Azure App Service via Oryx
# (.github/workflows/main_botc-scripts.yml). This image reproduces the steps that
# .devcontainer/postCreate.sh performs, minus the dev-only parts.
#
# The uv image is used rather than python:3.13-slim because pyproject.toml requires
# Python >=3.13 and the project is locked with uv; `uv sync --locked` is the
# reproducibility guarantee. If the fork ever adds a dependency, `uv lock` must be
# re-run or this build fails loudly rather than silently drifting.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=botc.docker

WORKDIR /app

# Dependencies first, from the lockfile only, so this layer caches across source edits.
# --no-install-project is correct here: pyproject.toml sets package-mode = false.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# WhiteNoise is deliberately NOT added to pyproject.toml/uv.lock. Upstream serves static
# and media from Azure Blob Storage and has no need of it; keeping it in this fork-only
# Dockerfile means uv.lock stays byte-identical to upstream and never conflicts on rebase.
RUN uv pip install --python /opt/venv "whitenoise[brotli]>=6.9,<7"

COPY . /app

# collectstatic needs no database (verified), so it runs at build time. Every container
# started from this image therefore shares one already-built, hashed, brotli-compressed
# static tree — there is no shared static volume and no nginx sidecar.
# This SECRET_KEY is used only to import settings during the build; nothing is signed
# with it and it never reaches a running container.
RUN SECRET_KEY=build-only-not-a-real-secret python manage.py collectstatic --noinput

# Create the mount points BEFORE dropping privileges. Docker seeds an empty named volume
# from the image's content *and ownership* at that path, so /data comes up owned by `app`.
# Adding a new volume path later needs a matching mkdir here or the container cannot write.
RUN useradd -m -u 10001 app \
 && mkdir -p /data/public/media /data/icon_cache \
 && chown -R app:app /data /app/staticfiles

USER app
EXPOSE 8000

# gunicorn.conf.py is auto-loaded from the working directory and supplies worker_class,
# workers and threads (tune with GUNICORN_WORKERS / GUNICORN_THREADS). It deliberately
# omits --bind and --timeout, so those are passed here.
CMD ["gunicorn", "botc.wsgi:application", "--bind", "0.0.0.0:8000", "--timeout", "120"]
