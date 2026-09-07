"""Settings for running botc-scripts in a container.

Selected by DJANGO_SETTINGS_MODULE=botc.docker, set in the Dockerfile. (manage.py and
botc/wsgi.py read DJANGO_SETTINGS with a default of botc.local; setdefault leaves an
already-set DJANGO_SETTINGS_MODULE alone, so the Dockerfile's value wins.)

botc/production.py is not usable here: it hard-requires Azure Blob Storage for both
static and media, and does os.environ.get("DJANGO_HOST", None).split(" "), which raises
AttributeError when DJANGO_HOST is unset.

Everything botc/settings.py deliberately leaves undefined must be supplied below.
UPLOAD_DISABLED and BANNER in particular are NOT optional: scripts/context_processors.py
reads both on every template render, so omitting either makes every page 500.
"""

import os

from .settings import *  # noqa: F403

# --- Identity and hosts -----------------------------------------------------------

try:
    SECRET_KEY = os.environ["SECRET_KEY"]
except KeyError as exc:  # pragma: no cover - configuration error, not runtime logic
    raise RuntimeError(
        "SECRET_KEY is not set. Generate one with:\n"
        "  python -c \"import secrets; print(secrets.token_urlsafe(64))\"\n"
        "and put it in the stack's .env file."
    ) from exc

DEBUG = os.environ.get("DEBUG", "False") == "True"

# Space-separated. MUST include "botc-scripts": that is the Host header the bot sends
# when it calls http://botc-scripts:8000 over the Compose network, and Django answers
# 400 to any host not listed here.
ALLOWED_HOSTS = os.environ.get("DJANGO_HOST", "localhost 127.0.0.1 botc-scripts").split()
CSRF_TRUSTED_ORIGINS = os.environ.get("CSRF_TRUSTED_ORIGINS", "http://localhost:8000").split()

# Set to "True" only when a TLS-terminating reverse proxy sits in front of the app.
if os.environ.get("BEHIND_TLS_PROXY", "False") == "True":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- Database ---------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DBNAME", "botc"),
        "HOST": os.environ.get("DBHOST", "db"),
        "PORT": os.environ.get("DBPORT", "5432"),
        "USER": os.environ.get("DBUSER", "botc"),
        "PASSWORD": os.environ.get("DBPASS", ""),
        "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
}

# --- Static files (baked into the image by collectstatic at build time) -------------

STATIC_URL = "/static/"
STATIC_ROOT = "/app/staticfiles"

# django_bootstrap_icons downloads each SVG from cdn.jsdelivr.net the first time it is
# rendered and caches it here, so this belongs on a persistent volume — otherwise every
# container restart re-fetches. A failure renders the literal text "Failed to read icon"
# into the page rather than raising, so an offline container degrades but does not break.
BS_ICONS_CACHE = os.environ.get("BS_ICONS_CACHE", "/data/icon_cache")

# --- Media (uploaded script PDFs) ---------------------------------------------------

MEDIA_URL = "/media/"
MEDIA_ROOT = "/data/public/media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MIDDLEWARE = list(MIDDLEWARE)  # noqa: F405
if "whitenoise.middleware.WhiteNoiseMiddleware" not in MIDDLEWARE:
    MIDDLEWARE.insert(
        MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
        "whitenoise.middleware.WhiteNoiseMiddleware",
    )

# WHITENOISE_ROOT is served at the URL root, so /data/public/media/... is reachable at
# /media/... . WhiteNoise builds its file map at startup, so without AUTOREFRESH every
# PDF uploaded after boot would 404 — and every PDF is uploaded after boot. The cost is
# one stat() per static request, which is irrelevant at this scale.
WHITENOISE_ROOT = "/data/public"
WHITENOISE_AUTOREFRESH = os.environ.get("WHITENOISE_AUTOREFRESH", "True") == "True"

# --- Required by scripts/context_processors.py (runs on every render) ----------------

UPLOAD_DISABLED = os.environ.get("UPLOAD_DISABLED", "False") == "True"
BANNER = os.environ.get("BANNER") or None
