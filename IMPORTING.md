# Importing scripts from botcscripts.com

This fork can copy scripts in from the official site, <https://www.botcscripts.com>, and
optionally keep following them, pulling new versions as the author publishes them.

**Only botcscripts.com is imported from.** Its maintainer has said how its API may be used
([discussion #740](https://github.com/AdmiralGT/botc-scripts/discussions/740)), and import
and sync are built to that. Other instances, including other copies of this fork, used to
be supported and no longer are. A link to anywhere else is refused.

Only the public read endpoints are used, so **no credentials are needed there**.
A fresh self-hosted instance starts with no scripts at all, so this is usually the first
thing you want after bringing the stack up.

## Import one script

```sh
docker compose exec botc-scripts python manage.py import_script 134
docker compose exec botc-scripts python manage.py import_script https://www.botcscripts.com/script/134
```

Both forms are equivalent. A link has to be to botcscripts.com, with or without `www.`.

What comes across: the script name, author, version, script type and the JSON content.
**PDFs do not**: botcscripts.com does not permit programmatic access to them, so an imported
or synced version arrives without one. Whoever may look after the script (staff, its owner,
or whoever imported it) can give the newest version one from the import page's **PDF** box,
when importing or by importing the script again, or give any version one later from its
script page with **Upload PDF**. Neither makes a new version, and `ACCOUNTS.md` says who
may. Until then the Discord bot's `/script` has no pages to show for it, and
`/json` still works. In the admin, the version list's **PDF** filter finds versions without. Character counts, edition and homebrew status are
recalculated locally by the same code the upload form uses, so an imported script is
stored like an uploaded one, and adding to an existing script follows the same
[ownership rule](#who-may-import-into-an-existing-script).

An import takes the script's **full history**: every version the source has, oldest
first, so the newest ends up with the `latest` flag. What it asks the source:

1. The script's list of versions, from `/api/script_ids/<id>/`. Always one request.
2. Each version not held here, from the rows the daily sync has stored (see
   [Staying linked](#staying-linked)), which costs nothing. If one missing from them is
   newer than where that read has got to, the read runs now, once, and finds it. One older
   than that is looked up by itself, from `/api/scripts/<version id>/`, and stored too.
So a new script whose versions are all stored costs **one request**, however many versions
it has. A version that has to be looked up adds one.

Importing is idempotent. A version already held here is never fetched again, so re-running asks the source for the version list only, reports `already held`, and
writes nothing.

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

**How it asks.** This is the approach the botcscripts.com maintainer asked for (see
[discussion #740](https://github.com/AdmiralGT/botc-scripts/discussions/740)), after this
instance was blocked for looking every linked script up one at a time.

`/api/scripts/` lists versions newest first, 50 to a page, and version ids there only ever
go up. Sync reads that list from the top until it reaches the newest version id it saw on
the previous run. Everything above that line was published since, and every row carries
the script's content. Sync stores **every** row it reads, for any script, so imports can
be served from them, and adds every new version of a script linked here, oldest first. It
then remembers the newest id it saw, in an **Upstream cursor** row you can see in the admin.

Left to its defaults the list holds only each script's latest version, and leaves out
hybrid and homebrew scripts. Sync asks for all of them, so a script that gained two versions
since the last run gets both, and no linked script is missed for how it is classified.

So a run costs the source one request per 50 versions published since the last run, which
for a daily run is normally **one request**, however many scripts are linked here, and
nothing else: each new version is added from its row, without a PDF.

A few things follow from reading the list rather than each script:

- **The first run reads one page.** With no cursor yet, there is nothing to say how far
  back to look, so sync reads the newest page and starts the cursor from there.
- **A long gap is capped.** A run reads at most 20 pages (a thousand versions) and
  says so if it stopped there. To look further back, lower `last_version_pk` on the cursor
  in the admin before the next run, at a request per 50 versions.

To fill in what any of that left out for one script, check it by itself. That is a lookup
of one known script, by hand, through `/api/script_ids/<id>/`: one request when nothing is
new, plus two for each version fetched.

```sh
docker compose exec botc-scripts python manage.py sync_upstream --script 12
docker compose exec botc-scripts python manage.py sync_upstream --full --script 12
```

Without `--full` that takes the latest version if it is newer than anything held here; with
it, every version missing here, as a first import does. `--full` only works with
`--script`. `12` is the script's id **here**, as shown in the admin. Do not loop these over
every linked script: that is the pattern that got this instance blocked.

**When the source refuses.** botcscripts.com blocks an instance that asks it too much,
answering `403`, and the block stays until it is lifted by hand. A refusal (`403` or `429`)
ends that source's run without moving its cursor, so the next run starts from the same
place. While blocked, stop the stack's `sync` service with `docker compose stop sync`.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Say how far each source would be read, without asking it anything |
| `--script <id>` | Check one local script by itself, whether or not sync is enabled for it |
| `--full` | With `--script`, fetch every version missing here, not only the latest |

Import without linking, if you want a one-time copy that sync will ignore:

```sh
python manage.py import_script 134 --no-link
```

### On a timer

The stack's `sync` service runs it for you, **once a day at 09:00 UK time**. `SYNC_AT`
sets the time of day and `SYNC_TIMEZONE` the zone it is read in (`Europe/London`), and the
clock change is handled. It sleeps until that time rather than counting from when the
container started, so a restart does not shift the schedule. A version published upstream
therefore reaches the Server page's *Needs deploying* tab by the next morning. Set
`SYNC_AT` empty to sync every `SYNC_PERIOD` seconds instead. A failed run is
logged and the schedule carries on, since someone else's server being down should not stop
it. Watch it with `docker compose logs sync`.

`SYNC_ON_START=true` also runs one pass at start-up. It is off by default: the stack is
restarted repeatedly while being set up, and every restart would be another request per
linked script to someone else's server, for no new data.

A run announces to Discord once, however many scripts gained a version — see
`NOTIFICATIONS.md`.

Without the `sync` service, run it from cron on the host instead, daily at 09:00 to match:

```sh
0 9 * * * cd /path/to/discord-botc-script-bot/stack && docker compose exec -T botc-scripts python manage.py sync_upstream
```

## From the web UI

**Import** sits in the site's navigation next to Upload, at `/script/import`, and the
upload page links to it. Paste an id or a link, choose whether to keep it linked, optionally
fill in its **Minecraft Customisations** (see `ACCOUNTS.md`), and submit. Every version missing here comes across, and the imported script's page opens with
a summary of what did.

It is open to whoever may upload, including anonymous visitors, because importing a
script someone else published is the same act as uploading it by hand. That includes the
ownership rule an upload has, described under
[Who may import into an existing script](#who-may-import-into-an-existing-script),
and the upload switch: while `UPLOAD_DISABLED` is set, only staff may import, here or
through the API. `sync_upstream` is not affected, since it runs as the administrator.

Where it fetches from is not up to the visitor: always botcscripts.com, and a pasted link
to anywhere else is refused. The fetch runs on this server, so letting a visitor choose
would let them aim it at any address it can reach, including services on your own network.

There is no rate limiting on the form. On an instance open to the public internet, that
means anyone can make your server ask botcscripts.com for scripts repeatedly, and it is
botcscripts.com that blocks an instance for asking too much. Put the form behind
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
| `reference` | required | Script id on botcscripts.com, or a link to its page there |
| `link` | `true` | Follow this script in `sync_upstream` |

A link to anywhere else is a `400`. A `source` field, which earlier versions of this fork
accepted, is now ignored.

Every import takes the full history of versions not already held here. An `all_versions`
field, which earlier versions of this fork accepted, is now ignored.

Responses:

| Status | When |
|---|---|
| `201` | At least one version was imported |
| `200` | Everything was already held — `imported` is empty and `skipped` counts them |
| `400` | Unusable reference, or the far side could not be reached or refused the request |
| `403` | No credentials, wrong credentials, or missing the permission, **or** the script it would import into belongs to someone else, **or** `UPLOAD_DISABLED` is set and the caller is not staff |

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
as the source has said what the script is called, before any version is fetched, so
it costs the other server one request.

`manage.py import_script` and `sync_upstream` are not held to it: they run as whoever
administers the instance, not as a visitor, and sync only touches scripts already linked.
Staff and superusers are let through on the page and the API as well, so an administrator
never needs the command line for this.

## In the admin

Linked scripts show their upstream id, sync state and last sync time in the script list,
where `sync_enabled` is editable inline. The **Check botcscripts.com for new versions of
linked scripts** action runs the same single read as the scheduled sync, so it brings every
linked script up to date, not only the ones selected.

**Upstream cursors** shows how far sync has read botcscripts.com, and is editable: lower it
to have the next run look further back. **Upstream versions** lists every version stored for
imports, newest first, searchable by its script id or version id on botcscripts.com. It is
read-only, since imports are built from those rows exactly as botcscripts.com sent them.

## What is stored

Five fields on `Script`:

| Field | Meaning |
|---|---|
| `imported_by` | Who imported it, if they were signed in, through the page or the API. Not set by sync or the command line, and kept if the script is imported again |
| `upstream_source` | `https://www.botcscripts.com` for anything imported. Only scripts with it are synced |
| `upstream_id` | The script id **there**, unrelated to the id here |
| `sync_enabled` | Whether `sync_upstream` follows it |
| `last_synced` | When it was last checked |

A unique constraint on `(upstream_source, upstream_id)` means a repeated import updates
the script it already created rather than forking a second copy.

One `UpstreamCursor` row: `last_version_pk`, the newest version id the last sync saw on
botcscripts.com, in its numbering. It is created by the first sync, and sync reads down to
it next time.

And an `UpstreamVersion` row for every version the daily sync has read, or an import has
looked up: the version's id and its script's id on botcscripts.com, and the API's row for
it, content and all. Imports are served from these. They are only ever added to or
replaced, never removed, and start empty: the store holds what has been published since
the first sync, plus whatever imports have looked up.

## Known limits

- **Tags, comments and votes do not come across.** Tags are per-instance (they carry an
  ordering and styling of their own), and comments and votes belong to accounts on the
  other instance. Only the inheritable tags of a script's own previous version carry
  forward, exactly as on upload.
- **No PDFs.** botcscripts.com does not permit programmatic access to them, and its
  `download_pdf` link is not to be used by programs. Nothing here fetches one.
- **The client identifies itself** with its own User-Agent rather than `python-requests`'.
  botcscripts.com's maintainer tells callers apart by it, so keep it.
- **The public site blocks instances that ask too much.** Keep sync to once a day,
  and see [When the source refuses](#staying-linked) for what happens once it does.
- **Nothing is pushed back.** This is a one-way copy: votes, comments and edits made here
  never reach botcscripts.com.
- A script created by an import has **no owner**, so anyone who can upload can add versions
  to it. Set an owner in the admin if that matters to you, and the rule below then applies.
  It does record who imported it, who can look after its PDFs and Minecraft Customisations
  as an owner can; see `ACCOUNTS.md`.
