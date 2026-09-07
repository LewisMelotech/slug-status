# Review before publishing

Every script version has a **status**: `offline` or `online`. Uploads and imports land
offline and are invisible to everyone but the person who added them, until a moderator
puts them online.

## Who can do what

| | Sees offline scripts | Puts them online |
|---|---|---|
| Anonymous visitors | No | No |
| A logged-in user | Their own only | No |
| Holder of `scripts.moderate_scripts` | All | Yes |
| Superuser | All | Yes |

`scripts.api_write_permission` deliberately does **not** confer moderation. A bot that may
upload or import is not thereby allowed to decide what the public sees, so scripts the bot
imports also wait for review. Grant the bot's account `moderate_scripts` as well if you
would rather its imports publish immediately.

A moderator's own upload skips the queue — asking them to approve themselves achieves
nothing.

## Reviewing

**Review** appears in the navigation for moderators, at `/moderation`. It lists what is
waiting, oldest first, with the script, version, author, who added it, when, and a link to
the PDF so you can look before deciding. One button puts a version online.

The same button appears on a script's own page, next to an "Offline — awaiting review"
badge, so you can approve while looking at the script itself.

In the Django admin, `ScriptVersion` gained a status column, a status filter, inline
editing, and bulk **Put selected versions online** / **Take selected versions offline**
actions — useful for approving a batch at once.

Changing a status is POST-only. It changes what the public can see, so it must not be
reachable by following a link or having one prefetched.

## Granting the permission

Django admin → Users → the user → Permissions → add
**scripts | script version | Can put uploaded scripts online, and see offline ones**.

## What being offline hides

An offline version does not appear in:

- the main script list, advanced search, statistics, or collections
- `/api/scripts/`, `/api/script_ids/`, or the statistics API — so a Discord bot reading
  the API anonymously will not serve it either
- its own script page, which 404s
- the JSON and PDF download URLs, which 404 rather than serving the file

That last one matters: downloads bypass every list and detail page, so without it an
offline script would still be fetchable by anyone who guessed the URL.

## A note on the implementation

The filter is applied at each public surface rather than in the model's default manager.
Related lookups such as `script.versions` go through that manager, and the upload and
import paths depend on seeing **every** version to decide what already exists and which is
latest — hiding rows there would let a second copy of an offline version be created. The
rule lives in one place, `scripts/moderation.py`, and every surface calls it.

## Existing scripts

Migration `0050_scriptversion_status` backfills everything already in the database to
`online`. Without that, adding the field would take a working instance's whole catalogue
offline the moment it was deployed.
