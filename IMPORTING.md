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
stored like an uploaded one, and adding to an existing script follows the same
[ownership rule](#who-may-import-into-an-existing-script).

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
edits are safe. The newest version imported takes the `latest` flag, exactly as an upload
would; older versions file in behind without disturbing it. Like every upload, what it adds
arrives `offline`, so a sync never changes what the Discord bot serves until someone puts
the version on the server.

Sync asks for **every** version the source holds, not only the newest, so a script
imported with just its latest version gains its older ones on the first sync.

**What a sync costs the other server.** A linked script is asked about in full whether or
not it has changed: one request for the script, then two for each of its versions — the
version itself and its PDF — including versions you already hold. A script with three
versions is seven requests, every run. That is worth knowing before you link a lot of
scripts, and it is why `--no-link` exists for a copy you do not need to follow.

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

The stack's `sync` service runs it for you, **hourly**. It calls `sync_upstream` every
`SYNC_PERIOD` seconds (`3600` by default) counted from the clock, not from when the
container started, so a restart does not shift the schedule. A version published upstream
therefore reaches the Server page's *Needs deploying* tab within the hour. A failed run is
logged and the schedule carries on, since someone else's server being down should not stop
it. Watch it with `docker compose logs sync`.

`SYNC_ON_START=true` also runs one pass at start-up. It is off by default: the stack is
restarted repeatedly while being set up, and every restart would be another full pass over
someone else's server for no new data.

A run announces to Discord once, however many scripts gained a version — see
`NOTIFICATIONS.md`.

Without the `sync` service, run it from cron on the host instead, hourly to match:

```sh
0 * * * * cd /path/to/discord-botc-script-bot/stack && docker compose exec -T botc-scripts python manage.py sync_upstream
```

## From the web UI

**Import** sits in the site's navigation next to Upload, at `/script/import`, and the
upload page links to it. Paste an id or a link, choose whether to take every version and
whether to keep it linked, and submit — the imported script's page opens with a summary
of what came across.

It is open to whoever may upload, including anonymous visitors, because importing a
script someone else published is the same act as uploading it by hand. That includes the
ownership rule an upload has, described under
[Who may import into an existing script](#who-may-import-into-an-existing-script).

What is restricted is **where** it may be fetched from. The fetch runs on the server, not
in the visitor's browser, so an unrestricted form would let anyone aim your server at any
address it can reach — including services on your own network that are not exposed to the
internet. So:

- Anyone may import from the instances listed in `IMPORT_SOURCES`, which defaults to
  `https://www.botcscripts.com`. Set it to a comma-separated list to allow more.
- Holders of `scripts.api_write_permission` are not restricted, and get a free-text
  source field. Your bot's API account already holds it; grant it to a person in the
  admin under their user's permissions.

The check runs against the **resolved** source rather than the dropdown, because a pasted
link carries its own instance — validating only the field would let any host in through
the reference box.

There is no rate limiting on the form. On an instance open to the public internet, that
means anyone can make your server fetch from an allowed source repeatedly; put it behind
authentication or a proxy rate limit if that matters to you.

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
| `403` | No credentials, wrong credentials, or missing the permission, **or** the script it would import into belongs to someone else |

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

## Who may import into an existing script

An import finds the local script it belongs to, and adds versions to it. It looks first
for the script **linked** to that source and id, and otherwise for one with the **same
name**. The two are treated differently:

| Found by | Owned? | Who may import into it |
|---|---|---|
| Its link to that source | Either | Anyone who may import. What lands is what the source published |
| Its name only | No owner | Anyone who may import, as with an upload |
| Its name only | Has an owner | **Only that owner**, signed in, or **staff or a superuser**. Anyone else is refused |

The last row is the same rule the upload form and the upload API apply, described in
`ACCOUNTS.md` under *Adding a version to someone else's script*. Without it, an import of a
script that merely shares a name with yours would add versions to yours and then link it
to the source, so sync kept adding to it afterwards.

A refusal is shown on the import page, and is a `403` from the API. It is decided as soon
as the source has said what the script is called, before any version or PDF is fetched, so
it costs the other server one request.

`manage.py import_script` and `sync_upstream` are not held to it: they run as whoever
administers the instance, not as a visitor, and sync only touches scripts already linked.
Staff and superusers are let through on the page and the API as well, so an administrator
never needs the command line for this.

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
- A script created by an import has **no owner**, so anyone who can upload can add versions
  to it. Set an owner in the admin if that matters to you, and the rule below then applies.
