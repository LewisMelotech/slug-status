"""Who may add a version to a script. One rule, shared by upload, the upload API and import."""

import inspect
import re

import pytest
from django.contrib.auth.models import AnonymousUser

from scripts import forms, models, viewsets


class Someone:
    """A user, as far as the rule can tell."""

    def __init__(self, pk, is_staff=False, is_superuser=False):
        self.pk, self.is_staff, self.is_superuser = pk, is_staff, is_superuser


OWNER = Someone(pk=1)
OTHER = Someone(pk=2)
STAFF = Someone(pk=3, is_staff=True)
SUPERUSER = Someone(pk=4, is_superuser=True)


@pytest.mark.parametrize("user", [None, AnonymousUser(), OWNER, OTHER, STAFF, SUPERUSER])
def test_a_script_nobody_owns_is_open_to_everyone(user):
    assert models.Script(owner_id=None).may_add_versions(user) is True


@pytest.mark.parametrize("user", [OWNER, STAFF, SUPERUSER])
def test_an_owned_script_is_open_to_its_owner_and_to_staff(user):
    assert models.Script(owner_id=1).may_add_versions(user) is True


@pytest.mark.parametrize("user", [None, AnonymousUser(), OTHER])
def test_an_owned_script_is_closed_to_everyone_else(user):
    assert models.Script(owner_id=1).may_add_versions(user) is False


def test_being_signed_in_is_not_being_staff():
    assert models.Script(owner_id=1).may_add_versions(Someone(pk=9)) is False


@pytest.mark.parametrize("path", [forms.ScriptForm.clean, viewsets.VersionViewSet.create])
def test_the_upload_paths_ask_the_shared_rule_and_do_not_compare_owners_themselves(path):
    # Import already goes through it. A path that compares script.owner to the user
    # directly would refuse staff the others let through, which is how they drifted apart.
    source = inspect.getsource(path)

    assert "may_add_versions" in source
    # Assigning an owner to a new script is fine; comparing one is what drifts.
    assert not re.search(r"\.owner\s*(!=|==)|\.owner\s+and\b", source)
