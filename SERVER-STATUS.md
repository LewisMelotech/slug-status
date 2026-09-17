# Minecraft server status

Every script version carries a **status**: `offline` or `online`. It records whether that
version has been put on the Minecraft server.

**It does not hide anything.** Every script stays visible, searchable, downloadable and in
the API whatever its status. This is a deployment record, not a permission — if you want a
moderation gate that hides unpublished scripts, that is a different feature.

Everything uploaded or imported starts `offline`, because nothing has been deployed at the
moment it arrives. Someone holding `scripts.set_server_status` marks it `online` once it is
actually on the server.

**At most one version of a script is online at a time.** Marking a version online takes
whichever version was there off, since only one can actually be on the server. None online
is perfectly fine — that is the normal state for a script you have not deployed. This is
enforced in the model, so the website, the admin and the shell all behave the same way.

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
  permission, with a badge showing how many scripts are waiting. JSON and PDF links on
  every row so you can grab the files you are about to deploy, and one button per row.
  See below for its two tabs.
- **The script's own page**, where a badge shows Online or Offline, with a button to flip
  it for those who may.
- **The Django admin**, which has a status column, a status filter, inline editing and
  bulk mark-on / mark-off actions for doing a batch at once.

Changing a status is POST-only, so it cannot happen by following or prefetching a link.

Marking a version online can also be announced to a Discord channel of its own, naming what
it replaced and who did it. See `DISCORD_ONLINE_WEBHOOK_URL` in `NOTIFICATIONS.md`.

### The two tabs on the Server page

Every version that is not online is "offline", but that covers two unrelated things, so
the page separates them.

**Needs deploying** is the queue: the newest version of a script, not on the server.
Either nothing of that script has been deployed, or the server is running an older one.
Each row shows what is currently online beside it, so an update reads as "1.0.5, replacing
1.0.4" and a first deployment reads as "nothing yet". Newest first, because what just
arrived is usually what needs doing — and a Discord announcement links straight here.

**Superseded** is everything else: older versions of scripts that already have a newer one
online. Deploying an update takes the previous version off the server, so this tab gains a
row every time the feature is used correctly, and never shrinks. Nothing here needs doing.

They are separate because the second grows without limit and the first does not. On a
41-script instance the split was 8 outstanding against 25 superseded, and a single list
ordered oldest-first put every one of the 8 on the last page.

Superseded versions are kept one click away rather than hidden, because marking one online
is how you **roll a script back** — it goes on the server and the current one comes off.


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
