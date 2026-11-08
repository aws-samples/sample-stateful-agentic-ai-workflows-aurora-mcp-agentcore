"""The editor launch uses the service login without changing administrator configuration."""

import pytest

from scripts.run_local import backend_environment


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [True, False])
async def test_browser_ready_notice_requires_a_listening_server(monkeypatch, capsys, started):
    import uvicorn
    from scripts.run_local import LocalServer

    async def startup(server, sockets=None):
        assert not capsys.readouterr().out
        server.started = started

    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    server = LocalServer(uvicorn.Config("backend.main:app"))
    await server.startup()
    output = capsys.readouterr().out
    assert ("Meridian browser: http://localhost:5173/showcase" in output) is started


def test_local_launch_selects_the_backend_secret_and_preserves_authentication():
    source = {
        "AURORA_CLUSTER_ARN": "cluster",
        "AURORA_SECRET_ARN": "administrator",
        "AURORA_BACKEND_SECRET_ARN": "backend-service",
        "MERIDIAN_AGENTCORE_AUTH": "jwt",
    }
    result = backend_environment(source)
    assert result["AURORA_SECRET_ARN"] == "backend-service"
    assert result["MERIDIAN_AGENTCORE_AUTH"] == "jwt"
    assert "MERIDIAN_ALLOW_INSECURE_LOCALHOST" not in result
    assert source["AURORA_SECRET_ARN"] == "administrator"


@pytest.mark.parametrize("missing", ["AURORA_CLUSTER_ARN", "AURORA_BACKEND_SECRET_ARN"])
def test_local_launch_refuses_missing_service_configuration(missing):
    source = {
        "AURORA_CLUSTER_ARN": "cluster",
        "AURORA_SECRET_ARN": "administrator",
        "AURORA_BACKEND_SECRET_ARN": "backend-service",
    }
    del source[missing]
    with pytest.raises(ValueError, match=missing):
        backend_environment(source)
