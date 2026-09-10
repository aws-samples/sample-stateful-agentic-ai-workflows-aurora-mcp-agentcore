#!/usr/bin/env python3
"""
Pre-session smoke test for the AgentCore platform behind Phase 4 (Production).

Run this before the talk to confirm the deployed Runtime, Gateway, and Memory
still exist and are healthy — WITHOUT redeploying (a redeploy mints new ARNs
and a fresh Memory, which would drop the seeded Tokyo conversation history that
Phase 4 recall depends on).

It resolves config exactly the way the app does at runtime
(``resolve_agentcore_config()``: AGENTCORE_* env vars first, then
``meridian_agentcore/agentcore/.cli/deployed-state.json``), then issues
read-only control-plane ``Get*`` calls on each resource. Nothing is mutated.

  Runtime   bedrock-agentcore-control:GetAgentRuntime   (status)
  Gateway   bedrock-agentcore-control:GetGateway        (status)
  Memory    bedrock-agentcore-control:GetMemory         (status)
  Identity  sts:GetCallerIdentity                       (caller ARN + account)

Exit code is 0 when Runtime + Gateway + Memory are all reachable and READY,
non-zero otherwise — so you can wire it into a one-line pre-flight check.

Usage:
    cd meridian
    python scripts/verify_agentcore.py

AWS docs:
  - GetAgentRuntime:
    https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_GetAgentRuntime.html
  - GetGateway:
    https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_GetGateway.html
  - GetMemory:
    https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_GetMemory.html
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# Make ``backend`` importable when run as a plain script (matches sibling
# scripts like sync_agentcore_env.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agentcore.cli_config import resolve_agentcore_config  # noqa: E402

load_dotenv()
console = Console()

# A control-plane resource is "good" once it reaches one of these terminal,
# usable states. Anything else (CREATING, UPDATING, FAILED, DELETING) is worth
# surfacing before you walk on stage.
READY_STATES = {"READY", "ACTIVE", "AVAILABLE"}


def _runtime_id_from_arn(runtime_arn: str) -> str:
    """``arn:aws:bedrock-agentcore:...:runtime/<id>`` -> ``<id>``."""
    return runtime_arn.split("/")[-1]


def _gateway_id_from_url(gateway_url: str) -> str:
    """``https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp``
    -> ``<id>`` (the first label of the host)."""
    host = urlparse(gateway_url).hostname or ""
    return host.split(".")[0]


def _status_ok(status: str | None) -> bool:
    return bool(status) and status.upper() in READY_STATES


def check_policy_engine(control, gateway_id: str) -> tuple[bool, str, str]:
    """Return (ok, identifier, status) for the gateway's policy engine association."""
    gateway = control.get_gateway(gatewayIdentifier=gateway_id)
    config = gateway.get("policyEngineConfiguration") or {}
    engine_arn = config.get("arn") or config.get("policyEngineArn") or ""
    if not engine_arn:
        return False, "no policy engine on the gateway", "MISSING"
    engine_id = engine_arn.rsplit("/", 1)[-1]
    engine = control.get_policy_engine(policyEngineId=engine_id)
    mode = config.get("mode", "UNKNOWN")
    ok = _status_ok(engine.get("status")) and mode == "ENFORCE"
    return ok, engine_id, f"{engine.get('status', 'UNKNOWN')} · {mode}"


def check_gateway_tools(cfg) -> tuple[bool, str, str]:
    """List the gateway's MCP tools with a SigV4-signed request from this laptop."""
    from backend.agentcore.gateway import AgentCoreGatewayAdapter

    tools, _ = AgentCoreGatewayAdapter(gateway_url=cfg.gateway_url, region=cfg.region).list_tools()
    names = sorted(tool["name"] for tool in tools)
    expected = {
        "SemanticTripSearchLambda___semantic_trip_search",
        "MeridianHolds___get_package_details",
        "MeridianHolds___create_courtesy_hold",
    }
    ok = expected.issubset(names)
    return ok, ", ".join(names) or "none", f"{len(names)} tools"


def check_observability(control, runtime_id: str) -> tuple[bool, str, str]:
    """ADOT spans reach CloudWatch only when the runtime carries the observability flag."""
    runtime = control.get_agent_runtime(agentRuntimeId=runtime_id)
    env = runtime.get("environmentVariables") or {}
    enabled = env.get("AGENT_OBSERVABILITY_ENABLED") == "true"
    log_group = f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT"
    return enabled, log_group, "READY" if enabled else "OFF"


def governance_rows(control, cfg, table) -> bool:
    """Add the policy, tools and observability rows; return True when all are good."""
    checks = []
    if cfg.gateway_url:
        gateway_id = _gateway_id_from_url(cfg.gateway_url)
        checks.append(("Policy engine", lambda: check_policy_engine(control, gateway_id)))
        checks.append(("Gateway tools", lambda: check_gateway_tools(cfg)))
    if cfg.runtime_arn:
        runtime_id = _runtime_id_from_arn(cfg.runtime_arn)
        checks.append(("Observability", lambda: check_observability(control, runtime_id)))
    all_ok = True
    for label, check in checks:
        try:
            ok, identifier, status = check()
        except (ClientError, BotoCoreError, RuntimeError) as exc:
            ok, identifier, status = False, "unreachable", f"✗ {str(exc)[:60]}"
        all_ok &= ok
        mark = "[green]✓ {}[/green]" if ok else "[yellow]! {}[/yellow]"
        table.add_row(label, identifier, mark.format(status))
    return all_ok


def main() -> int:
    cfg = resolve_agentcore_config()

    console.print()
    console.rule("[bold]AgentCore pre-session check[/bold]")
    console.print(
        f"[dim]region[/dim] {cfg.region}   "
        f"[dim]config sources[/dim] {', '.join(cfg.sources) or 'none'}\n"
    )

    # --- Identity / credentials first: nothing else works without creds. ----
    try:
        ident = boto3.client("sts").get_caller_identity()
        console.print(
            f"[green]✓[/green] Credentials  account [bold]{ident.get('Account')}[/bold]\n"
            f"             {ident.get('Arn', 'unknown')}\n"
        )
    except (NoCredentialsError, ClientError, BotoCoreError) as exc:
        console.print(
            "[red]✗ No usable AWS credentials.[/red] Refresh them (e.g. ada/isengard "
            f"for the account that owns the demo), then re-run.\n   {exc}\n"
        )
        return 2

    control = boto3.client("bedrock-agentcore-control", region_name=cfg.region)

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Component")
    table.add_column("Identifier", overflow="fold")
    table.add_column("Status")

    all_ok = True

    # --- Runtime --------------------------------------------------------------
    if not cfg.runtime_arn:
        table.add_row("Runtime", "[red]not configured[/red]", "[red]MISSING[/red]")
        all_ok = False
    else:
        runtime_id = _runtime_id_from_arn(cfg.runtime_arn)
        try:
            resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
            status = resp.get("status")
            ok = _status_ok(status)
            all_ok &= ok
            mark = "[green]✓ {}[/green]" if ok else "[yellow]! {}[/yellow]"
            table.add_row("Runtime", runtime_id, mark.format(status or "UNKNOWN"))
        except ClientError as exc:
            all_ok = False
            code = exc.response.get("Error", {}).get("Code", "Error")
            table.add_row("Runtime", runtime_id, f"[red]✗ {code}[/red]")

    # --- Gateway --------------------------------------------------------------
    if not cfg.gateway_url:
        table.add_row("Gateway", "[red]not configured[/red]", "[red]MISSING[/red]")
        all_ok = False
    else:
        gateway_id = _gateway_id_from_url(cfg.gateway_url)
        try:
            resp = control.get_gateway(gatewayIdentifier=gateway_id)
            status = resp.get("status")
            ok = _status_ok(status)
            all_ok &= ok
            mark = "[green]✓ {}[/green]" if ok else "[yellow]! {}[/yellow]"
            table.add_row("Gateway", gateway_id, mark.format(status or "UNKNOWN"))
        except ClientError as exc:
            all_ok = False
            code = exc.response.get("Error", {}).get("Code", "Error")
            table.add_row("Gateway", gateway_id, f"[red]✗ {code}[/red]")

    # --- Memory ---------------------------------------------------------------
    if not cfg.memory_id:
        table.add_row("Memory", "[red]not configured[/red]", "[red]MISSING[/red]")
        all_ok = False
    else:
        try:
            resp = control.get_memory(memoryId=cfg.memory_id)
            status = (resp.get("memory") or {}).get("status")
            ok = _status_ok(status)
            all_ok &= ok
            mark = "[green]✓ {}[/green]" if ok else "[yellow]! {}[/yellow]"
            table.add_row("Memory", cfg.memory_id, mark.format(status or "UNKNOWN"))
        except ClientError as exc:
            all_ok = False
            code = exc.response.get("Error", {}).get("Code", "Error")
            table.add_row("Memory", cfg.memory_id, f"[red]✗ {code}[/red]")

    # --- Identity (soft): degrades to IAM principal, never blocks Phase 4. ----
    identity_note = (
        cfg.workload_identity
        if cfg.workload_identity
        else "(none — Phase 4 uses the IAM principal above)"
    )
    table.add_row("Identity", identity_note, "[green]✓ ok[/green]")

    all_ok &= governance_rows(control, cfg, table)

    console.print(table)
    console.print()

    if all_ok:
        console.print(
            "[bold green]All set.[/bold green] Runtime, Gateway, Memory, policy engine, "
            "gateway tools and observability are all live. No redeploy needed.\n"
        )
        return 0

    console.print(
        "[bold yellow]One or more components are not READY.[/bold yellow]\n"
        "If a resource is [red]✗ ResourceNotFound[/red], it was likely swept from the "
        "account and you'll need to redeploy:\n"
        "  [dim]cd meridian/meridian_agentcore && agentcore deploy -y[/dim]\n"
        "  [dim]python scripts/sync_agentcore_env.py --write[/dim]\n"
        "(then re-seed Memory so Phase 4 recall has history). If it's [yellow]CREATING/"
        "UPDATING[/yellow], just wait and re-run.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
