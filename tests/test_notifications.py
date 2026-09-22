import pytest
import requests

from scripts import notifications
from scripts.notifications import Announcement, Credit

WEBHOOK = "https://discord.com/api/webhooks/1/abcdef"


def make(name="Trouble Brewing", version="1.0.0", author="TPI", new_script=False, pk=1, path=None, credit=None):
    return Announcement(
        script_pk=pk,
        name=name,
        version=version,
        author=author,
        new_script=new_script,
        path=path or f"/script/{pk}",
        credit=credit,
    )


@pytest.fixture
def enabled(settings):
    settings.DISCORD_ARRIVALS_WEBHOOK_URL = WEBHOOK
    settings.DISCORD_ARRIVALS_WEBHOOK_MENTION = ""
    settings.SITE_URL = "https://scripts.example.com"
    return settings


@pytest.fixture
def deliveries():
    """(webhook, payload) for everything sent, for tests that care where it went."""
    return []


@pytest.fixture
def sent(monkeypatch, deliveries):
    """Capture payloads instead of posting them."""
    payloads = []

    def capture(payload, webhook):
        payloads.append(payload)
        deliveries.append((webhook, payload))
        return True

    monkeypatch.setattr(notifications, "post", capture)
    return payloads


@pytest.fixture
def immediate_commit(monkeypatch):
    """on_commit outside a transaction, without needing a database."""
    monkeypatch.setattr(notifications.transaction, "on_commit", lambda func: func())


# --- Configuration --------------------------------------------------------------------


def test_no_webhook_means_no_announcement(settings, monkeypatch):
    settings.DISCORD_ARRIVALS_WEBHOOK_URL = ""
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
    settings.DISCORD_ARRIVALS_WEBHOOK_URL = WEBHOOK
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


def test_several_scripts_become_one_batch(enabled, sent):
    notifications.announce([make(pk=1, name="One"), make(pk=2, name="Two", new_script=True)])
    assert len(sent) == 1
    embed = sent[0]["embeds"][0]
    assert embed["title"] == "2 new script versions"
    assert "One" in embed["description"]
    assert "new script" in embed["description"]
    assert "new version" in embed["description"]


def test_a_long_batch_is_truncated(enabled, sent):
    notifications.announce([make(pk=index, name=f"Script {index}") for index in range(notifications.MAX_LINES + 5)])
    description = sent[0]["embeds"][0]["description"]
    assert description.count("\n") == notifications.MAX_LINES
    assert "…and 5 more" in description


def test_a_batch_of_the_longest_names_discord_would_still_accept(enabled, sent):
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


def test_a_batch_that_fits_is_not_truncated(enabled, sent):
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
    settings.DISCORD_ARRIVALS_WEBHOOK_URL = WEBHOOK
    settings.DISCORD_ARRIVALS_WEBHOOK_MENTION = "<@&123456789012345678>"
    notifications.announce([make(name="@everyone look at this")])
    assert sent[0]["content"] == "<@&123456789012345678>"
    assert sent[0]["allowed_mentions"] == {"parse": [], "roles": ["123456789012345678"]}


def test_here_may_be_configured_deliberately(settings, sent):
    settings.DISCORD_ARRIVALS_WEBHOOK_URL = WEBHOOK
    settings.DISCORD_ARRIVALS_WEBHOOK_MENTION = "@here"
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
    assert notifications.post({"embeds": []}, notifications.ARRIVALS) is True


def test_an_unreachable_discord_is_swallowed(enabled, monkeypatch):
    def refuse(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(notifications.requests, "post", refuse)
    assert notifications.post({"embeds": []}, notifications.ARRIVALS) is False


def test_a_rejected_webhook_is_swallowed(enabled, monkeypatch):
    class Response:
        status_code = 404
        text = '{"message": "Unknown Webhook"}'

    monkeypatch.setattr(notifications.requests, "post", lambda *a, **k: Response())
    assert notifications.post({"embeds": []}, notifications.ARRIVALS) is False


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
    settings.DISCORD_ARRIVALS_WEBHOOK_URL = ""
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


# --- Deployments: a second, separate webhook ----------------------------------------------

ONLINE_WEBHOOK = "https://discord.com/api/webhooks/2/online"


def deploy(name="Trouble Brewing", version="1.1.0", replacing="1.0.0", by="lewis", pk=1):
    return notifications.Deployment(
        script_pk=pk, name=name, version=version, path=f"/script/{pk}", replacing=replacing, by=by
    )


@pytest.fixture
def both(settings):
    settings.DISCORD_ARRIVALS_WEBHOOK_URL = WEBHOOK
    settings.DISCORD_ARRIVALS_WEBHOOK_MENTION = ""
    settings.DISCORD_ONLINE_WEBHOOK_URL = ONLINE_WEBHOOK
    settings.DISCORD_ONLINE_WEBHOOK_MENTION = ""
    settings.SITE_URL = "https://scripts.example.com"
    return settings


def test_the_two_webhooks_are_configured_separately():
    assert notifications.ARRIVALS.url_setting == "DISCORD_ARRIVALS_WEBHOOK_URL"
    assert notifications.WENT_ONLINE.url_setting == "DISCORD_ONLINE_WEBHOOK_URL"
    assert notifications.ARRIVALS.mention_setting != notifications.WENT_ONLINE.mention_setting


def test_a_deployment_goes_to_the_deployment_webhook(both, sent, deliveries):
    notifications.announce_deployments([deploy()])
    assert [webhook for webhook, _ in deliveries] == [notifications.WENT_ONLINE]


def test_a_new_version_still_goes_to_its_own_webhook(both, sent, deliveries):
    notifications.announce([make()])
    assert [webhook for webhook, _ in deliveries] == [notifications.ARRIVALS]


def test_a_batch_of_both_kinds_is_one_message_to_each_channel(both, sent, deliveries, monkeypatch):
    monkeypatch.setattr(notifications, "describe", lambda version: version)
    monkeypatch.setattr(notifications, "describe_deployment", lambda version: version)
    with notifications.batched():
        notifications.record(make(pk=1))
        notifications.record_deployment(deploy(pk=2))
        notifications.record(make(pk=3))
    assert sorted(webhook.label for webhook, _ in deliveries) == ["arrivals", "online"]


def test_deployments_are_silent_without_their_own_webhook(settings, sent, deliveries):
    settings.DISCORD_ARRIVALS_WEBHOOK_URL = WEBHOOK
    settings.DISCORD_ONLINE_WEBHOOK_URL = ""
    assert notifications.announce_deployments([deploy()]) is False
    assert deliveries == []


def test_new_versions_are_silent_without_their_own_webhook(settings, sent, deliveries):
    settings.DISCORD_ARRIVALS_WEBHOOK_URL = ""
    settings.DISCORD_ONLINE_WEBHOOK_URL = ONLINE_WEBHOOK
    assert notifications.announce([make()]) is False
    assert deliveries == []


def test_each_webhook_pings_only_its_own_mention(both, sent, deliveries):
    both.DISCORD_ARRIVALS_WEBHOOK_MENTION = "<@&111111111111111111>"
    both.DISCORD_ONLINE_WEBHOOK_MENTION = "<@&222222222222222222>"
    notifications.announce([make()])
    notifications.announce_deployments([deploy()])
    by_label = {webhook.label: payload for webhook, payload in deliveries}
    assert by_label["arrivals"]["allowed_mentions"]["roles"] == ["111111111111111111"]
    assert by_label["online"]["allowed_mentions"]["roles"] == ["222222222222222222"]


# --- Deployment messages ------------------------------------------------------------------


def test_an_update_says_what_it_replaced(both, sent):
    notifications.announce_deployments([deploy(name="Party Lines", version="1.2.0", replacing="1.1.0")])
    embed = sent[0]["embeds"][0]
    assert embed["title"] == "Now on the server: Party Lines"
    assert embed["description"] == "**v1.2.0**, replacing v1.1.0"
    assert embed["url"] == "https://scripts.example.com/script/1"
    assert embed["color"] == notifications.COLOUR_ONLINE


def test_a_first_deployment_says_so(both, sent):
    notifications.announce_deployments([deploy(version="1.0.0", replacing=None)])
    assert sent[0]["embeds"][0]["description"] == "**v1.0.0**, the first version of it on the server"


def test_putting_an_older_version_back_is_a_rollback(both, sent):
    notifications.announce_deployments([deploy(version="1.0.4", replacing="1.0.5")])
    embed = sent[0]["embeds"][0]
    assert embed["description"] == "**v1.0.4**, rolled back from v1.0.5"
    assert embed["color"] == notifications.COLOUR_ROLLED_BACK


def test_version_ordering_is_numeric_not_alphabetical():
    # "1.0.10" sorts before "1.0.9" as text, which would call this upgrade a rollback.
    assert deploy(version="1.0.10", replacing="1.0.9").rolled_back is False
    assert deploy(version="1.0.9", replacing="1.0.10").rolled_back is True


def test_it_says_who_marked_it_online(both, sent):
    notifications.announce_deployments([deploy(by="lewis")])
    assert sent[0]["embeds"][0]["footer"] == {"text": "Marked online by lewis"}


def test_an_unknown_marker_leaves_the_footer_off(both, sent):
    notifications.announce_deployments([deploy(by=None)])
    assert "footer" not in sent[0]["embeds"][0]


def test_a_username_is_shown_as_typed_in_the_footer(both, sent):
    """Discord renders markdown in a description but not in a footer, which is plain text.

    Escaping there would put visible backslashes in the name, so the footer is the one
    place a username goes in verbatim. Mentions cannot fire from an embed either way.
    """
    notifications.announce_deployments([deploy(by="__lewis__")])
    assert sent[0]["embeds"][0]["footer"]["text"] == "Marked online by __lewis__"


def test_several_deployments_become_one_batch(both, sent):
    notifications.announce_deployments(
        [
            deploy(pk=1, name="One", version="2.0.0", replacing="1.0.0"),
            deploy(pk=2, name="Two", version="1.0.0", replacing=None),
        ]
    )
    embed = sent[0]["embeds"][0]
    assert embed["title"] == "2 scripts now on the server"
    assert "replacing v1.0.0" in embed["description"]
    assert "the first version of it on the server" in embed["description"]
    assert embed["footer"] == {"text": "Marked online by lewis"}


def test_a_deployment_batch_fits_discords_limits(both, sent):
    worst = [deploy(pk=index, name="*" * 100, version="10.10.10", replacing="9.9.9") for index in range(60)]
    notifications.announce_deployments(worst)
    embed = sent[0]["embeds"][0]
    assert len(embed["description"]) <= notifications.DESCRIPTION_LIMIT
    assert "more" in embed["description"].rsplit("\n", 1)[-1]


# --- Collapsing a batch of deployments ----------------------------------------------------


def test_a_batch_reports_where_the_script_started_and_where_it_ended():
    # Admin marks 1.0.4 then 1.0.5 online in one action, starting from 1.0.3.
    collapsed = notifications.collapse_deployments(
        [deploy(version="1.0.4", replacing="1.0.3"), deploy(version="1.0.5", replacing="1.0.4")]
    )
    assert len(collapsed) == 1
    assert (collapsed[0].version, collapsed[0].replacing) == ("1.0.5", "1.0.3")


def test_a_batch_that_ends_where_it_began_says_nothing(both, sent):
    notifications.announce_deployments(
        [deploy(version="1.0.4", replacing="1.0.3"), deploy(version="1.0.3", replacing="1.0.4")]
    )
    assert sent == []


def test_deployments_of_different_scripts_are_kept_apart():
    collapsed = notifications.collapse_deployments([deploy(pk=1), deploy(pk=2)])
    assert sorted(item.script_pk for item in collapsed) == [1, 2]


# --- Noticing a move to online ------------------------------------------------------------


@pytest.fixture
def stored(monkeypatch):
    """What the database would say, without a database. Records every lookup made."""
    state = {"status": models_offline(), "online": "1.0.4", "lookups": []}

    def stored_status(pk):
        state["lookups"].append(("status", pk))
        return state["status"]

    def online_version_of(script_id, excluding_pk):
        state["lookups"].append(("online", script_id))
        return state["online"]

    monkeypatch.setattr(notifications, "_stored_status", stored_status)
    monkeypatch.setattr(notifications, "_online_version_of", online_version_of)
    return state


def models_offline():
    from scripts import models

    return models.ScriptStatus.OFFLINE


def a_version(status="online", pk=34, version="1.0.5"):
    from scripts import models

    script = models.Script(pk=12, name="Assigned Mutant at Birth", slug=None)
    return models.ScriptVersion(pk=pk, script=script, version=version, status=status)


@pytest.fixture
def deployments_recorded(monkeypatch):
    recorded = []
    monkeypatch.setattr(notifications, "record_deployment", recorded.append)
    return recorded


def save(instance, **kwargs):
    """Run the pre- and post-save hooks around an imaginary save."""
    notifications.note_status_before_save(None, instance, **kwargs)
    notifications.announce_went_online(None, instance)


def test_marking_a_version_online_is_announced(both, stored, deployments_recorded):
    version = a_version(status="online")
    save(version)
    assert deployments_recorded == [version]
    assert version._replacing == "1.0.4"


def test_saving_a_version_that_was_already_online_is_not(both, stored, deployments_recorded):
    stored["status"] = "online"
    save(a_version(status="online"))
    assert deployments_recorded == []


def test_saving_a_version_as_offline_is_not(both, stored, deployments_recorded):
    save(a_version(status="offline"))
    assert deployments_recorded == []
    assert stored["lookups"] == []


def test_a_save_that_does_not_write_the_status_is_not(both, stored, deployments_recorded):
    save(a_version(status="online"), update_fields=["notes"])
    assert deployments_recorded == []


def test_loading_fixtures_is_not(both, stored, deployments_recorded):
    save(a_version(status="online"), raw=True)
    assert deployments_recorded == []


def test_without_the_deployment_webhook_nothing_is_looked_up(settings, stored, deployments_recorded):
    settings.DISCORD_ONLINE_WEBHOOK_URL = ""
    save(a_version(status="online"))
    assert deployments_recorded == []
    assert stored["lookups"] == [], "queried the database for a webhook nobody configured"


def test_saving_the_same_instance_twice_announces_once(both, stored, deployments_recorded):
    version = a_version(status="online")
    save(version)
    # The first save stored it as online.
    stored["status"] = "online"
    save(version)
    assert len(deployments_recorded) == 1


def test_a_brand_new_version_saved_as_online_is_announced(both, stored, deployments_recorded):
    stored["status"] = None
    save(a_version(status="online", pk=None))
    assert len(deployments_recorded) == 1


# --- Who did it ---------------------------------------------------------------------------


class _User:
    is_authenticated = True

    def __init__(self, name):
        self.name = name

    def get_username(self):
        return self.name


class _Anonymous:
    is_authenticated = False

    def get_username(self):
        return ""


def test_the_person_changing_the_status_is_named(both):
    version = a_version()
    version._replacing = "1.0.4"
    notifications.note_changed_by(version, _User("lewis"))
    assert notifications.describe_deployment(version).by == "lewis"


@pytest.mark.parametrize("user", [None, _Anonymous()])
def test_nobody_in_particular_is_not_named(both, user):
    version = a_version()
    notifications.note_changed_by(version, user)
    assert notifications.describe_deployment(version).by is None


def test_a_deployment_links_to_the_script(both):
    deployment = notifications.describe_deployment(a_version())
    assert deployment.path == "/script/12"
    assert deployment.name == "Assigned Mutant at Birth"
    assert deployment.version == "1.0.5"


# --- Wiring -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signal_name, dispatch_uid, receiver_name",
    [
        ("pre_save", "note_status_before_save", "note_status_before_save"),
        ("post_save", "announce_went_online", "announce_went_online"),
    ],
)
def test_the_deployment_hooks_are_connected_at_startup(signal_name, dispatch_uid, receiver_name):
    from django.db.models import signals

    from scripts import models

    signal = getattr(signals, signal_name)
    was_connected = signal.disconnect(dispatch_uid=dispatch_uid, sender=models.ScriptVersion)
    if was_connected:
        signal.connect(getattr(notifications, receiver_name), sender=models.ScriptVersion, dispatch_uid=dispatch_uid)
    assert was_connected, f"{receiver_name} is not connected to {signal_name}"


def test_every_admin_route_names_who_marked_it_online():
    """The bulk action, the change form and the inline status column all attribute."""
    import inspect

    from scripts import admin

    assert "note_changed_by" in inspect.getsource(admin.mark_on_server)
    assert "batched()" in inspect.getsource(admin.mark_on_server)
    assert "note_changed_by" in inspect.getsource(admin.ScriptVersionAdmin.save_model)
    assert "batched()" in inspect.getsource(admin.ScriptVersionAdmin.changelist_view)


def test_the_status_button_names_who_pressed_it():
    import inspect

    from scripts import views

    source = inspect.getsource(views.set_script_status)
    # Before the save, or the save has already been announced without a name.
    assert source.index("note_changed_by") < source.index(".save(")


# --- manage.py test_notification ----------------------------------------------------------


@pytest.fixture
def samples(monkeypatch):
    """Which webhooks the command sent to, without sending anything."""
    calls = []
    outcome = {"arrivals": True, "online": True}

    def arrivals(items):
        calls.append(("arrivals", len(items)))
        return outcome["arrivals"]

    def online(items):
        calls.append(("online", len(items)))
        return outcome["online"]

    monkeypatch.setattr(notifications, "announce", arrivals)
    monkeypatch.setattr(notifications, "announce_deployments", online)
    return {"calls": calls, "outcome": outcome}


def run_command(*args):
    import io

    from django.core.management import call_command

    out = io.StringIO()
    call_command("test_notification", *args, stdout=out)
    return out.getvalue()


def test_with_no_flag_every_configured_webhook_is_tested(both, samples):
    output = run_command()
    assert [name for name, _ in samples["calls"]] == ["arrivals", "online"]
    assert "Sent to the arrivals webhook" in output
    assert "Sent to the online webhook" in output


def test_with_no_flag_an_unset_webhook_is_skipped_not_an_error(both, samples):
    both.DISCORD_ONLINE_WEBHOOK_URL = ""
    output = run_command()
    assert [name for name, _ in samples["calls"]] == ["arrivals"]
    assert "DISCORD_ONLINE_WEBHOOK_URL is not set — skipping the online webhook" in output


def test_with_no_flag_and_nothing_configured_it_is_an_error(both, samples):
    from django.core.management.base import CommandError

    both.DISCORD_ARRIVALS_WEBHOOK_URL = ""
    both.DISCORD_ONLINE_WEBHOOK_URL = ""
    with pytest.raises(CommandError, match="Neither"):
        run_command()
    assert samples["calls"] == []


@pytest.mark.parametrize("flag, expected", [("--arrivals", ["arrivals"]), ("--online", ["online"])])
def test_a_flag_tests_only_that_webhook(both, samples, flag, expected):
    run_command(flag)
    assert [name for name, _ in samples["calls"]] == expected


@pytest.mark.parametrize(
    "flag, setting",
    [("--arrivals", "DISCORD_ARRIVALS_WEBHOOK_URL"), ("--online", "DISCORD_ONLINE_WEBHOOK_URL")],
)
def test_naming_an_unset_webhook_is_an_error(both, samples, flag, setting):
    from django.core.management.base import CommandError

    setattr(both, setting, "")
    with pytest.raises(CommandError, match=setting):
        run_command(flag)
    assert samples["calls"] == []


def test_naming_both_checks_both_before_sending_either(both, samples):
    from django.core.management.base import CommandError

    both.DISCORD_ONLINE_WEBHOOK_URL = ""
    with pytest.raises(CommandError, match="DISCORD_ONLINE_WEBHOOK_URL"):
        run_command("--arrivals", "--online")
    assert samples["calls"] == [], "sent to arrivals before finding online unset"


def test_batch_sends_the_several_at_once_form_to_each(both, samples):
    run_command("--batch")
    assert dict(samples["calls"]) == {"arrivals": 2, "online": 3}


def test_one_refused_webhook_fails_the_command_but_the_other_still_sends(both, samples):
    from django.core.management.base import CommandError

    samples["outcome"]["online"] = False
    with pytest.raises(CommandError, match="The online webhook did not accept it"):
        run_command()
    assert [name for name, _ in samples["calls"]] == ["arrivals", "online"]


# --- Who made it arrive: for staff reading the channel ---------------------------------------


class Person:
    """A user as far as the credit can tell: a username, and whether they are signed in."""

    def __init__(self, username="alice", signed_in=True, first_name="Alice"):
        self._username, self.is_authenticated, self.first_name = username, signed_in, first_name

    def get_username(self):
        return self._username


class Request:
    def __init__(self, user):
        self.user = user


@pytest.fixture(autouse=True)
def no_leftover_request():
    """The credit reads thread-local state, so no test may leave a request behind."""
    notifications.forget_request()
    yield
    notifications.forget_request()


def test_the_credit_reads_by_for_a_person_and_anonymously_for_nobody():
    assert Credit("Uploaded", by="alice").text() == "Uploaded by alice"
    assert Credit("Imported", by="alice").text() == "Imported by alice"
    assert Credit("Uploaded").text() == "Uploaded anonymously"
    assert Credit("Synced", origin="botcscripts.com").text() == "Synced from botcscripts.com"
    assert Credit("Imported", origin="the command line").text() == "Imported from the command line"


def test_a_single_announcement_puts_the_credit_in_the_footer_ahead_of_the_note():
    payload = notifications.build_payload([make(credit=Credit("Uploaded", by="alice"))])

    assert payload["embeds"][0]["footer"] == {"text": "Uploaded by alice · Not on the Minecraft server yet"}


def test_without_a_credit_the_footer_is_exactly_what_it_was():
    payload = notifications.build_payload([make()])

    assert payload["embeds"][0]["footer"] == {"text": "Not on the Minecraft server yet"}


def test_an_anonymous_upload_is_credited_as_anonymous_and_not_left_blank():
    payload = notifications.build_payload([make(credit=Credit("Uploaded"))])

    assert payload["embeds"][0]["footer"]["text"].startswith("Uploaded anonymously")


def test_the_credit_is_only_ever_in_the_footer_so_a_hostile_username_cannot_ping():
    """A footer is plain text in Discord, which renders no mention there, and allowed_mentions
    is empty besides. The name goes nowhere else, and it is not escaped: escaping would show
    the backslashes."""
    nasty = "@everyone <@123456789> <@&987654321> **bold**"
    payload = notifications.build_payload([make(credit=Credit("Uploaded", by=nasty))])
    embed = payload["embeds"][0]

    assert nasty in embed["footer"]["text"]
    assert nasty not in embed["title"] + embed["description"] + payload.get("content", "")
    assert payload["allowed_mentions"] == {"parse": []}


def test_a_batch_names_each_distinct_credit_once_in_the_footer():
    announcements = [
        make(pk=1, credit=Credit("Uploaded", by="alice")),
        make(pk=2, credit=Credit("Uploaded", by="alice")),
        make(pk=3, credit=Credit("Imported", by="bob")),
        make(pk=4, credit=Credit("Uploaded")),
    ]

    footer = notifications.build_payload(announcements)["embeds"][0]["footer"]["text"]

    assert footer == (
        "Uploaded by alice, Imported by bob, Uploaded anonymously · None of them are on the Minecraft server yet"
    )


def test_a_batch_with_no_credits_keeps_its_footer():
    footer = notifications.build_payload([make(pk=1), make(pk=2)])["embeds"][0]["footer"]["text"]

    assert footer == "None of them are on the Minecraft server yet"


def test_many_credits_are_cut_short_so_the_message_stays_inside_discords_limits():
    announcements = [
        make(pk=i, name="N" * 100, credit=Credit("Uploaded", by=f"user-{i}-" + "x" * 100)) for i in range(60)
    ]

    embed = notifications.build_payload(announcements)["embeds"][0]

    assert len(embed["footer"]["text"]) <= 2048
    assert embed["footer"]["text"].startswith("Uploaded by user-0-")
    assert "…" in embed["footer"]["text"]
    assert len(embed["description"]) + len(embed["title"]) + len(embed["footer"]["text"]) <= 6000


def test_collapsing_a_history_keeps_the_credit_of_the_version_it_keeps():
    older = make(version="1.0.0", credit=Credit("Imported", by="alice"))
    newer = make(version="1.1.0", credit=Credit("Imported", by="alice"))

    (kept,) = notifications.collapse([older, newer])

    assert kept.version == "1.1.0"
    assert kept.credit == Credit("Imported", by="alice")


# --- Where the credit comes from ---------------------------------------------------------------


def test_nothing_is_credited_outside_a_request_or_a_named_route():
    # The shell, a test, anything with no request and nothing declared: no line, as before.
    assert notifications.current_credit() is None


def test_a_signed_in_person_in_a_request_is_credited_by_username_and_not_first_name():
    notifications.remember_request(Request(Person(username="alice99", first_name="Alice")))

    assert notifications.current_credit() == Credit("Uploaded", by="alice99")


def test_an_anonymous_visitor_in_a_request_is_credited_as_anonymous():
    notifications.remember_request(Request(Person(signed_in=False)))

    assert notifications.current_credit() == Credit("Uploaded")


def test_being_signed_in_is_enough_whether_or_not_they_own_the_script():
    """ "Upload without owning the script" is about ownership, not privacy: staff still want
    to know who uploaded. Nothing about ownership reaches the credit at all."""
    notifications.remember_request(Request(Person(username="alice")))

    assert notifications.current_credit().by == "alice"


def test_the_user_is_read_when_the_credit_is_taken_not_when_the_request_began():
    """DRF authenticates inside the view and writes the user back to the request then, so a
    Basic-auth upload starts anonymous and is somebody by the time the version is saved."""
    request = Request(Person(signed_in=False))
    notifications.remember_request(request)
    request.user = Person(username="discordbot")

    assert notifications.current_credit() == Credit("Uploaded", by="discordbot")


def test_a_named_route_changes_the_verb_but_keeps_the_person():
    notifications.remember_request(Request(Person(username="alice")))

    with notifications.attributed("Imported"):
        assert notifications.current_credit() == Credit("Imported", by="alice")
    assert notifications.current_credit() == Credit("Uploaded", by="alice")


def test_a_route_with_nobody_behind_it_says_where_it_came_from_instead():
    with notifications.attributed("Synced", user=None, origin="botcscripts.com"):
        assert notifications.current_credit() == Credit("Synced", origin="botcscripts.com")


def test_an_operator_action_does_not_borrow_the_person_who_happened_to_trigger_it():
    # The admin's "sync now" runs sync for a signed-in staff member, but the versions came
    # from the source, not from them.
    notifications.remember_request(Request(Person(username="staffer")))

    with notifications.attributed("Synced", user=None, origin="botcscripts.com"):
        assert notifications.current_credit().by is None


def test_named_routes_nest_and_restore():
    with notifications.attributed("Imported", user=None, origin="the command line"):
        with notifications.attributed("Synced", user=None, origin="botcscripts.com"):
            assert notifications.current_credit().verb == "Synced"
        assert notifications.current_credit().verb == "Imported"
    assert notifications.current_credit() is None


def test_a_named_route_restores_the_previous_state_even_when_it_raises():
    with pytest.raises(RuntimeError), notifications.attributed("Imported", user=None):
        raise RuntimeError("boom")

    assert notifications.current_credit() is None


def test_the_credit_can_be_switched_off(settings):
    settings.DISCORD_ARRIVALS_SHOW_UPLOADER = False
    notifications.remember_request(Request(Person(username="alice")))

    assert notifications.current_credit() is None
    with notifications.attributed("Imported"):
        assert notifications.current_credit() is None


def test_the_middleware_remembers_the_request_for_its_duration_only():
    from scripts.middleware import RememberRequest

    request = Request(Person(username="alice"))
    seen = []

    def view(req):
        seen.append(notifications.current_credit())
        return "response"

    assert RememberRequest(view)(request) == "response"

    assert seen == [Credit("Uploaded", by="alice")]
    assert notifications.current_credit() is None


def test_the_middleware_forgets_the_request_even_when_the_view_raises():
    from scripts.middleware import RememberRequest

    def view(req):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        RememberRequest(view)(Request(Person()))

    assert notifications.current_credit() is None


def test_describe_takes_the_credit_when_the_version_is_created(monkeypatch):
    from scripts import models

    monkeypatch.setattr(notifications.models.ScriptVersion, "plain_objects", _CountingManager(1))
    version = models.ScriptVersion(script=models.Script(pk=3, name="Trouble Brewing"), version="1.0.0")
    notifications.remember_request(Request(Person(username="alice")))

    announcement = notifications.describe(version)
    notifications.forget_request()

    # Snapshot at creation, like everything else about it: sending happens later.
    assert announcement.credit == Credit("Uploaded", by="alice")


def test_middleware_is_installed_after_authentication_so_there_is_a_user_to_read():
    from django.conf import settings

    stack = settings.MIDDLEWARE
    assert stack.index("scripts.middleware.RememberRequest") > stack.index(
        "django.contrib.auth.middleware.AuthenticationMiddleware"
    )
