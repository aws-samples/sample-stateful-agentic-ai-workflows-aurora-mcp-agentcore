"""Importing the identity modules must not drag in the rest of the AgentCore adapters.

The MeridianWorkflow Runtime bundles auth_mode, caller_credential and caller_claims with stdlib
only. A heavy ``backend/agentcore/__init__.py`` would import boto3 and the Gateway client first.
"""

import subprocess
import sys
from pathlib import Path

import pytest

import backend.agentcore as agentcore

ROOT = Path(__file__).resolve().parents[1]
LIGHT = ("auth_mode", "caller_credential", "caller_claims", "errors", "runtime_https")
PUBLIC = {
    "agentcore_project_dir": "backend.agentcore.cli_config",
    "deployed_state_path": "backend.agentcore.cli_config",
    "resolve_agentcore_config": "backend.agentcore.cli_config",
    "get_agentcore_gateway": "backend.agentcore.gateway",
    "get_agentcore_identity": "backend.agentcore.identity",
    "get_agentcore_runtime": "backend.agentcore.runtime",
}


def test_the_identity_modules_import_without_boto3_or_the_gateway():
    imports = ", ".join(f"backend.agentcore.{name}" for name in LIGHT)
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
        f"import {imports}\n"
        "heavy = [m for m in ('boto3', 'botocore', 'backend.agentcore.gateway',\n"
        "         'backend.agentcore.identity', 'backend.agentcore.runtime',\n"
        "         'backend.authorization') if m in sys.modules]\n"
        "assert not heavy, heavy\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          check=False, cwd=ROOT)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize(("name", "module"), sorted(PUBLIC.items()))
def test_every_public_name_still_resolves_to_its_module_attribute(name, module):
    exported = getattr(agentcore, name)
    assert exported.__module__ == module
    assert name in agentcore.__all__


def test_from_import_of_a_public_name_still_works():
    from backend.agentcore import get_agentcore_gateway, resolve_agentcore_config

    assert callable(get_agentcore_gateway) and callable(resolve_agentcore_config)


def test_an_unknown_name_is_an_attribute_error_not_an_import_error():
    with pytest.raises(AttributeError, match="no_such_name"):
        agentcore.no_such_name  # noqa: B018 - the access itself is the behavior under test


def test_the_all_list_is_exactly_the_public_names():
    assert sorted(agentcore.__all__) == sorted(PUBLIC)


def test_dir_lists_the_lazy_public_names_on_a_cold_import():
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
        "import backend.agentcore as pkg\n"
        "assert 'get_agentcore_gateway' in dir(pkg), dir(pkg)\n"
        "assert 'backend.agentcore.gateway' not in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                          check=False, cwd=ROOT)
    assert done.returncode == 0, done.stderr
