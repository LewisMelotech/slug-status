"""Settings for the test suite.

This module was empty, which worked while every test exercised a pure function. Tests
that touch models, tables, the admin or URL reversing need the app registry populated,
so the real settings are imported here and only the pieces the container overlay
normally supplies are filled in.

Nothing here connects to a database: these values exist so Django can configure
itself, and no test in this suite is marked django_db.
"""

from botc.settings import *

SECRET_KEY = "test-only-key-not-used-for-anything-signed"

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

ROOT_URLCONF = "botc.urls"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

STATIC_URL = "/static/"

# locmem so a test that trips one of allauth's flows does not need a mail server.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
