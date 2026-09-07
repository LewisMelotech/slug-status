# Minecraft server status

Every script version carries a **status**: `offline` or `online`. It records whether that
version has been put on the Minecraft server.

**It does not hide anything.** Every script stays visible, searchable, downloadable and in
the API whatever its status. This is a deployment record, not a permission — if you want a
moderation gate that hides unpublished scripts, that is a different feature.

Everything uploaded or imported starts `offline`, because nothing has been deployed at the
moment it arrives. Someone holding `scripts.set_server_status` marks it `online` once it is
actually on the server.

## Who can change it

| | Sees the status | Changes it |
|---|---|---|
| Anyone, including anonymous visitors | Yes | No |
| Holder of `scripts.set_server_status` | Yes | Yes |
| Superuser | Yes | Yes |

`api_write_permission` deliberately does not confer it: a bot that may upload is not
thereby deciding what has been deployed.

Grant it in the Django admin → Users → the user → Permissions →
**scripts | script version | Can mark a script as live on the Minecraft server**.

## Where to change it

- **The Server page**, at `/server`, linked from the nav for those who hold the
  permission. Lists everything not yet on the server, oldest first, with JSON and PDF
  links so you can grab the files you are about to deploy, and one button per row.
- **The script's own page**, where a badge shows "On the server" or "Not on the server",
  with a button to flip it for those who may.
- **The Django admin**, which has a status column, a status filter, inline editing and
  bulk mark-on / mark-off actions for doing a batch at once.

Changing a status is POST-only, so it cannot happen by following or prefetching a link.

## From the API

`status` appears on every version row from `/api/scripts/`, read-only:

```json
{"pk": 9, "name": "Sects and Violets", "version": "1.0.0", "status": "offline", ...}
```

It is read-only there on purpose — an upload never declares itself deployed. Use the site
or the admin to change it.

## Custom ids in the web UI

Alongside this, a script's custom id (its slug) is editable from the script page by anyone
holding `scripts.api_write_permission` — the same permission the `/alias` Discord command
and the slug API use. The box sits next to the short-link badge; leave it blank and submit
to clear the id.

It validates through the same serializer the API uses, so the site and the API cannot
disagree about what a valid id is: lowercase letters, digits and single hyphens, never
something that parses as a number, and not a word that is already part of a site URL.
