# Announcing new scripts to Discord

The instance can post to a Discord channel whenever a script arrives that might need
putting on the Minecraft server. Two things are announced:

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
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123456789/abcdef...
```

and restart the stack. Check it without waiting for a real upload:

```
docker compose exec botc-scripts python manage.py test_notification
```

`--digest` sends the several-at-once form instead.

**Who sees the announcements is a Discord question, not a setting here.** The webhook
belongs to one channel, so channel permissions decide the audience. To change who sees
them, move the webhook to another channel — or make a webhook on a different channel and
swap the URL. Nothing in this app needs to know.

Treat the URL as a secret. Anyone holding it can post to that channel as the webhook, and
it carries no other access.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | *(blank)* | The webhook. Blank switches announcements off entirely. |
| `DISCORD_WEBHOOK_MENTION` | *(blank)* | Prepended to each announcement, e.g. `<@&123456789012345678>` for a role, or `@here`. |
| `SITE_URL` | *(blank)* | Absolute base for the links in an announcement, e.g. `https://scripts.example.com`. Falls back to the first `CSRF_TRUSTED_ORIGINS` entry. |

`SITE_URL` exists because nothing outside a web request knows the site's own address, and
the container that runs the hourly sync never has one. Without it — and without a trusted
origin to borrow — announcements still send, just with no link back to the script.

To find a role's id for `DISCORD_WEBHOOK_MENTION`: Discord **Settings → Advanced →
Developer Mode**, then right-click the role → **Copy Role ID**, and wrap it as
`<@&THEID>`.

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

## When it fails

Announcements are best effort and never load-bearing. A webhook that is unreachable,
revoked or rejected is logged in the container and otherwise ignored — an upload still
succeeds, and a sync still finishes. Nothing is retried and nothing is queued for later,
so an announcement lost while Discord was down stays lost. The script itself is safe; it
is sitting on the site either way, and `/server` lists everything not yet on the Minecraft
server whether or not its arrival was ever announced.

Failures are logged at WARNING. The webhook URL is never written to the log.

## What it does not do

It does not tell you what still needs deploying — only what has just arrived. `/server` is
the standing list, and it does not depend on anyone having seen a message.

It does not announce a version being marked online or offline, deleted, or edited in
place. Only new arrivals.
