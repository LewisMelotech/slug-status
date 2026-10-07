"""Importing scripts from another botc-scripts instance, and keeping them in step.

The public site at botcscripts.com is the usual source, but nothing here is specific
to it: any instance exposing the same read API works, including another copy of this
one. Only the public read endpoints are used, so no credentials are needed on the far
side.
"""

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from versionfield import Version

from scripts import models, notifications, script_json

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = "https://www.botcscripts.com"
TIMEOUT = 30
USER_AGENT = "botc-scripts-importer/1.0 (self-hosted instance)"


class UpstreamError(Exception):
    """Anything that stopped us reading from the upstream instance."""


class NotOwner(UpstreamError):
    """The script an import would change belongs to someone other than the importer.

    An UpstreamError so the import page and the commands, which already catch that and
    show its message, report it without knowing about ownership.
    """


class Blocked(UpstreamError):
    """The source refused us outright (403) or told us to slow down (429).

    botcscripts.com's firewall blocks an instance that has asked it too much, and the
    block stays until it is lifted by hand. Every further request in the same run would
    be refused too and could only count against us, so the commands stop asking that
    source at the first refusal.
    """


BLOCKED_STATUSES = (403, 429)


def normalise_source(source):
    """Reduce a source to a bare scheme://host[:port], so links compare equal."""
    source = str(source or "").strip()
    parsed = urlparse(source if "//" in source else f"https://{source}")
    host = parsed.netloc
    # urlparse invents a netloc from almost any string, so check the host looks like
    # one rather than trusting that it parsed at all.
    if parsed.scheme not in ("http", "https") or not host or any(c.isspace() for c in host):
        raise UpstreamError(f"'{source}' is not a usable instance URL.")
    return f"{parsed.scheme}://{host}"


def allowed_sources():
    """The instances an ordinary uploader may import from, normalised."""
    from django.conf import settings

    configured = getattr(settings, "IMPORT_SOURCES", None) or [DEFAULT_SOURCE]
    allowed = []
    for source in configured:
        try:
            allowed.append(normalise_source(source))
        except UpstreamError:
            logger.warning("Ignoring unusable entry in IMPORT_SOURCES: %r", source)
    return allowed or [DEFAULT_SOURCE]


def may_import_from(source, user=None):
    """Whether this user may pull from this instance.

    The permission that guards the write API also lifts the source restriction; for
    everyone else the source has to be one this instance has nominated, because the
    fetch happens from the server rather than from the visitor's browser.
    """
    if user is not None and getattr(user, "is_authenticated", False) and user.has_perm("scripts.api_write_permission"):
        return True
    return normalise_source(source) in allowed_sources()


def parse_reference(reference, default_source=DEFAULT_SOURCE):
    """Turn '134', a '/script/134' path, or a full URL into (source, script_id)."""
    reference = str(reference).strip()
    if reference.isascii() and reference.isdigit():
        return normalise_source(default_source), int(reference)

    parsed = urlparse(reference if "//" in reference else f"https://{reference}")
    parts = [p for p in parsed.path.split("/") if p]
    for part in parts:
        if part.isascii() and part.isdigit():
            return normalise_source(f"{parsed.scheme}://{parsed.netloc}"), int(part)
    raise UpstreamError(f"Could not find a script id in '{reference}'. Give an id, or a link to a script page.")


class UpstreamClient:
    def __init__(self, source=DEFAULT_SOURCE, timeout=TIMEOUT, session=None):
        self.source = normalise_source(source)
        self.timeout = timeout
        self.session = session or requests.Session()
        # Assigned, not setdefault: a Session always carries a python-requests
        # User-Agent already, which botcscripts.com answers with a 403 on the PDF
        # download path, so setdefault would leave the blocked value in place.
        self.session.headers["User-Agent"] = USER_AGENT

    def _get(self, path, **kwargs):
        url = path if path.startswith("http") else f"{self.source}{path}"
        try:
            return self.session.get(url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise UpstreamError(f"{self.source} could not be reached: {exc}") from exc

    def _blocked(self, response):
        return Blocked(
            f"{self.source} refused the request with HTTP {response.status_code}: this instance has "
            "probably been blocked for making too many requests. Nothing more was asked of it."
        )

    def _json(self, path):
        response = self._get(path)
        if response.status_code in BLOCKED_STATUSES:
            raise self._blocked(response)
        if response.status_code == 404:
            raise UpstreamError(f"{url_of(response)} was not found on {self.source}.")
        if response.status_code != 200:
            raise UpstreamError(f"{self.source} returned HTTP {response.status_code} for {url_of(response)}.")
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(f"{self.source} did not return JSON for {url_of(response)}.") from exc

    def script(self, script_id):
        """The upstream script: {pk, name, versions: {version: url}, latest_version}."""
        return self._json(f"/api/script_ids/{script_id}/?format=json")

    def version(self, url):
        """One version row: {pk, script_id, name, version, script_type, author, content}."""
        return self._json(url)

    def newest_versions(self, page=1):
        """One page of the latest version of every script, newest first.

        {count, next, results}, where each result is a version row like ``version`` returns,
        content and all. Left to its defaults the list leaves out hybrid and homebrew
        scripts, which can be linked here as well as any other, so it is asked for both.
        """
        return self._json(
            f"/api/scripts/?format=json&ordering=-pk&include_hybrid=true&include_homebrew=true&page={page}"
        )

    def search(self, query, limit=10):
        payload = self._json(f"/api/scripts/?format=json&search={requests.utils.quote(query)}")
        results = payload.get("results", []) if isinstance(payload, dict) else []
        return results[:limit]

    def pdf(self, script_id, version):
        """The version's PDF, or None when upstream has none.

        Upstream answers a missing PDF with a 500 carrying an HTML page rather than a
        404, so anything that is not a PDF body is treated as 'no PDF' instead of an
        error — importing the JSON is still worthwhile when the PDF is absent. Being
        refused is the exception: that raises, so a version is not stored without a PDF
        it does have just because the source had stopped answering us.
        """
        response = self._get(f"/script/{script_id}/{version}/download_pdf")
        if response.status_code in BLOCKED_STATUSES:
            raise self._blocked(response)
        if response.status_code != 200 or not response.content.startswith(b"%PDF"):
            return None
        return response.content


def url_of(response):
    return response.url


def _view_helpers():
    """Import the shared counting helpers lazily.

    They live in scripts.views, which imports scripts.forms, which imports this
    module for the import form — so importing them at module level would be a cycle.
    """
    from scripts.views import (
        calculate_edition,
        count_character,
        create_characters_and_determine_homebrew_status,
    )

    return calculate_edition, count_character, create_characters_and_determine_homebrew_status


def _counts(content):
    _, count_character, _ = _view_helpers()
    return {
        "num_townsfolk": count_character(content, models.CharacterType.TOWNSFOLK),
        "num_outsiders": count_character(content, models.CharacterType.OUTSIDER),
        "num_minions": count_character(content, models.CharacterType.MINION),
        "num_demons": count_character(content, models.CharacterType.DEMON),
        "num_fabled": count_character(content, models.CharacterType.FABLED),
        "num_loric": count_character(content, models.CharacterType.LORIC),
        "num_travellers": count_character(content, models.CharacterType.TRAVELLER),
    }


def _target_script(source, upstream_id, name, user=None, enforce_owner=True):
    """The local Script for this upstream one: the linked one, else by name, else new.

    A script found by its link is the source's own, so anyone may import into it: what
    lands is what the source published. One found only by sharing a name is a different
    matter, because an import would add versions to it and then link it to the source.
    If someone owns it, only that owner may, or staff: the upload form's rule, with staff
    let through since they can change any script from the admin.

    ``user`` is who is importing, and None or an anonymous user owns nothing. Callers that
    act as the operator rather than for a visitor, the command line and sync, pass
    ``enforce_owner=False``. Forgetting to say leaves the rule on.
    """
    script = models.Script.objects.filter(upstream_source=source, upstream_id=upstream_id).first()
    if script:
        return script, False
    script = models.Script.objects.filter(name=name).first()
    if script:
        if enforce_owner and not script.may_add_versions(user):
            raise NotOwner(
                f"'{script.name}' already exists here and belongs to another user, "
                "so only its owner or staff can import into it."
            )
        return script, False
    return models.Script(name=name), True


def _record_check(script, source, upstream_id, link):
    """Note that the source was asked about ``script`` just now, linking it if wanted."""
    if link:
        script.upstream_source = source
        script.upstream_id = upstream_id
        script.sync_enabled = True
    script.last_synced = timezone.now()
    script.save()


def _version(number):
    try:
        return Version(str(number))
    except (ValueError, NotImplementedError) as exc:
        raise UpstreamError(f"'{number}' is not a version number this instance can store.") from exc


def _held_versions(script):
    """The version numbers this instance holds for ``script``; none for one not yet saved."""
    if script.pk is None:
        return []
    return list(script.versions.values_list("version", flat=True))


def _latest_of(available, declared_url=None):
    """The source's latest version number: the one it flags as latest, else the highest."""
    for number, url in available.items():
        if declared_url and url == declared_url:
            return number
    return max(available, key=_version)


def _select_versions(available, held, latest=None):
    """Which of the source's versions to fetch, oldest first, and how many are held here.

    ``available`` is the source's {version number: url} and ``held`` the numbers this
    instance already has. Everything not held is wanted, unless ``latest`` names the
    source's latest version: then that one alone is, and only if it is newer than the
    newest held. Versions it skips, between the two or further back, are a full sync's.
    """
    held = {_version(number) for number in held}
    offered = sorted(((_version(number), number, url) for number, url in available.items()), key=lambda o: o[0])
    newest = max(held) if held else None
    wanted = [
        (number, url)
        for parsed, number, url in offered
        if parsed not in held and (latest is None or (number == latest and (newest is None or parsed > newest)))
    ]
    already_held = sum(1 for parsed, _, _ in offered if parsed in held)
    return wanted, already_held


@transaction.atomic
def import_version(source, row, pdf=None, link=True, user=None, enforce_owner=True):
    """Create one ScriptVersion from an upstream version row.

    Returns the new ScriptVersion, or None when that version is already held locally.
    Raises NotOwner when ``user`` may not import into the script it matches; see
    ``_target_script``.
    """
    source = normalise_source(source)
    upstream_id = row.get("script_id")
    name = row.get("name")
    version = row.get("version")
    if not (upstream_id and name and version):
        raise UpstreamError(f"Upstream version row is missing script_id, name or version: {row!r}")

    script, is_new = _target_script(source, upstream_id, name, user, enforce_owner)
    _record_check(script, source, upstream_id, link)

    if not is_new and script.versions.filter(version=version).exists():
        return None

    # Mirror the upload API: a version newer than the current latest takes the latest
    # flag from it, an older one is filed behind without disturbing anything.
    is_latest = True
    inherited_tags = None
    current_latest = script.latest_version()
    if current_latest:
        if Version(version) > current_latest.version:
            inherited_tags = current_latest.tags
            current_latest.latest = False
            current_latest.save()
        else:
            is_latest = False

    calculate_edition, _, determine_homebrewiness = _view_helpers()
    content = script_json.get_json_content({"content": row.get("content")})
    homebrewiness = determine_homebrewiness(content, script)

    script_version = models.ScriptVersion.objects.create(
        script=script,
        version=version,
        content=content,
        script_type=row.get("script_type") or models.ScriptTypes.FULL,
        author=row.get("author") or None,
        latest=is_latest,
        edition=calculate_edition(content),
        homebrewiness=homebrewiness,
        **_counts(content),
    )

    if pdf:
        script_version.pdf.save(f"{name}.pdf", ContentFile(pdf), save=True)
    if inherited_tags:
        script_version.tags.add(*inherited_tags.all())

    return script_version


def import_script(
    reference,
    source=DEFAULT_SOURCE,
    link=True,
    client=None,
    user=None,
    enforce_owner=True,
    latest_only=False,
):
    """Import a script from upstream by id or URL.

    Takes every version the source has and this instance does not, so a first import
    brings the script's whole history. ``latest_only`` narrows that to the source's latest
    version, when it is newer than the newest held here, which is all routine sync asks
    for; see ``sync_script``.

    Returns (script, imported, skipped) where imported is the list of ScriptVersions
    created and skipped counts the source's versions already held locally. ``user`` is
    who is importing; see ``_target_script`` for the ownership rule and ``enforce_owner``.
    """
    source, upstream_id = parse_reference(reference, source)
    client = client or UpstreamClient(source)
    detail = client.script(upstream_id)
    versions = detail.get("versions") or {}
    if not versions:
        raise UpstreamError(f"Script {upstream_id} on {source} has no versions to import.")

    # Refused here, before anything else is fetched: every version and its PDF is a request
    # to someone else's server, and a refusal does not need any of them.
    script, _ = _target_script(source, upstream_id, detail.get("name"), user, enforce_owner)

    # Decided from the version list the source has already sent, so a version held here
    # costs it nothing: only what is actually wanted is fetched, two requests apiece.
    latest = _latest_of(versions, detail.get("latest_version")) if latest_only else None
    wanted, skipped = _select_versions(versions, _held_versions(script), latest)
    if not wanted:
        # import_version records the check when it runs; nothing else will this time.
        _record_check(script, source, upstream_id, link)

    # One announcement for the import, not one per version: pulling a script's whole
    # history writes a row per version, and that is still only one new script worth
    # telling a channel about.
    imported = []
    with notifications.batched():
        for version_number, url in wanted:
            row = client.version(url)
            pdf = client.pdf(upstream_id, row.get("version") or version_number)
            created = import_version(source, row, pdf=pdf, link=link, user=user, enforce_owner=enforce_owner)
            if created is None:
                # Arrived here by another route since the list was compared.
                skipped += 1
                logger.info("Already held %s %s from %s", detail.get("name"), version_number, source)
            else:
                imported.append(created)
                logger.info("Imported %s %s from %s", detail.get("name"), version_number, source)

    script = models.Script.objects.filter(upstream_source=source, upstream_id=upstream_id).first()
    if script is None:
        script = models.Script.objects.filter(name=detail.get("name")).first()
    return script, imported, skipped


def sync_script(script, client=None, full=False):
    """Pull versions upstream has that this one script does not, by hand.

    This is a lookup of one known script, not the scheduled sync, which is ``sync_source``.
    It asks the source for the script's list of version numbers and fetches the latest
    version and its PDF only when that is newer than the newest held here: one request when
    nothing is new, three when something is. ``full`` instead fetches every version missing
    here, the way a first import does.

    Returns the list of ScriptVersions created, which is empty when already in step.
    """
    if not (script.upstream_source and script.upstream_id):
        raise UpstreamError(f"{script} is not linked to an upstream instance.")
    with notifications.attributed("Synced", user=None, origin=script.upstream_source):
        _, imported, _ = import_script(
            script.upstream_id,
            source=script.upstream_source,
            link=True,
            client=client,
            # Sync follows scripts already linked to their source, and runs as the system.
            enforce_owner=False,
            latest_only=not full,
        )
    return imported


# The most pages of the newest-versions list one sync reads. At 50 versions a page that is
# a thousand new versions, weeks of activity on botcscripts.com. A run that reaches it says
# so, and the next one carries on from the newest version it saw.
MAX_PAGES = 20


@dataclass
class SourceSync:
    """What one ``sync_source`` run did."""

    source: str
    pages: int = 0
    first_run: bool = False
    limited: bool = False
    imported: list = field(default_factory=list)
    failed: list = field(default_factory=list)


def sync_source(source, client=None):
    """Bring every script linked to ``source`` up to date, in one read of its newest versions.

    This is how the botcscripts.com maintainer asked instances to sync. /api/scripts/ lists
    the latest version of every script, newest first, and version ids there only ever
    increase. So reading it page by page until reaching the newest id seen last time finds
    every script that has gained a version since, usually in one request. Each row already
    carries its content, so the only other request is the PDF of a new version of a script
    linked here. Rows for other scripts are passed over.

    With no cursor yet, only the first page is read, and the cursor starts from there: there
    is nothing to say how far back to look. Versions published before that are for
    `sync_upstream --full --script <id>` to fetch.

    The cursor only moves once the whole read has succeeded, so a run stopped by a refusal
    starts from the same place next time. Versions it had already written are held by then,
    and are passed over rather than fetched again.
    """
    source = normalise_source(source)
    client = client or UpstreamClient(source)
    linked = _linked_by_upstream_id(source)
    last_seen = cursor_for(source)
    result = SourceSync(source=source, first_run=last_seen is None)

    rows, newest = [], None
    for page in range(1, MAX_PAGES + 1):
        payload = client.newest_versions(page)
        result.pages = page
        reached = False
        for row in payload.get("results") or []:
            pk = row.get("pk")
            if not isinstance(pk, int):
                continue
            newest = pk if newest is None else max(newest, pk)
            if last_seen is not None and pk <= last_seen:
                reached = True
                break
            if row.get("script_id") in linked:
                rows.append(row)
        if reached or last_seen is None or not payload.get("next"):
            break
    else:
        result.limited = True

    with notifications.attributed("Synced", user=None, origin=source), notifications.batched():
        # Oldest first, as an upload would arrive. Each row is a different script, since the
        # list holds only latest versions.
        for row in reversed(rows):
            script = linked[row["script_id"]]
            version = row.get("version")
            try:
                wanted, _ = _select_versions({version: None}, _held_versions(script), latest=version)
                if not wanted:
                    continue
                pdf = client.pdf(row["script_id"], version)
                created = import_version(source, row, pdf=pdf, link=True, enforce_owner=False)
            except Blocked:
                raise
            except UpstreamError as exc:
                result.failed.append(f"{script.name} {version}: {exc}")
                continue
            if created is not None:
                result.imported.append(created)

    if newest is not None and (last_seen is None or newest > last_seen):
        _advance_cursor(source, newest)
    _mark_checked(source)
    return result


def _linked_by_upstream_id(source):
    return {script.upstream_id: script for script in linked_scripts().filter(upstream_source=source)}


def cursor_for(source):
    """The newest version id seen at ``source`` by the last sync, or None before the first."""
    return models.UpstreamCursor.objects.filter(source=source).values_list("last_version_pk", flat=True).first()


def _advance_cursor(source, pk):
    models.UpstreamCursor.objects.update_or_create(source=source, defaults={"last_version_pk": pk})


def _mark_checked(source):
    linked_scripts().filter(upstream_source=source).update(last_synced=timezone.now())


def linked_scripts():
    return models.Script.objects.filter(
        sync_enabled=True,
        upstream_id__isnull=False,
        upstream_source__isnull=False,
    ).order_by("pk")


def linked_sources():
    """Every instance at least one script here is linked to, normalised."""
    return sorted({normalise_source(source) for source in linked_scripts().values_list("upstream_source", flat=True)})
