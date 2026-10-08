"""Call the throwaway Gateway with real tokens and record what came back."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from botocore.exceptions import ClientError

from scripts.gateway_harness.resources import ACTION, ThrowawayGateway
from scripts.gateway_harness.verdicts import (
    DECOY,
    JORDAN,
    JWT_SHAPE,
    Observations,
    Outcome,
    classify,
)

RECORD_MARKER = "RECORDED_EVENT "
CONTROL_ATTEMPTS = 8
CONTROL_INTERVAL = 10.0
LOG_POLL_SECONDS = 5
# httpx.InvalidURL is not an HTTPError, so a bad Gateway URL needs naming here.
TRANSPORT_FAILURES = (httpx.HTTPError, httpx.InvalidURL)


@dataclass(frozen=True)
class Probe:
    """One tools/call: who calls, which interceptor mode, and what the caller sends."""

    name: str
    mode: str
    who: str
    arguments: dict[str, Any]


PROBES = (
    Probe("jordan_names_jordan", "pin", "jordan", {"travelerId": JORDAN, "note": "control"}),
    Probe("decoy_names_jordan", "pin", "decoy", {"travelerId": JORDAN, "note": "decoy"}),
    Probe("omitted_required", "pin", "decoy", {"note": "travelerId left out"}),
    Probe("bad_type", "bad_type", "decoy", {"travelerId": DECOY, "note": "rewritten to a number"}),
    Probe("drop_required", "drop_required", "decoy",
          {"travelerId": DECOY, "note": "travelerId removed"}),
    Probe("forced_refusal", "refuse", "decoy", {"travelerId": DECOY, "note": "refused"}),
    Probe("cedar_alone", "off", "decoy", {"travelerId": JORDAN, "note": "interceptor off"}),
)


def _json_body(response: httpx.Response) -> Any:
    text = response.text
    if "text/event-stream" in response.headers.get("content-type", ""):
        data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        text = data[0] if data else ""
    try:
        return json.loads(text)
    except ValueError:
        return text[:300]


class McpHttp:
    """A minimal MCP-over-HTTP caller with a bearer token."""

    def __init__(self, url: str, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30.0) -> None:
        self._url = url
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def _post(self, token: str, method: str, params: dict[str, Any] | None) -> httpx.Response:
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        return self._client.post(self._url, json=body, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"})

    def call_tool(self, token: str, arguments: dict[str, Any]) -> Outcome:
        """``tools/call`` of the echo tool; the token is sent and never stored.

        A transport failure becomes an ``http_error`` outcome that names only the exception type,
        so no URL or header text reaches the verdict table.
        """
        try:
            response = self._post(token, "tools/call", {"name": ACTION, "arguments": arguments})
        except TRANSPORT_FAILURES as exc:
            return Outcome("http_error", 0, f"transport failure: {type(exc).__name__}")
        return classify(response.status_code, _json_body(response))

    def list_tools(self, token: str) -> list[str]:
        """``initialize`` then ``tools/list``: the tool names, or an empty list on any failure.

        Both requests go through the interceptor unchanged, which is what the call proves.
        """
        try:
            self._post(token, "initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "meridian-harness", "version": "1"}})
            body = _json_body(self._post(token, "tools/list", None))
        except TRANSPORT_FAILURES:
            return []
        result = body.get("result") if isinstance(body, dict) else None
        tools = (result.get("tools") if isinstance(result, dict) else None) or []
        return [tool.get("name", "") for tool in tools if isinstance(tool, dict)]

    def close(self) -> None:
        """Release the connection pool."""
        self._client.close()


def run_probes(gateway: ThrowawayGateway, http: McpHttp, tokens: dict[str, str], *,
               sleep: Callable[[float], None] = time.sleep) -> Observations:
    """Run every probe, switching the interceptor mode between groups, then detach it.

    The first probe is retried while the Gateway, its roles and the policy engine settle. The
    detach comes last because it removes the interceptor the other probes need.
    """
    observations = Observations(binding_policy_accepted=gateway.live.binding_policy_accepted)
    current = "pin"
    for probe in PROBES:
        if probe.mode != current:
            gateway.set_interceptor_mode(probe.mode)
            current = probe.mode
        attempts = CONTROL_ATTEMPTS if probe is PROBES[0] else 1
        for attempt in range(attempts):
            outcome = http.call_tool(tokens[probe.who], probe.arguments)
            if outcome.kind == "ok" or attempt == attempts - 1:
                break
            sleep(CONTROL_INTERVAL)
        observations.outcomes[probe.name] = outcome
        if probe is PROBES[0]:
            observations.listed_tools = http.list_tools(tokens["jordan"])
    observations.interceptors_after_detach = gateway.detach_interceptor()
    return observations


def recorded_events(logs: Any, function_name: str, *, wanted: int = 3, attempts: int = 12,
                    sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """Events the interceptor logged with their tokens replaced, one per distinct request.

    Log delivery lags the call, so this polls until ``wanted`` distinct events are found.
    """
    found: dict[str, dict[str, Any]] = {}
    for _ in range(attempts):
        try:
            pages = logs.get_paginator("filter_log_events").paginate(
                logGroupName=f"/aws/lambda/{function_name}", filterPattern="RECORDED_EVENT")
            for page in pages:
                for entry in page.get("events", []):
                    _keep(found, entry["message"])
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        if len(found) >= wanted:
            break
        sleep(LOG_POLL_SECONDS)
    return list(found.values())


def _keep(found: dict[str, dict[str, Any]], message: str) -> None:
    match = re.search(re.escape(RECORD_MARKER) + r"(\{.*\})", message)
    if not match:
        return
    try:
        event = json.loads(JWT_SHAPE.sub("{{JWT}}", match.group(1)))
        body = event["mcp"]["gatewayRequest"]["body"]
        tool = (body.get("params") or {}).get("name", "")
        method = body.get("method")
    except (ValueError, KeyError, TypeError, AttributeError):
        return
    found.setdefault(f"{method}|{tool}", event)
