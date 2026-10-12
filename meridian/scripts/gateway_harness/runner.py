"""Orchestrate one harness run: guard, create, probe, print, always tear down.

The order is fixed. The caller guard (account, region) runs with the STS client as the only client
that exists. Only after it passes are the other clients built, the tokens minted and the ledger
file written, and that file (with the run id) exists before the first create call. Teardown runs
in a ``finally`` block whatever the probes do.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from scripts.gateway_harness import guards, private_files, probes, verdicts
from scripts.gateway_harness.resources import (
    BINDING_POLICY,
    RUN_TAG,
    Clients,
    HarnessConfig,
    HarnessFailure,
    Ledger,
    TeardownIncomplete,
    ThrowawayGateway,
)

ACCOUNT_ID = verdicts.ACCOUNT_ID
BEARER_TEXT = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
COGNITO_KEYS = ("MERIDIAN_COGNITO_REGION", "MERIDIAN_COGNITO_USER_POOL_ID",
                "MERIDIAN_COGNITO_APP_CLIENT_ID")
EXIT_PASS, EXIT_FAIL, EXIT_LEFTOVERS, EXIT_REFUSED = 0, 1, 2, 3
CONFIRM_FLAG = "--i-understand-this-creates-aws-resources"
LIVE_COMMAND = (
    "AWS_PROFILE=claude-code venv/bin/python scripts/run_gateway_harness.py "
    f"--apply {CONFIRM_FLAG}"
)
TAG_PERMISSIONS = (
    "lambda:TagResource", "lambda:ListTags", "iam:TagRole", "iam:ListRoleTags",
    "bedrock-agentcore:TagResource", "bedrock-agentcore:ListTagsForResource",
)
RUN_FAILURES = (HarnessFailure, ClientError, BotoCoreError, *probes.TRANSPORT_FAILURES)
MAX_ERROR_TEXT = 500


def mask(text: str) -> str:
    """Replace token-shaped text, bearer text and 12-digit account ids; keep the layout."""
    text = verdicts.JWT_SHAPE.sub("<token>", text)
    return ACCOUNT_ID.sub("<acct>", BEARER_TEXT.sub("Bearer <token>", text))


def say(text: str) -> None:
    """Print with account ids, bearer text and tokens masked."""
    print(mask(text))


@dataclass
class Dependencies:
    """Everything the run reaches outside itself for, replaceable in tests."""

    session: Callable[[str], Any]
    mint: Callable[[str], str]
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    token_hex: Callable[[int], str] = field(default=secrets.token_hex)


def _binding_statement(template_path: Path) -> str:
    try:
        policies = json.loads(template_path.read_text())["policyEngines"][0]["policies"]
        return next(p["statement"] for p in policies if p["name"] == BINDING_POLICY)
    except (OSError, ValueError, KeyError, IndexError, StopIteration, TypeError) as exc:
        raise guards.HarnessRefusal(
            f"cannot read the {BINDING_POLICY} rule from {template_path.name} "
            f"({type(exc).__name__}); the harness tests the committed template's rule."
        ) from None


def load_config(env: Mapping[str, str | None], name: str, template_path: Path, *,
                run_id: str | None = None) -> HarnessConfig:
    """The harness settings from ``meridian/.env``-style values and the committed template.

    Raises:
        HarnessRefusal: The name is not a throwaway name, the cluster ARN or a Cognito setting is
            missing, or the template's deny rule cannot be read.
    """
    checked = guards.check_name(name)
    account, region = guards.deployment_target(env)
    missing = [key for key in COGNITO_KEYS if not (env.get(key) or "").strip()]
    if missing:
        raise guards.HarnessRefusal(
            f"{', '.join(missing)} not set; run scripts/sync_cognito_env.py --write first."
        )
    pool = (env["MERIDIAN_COGNITO_USER_POOL_ID"] or "").strip()
    cognito_region = (env["MERIDIAN_COGNITO_REGION"] or "").strip()
    settings: dict[str, Any] = {} if run_id is None else {"run_id": run_id}
    return HarnessConfig(
        name=checked, account=account, region=region,
        discovery_url=(f"https://cognito-idp.{cognito_region}.amazonaws.com/{pool}"
                       "/.well-known/openid-configuration"),
        allowed_client_id=(env["MERIDIAN_COGNITO_APP_CLIENT_ID"] or "").strip(),
        binding_template=_binding_statement(template_path), **settings,
    )


def dry_run(env: Mapping[str, str | None], template_path: Path,
            token_hex: Callable[[int], str] = secrets.token_hex) -> int:
    """Print the plan, the guard results and the permissions. Makes no AWS call."""
    try:
        config = load_config(env, guards.new_throwaway_name(token_hex), template_path)
    except guards.HarnessRefusal as exc:
        say(f"REFUSED: {exc}")
        return EXIT_REFUSED
    gateway = ThrowawayGateway(config, Clients(None, None, None, None), Ledger())
    say(f"DRY RUN. Nothing is created. Throwaway name: {config.name}")
    say(f"Account <acct>, region {config.region}; the real Gateway "
        f"'{guards.REAL_GATEWAY_NAME}' is never touched.")
    say(f"Cognito discovery URL: {config.discovery_url}")
    for index, line in enumerate(gateway.plan(), start=1):
        say(f"  {index}. {line}")
    say(f"Besides create and delete of those resources, the profile needs the tag permissions "
        f"the run tag '{RUN_TAG}' uses (teardown refuses to delete anything it cannot verify):")
    say("  " + ", ".join(TAG_PERMISSIONS))
    say(f"Live run (ASK FIRST; needs {CONFIRM_FLAG}): {LIVE_COMMAND}")
    return EXIT_PASS


def _refused(exc: Exception) -> int:
    say(f"REFUSED: {exc}")
    return EXIT_REFUSED


def _connect(env: Mapping[str, str | None], deps: Dependencies) -> Clients:
    """Build STS, check the caller, and only then build the other clients.

    Raises:
        HarnessRefusal: The caller cannot be identified, or is another account or region.
    """
    account, region = guards.deployment_target(env)
    session = deps.session(region)
    try:
        caller = session.client("sts").get_caller_identity()["Account"]
    except (ClientError, BotoCoreError) as exc:
        raise guards.HarnessRefusal(
            f"cannot identify the AWS caller ({mask(str(exc))[:MAX_ERROR_TEXT]}); sign in with "
            "the claude-code profile."
        ) from None
    guards.check_caller(caller, session.region_name, (account, region))
    return Clients(
        iam=session.client("iam"), lambda_=session.client("lambda"),
        control=session.client("bedrock-agentcore-control"), logs=session.client("logs"))


def _refuse_existing(control: Any, name: str) -> None:
    for page in control.get_paginator("list_gateways").paginate():
        if any(item.get("name") == name for item in page.get("items", [])):
            raise guards.HarnessRefusal(f"a gateway named '{name}' already exists.")


def _mint_tokens(deps: Dependencies) -> dict[str, str]:
    try:
        return {who: deps.mint(who) for who in ("jordan", "decoy")}
    except RuntimeError as exc:
        raise guards.HarnessRefusal(
            f"cannot mint the test tokens, so nothing was created: {exc}") from None


def _ledger_writer(path: Path) -> Callable[[dict[str, Any]], None]:
    def save(payload: dict[str, Any]) -> None:
        private_files.write_private(path, json.dumps(payload, indent=2))
    return save


def _save_events(events: list[dict[str, Any]], out_dir: Path) -> None:
    folder = out_dir / "recorded"
    private_files.private_dir(folder)
    for index, event in enumerate(events, start=1):
        private_files.write_private(folder / f"event_{index}.json", json.dumps(event, indent=2))
    say(f"Recorded {len(events)} real interceptor events (tokens replaced) in {folder}")


def _probe_and_report(gateway: ThrowawayGateway, deps: Dependencies, tokens: dict[str, str],
                      out_dir: Path) -> int:
    live = gateway.live
    if live.binding_policy_accepted:
        http = probes.McpHttp(live.gateway_url, transport=deps.transport)
        try:
            observations = probes.run_probes(gateway, http, tokens, sleep=deps.sleep)
        finally:
            http.close()
        _save_events(probes.recorded_events(gateway.aws.logs, live.interceptor_name,
                                            sleep=deps.sleep), out_dir)
    else:
        say(f"Policy rejected the template's deny rule: {live.binding_policy_reason}")
        observations = verdicts.Observations(binding_policy_accepted=False)
    rows = verdicts.derive_verdicts(observations)
    say(verdicts.format_table(rows))
    private_files.write_private(out_dir / "verdicts.json",
                                json.dumps([asdict(row) for row in rows], indent=2))
    return EXIT_PASS if verdicts.passed(rows) else EXIT_FAIL


def _teardown(gateway: ThrowawayGateway, code: int, *, from_file: bool = False) -> int:
    if not gateway.ledger.entries and not from_file:
        say("Nothing was created, so there is nothing to delete.")
        return code
    try:
        gateway.teardown(from_file=from_file)
    except TeardownIncomplete as exc:
        say(f"TEARDOWN INCOMPLETE: {exc}")
        return EXIT_LEFTOVERS
    except guards.HarnessRefusal as exc:
        return _refused(exc)
    except Exception as exc:  # teardown must report what is left, never end in a traceback
        _report_leftovers(gateway, exc)
        return EXIT_LEFTOVERS
    say(f"Deleted every resource named {gateway.config.name}.")
    return code


def _report_leftovers(gateway: ThrowawayGateway, exc: Exception) -> None:
    """Name every ledger entry and the run tag when teardown stopped on an unexpected error."""
    say(f"TEARDOWN INCOMPLETE: {type(exc).__name__}: {mask(str(exc))[:MAX_ERROR_TEXT]}")
    say(f"Treat everything below as possibly still existing. Run tag: "
        f"{RUN_TAG}={gateway.config.run_id}")
    for kind, identifier in gateway.ledger.entries:
        say(f"  {kind} {identifier}")
    say("Remove by hand, or re-run with --teardown <ledger.json> "
        f"{CONFIRM_FLAG} (it deletes only entries whose run tag matches).")


def _create_probe_teardown(config: HarnessConfig, clients: Clients, tokens: dict[str, str],
                           out_dir: Path, deps: Dependencies) -> int:
    ledger = Ledger(save=_ledger_writer(out_dir / "ledger.json"))
    gateway = ThrowawayGateway(config, clients, ledger, sleep=deps.sleep)
    code = EXIT_FAIL
    try:
        gateway.create()
        code = _probe_and_report(gateway, deps, tokens, out_dir)
    except guards.HarnessRefusal as exc:
        code = _refused(exc) if not ledger.entries else EXIT_FAIL
    except RUN_FAILURES as exc:
        say(f"FAILED: {mask(str(exc))[:MAX_ERROR_TEXT]}")
        say("RESULT: FAIL")
    finally:
        code = _teardown(gateway, code)
    return code


def run_live(env: Mapping[str, str | None], template_path: Path, base_dir: Path,
             deps: Dependencies) -> int:
    """Create the throwaway Gateway, probe it, print the table, and always delete it."""
    try:
        name = guards.new_throwaway_name(deps.token_hex)
        config = load_config(env, name, template_path, run_id=deps.token_hex(8))
        clients = _connect(env, deps)
        _refuse_existing(clients.control, name)
        tokens = _mint_tokens(deps)
    except guards.HarnessRefusal as exc:
        return _refused(exc)
    return _create_probe_teardown(config, clients, tokens, base_dir / name, deps)


def read_ledger(path: Path) -> tuple[str, list[tuple[str, str]]]:
    """The run id and entries a previous run saved.

    Raises:
        HarnessRefusal: The file is missing, unreadable, not the saved format, has no run id or
            has no entries. Nothing is deleted without a trustworthy ledger.
    """
    refusal = f"the ledger file {path} is {{}}; nothing was deleted."
    try:
        payload = json.loads(path.read_text())
    except OSError as exc:
        raise guards.HarnessRefusal(
            refusal.format(f"missing or unreadable ({type(exc).__name__})")) from None
    except ValueError:
        raise guards.HarnessRefusal(refusal.format("not valid JSON")) from None
    run_id = payload.get("run_id") if isinstance(payload, dict) else None
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(run_id, str) or not run_id.strip() or not isinstance(entries, list):
        raise guards.HarnessRefusal(refusal.format("not in the saved format (run_id, entries)"))
    if not entries:
        raise guards.HarnessRefusal(refusal.format("empty (lost or truncated)"))
    if not all(isinstance(e, list) and len(e) == 2 and all(isinstance(x, str) for x in e)
               for e in entries):
        raise guards.HarnessRefusal(refusal.format("malformed (entries are [kind, id] pairs)"))
    return run_id, [(kind, identifier) for kind, identifier in entries]


def teardown_from_ledger(env: Mapping[str, str | None], template_path: Path,
                         ledger_path: Path, deps: Dependencies) -> int:
    """Delete what a previous run left behind, from its saved ledger.

    Every refusal that needs no AWS call (folder name, settings, ledger file) comes before the
    STS call, and the other clients are built only after the caller guard passes.
    """
    try:
        config = load_config(env, ledger_path.parent.name, template_path)
        run_id, entries = read_ledger(ledger_path)
        config = replace(config, run_id=run_id)
        clients = _connect(env, deps)
    except guards.HarnessRefusal as exc:
        return _refused(exc)
    ledger = Ledger(list(entries), run_id=run_id)
    gateway = ThrowawayGateway(config, clients, ledger, sleep=deps.sleep)
    return _teardown(gateway, EXIT_PASS, from_file=True)
