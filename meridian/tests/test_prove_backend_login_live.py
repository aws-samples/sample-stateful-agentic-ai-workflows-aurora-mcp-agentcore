"""The proof's identity read against the live cluster (left unrun until the owner approves)."""

import os

import pytest

from scripts import prove_backend_login as proof

pytestmark = pytest.mark.database


def test_the_backend_secret_connects_as_the_login_without_bypassrls():
    secret = os.environ.get("AURORA_BACKEND_SECRET_ARN")
    if not secret:
        pytest.fail("AURORA_BACKEND_SECRET_ARN is not set: run "
                    "scripts/provision_service_logins.py --login backend --apply --write-env")

    who = proof._identity(secret, dict(os.environ))

    assert who == {"login": proof.BACKEND_LOGIN, "bypass_rls": False, "superuser": False,
                   "master_member": False}
