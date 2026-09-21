# Announcing script activity to Discord

The instance can post to Discord at two moments, each through its **own webhook** so they
can go to separate channels — or the same one, or only one of them:

- **Arrivals** (`DISCORD_ARRIVALS_WEBHOOK_URL`) — a script arrives that might need putting on
  the Minecraft server.
- **Deployments** (`DISCORD_ONLINE_WEBHOOK_URL`) — a version is marked as on the server.
  See [Deployments](#deployments) below.

The two are fully independent: each is switched on by its own URL, pings its own mention,
and says nothing if its URL is blank.

## Arrivals

Two things are announced:

- **A new script** — the first version of something not held before.
- **A new version** of a script already held, when it becomes the newest one.

A version filed in *behind* the latest is not announced. Backfilling v0.9 of a script
already at v1.2 changes nothing about what should be on the server, and that is the whole
point of the announcement.

Every route counts: the upload form, the API, the import page, the hourly upstream sync,
the Django admin and the shell. They all end at the same model save, and the announcement
hangs off that, so there is nowhere to create a version that quietly skips it.

## Setting it up

In Discord, on the channel you want the announcements in:

**Edit Channel → Integrations → Webhooks → New Webhook**, then **Copy Webhook URL**.

Put it in the stack's `.env`:

```
DISCORD_ARRIVALS_WEBHOOK_URL=https://discord.com/api/webhooks/123456789/abcdef...
```

and restart the stack. Check it without waiting for a real upload:

```
docker compose exec botc-scripts python manage.py test_notification --arrivals
```

`--batch` sends the several-at-once form instead. With no flag at all, `test_notification`
tests every webhook that is configured and skips any left blank — the quickest check after
a deploy. From a shell already inside the container, drop the
`docker compose exec botc-scripts` prefix.

**Who sees the announcements is a Discord question, not a setting here.** The webhook
belongs to one channel, so channel permissions decide the audience. To change who sees
them, move the webhook to another channel — or make a webhook on a different channel and
swap the URL. Nothing in this app needs to know.

Treat the URL as a secret. Anyone holding it can post to that channel as the webhook, and
it carries no other access.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `DISCORD_ARRIVALS_WEBHOOK_URL` | *(blank)* | The webhook. Blank switches announcements off entirely. |
| `DISCORD_ARRIVALS_WEBHOOK_MENTION` | *(blank)* | Prepended to each announcement, e.g. `<@&123456789012345678>` for a role, or `@here`. |
| `DISCORD_ONLINE_WEBHOOK_URL` | *(blank)* | The deployment webhook. Blank switches deployment announcements off. |
| `DISCORD_ONLINE_WEBHOOK_MENTION` | *(blank)* | Same as above, for deployment announcements only. |
| `SITE_URL` | *(blank)* | Absolute base for the links in an announcement, e.g. `https://scripts.example.com`. Falls back to the first `CSRF_TRUSTED_ORIGINS` entry. Shared by both. |

`SITE_URL` exists because nothing outside a web request knows the site's own address, and
the container that runs the hourly sync never has one. Without it — and without a trusted
origin to borrow — announcements still send, just with no link back to the script.

Mentions must be ids, not names — `@lewis` is sent as plain text and pings nobody,
because turning a typed `@name` into a mention is something Discord's own message box
does, and a webhook skips it. The forms that work:

| Write | Pings |
|---|---|
| `<@&ROLE_ID>` | a role |
| `<@USER_ID>` | a person |
| `@here` / `@everyone` | online members / everyone |

Several at once are space-separated: `<@&111...> <@222...>`. No quotes are needed in
`.env`. To find an id, turn on Discord **Settings → Advanced → Developer Mode**, then
right-click the role or person → **Copy Role ID** / **Copy User ID**. A mention that is
working shows in the channel as a coloured pill; raw `<...>` text means the format is
wrong.

Only the mention configured here is ever allowed to ping. Script names are typed by
whoever uploaded them, so a script called `@everyone` would otherwise ping the channel
every time it was mentioned; announcements name exactly the one id they are allowed to
notify and nothing else in the message can ping anybody.

## How noisy is it

One message per event, with two deliberate exceptions:

- **An import of a whole history** is one message. Pulling every version of a script
  writes a row per version; that is still one new script worth hearing about, reported at
  its newest version.
- **An hourly sync is one message for the whole run**, however many linked scripts gained
  versions. A message per script would be a burst of near-identical pings on the hour.

So the normal steady state is: nothing most hours, one line when something arrives.

## Deployments

A second, separate webhook announces a version being **marked as on the Minecraft
server**. Make a webhook on whichever channel should see deployments — it can be a
different one from arrivals — and set:

```
DISCORD_ONLINE_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Check it with:

```
docker compose exec botc-scripts python manage.py test_notification --online
```

(or `python manage.py test_notification --online` from a shell already inside the
container).

Each announcement says what went on the server, what it replaced, and who did it:

- **v1.0.5, replacing v1.0.4** — an update
- **v1.0.0, the first version of it on the server** — nothing was online before
- **v1.0.4, rolled back from v1.0.5** — an older version put back, shown in amber

Every route counts — the Mark online button on the Server page and the script page, the
Django admin's bulk action, its change form and its inline status column, and the shell.
Who marked it is named for everything except the shell, which has no one to name.

Only the **move to online** is announced. Saving a version that is already online says
nothing, and neither does marking one offline without putting another on. Taking the
previous version off when a new one goes on is not a separate message; it is the
"replacing" in the one above.

Marking a whole selection online from the admin is **one message**. If a selection holds
several versions of the same script, the message reports where that script started and
where it ended up — and if it ended where it started, nothing is said.

## When it fails

Announcements are best effort and never load-bearing. A webhook that is unreachable,
revoked or rejected is logged in the container and otherwise ignored — an upload still
succeeds, and a sync still finishes. Nothing is retried and nothing is queued for later,
so an announcement lost while Discord was down stays lost. The script itself is safe; it
is sitting on the site either way, and `/server` lists what still needs putting on the
Minecraft server (the newest version of each script that is not on it) whether or not its
arrival was ever announced.

Failures are logged at WARNING. The webhook URL is never written to the log.

## What it does not do

It does not tell you what still needs deploying — only what has just arrived. `/server`'s
*Needs deploying* tab is the standing list, and it does not depend on anyone having seen a message.

Nothing is announced when a version is marked offline, deleted, or edited in place. The
arrivals webhook covers new scripts and versions, and the deployment webhook covers versions
going online; taking the previous version off is the "replacing" in a deployment message,
not a message of its own.
