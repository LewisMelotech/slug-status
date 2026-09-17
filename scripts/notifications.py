"""Announcing script activity to Discord channels.

A Discord incoming webhook is the whole mechanism: one URL, posted to with an ordinary
HTTPS request. No bot account, no gateway connection, no permissions to grant. Who sees
the announcements is a property of the channel that webhook belongs to, so changing the
audience is a Discord-side change and needs nothing here — move the webhook to another
channel, or make a new one and swap the URL.

There are two webhooks, each switched on by its own URL, so each kind of announcement can
go to its own channel — or both to the same one, or either to none:

- ARRIVALS: a new script arrives, or a script already held gains a newer version.
  A version filed in *behind* the latest is not announced; backfilling v0.9 of a script
  already at v1.2 changes nothing about what should be on the Minecraft server.
- WENT_ONLINE: a version is marked as on the Minecraft server. Only the move to online
  is announced, with what it replaced. Re-saving a version that is already online says
  nothing, and neither does taking one off without putting another on.

Announcements are best effort and never load-bearing. Every failure is logged and
swallowed: Discord being down must not fail somebody's upload, stop a sync, or leave a
status change half made.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace

import requests
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse

from scripts import models

logger = logging.getLogger(__name__)

# Discord webhooks normally answer in well under a second. This is generous because
# losing an announcement is worse than an occasional slow request, and the common
# failure — Discord unreachable at all — refuses the connection immediately rather
# than running the clock down.
TIMEOUT = 10

USERNAME = "BotC Scripts"
COLOUR_NEW_SCRIPT = 0x2ECC71
COLOUR_NEW_VERSION = 0x5865F2
COLOUR_ONLINE = 0x23A55A
COLOUR_ROLLED_BACK = 0xF0B232

# Lines in a batch before the rest become "and N more".
MAX_LINES = 20

# Discord's own cap on an embed description, and it rejects the whole message for going
# over. Script names and author names run to 100 characters each and escaping can double
# them, so twenty lines do not reliably fit in it — batches are built to a character
# budget as well as a line count.
DESCRIPTION_LIMIT = 4096

_ROLE_MENTION = re.compile(r"<@&(\d+)>")
_USER_MENTION = re.compile(r"<@!?(\d+)>")

# Script names, author names and usernames are all typed by somebody, so they are escaped
# before going anywhere near a message body.
_MARKDOWN = str.maketrans({character: "\\" + character for character in "*_~`|\\>"})


# --- Destinations -----------------------------------------------------------------------


@dataclass(frozen=True)
class Webhook:
    """One Discord destination: where it posts, and who it may ping.

    Read from settings on every use rather than once at import, so a test — or anything
    else overriding settings — sees the value in force at the time.
    """

    label: str
    url_setting: str
    mention_setting: str

    @property
    def url(self) -> str:
        """The configured webhook, or "" when this destination is switched off."""
        return (getattr(settings, self.url_setting, "") or "").strip()

    @property
    def mention(self) -> str:
        return (getattr(settings, self.mention_setting, "") or "").strip()


ARRIVALS = Webhook("arrivals", "DISCORD_ARRIVALS_WEBHOOK_URL", "DISCORD_ARRIVALS_WEBHOOK_MENTION")
WENT_ONLINE = Webhook("online", "DISCORD_ONLINE_WEBHOOK_URL", "DISCORD_ONLINE_WEBHOOK_MENTION")


# --- What is worth saying ---------------------------------------------------------------


@dataclass(frozen=True)
class Announcement:
    """One newly created version."""

    script_pk: int
    name: str
    version: str
    author: str | None
    new_script: bool
    path: str

    @property
    def wording(self) -> str:
        return "New script" if self.new_script else "New version"


@dataclass(frozen=True)
class Deployment:
    """One version that has just been marked as on the Minecraft server."""

    script_pk: int
    name: str
    version: str
    path: str
    replacing: str | None
    by: str | None

    @property
    def rolled_back(self) -> bool:
        return self.replacing is not None and _version_key(self.version) < _version_key(self.replacing)


# --- Configuration ------------------------------------------------------------------------


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


# --- Describing a version -----------------------------------------------------------------


def escape(text: str | None) -> str:
    return (text or "").translate(_MARKDOWN)


def _script_path(script) -> str:
    if script.slug:
        return reverse("script_by_slug", args=[script.slug])
    return reverse("script", args=[script.pk])


def describe(script_version) -> Announcement:
    """Snapshot one new version as an Announcement.

    Taken at creation time rather than at send time on purpose: a batch importing a
    script's whole history would otherwise see three versions by the time it came to
    report the first, and call a brand new script an update to a known one.
    """
    script = script_version.script
    held = models.ScriptVersion.plain_objects.filter(script_id=script.pk).count()
    return Announcement(
        script_pk=script.pk,
        name=script.name,
        version=str(script_version.version),
        author=script_version.author or None,
        new_script=held <= 1,
        path=_script_path(script),
    )


def describe_deployment(script_version) -> Deployment:
    """Snapshot one version that has just gone online.

    What it replaced was noted before the save, by note_status_before_save: by now the
    save has happened, and the version it replaced is about to be taken off.
    """
    script = script_version.script
    return Deployment(
        script_pk=script.pk,
        name=script.name,
        version=str(script_version.version),
        path=_script_path(script),
        replacing=getattr(script_version, "_replacing", None),
        by=_username(getattr(script_version, "_status_changed_by", None)),
    )


def _username(user) -> str | None:
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user.get_username() or None


def note_changed_by(script_version, user) -> None:
    """Record who is changing a version's status, so the announcement can say.

    A model save has no idea which request caused it, so the routes that have one — the
    status button and the Django admin — pass it along on the instance. Anything else,
    the shell included, is announced without a name.
    """
    script_version._status_changed_by = user


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


def collapse_deployments(deployments: list[Deployment]) -> list[Deployment]:
    """One line per script: what was on the server before the batch, and what is now.

    Marking several versions of one script online in a single admin action leaves the
    last one saved on the server, each save having taken the previous one off. So the
    last event says what is there now and the first says what was there before. A batch
    that ends where it began is dropped — nothing on the server changed.
    """
    first: dict[int, Deployment] = {}
    last: dict[int, Deployment] = {}
    for item in deployments:
        first.setdefault(item.script_pk, item)
        last[item.script_pk] = item
    collapsed = [replace(last[pk], replacing=first[pk].replacing) for pk in last]
    return [item for item in collapsed if item.version != item.replacing]


# --- Building the message -----------------------------------------------------------------


def _link(item: Announcement | Deployment) -> str | None:
    base = site_url()
    return f"{base}{item.path}" if base else None


def _label(item: Announcement | Deployment) -> str:
    name = escape(item.name)
    link = _link(item)
    return f"[{name}]({link})" if link else f"**{name}**"


def _by(announcement: Announcement) -> str:
    return f" by {escape(announcement.author)}" if announcement.author else ""


def _budgeted(lines: list[str]) -> str:
    """Join batch lines within Discord's limits, ending in "and N more" if cut short."""
    kept: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        tail = f"- …and {len(lines) - index} more"
        # Room is always kept for the tail, so running out of either budget still leaves
        # a message that says how much it is not showing.
        if len(kept) >= MAX_LINES or used + len(line) + len(tail) + 2 > DESCRIPTION_LIMIT:
            kept.append(tail)
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


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


def _batch_embed(announcements: list[Announcement]) -> dict:
    lines = [f"- {_label(item)} v{escape(item.version)}{_by(item)} — {item.wording.lower()}" for item in announcements]
    return {
        "title": f"{len(announcements)} new script versions",
        "description": _budgeted(lines),
        "color": COLOUR_NEW_VERSION,
        "footer": {"text": "None of them are on the Minecraft server yet"},
    }


def _change(deployment: Deployment) -> str:
    if deployment.replacing is None:
        return "the first version of it on the server"
    if deployment.rolled_back:
        return f"rolled back from v{escape(deployment.replacing)}"
    return f"replacing v{escape(deployment.replacing)}"


def _marked_by(deployments: list[Deployment]) -> dict | None:
    # Not escaped: a footer is plain text in Discord, so escaping would show the
    # backslashes. Descriptions render markdown, which is why everything there is.
    names = list(dict.fromkeys(item.by for item in deployments if item.by))
    return {"text": f"Marked online by {', '.join(names)}"} if names else None


def _single_deployment_embed(deployment: Deployment) -> dict:
    embed = {
        "title": f"Now on the server: {deployment.name}"[:256],
        "description": f"**v{escape(deployment.version)}**, {_change(deployment)}",
        "color": COLOUR_ROLLED_BACK if deployment.rolled_back else COLOUR_ONLINE,
    }
    link = _link(deployment)
    if link:
        embed["url"] = link
    footer = _marked_by([deployment])
    if footer:
        embed["footer"] = footer
    return embed


def _batch_deployment_embed(deployments: list[Deployment]) -> dict:
    lines = [f"- {_label(item)} v{escape(item.version)}, {_change(item)}" for item in deployments]
    embed = {
        "title": f"{len(deployments)} scripts now on the server",
        "description": _budgeted(lines),
        "color": COLOUR_ONLINE,
    }
    footer = _marked_by(deployments)
    if footer:
        embed["footer"] = footer
    return embed


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


def _payload(embed: dict, notify: str) -> dict:
    payload = {
        "username": USERNAME,
        "embeds": [embed],
        "allowed_mentions": allowed_mentions(notify),
    }
    if notify:
        payload["content"] = notify
    return payload


def build_payload(announcements: list[Announcement], notify: str = "") -> dict:
    embed = _single_embed(announcements[0]) if len(announcements) == 1 else _batch_embed(announcements)
    return _payload(embed, notify)


def build_deployment_payload(deployments: list[Deployment], notify: str = "") -> dict:
    if len(deployments) == 1:
        embed = _single_deployment_embed(deployments[0])
    else:
        embed = _batch_deployment_embed(deployments)
    return _payload(embed, notify)


# --- Sending ------------------------------------------------------------------------------


def post(payload: dict, webhook: Webhook) -> bool:
    url = webhook.url
    if not url:
        return False
    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Could not reach the %s webhook: %s", webhook.label, exc)
        return False
    if response.status_code >= 400:
        # The body carries Discord's own reason. The URL never goes in the log: anyone
        # holding it can post to that channel.
        logger.warning(
            "The %s webhook rejected the announcement: %s %s",
            webhook.label,
            response.status_code,
            (response.text or "")[:300],
        )
        return False
    return True


def announce(announcements: list[Announcement]) -> bool:
    """Send one message covering these new versions. Never raises."""
    try:
        if not announcements or not ARRIVALS.url:
            return False
        return post(build_payload(collapse(announcements), ARRIVALS.mention), ARRIVALS)
    except Exception:  # pragma: no cover - an announcement must never break its subject
        logger.exception("Could not announce %d new script version(s)", len(announcements))
        return False


def announce_deployments(deployments: list[Deployment]) -> bool:
    """Send one message covering these deployments. Never raises."""
    try:
        if not deployments or not WENT_ONLINE.url:
            return False
        collapsed = collapse_deployments(deployments)
        if not collapsed:
            return False
        return post(build_deployment_payload(collapsed, WENT_ONLINE.mention), WENT_ONLINE)
    except Exception:  # pragma: no cover - an announcement must never break its subject
        logger.exception("Could not announce %d deployment(s)", len(deployments))
        return False


# --- Collecting ---------------------------------------------------------------------------

_batch = threading.local()


@contextmanager
def batched():
    """Collect announcements raised inside this block and send them together.

    The hourly sync walks every linked script, a single import can create a version row
    per version in a script's history, and an admin action can mark a whole selection
    online, so without this one action could produce a dozen separate messages. Each kind
    still goes to its own webhook — one message per destination, not one in total.

    Nested blocks belong to the outermost one, which is what lets sync_upstream batch a
    whole run while import_script still batches its own other callers.
    """
    if getattr(_batch, "pending", None) is not None:
        yield  # already inside a batch; the outermost block does the sending
        return

    _batch.pending = []
    try:
        yield
    finally:
        pending, _batch.pending = _batch.pending, None
        # In the finally, so whatever was written before something went wrong is still
        # announced. Both senders swallow their own failures, so this cannot mask the
        # exception on its way out.
        announce([item for item in pending if isinstance(item, Announcement)])
        announce_deployments([item for item in pending if isinstance(item, Deployment)])


def _queue(item, send: Callable[[list], bool]) -> None:
    pending = getattr(_batch, "pending", None)
    if pending is not None:
        pending.append(item)
        return
    # After commit, not now: a change that rolls back must not announce anything, and a
    # status change must finish taking the previous version off before Discord is asked
    # to wait on — see ScriptVersion.save.
    transaction.on_commit(lambda: send([item]))


def record(script_version) -> None:
    """Queue an announcement for a version that has just been created."""
    try:
        if not ARRIVALS.url:
            return
        _queue(describe(script_version), announce)
    except Exception:  # pragma: no cover - an announcement must never break an upload
        logger.exception("Could not queue a Discord announcement for %s", script_version)


def record_deployment(script_version) -> None:
    """Queue an announcement for a version that has just been marked online."""
    try:
        if not WENT_ONLINE.url:
            return
        _queue(describe_deployment(script_version), announce_deployments)
    except Exception:  # pragma: no cover - an announcement must never break a status change
        logger.exception("Could not queue a Discord deployment announcement for %s", script_version)


# --- Hooks --------------------------------------------------------------------------------


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


def _stored_status(pk) -> str | None:
    if pk is None:
        return None
    return models.ScriptVersion.plain_objects.filter(pk=pk).values_list("status", flat=True).first()


def _online_version_of(script_id, excluding_pk) -> str | None:
    online = models.ScriptVersion.plain_objects.filter(script_id=script_id, status=models.ScriptStatus.ONLINE)
    if excluding_pk is not None:
        online = online.exclude(pk=excluding_pk)
    current = online.first()
    return str(current.version) if current else None


@receiver(pre_save, sender=models.ScriptVersion, dispatch_uid="note_status_before_save")
def note_status_before_save(sender, instance, raw=False, update_fields=None, **kwargs):
    """Note whether this save is a move to online, and what it will take off the server.

    Has to happen before the save: afterwards the new status is already stored, and the
    version being replaced is about to be taken off. Costs nothing unless the deployment
    webhook is configured and the version is actually being saved as online.
    """
    instance._went_online = False
    if raw or not WENT_ONLINE.url or instance.status != models.ScriptStatus.ONLINE:
        return
    if update_fields is not None and "status" not in update_fields:
        return
    try:
        if _stored_status(instance.pk) == models.ScriptStatus.ONLINE:
            return  # already online; saving it again deploys nothing
        instance._replacing = _online_version_of(instance.script_id, instance.pk)
        instance._went_online = True
    except Exception:  # pragma: no cover - an announcement must never break a status change
        logger.exception("Could not check the previous status of %s", instance)


@receiver(post_save, sender=models.ScriptVersion, dispatch_uid="announce_went_online")
def announce_went_online(sender, instance, **kwargs):
    """The status button, the admin action, admin edits and the shell all end here."""
    if not getattr(instance, "_went_online", False):
        return
    # note_status_before_save resets this on every save; clearing it here as well means
    # nothing that fires post_save again without a fresh pre_save can repeat it.
    instance._went_online = False
    record_deployment(instance)
