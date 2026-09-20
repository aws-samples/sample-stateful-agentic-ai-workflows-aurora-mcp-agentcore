"""Hosted MCP children must retain the parent's refreshable AWS provider."""

import os
from unittest.mock import patch

import botocore.session
from botocore.utils import ContainerMetadataFetcher
import pytest

from backend.mcp import concierge_mcp_client, mcp_client, memory_mcp_client


def postgres_params():
    return mcp_client.MCPPostgresClient(mcp_client.MCPConnectionConfig(
        cluster_arn="arn:aws:rds:us-east-1:000000000000:cluster:test",
        secret_arn="arn:aws:secretsmanager:us-east-1:000000000000:secret:test",
    ))._get_server_params()


@pytest.mark.parametrize("factory", [
    postgres_params, concierge_mcp_client._server_params, memory_mcp_client._server_params,
])
@pytest.mark.parametrize("provider", [
    {"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/test-role"},
    {"AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://127.0.0.1/test-role",
     "AWS_CONTAINER_AUTHORIZATION_TOKEN": "test-provider-token"},
])
def test_child_sdk_uses_container_role(factory, provider, monkeypatch, tmp_path):
    observed = []

    def metadata(_self, uri, headers=None):
        observed.append((uri, headers))
        return {"AccessKeyId": "test-key", "SecretAccessKey": "test-secret",
                "Token": "test-session", "Expiration": "2099-01-01T00:00:00Z"}

    monkeypatch.setattr(ContainerMetadataFetcher, "retrieve_full_uri", metadata)
    parent = {**provider, "MERIDIAN_API_TOKEN": "unrelated-test-token",
              "AWS_CONFIG_FILE": str(tmp_path / "no-config"),
              "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "no-credentials"),
              "AWS_EC2_METADATA_DISABLED": "true"}
    with patch.dict(os.environ, parent, clear=True):
        params = factory()
    assert "MERIDIAN_API_TOKEN" not in params.env
    with patch.dict(os.environ, params.env, clear=True):
        credentials = botocore.session.Session().get_credentials()
        assert credentials.method == "container-role"
        assert credentials.get_frozen_credentials().access_key == "test-key"
    assert observed[0][0].endswith("/test-role")
    if "AWS_CONTAINER_AUTHORIZATION_TOKEN" in provider:
        assert observed[0][1] == {"Authorization": "test-provider-token"}


@pytest.mark.parametrize("factory", [
    postgres_params, concierge_mcp_client._server_params, memory_mcp_client._server_params,
])
def test_child_keeps_file_based_provider_references(factory):
    provider = {"AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://127.0.0.1/test-role",
                "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/test/provider-token",
                "AWS_ROLE_ARN": "arn:aws:iam::000000000000:role/test",
                "AWS_ROLE_SESSION_NAME": "test-session",
                "AWS_WEB_IDENTITY_TOKEN_FILE": "/test/web-identity"}
    with patch.dict(os.environ, provider, clear=True):
        params = factory()
    assert provider.items() <= params.env.items()
