"""Announcing new script versions to a Discord channel.

A Discord incoming webhook is the whole mechanism: one URL, posted to with an ordinary
HTTPS request. No bot account, no gateway connection, no permissions to grant. Who sees
the announcements is a property of the channel that webhook belongs to, so changing the
audience is a Discord-side change and needs nothing here — move the webhook to another
channel, or make a new one and swap the URL.

Announcements are best effort and never load-bearing. Every failure is logged and
swallowed: Discord being down must not fail somebody's upload or stop a sync.

What gets announced:

- a new script, the first time any version of it arrives, and
- a new version that became the newest version of a script already held.

A version filed in *behind* the latest one is not announced. Nothing about what should be
on the Minecraft server changes when someone backfills v0.9 of a script already at v1.2,
and the point of the announcement is that something new may need putting there.
"""

from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace

import requests
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse

from scripts import models

logger = logging.getLogger(__name__)

# Discord webhooks normally answer in well under a second. This is generous because
# losing an announcement is worse than an occasional slow upload, and the common
# failure — Discord unreachable at all — refuses the connection immediately rather
# than running the clock down.
TIMEOUT = 10

USERNAME = "BotC Scripts"
COLOUR_NEW_SCRIPT = 0x2ECC71
COLOUR_NEW_VERSION = 0x5865F2

# Lines in a digest before the rest become "and N more".
MAX_LINES = 20

# Discord's own cap on an embed description, and it rejects the whole message for going
# over. Script names and author names run to 100 characters each and escaping can double
# them, so twenty lines do not reliably fit in it — the digest is built to a character
# budget as well as a line count.
DESCRIPTION_LIMIT = 4096

_ROLE_MENTION = re.compile(r"<@&(\d+)>")
_USER_MENTION = re.compile(r"<@!?(\d+)>")

# Script and author names are typed by whoever uploaded them, so they are escaped before
# going anywhere near a message body.
_MARKDOWN = str.maketrans({character: "\\" + character for character in "*_~`|\\>"})


@dataclass(frozen=True)
class Announcement:
    """What is worth saying about one newly created version."""

    script_pk: int
    name: str
    version: str
    author: str | None
    new_script: bool
    path: str

    @property
    def wording(self) -> str:
        return "New script" if self.new_script else "New version"


# --- Configuration ------------------------------------------------------------------


def webhook_url() -> str:
    """The configured webhook, or "" when the feature is switched off."""
    return (getattr(settings, "DISCORD_WEBHOOK_URL", "") or "").strip()


def mention() -> str:
    return (getattr(settings, "DISCORD_WEBHOOK_MENTION", "") or "").strip()


def site_url() -> str:
    """Absolute base for links in announcements.

    Django only knows its own address inside a request, and the sync container never
    has one, so this is configured rather than derived. SITE_URL is the explicit
    answer; failing that the CSRF trusted origins are the same public origin in every
    deployment that has one, which saves configuring the address twice.
    """
    configured = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    if configured:
        return configured

    origins = [(origin or "").strip().rstrip("/") for origin in getattr(settings, "CSRF_TRUSTED_ORIGINS", [])]
    origins = [origin for origin in origins if origin]
    for origin in origins:
        if origin.startswith("https://"):
            return origin
    return origins[0] if origins else ""


# --- Building the message -----------------------------------------------------------


def escape(text: str | None) -> str:
    return (text or "").translate(_MARKDOWN)


def describe(script_version) -> Announcement:
    """Snapshot one new version as an Announcement.

    Taken at creation time rather than at send time on purpose: a batch importing a
    script's whole history would otherwise see three versions by the time it came to
    report the first, and call a brand new script an update to a known one.
    """
    script = script_version.script
    held = models.ScriptVersion.plain_objects.filter(script_id=script.pk).count()
    if script.slug:
        path = reverse("script_by_slug", args=[script.slug])
    else:
        path = reverse("script", args=[script.pk])
    return Announcement(
        script_pk=script.pk,
        name=script.name,
        version=str(script_version.version),
        author=script_version.author or None,
        new_script=held <= 1,
        path=path,
    )


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in str(value).split("."))


def collapse(announcements: list[Announcement]) -> list[Announcement]:
    """One line per script, keeping its newest version.

    Importing a script's whole history creates a row per version, each of them the
    newest at the moment it was written. Ten versions of one script is one thing worth
    saying, not ten.
    """
    best: dict[int, Announcement] = {}
    for item in announcements:
        current = best.get(item.script_pk)
        if current is None:
            best[item.script_pk] = item
            continue
        newest = item if _version_key(item.version) > _version_key(current.version) else current
        # The script is new if any version in the batch was its first.
        best[item.script_pk] = replace(newest, new_script=current.new_script or item.new_script)
    return list(best.values())


def _link(announcement: Announcement) -> str | None:
    base = site_url()
    return f"{base}{announcement.path}" if base else None


def _by(announcement: Announcement) -> str:
    return f" by {escape(announcement.author)}" if announcement.author else ""


def _single_embed(announcement: Announcement) -> dict:
    embed = {
        "title": f"{announcement.wording}: {announcement.name}"[:256],
        "description": f"**v{escape(announcement.version)}**{_by(announcement)}",
        "color": COLOUR_NEW_SCRIPT if announcement.new_script else COLOUR_NEW_VERSION,
        "footer": {"text": "Not on the Minecraft server yet"},
    }
    link = _link(announcement)
    if link:
        embed["url"] = link
    return embed


def _digest_line(announcement: Announcement) -> str:
    name = escape(announcement.name)
    link = _link(announcement)
    label = f"[{name}]({link})" if link else f"**{name}**"
    return f"- {label} v{escape(announcement.version)}{_by(announcement)} — {announcement.wording.lower()}"


def _digest_embed(announcements: list[Announcement]) -> dict:
    lines: list[str] = []
    used = 0
    for index, announcement in enumerate(announcements):
        line = _digest_line(announcement)
        tail = f"- …and {len(announcements) - index} more"
        # Room is always kept for the tail, so running out of either budget still leaves
        # a message that says how much it is not showing.
        if len(lines) >= MAX_LINES or used + len(line) + len(tail) + 2 > DESCRIPTION_LIMIT:
            lines.append(tail)
            break
        lines.append(line)
        used += len(line) + 1
    return {
        "title": f"{len(announcements)} new script versions",
        "description": "\n".join(lines),
        "color": COLOUR_NEW_VERSION,
        "footer": {"text": "None of them are on the Minecraft server yet"},
    }


def allowed_mentions(text: str) -> dict:
    """Let only the configured mention ping, and nothing found in a script's name.

    Script names come from whoever uploaded them. Without this a script called
    "@everyone" would ping the whole channel every time it was announced.
    """
    if not text:
        return {"parse": []}
    allowed: dict = {"parse": [], "roles": _ROLE_MENTION.findall(text)}
    users = [uid for uid in _USER_MENTION.findall(text)]
    if users:
        allowed["users"] = users
    if "@everyone" in text or "@here" in text:
        allowed["parse"] = ["everyone"]
    return allowed


def build_payload(announcements: list[Announcement], notify: str = "") -> dict:
    embed = _single_embed(announcements[0]) if len(announcements) == 1 else _digest_embed(announcements)
    payload = {
        "username": USERNAME,
        "embeds": [embed],
        "allowed_mentions": allowed_mentions(notify),
    }
    if notify:
        payload["content"] = notify
    return payload


# --- Sending --------------------------------------------------------------------------


def post(payload: dict) -> bool:
    url = webhook_url()
    if not url:
        return False
    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Could not reach the Discord webhook: %s", exc)
        return False
    if response.status_code >= 400:
        # The body carries Discord's own reason. The URL never goes in the log: anyone
        # holding it can post to that channel.
        logger.warning(
            "Discord webhook rejected the announcement: %s %s",
            response.status_code,
            (response.text or "")[:300],
        )
        return False
    return True


def announce(announcements: list[Announcement]) -> bool:
    """Send one message covering these announcements. Never raises."""
    try:
        if not announcements or not webhook_url():
            return False
        return post(build_payload(collapse(announcements), mention()))
    except Exception:  # pragma: no cover - an announcement must never break its subject
        logger.exception("Could not announce %d new script version(s)", len(announcements))
        return False


# --- Collecting -----------------------------------------------------------------------

_batch = threading.local()


@contextmanager
def batched():
    """Collect announcements raised inside this block and send them as one message.

    The hourly sync walks every linked script and a single import can create a version
    row per version in a script's history, so without this a quiet sync could produce a
    dozen separate messages. Nested blocks belong to the outermost one, which is what
    lets sync_upstream batch a whole run while import_script still batches its own
    other callers.
    """
    if getattr(_batch, "pending", None) is not None:
        yield  # already inside a batch; the outermost block does the sending
        return

    _batch.pending = []
    try:
        yield
    finally:
        pending, _batch.pending = _batch.pending, None
        # In the finally, so versions already written before something went wrong are
        # still announced. announce() swallows its own failures, so this cannot mask
        # whatever exception is on its way out.
        announce(pending)


def record(script_version) -> None:
    """Queue an announcement for a version that has just been created."""
    try:
        if not webhook_url():
            return
        announcement = describe(script_version)
        pending = getattr(_batch, "pending", None)
        if pending is not None:
            pending.append(announcement)
            return
        # After commit, not now: an upload that rolls back must not announce a version
        # that no longer exists.
        transaction.on_commit(lambda: announce([announcement]))
    except Exception:  # pragma: no cover - an announcement must never break an upload
        logger.exception("Could not queue a Discord announcement for %s", script_version)


@receiver(post_save, sender=models.ScriptVersion, dispatch_uid="announce_new_script_version")
def announce_new_version(sender, instance, created, **kwargs):
    """Every route that creates a version passes through here.

    The web upload, the API, importing, the hourly sync, the admin and the shell all end
    at ScriptVersion.save(), so this is the one place that catches them all — including
    any route written later.
    """
    if not created or not instance.latest:
        return
    record(instance)
