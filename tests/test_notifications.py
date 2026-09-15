import pytest
import requests

from scripts import notifications
from scripts.notifications import Announcement

WEBHOOK = "https://discord.com/api/webhooks/1/abcdef"


def make(name="Trouble Brewing", version="1.0.0", author="TPI", new_script=False, pk=1, path=None):
    return Announcement(
        script_pk=pk,
        name=name,
        version=version,
        author=author,
        new_script=new_script,
        path=path or f"/script/{pk}",
    )


@pytest.fixture
def enabled(settings):
    settings.DISCORD_WEBHOOK_URL = WEBHOOK
    settings.DISCORD_WEBHOOK_MENTION = ""
    settings.SITE_URL = "https://scripts.example.com"
    return settings


@pytest.fixture
def sent(monkeypatch):
    """Capture payloads instead of posting them."""
    payloads = []
    monkeypatch.setattr(notifications, "post", lambda payload: payloads.append(payload) or True)
    return payloads


@pytest.fixture
def immediate_commit(monkeypatch):
    """on_commit outside a transaction, without needing a database."""
    monkeypatch.setattr(notifications.transaction, "on_commit", lambda func: func())


# --- Configuration --------------------------------------------------------------------


def test_no_webhook_means_no_announcement(settings, monkeypatch):
    settings.DISCORD_WEBHOOK_URL = ""
    called = []
    monkeypatch.setattr(notifications.requests, "post", lambda *a, **k: called.append(a))
    assert notifications.announce([make()]) is False
    assert called == []


def test_nothing_to_say_is_not_announced(enabled, sent):
    assert notifications.announce([]) is False
    assert sent == []


def test_site_url_prefers_the_explicit_setting(settings):
    settings.SITE_URL = "https://scripts.example.com"
    settings.CSRF_TRUSTED_ORIGINS = ["https://other.example.com"]
    assert notifications.site_url() == "https://scripts.example.com"


def test_site_url_falls_back_to_a_trusted_origin(settings):
    settings.SITE_URL = ""
    settings.CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "https://scripts.example.com/"]
    assert notifications.site_url() == "https://scripts.example.com"


def test_site_url_takes_what_it_can_get(settings):
    settings.SITE_URL = ""
    settings.CSRF_TRUSTED_ORIGINS = ["http://localhost:8000"]
    assert notifications.site_url() == "http://localhost:8000"


def test_site_url_may_be_unknown(settings):
    settings.SITE_URL = ""
    settings.CSRF_TRUSTED_ORIGINS = []
    assert notifications.site_url() == ""


def test_an_announcement_without_a_site_url_still_sends(settings, sent):
    settings.DISCORD_WEBHOOK_URL = WEBHOOK
    settings.SITE_URL = ""
    settings.CSRF_TRUSTED_ORIGINS = []
    assert notifications.announce([make()]) is True
    assert "url" not in sent[0]["embeds"][0]


# --- Message content ------------------------------------------------------------------


def test_markdown_in_a_name_is_escaped():
    assert notifications.escape("**Sects** _and_ `Violets`") == "\\*\\*Sects\\*\\* \\_and\\_ \\`Violets\\`"


def test_a_new_script_is_announced_as_one(enabled, sent):
    notifications.announce([make(name="String Theory", version="3.0.0", new_script=True)])
    embed = sent[0]["embeds"][0]
    assert embed["title"] == "New script: String Theory"
    assert "3.0.0" in embed["description"]
    assert embed["url"] == "https://scripts.example.com/script/1"
    assert embed["color"] == notifications.COLOUR_NEW_SCRIPT


def test_a_new_version_of_a_known_script_is_announced_as_one(enabled, sent):
    notifications.announce([make(name="Sects and Violets", version="2.1.0")])
    embed = sent[0]["embeds"][0]
    assert embed["title"] == "New version: Sects and Violets"
    assert embed["color"] == notifications.COLOUR_NEW_VERSION


def test_an_unknown_author_is_left_out(enabled, sent):
    notifications.announce([make(author=None)])
    assert "by" not in sent[0]["embeds"][0]["description"]


def test_several_scripts_become_one_digest(enabled, sent):
    notifications.announce([make(pk=1, name="One"), make(pk=2, name="Two", new_script=True)])
    assert len(sent) == 1
    embed = sent[0]["embeds"][0]
    assert embed["title"] == "2 new script versions"
    assert "One" in embed["description"]
    assert "new script" in embed["description"]
    assert "new version" in embed["description"]


def test_a_long_digest_is_truncated(enabled, sent):
    notifications.announce([make(pk=index, name=f"Script {index}") for index in range(notifications.MAX_LINES + 5)])
    description = sent[0]["embeds"][0]["description"]
    assert description.count("\n") == notifications.MAX_LINES
    assert "…and 5 more" in description


def test_a_digest_of_the_longest_names_discord_would_still_accept(enabled, sent):
    """Names and authors are 100 characters each in the model, and escaping doubles them.

    Twenty such lines run past Discord's 4096-character description cap, and it rejects
    the whole message for going over rather than trimming it.
    """
    worst = [make(pk=index, name="*" * 100, author="_" * 100, version="10.10.10") for index in range(50)]
    notifications.announce(worst)
    embed = sent[0]["embeds"][0]
    assert len(embed["description"]) <= notifications.DESCRIPTION_LIMIT
    assert len(embed["title"]) <= 256
    # Discord also caps the whole embed at 6000 characters.
    assert len(embed["description"]) + len(embed["title"]) + len(embed["footer"]["text"]) <= 6000
    assert "more" in embed["description"].rsplit("\n", 1)[-1]


def test_a_digest_that_fits_is_not_truncated(enabled, sent):
    notifications.announce([make(pk=index, name=f"Script {index}") for index in range(3)])
    description = sent[0]["embeds"][0]["description"]
    assert "more" not in description
    assert description.count("\n") == 2


# --- Mentions -------------------------------------------------------------------------


def test_a_script_named_everyone_cannot_ping_the_channel(enabled, sent):
    notifications.announce([make(name="@everyone look at this")])
    assert sent[0]["allowed_mentions"] == {"parse": []}
    assert "content" not in sent[0]


def test_the_configured_role_may_ping(settings, sent):
    settings.DISCORD_WEBHOOK_URL = WEBHOOK
    settings.DISCORD_WEBHOOK_MENTION = "<@&123456789012345678>"
    notifications.announce([make(name="@everyone look at this")])
    assert sent[0]["content"] == "<@&123456789012345678>"
    assert sent[0]["allowed_mentions"] == {"parse": [], "roles": ["123456789012345678"]}


def test_here_may_be_configured_deliberately(settings, sent):
    settings.DISCORD_WEBHOOK_URL = WEBHOOK
    settings.DISCORD_WEBHOOK_MENTION = "@here"
    notifications.announce([make()])
    assert sent[0]["allowed_mentions"]["parse"] == ["everyone"]


# --- Collapsing -----------------------------------------------------------------------


def test_a_whole_history_collapses_to_its_newest_version():
    collapsed = notifications.collapse(
        [
            make(pk=7, version="1.0.0", new_script=True),
            make(pk=7, version="1.0.10"),
            make(pk=7, version="1.0.2"),
        ]
    )
    assert len(collapsed) == 1
    assert collapsed[0].version == "1.0.10"
    # Its first version arrived in the same batch, so it is still a new script.
    assert collapsed[0].new_script is True


def test_different_scripts_are_kept_apart():
    collapsed = notifications.collapse([make(pk=1), make(pk=2), make(pk=1, version="2.0.0")])
    assert sorted(item.script_pk for item in collapsed) == [1, 2]


def test_an_odd_version_string_does_not_break_ordering():
    collapsed = notifications.collapse([make(pk=1, version="alpha"), make(pk=1, version="1.0.0")])
    assert collapsed[0].version == "1.0.0"


# --- Delivery -------------------------------------------------------------------------


def test_a_successful_post_reports_success(enabled, monkeypatch):
    class Response:
        status_code = 204
        text = ""

    monkeypatch.setattr(notifications.requests, "post", lambda *a, **k: Response())
    assert notifications.post({"embeds": []}) is True


def test_an_unreachable_discord_is_swallowed(enabled, monkeypatch):
    def refuse(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(notifications.requests, "post", refuse)
    assert notifications.post({"embeds": []}) is False


def test_a_rejected_webhook_is_swallowed(enabled, monkeypatch):
    class Response:
        status_code = 404
        text = '{"message": "Unknown Webhook"}'

    monkeypatch.setattr(notifications.requests, "post", lambda *a, **k: Response())
    assert notifications.post({"embeds": []}) is False


def test_announce_never_raises(enabled, monkeypatch):
    def explode(*args, **kwargs):
        raise ValueError("unbuildable")

    monkeypatch.setattr(notifications, "build_payload", explode)
    assert notifications.announce([make()]) is False


# --- Batching -------------------------------------------------------------------------


@pytest.fixture
def recording(monkeypatch):
    """record() without a database: describe() returns whatever it is handed."""
    monkeypatch.setattr(notifications, "describe", lambda version: version)


def test_one_message_per_batch(enabled, sent, recording):
    with notifications.batched():
        notifications.record(make(pk=1, name="One"))
        notifications.record(make(pk=2, name="Two"))
    assert len(sent) == 1
    assert sent[0]["embeds"][0]["title"] == "2 new script versions"


def test_a_nested_batch_belongs_to_the_outer_one(enabled, sent, recording):
    with notifications.batched():
        notifications.record(make(pk=1))
        with notifications.batched():
            notifications.record(make(pk=2))
        notifications.record(make(pk=3))
    assert len(sent) == 1
    assert sent[0]["embeds"][0]["title"] == "3 new script versions"


def test_an_empty_batch_says_nothing(enabled, sent, recording):
    with notifications.batched():
        pass
    assert sent == []


def test_a_failed_run_still_announces_what_it_wrote(enabled, sent, recording):
    with pytest.raises(RuntimeError), notifications.batched():
        notifications.record(make(pk=1, name="Imported before the failure"))
        raise RuntimeError("upstream went away")
    assert len(sent) == 1
    assert "Imported before the failure" in sent[0]["embeds"][0]["title"]


def test_the_batch_does_not_leak_into_the_next_one(enabled, sent, recording):
    with notifications.batched():
        notifications.record(make(pk=1))
    with notifications.batched():
        notifications.record(make(pk=2))
    assert len(sent) == 2


def test_outside_a_batch_each_version_is_announced(enabled, sent, recording, immediate_commit):
    notifications.record(make(pk=1))
    notifications.record(make(pk=2))
    assert len(sent) == 2


def test_nothing_is_recorded_when_the_webhook_is_unset(settings, sent, recording):
    settings.DISCORD_WEBHOOK_URL = ""
    with notifications.batched():
        notifications.record(make())
    assert sent == []


# --- What triggers an announcement ------------------------------------------------------


class FakeVersion:
    def __init__(self, latest=True):
        self.latest = latest


def test_a_new_latest_version_is_announced(monkeypatch):
    recorded = []
    monkeypatch.setattr(notifications, "record", recorded.append)
    notifications.announce_new_version(None, instance=FakeVersion(latest=True), created=True)
    assert len(recorded) == 1


def test_an_edit_to_an_existing_version_is_not(monkeypatch):
    recorded = []
    monkeypatch.setattr(notifications, "record", recorded.append)
    notifications.announce_new_version(None, instance=FakeVersion(latest=True), created=False)
    assert recorded == []


def test_a_version_filed_behind_the_latest_is_not(monkeypatch):
    recorded = []
    monkeypatch.setattr(notifications, "record", recorded.append)
    notifications.announce_new_version(None, instance=FakeVersion(latest=False), created=True)
    assert recorded == []


# --- Wiring -----------------------------------------------------------------------------


def test_the_receiver_is_connected_at_startup():
    """ScriptsConfig.ready() is what registers it; without that nothing announces anything.

    Every test above calls the receiver directly, so all of them would still pass with
    the import missing from apps.py.
    """
    from django.db.models.signals import post_save

    from scripts import models

    # disconnect() reports whether there was anything to disconnect, which is the public
    # way to ask. Reconnected immediately, so the rest of the suite is unaffected.
    was_connected = post_save.disconnect(dispatch_uid="announce_new_script_version", sender=models.ScriptVersion)
    if was_connected:
        post_save.connect(
            notifications.announce_new_version,
            sender=models.ScriptVersion,
            dispatch_uid="announce_new_script_version",
        )
    assert was_connected, "ScriptsConfig.ready() did not connect the announcement receiver"


def test_a_script_with_a_slug_is_linked_by_it(monkeypatch):
    from scripts import models

    monkeypatch.setattr(notifications.models.ScriptVersion, "plain_objects", _CountingManager(3))
    script = models.Script(pk=12, name="Sects and Violets", slug="sects")
    version = models.ScriptVersion(script=script, version="2.1.0", author="TPI")
    announcement = notifications.describe(version)
    assert announcement.path == "/script/sects"
    assert announcement.new_script is False
    assert announcement.name == "Sects and Violets"


def test_a_script_without_a_slug_is_linked_by_its_id(monkeypatch):
    from scripts import models

    monkeypatch.setattr(notifications.models.ScriptVersion, "plain_objects", _CountingManager(1))
    script = models.Script(pk=12, name="Trouble Brewing", slug=None)
    version = models.ScriptVersion(script=script, version="1.0.0", author=None)
    announcement = notifications.describe(version)
    assert announcement.path == "/script/12"
    # Its only version, so the script itself is new.
    assert announcement.new_script is True


class _CountingManager:
    """Stands in for ScriptVersion.plain_objects, counting without a database."""

    def __init__(self, held):
        self.held = held

    def filter(self, **kwargs):
        return self

    def count(self):
        return self.held
