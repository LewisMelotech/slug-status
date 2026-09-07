# Importing scripts from another instance

This fork can copy scripts in from another botc-scripts instance — normally the public
site at <https://www.botcscripts.com> — and optionally keep following them, pulling new
versions as the author publishes them.

Only the public read endpoints are used, so **no credentials are needed on the far side**.
A fresh self-hosted instance starts with no scripts at all, so this is usually the first
thing you want after bringing the stack up.

## Import one script

```sh
docker compose exec botc-scripts python manage.py import_script 134
docker compose exec botc-scripts python manage.py import_script https://www.botcscripts.com/script/134
```

Both forms are equivalent: a bare id is looked up on `--source` (the public site by
default), and a URL carries its own instance, so you can import from any instance
including another copy of this one:

```sh
python manage.py import_script 7 --source http://botc-scripts:8000
```

What comes across: the script name, author, version, script type, the JSON content, and
the PDF when upstream has one. Character counts, edition and homebrew status are
recalculated locally by the same code the upload form uses, so an imported script is
indistinguishable from an uploaded one.

By default only the **latest** version is imported. For the full history:

```sh
python manage.py import_script 77 --all-versions
```

Importing is idempotent. Re-running reports `already held` and writes nothing, so it is
safe in a script or a cron job.

## Staying linked

An import records where the script came from and sets `sync_enabled`, so this pulls any
versions upstream has gained since:

```sh
docker compose exec botc-scripts python manage.py sync_upstream
```

It only ever **adds** versions. It never edits or deletes what you already hold, so local
edits are safe, and a script that has not changed upstream costs one request. The newest
version imported takes the `latest` flag, exactly as an upload would; older versions file
in behind without disturbing it.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | List what would be synced, write nothing |
| `--script <id>` | Sync one local script, whether or not sync is enabled for it |

Import without linking, if you want a one-time copy that sync will ignore:

```sh
python manage.py import_script 134 --no-link
```

### On a timer

`sync_upstream` is designed to be run by cron on the host. Daily is plenty — scripts do
not change often, and the public site is someone else's server:

```sh
0 4 * * * cd /path/to/discord-botc-script-bot/stack && docker compose exec -T botc-scripts python manage.py sync_upstream
```

## Over the API

`POST /api/script_ids/import/` does the same job as `import_script`, so a Discord bot or
any other client can import without shell access. Reads on this instance stay anonymous;
this write needs HTTP Basic and the `scripts.api_write_permission` permission — the same
credential the upload API uses.

```sh
curl -u botuser:botpass -X POST https://your-instance/api/script_ids/import/ \
  -H "Content-Type: application/json" \
  -d '{"reference": "134"}'
```

| Field | Default | Meaning |
|---|---|---|
| `reference` | required | Script id, or a link to a script page |
| `source` | the public site | Instance to import from, when `reference` is a bare id |
| `all_versions` | `false` | Import the full history rather than only the latest |
| `link` | `true` | Follow this script in `sync_upstream` |

Responses:

| Status | When |
|---|---|
| `201` | At least one version was imported |
| `200` | Everything was already held — `imported` is empty and `skipped` counts them |
| `400` | Unusable reference, or the far side could not be reached |
| `403` | No credentials, wrong credentials, or missing the permission |

The body carries the local script, the versions imported, how many were skipped, the
source and upstream id, and whether it is now linked:

```json
{
  "script": {"pk": 7, "name": "Let the Dead Rest in Peace", "slug": null, "versions": {...}},
  "imported": ["1.0.0"],
  "skipped": 0,
  "source": "https://www.botcscripts.com",
  "upstream_id": 13108,
  "linked": true
}
```

The 200/201 split is deliberate: a caller can tell "nothing changed" from "something was
created" without diffing the version lists.

## In the admin

Linked scripts show their upstream id, sync state and last sync time in the script list,
where `sync_enabled` is editable inline. Select any number of scripts and use the **Sync
selected scripts from their upstream instance** action to pull immediately.

## What is stored

Four fields on `Script`:

| Field | Meaning |
|---|---|
| `upstream_source` | Base URL of the instance it came from |
| `upstream_id` | The script id **there**, unrelated to the id here |
| `sync_enabled` | Whether `sync_upstream` follows it |
| `last_synced` | When it was last checked |

A unique constraint on `(upstream_source, upstream_id)` means a repeated import updates
the script it already created rather than forking a second copy.

## Known limits

- **Tags, comments and votes do not come across.** Tags are per-instance (they carry an
  ordering and styling of their own), and comments and votes belong to accounts on the
  other instance. Only the inheritable tags of a script's own previous version carry
  forward, exactly as on upload.
- **Upstream cannot distinguish "no PDF" from "server error"** — it answers both with a
  500 carrying an HTML page. A version whose PDF cannot be fetched is imported without
  one rather than failing, and `import_script` reports `no PDF` so you can tell.
- **The public site rejects the `python-requests` User-Agent** with a 403 on the PDF
  download path. The client sets its own; do not remove it or PDFs will silently stop
  importing while the JSON keeps working.
- **Nothing is pushed back.** This is a one-way copy: votes, comments and edits made here
  never reach the source instance.
- Imported scripts have **no owner** locally, so anyone with upload rights can add
  versions to them. Set an owner in the admin if that matters to you.
