"""Create the pg_trgm extension the name/author search depends on.

scripts/filters.py and scripts/views.py use django.contrib.postgres.search.TrigramSimilarity
for the ?search= and ?author= filters, but no upstream migration creates the extension —
DEVELOPMENT.md tells you to write this migration yourself and never commits it. Without it
the site boots and browses fine and then returns 500 the moment anyone searches, with
"function similarity(character varying, unknown) does not exist".

This runs inside migrate, so it also fixes an already-populated database — unlike a
/docker-entrypoint-initdb.d script, which only ever runs on an empty data directory.
Creating an extension needs superuser, which the Compose Postgres role has.
"""

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("scripts", "0047_script_slug")]

    operations = [TrigramExtension()]
