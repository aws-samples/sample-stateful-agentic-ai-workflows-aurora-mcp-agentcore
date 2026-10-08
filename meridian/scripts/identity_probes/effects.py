"""The real ports: HTTP to the backend, Runtime and Gateway calls as a seeded user, and Aurora.

Nothing here prints or stores a token. Tokens are minted once per user, kept in memory, and sent
only as ``Authorization: Bearer`` headers or bound for one Gateway call.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.agentcore.caller_credential import caller_token_scope
from backend.agentcore.cli_config import resolve_agentcore_config
from backend.agentcore.gateway import get_agentcore_gateway
from backend.agentcore.runtime import iter_sse, stream_chunks
from backend.agentcore.runtime_https import RuntimeHttpClient, invocation_url
from backend.db.rds_data_client import RDSDataClient
from scripts.agentcore_caller import require_token_safe_url
from scripts.cognito_tokens import mint_access_token
from scripts.identity_probes.probes import CONCIERGE, JORDAN_TRAVELER, WORKFLOW, Ports
from scripts.identity_probes.receipt import DECOY, JORDAN
from scripts.prove_backend_login import SWEEP_SQL

APP_ROLE = "meridian_app"
COUNT_SQL = "SELECT COUNT(*) AS n FROM traveler_preferences WHERE traveler_id = %s"
DENY_SQL = ("SELECT COUNT(*) AS n FROM traveler_access_audit "
            "WHERE requested_traveler_id = %s AND decision = 'deny'")
BOOKING_SQL = "SELECT booking_id FROM bookings WHERE traveler_id = %s"
BOOKING_EXISTS_SQL = "SELECT booking_id FROM bookings WHERE booking_id = %s"
ROLE_SQL = "SELECT current_user AS role, row_security_active('traveler_preferences') AS active"
PIN_SQL = ("SELECT set_config('row_security', 'on', true), "
           "set_config('app.current_traveler_id', %s, true)")
HTTP_REFUSAL = re.compile(r"^Gateway HTTP (4\d\d): (.*)$", re.DOTALL)
HTTP_SECONDS = 60


def utc_now() -> str:
    """The current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TokenCache:
    """Mints each seeded user's access token once and keeps it in memory."""

    def __init__(self, mint: Callable[[str], str] = mint_access_token) -> None:
        self._mint = mint
        self._tokens: dict[str, str] = {}

    def __call__(self, user: str) -> str:
        if user not in self._tokens:
            self._tokens[user] = self._mint(user)
        return self._tokens[user]

    def warm(self) -> None:
        """Mint both tokens now, so a missing user or secret fails before any probe runs."""
        for user in (JORDAN, DECOY):
            self(user)


class HttpPort:
    """Requests to the hosted backend with the user's bearer token."""

    def __init__(self, base_url: str, tokens: TokenCache, *,
                 transport: httpx.BaseTransport | None = None) -> None:
        require_token_safe_url(base_url, always=True)
        self._base = base_url.rstrip("/")
        self._tokens = tokens
        self._client = httpx.Client(timeout=HTTP_SECONDS, follow_redirects=False,
                                    trust_env=False, transport=transport)

    def __call__(self, user: str, method: str, path: str, body: dict | None):
        response = self._client.request(
            method, self._base + path, json=body,
            headers={"Authorization": f"Bearer {self._tokens(user)}"})
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text[:200]
        return response.status_code, payload


class RuntimePort:
    """Invocations of the two Runtimes over HTTPS with the user's bearer token."""

    def __init__(self, region: str, arns: Mapping[str, str], tokens: TokenCache, *,
                 client: RuntimeHttpClient | None = None) -> None:
        self._region, self._arns, self._tokens = region, arns, tokens
        self._client = client or RuntimeHttpClient()

    def __call__(self, user: str, runtime: str, payload: dict, limit: int) -> list[dict]:
        url = invocation_url(self._region, self._arns[runtime], "DEFAULT")
        response = self._client.invoke(
            url=url, token=self._tokens(user), session_id=f"idproof-{uuid.uuid4().hex}",
            payload=json.dumps(payload).encode("utf-8"))
        chunks = stream_chunks(response)
        events: list[dict] = []
        try:
            for event in iter_sse(chunks):
                events.append(event)
                if len(events) >= limit or event.get("type") in ("result", "error"):
                    break
        finally:
            chunks.close()
        return events


class GatewayPort:
    """Tool calls through the Gateway with the user's token bound for the one call."""

    def __init__(self, gateway: Any, tokens: TokenCache) -> None:
        self._gateway, self._tokens = gateway, tokens

    def __call__(self, user: str, tool: str, arguments: dict):
        with caller_token_scope(self._tokens(user)):
            try:
                return self._gateway.call_tool(tool, arguments)
            except RuntimeError as exc:
                match = HTTP_REFUSAL.match(str(exc))
                if not match:
                    raise
                return {"error": {"http_status": int(match.group(1)), "message": match.group(2)}}


def _count(rows: list[dict]) -> int:
    return int(rows[0]["n"]) if rows else 0


async def pinned_rows(client: RDSDataClient, traveler: str, query: str,
                      params: tuple) -> list[dict]:
    """Read with ``traveler`` pinned in one transaction that is always rolled back.

    The tables the proof reads force row-level security, so an unscoped read can match zero rows
    even when rows exist; pinning the traveler is what makes the answer mean something.
    """
    transaction = client.begin_transaction()
    try:
        await client.execute(PIN_SQL, (traveler,), transaction_id=transaction)
        return await client.execute(query, params, transaction_id=transaction)
    finally:
        client.rollback_transaction(transaction)


def _truthy(value: Any) -> bool:
    return str(value).lower() in ("true", "t", "1")


class AuroraPort:
    """Counts rows the way the application's role sees them, then rolls the transaction back."""

    def __init__(self, client: RDSDataClient) -> None:
        self._client = client

    def scoped_count(self, context_traveler: str, target_traveler: str) -> int:
        """Jordan's (or anyone's) rows as ``meridian_app`` sees them with a traveler pinned."""
        return asyncio.run(self._scoped_count(context_traveler, target_traveler))

    async def _scoped_count(self, context_traveler: str, target_traveler: str) -> int:
        transaction = self._client.begin_transaction()
        try:
            await self._client.execute(PIN_SQL, (context_traveler,), transaction_id=transaction)
            await self._client.execute(f"SET LOCAL ROLE {APP_ROLE}", transaction_id=transaction)
            seen = await self._client.execute(ROLE_SQL, transaction_id=transaction)
            if not seen or seen[0]["role"] != APP_ROLE or not _truthy(seen[0]["active"]):
                raise RuntimeError(
                    f"the probe is not running as {APP_ROLE} with row security on, so a zero "
                    "count would prove nothing")
            rows = await self._client.execute(
                COUNT_SQL, (target_traveler,), transaction_id=transaction)
            return _count(rows)
        finally:
            self._client.rollback_transaction(transaction)

    def baseline_count(self, target_traveler: str) -> int:
        """Rows that exist for the traveler, counted with that traveler pinned."""
        return _count(asyncio.run(
            pinned_rows(self._client, target_traveler, COUNT_SQL, (target_traveler,))))

    def deny_audit_count(self, traveler_id: str) -> int:
        """Deny rows in ``traveler_access_audit`` that name the traveler (not row-secured)."""
        return _count(asyncio.run(self._client.execute(DENY_SQL, (traveler_id,))))

    def booking_ids(self, traveler_id: str) -> set[str]:
        """The traveler's booking ids, read with the traveler pinned."""
        rows = asyncio.run(pinned_rows(self._client, traveler_id, BOOKING_SQL, (traveler_id,)))
        return {row["booking_id"] for row in rows}


class AuroraCleanup:
    """Removes what the positive controls created, by id."""

    def __init__(self, client: RDSDataClient) -> None:
        self._client = client

    def purge_thread(self, thread: str) -> None:
        """Purge a ``phase5-proof-`` journey or thread; any other name is refused."""
        from scripts import stop_and_resume_proof as recovery

        asyncio.run(recovery._purge_run(self._client, thread))

    def release_bookings(self, booking_ids: list[str]) -> int:
        """Release Jordan's bookings by id, with their lines and hold requests."""
        from scripts.release_demo_bookings import release

        return sum(asyncio.run(release(JORDAN_TRAVELER, False, False, booking_id))
                   for booking_id in booking_ids)

    def leftovers(self, prefix: str, booking_ids: list[str]) -> int:
        """Threads with the prefix plus listed bookings that still exist."""
        threads = {row["thread"] for row in asyncio.run(
            self._client.execute(SWEEP_SQL, (prefix + "%",) * 3))}
        existing = sum(
            len(asyncio.run(pinned_rows(
                self._client, JORDAN_TRAVELER, BOOKING_EXISTS_SQL, (booking_id,))))
            for booking_id in booking_ids)
        return len(threads) + existing


def build_rig(
    env: Mapping[str, str | None], region: str, base_url: str, *,
    tokens: TokenCache | None = None,
) -> tuple[Ports, AuroraCleanup]:
    """The real ports and cleanup for the deployment named by ``env``.

    Raises:
        RuntimeError: When a Runtime ARN is not configured, or a seeded user cannot sign in.
    """
    config = resolve_agentcore_config()
    arns = {WORKFLOW: config.workflow_runtime_arn, CONCIERGE: config.runtime_arn}
    missing = [name for name, arn in arns.items() if not arn]
    if missing:
        raise RuntimeError(f"no Runtime ARN is configured for: {', '.join(missing)}; run "
                           "scripts/sync_agentcore_env.py --write")
    cache = tokens or TokenCache()
    cache.warm()
    client = RDSDataClient(
        cluster_arn=env.get("AURORA_CLUSTER_ARN"), secret_arn=env.get("AURORA_SECRET_ARN"),
        database=env.get("AURORA_DATABASE") or None, region=region)
    ports = Ports(
        http=HttpPort(base_url, cache), runtime=RuntimePort(region, arns, cache),
        gateway=GatewayPort(get_agentcore_gateway(), cache), database=AuroraPort(client),
        clock=time.monotonic, now=utc_now)
    return ports, AuroraCleanup(client)
