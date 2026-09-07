"""Importing scripts from another botc-scripts instance, and keeping them in step.

The public site at botcscripts.com is the usual source, but nothing here is specific
to it: any instance exposing the same read API works, including another copy of this
one. Only the public read endpoints are used, so no credentials are needed on the far
side.
"""

import logging
from urllib.parse import urlparse

import requests
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from versionfield import Version

from scripts import models, script_json

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = "https://www.botcscripts.com"
TIMEOUT = 30
USER_AGENT = "botc-scripts-importer/1.0 (self-hosted instance)"


class UpstreamError(Exception):
    """Anything that stopped us reading from the upstream instance."""


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

    def _json(self, path):
        response = self._get(path)
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

    def search(self, query, limit=10):
        payload = self._json(f"/api/scripts/?format=json&search={requests.utils.quote(query)}")
        results = payload.get("results", []) if isinstance(payload, dict) else []
        return results[:limit]

    def pdf(self, script_id, version):
        """The version's PDF, or None when upstream has none.

        Upstream answers a missing PDF with a 500 carrying an HTML page rather than a
        404, so anything that is not a PDF body is treated as 'no PDF' instead of an
        error — importing the JSON is still worthwhile when the PDF is absent.
        """
        response = self._get(f"/script/{script_id}/{version}/download_pdf")
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


def _target_script(source, upstream_id, name):
    """The local Script for this upstream one: the linked one, else by name, else new."""
    script = models.Script.objects.filter(upstream_source=source, upstream_id=upstream_id).first()
    if script:
        return script, False
    script = models.Script.objects.filter(name=name).first()
    if script:
        return script, False
    return models.Script(name=name), True


@transaction.atomic
def import_version(source, row, pdf=None, link=True, status=None):
    """Create one ScriptVersion from an upstream version row.

    Returns the new ScriptVersion, or None when that version is already held locally.
    """
    source = normalise_source(source)
    upstream_id = row.get("script_id")
    name = row.get("name")
    version = row.get("version")
    if not (upstream_id and name and version):
        raise UpstreamError(f"Upstream version row is missing script_id, name or version: {row!r}")

    script, is_new = _target_script(source, upstream_id, name)
    if link:
        script.upstream_source = source
        script.upstream_id = upstream_id
        script.sync_enabled = True
    script.last_synced = timezone.now()
    script.save()

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
        status=status or models.ScriptStatus.ONLINE,
        **_counts(content),
    )

    if pdf:
        script_version.pdf.save(f"{name}.pdf", ContentFile(pdf), save=True)
    if inherited_tags:
        script_version.tags.add(*inherited_tags.all())

    return script_version


def import_script(reference, source=DEFAULT_SOURCE, link=True, all_versions=False, client=None, status=None):
    """Import a script from upstream by id or URL.

    Returns (script, imported, skipped) where imported is the list of ScriptVersions
    created and skipped counts versions already held locally.
    """
    source, upstream_id = parse_reference(reference, source)
    client = client or UpstreamClient(source)
    detail = client.script(upstream_id)
    versions = detail.get("versions") or {}
    if not versions:
        raise UpstreamError(f"Script {upstream_id} on {source} has no versions to import.")

    wanted = versions.items() if all_versions else [_latest_of(versions, detail)]

    imported, skipped = [], 0
    for version_number, url in wanted:
        row = client.version(url)
        pdf = client.pdf(upstream_id, row.get("version") or version_number)
        created = import_version(source, row, pdf=pdf, link=link, status=status)
        if created is None:
            skipped += 1
            logger.info("Already held %s %s from %s", detail.get("name"), version_number, source)
        else:
            imported.append(created)
            logger.info("Imported %s %s from %s", detail.get("name"), version_number, source)

    script = models.Script.objects.filter(upstream_source=source, upstream_id=upstream_id).first()
    if script is None:
        script = models.Script.objects.filter(name=detail.get("name")).first()
    return script, imported, skipped


def _latest_of(versions, detail):
    latest_url = detail.get("latest_version")
    for version_number, url in versions.items():
        if url == latest_url:
            return version_number, url
    return max(versions.items(), key=lambda item: Version(item[0]))


def sync_script(script, client=None):
    """Pull any versions upstream has that this script does not.

    Returns the list of ScriptVersions created, which is empty when already in step.
    """
    if not (script.upstream_source and script.upstream_id):
        raise UpstreamError(f"{script} is not linked to an upstream instance.")
    _, imported, _ = import_script(
        script.upstream_id,
        source=script.upstream_source,
        link=True,
        all_versions=True,
        client=client,
    )
    return imported


def linked_scripts():
    return models.Script.objects.filter(
        sync_enabled=True,
        upstream_id__isnull=False,
        upstream_source__isnull=False,
    ).order_by("pk")
