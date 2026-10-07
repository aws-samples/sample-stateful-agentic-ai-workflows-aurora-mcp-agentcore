#!/usr/bin/env python3
"""Answer the open Gateway questions on a separate throwaway Gateway.

Dry run (default): print the plan, the guard results and the permissions. No AWS call.
    venv/bin/python scripts/run_gateway_harness.py

Live run (ask the owner first): create ``meridian-throwaway-<id>`` in the deployment account,
probe it with the real jordan and decoy tokens (from scripts/cognito_tokens.py; never printed),
print the verdict table, and delete everything in a ``finally`` block. It needs both flags, so it
cannot run by accident:
    AWS_PROFILE=claude-code venv/bin/python scripts/run_gateway_harness.py \\
        --apply --i-understand-this-creates-aws-resources

Clean up after an interrupted run from its saved ledger (same confirmation). The ledger must be
inside .local/gateway-harness (the resolved path is printed). A missing, unreadable or empty ledger
is refused, and only the exact entries whose run tag matches are deleted:
    AWS_PROFILE=claude-code venv/bin/python scripts/run_gateway_harness.py \\
        --teardown .local/gateway-harness/<name>/ledger.json \\
        --i-understand-this-creates-aws-resources

Permissions the profile needs besides create and delete of the Lambda functions, IAM roles,
Gateway and policy engine: the run tag is written and read back before every delete, so it needs
lambda:TagResource and lambda:ListTags, iam:TagRole and iam:ListRoleTags, and AgentCore
bedrock-agentcore:TagResource and bedrock-agentcore:ListTagsForResource (gateways and policy
engines).

Flags must be spelled out in full; an abbreviation such as --app is a usage error, not --apply.

Exit codes: 0 every check passed, 1 a check or a create step failed or an unexpected error
occurred (a check that stayed unknown counts), 2 resources were left behind, 3 refused (also: any
command-line usage error, so a typo can never look like leftovers).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import boto3
from dotenv import dotenv_values

MERIDIAN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MERIDIAN_DIR))

from scripts.cognito_tokens import mint_access_token  # noqa: E402
from scripts.gateway_harness import runner  # noqa: E402

TEMPLATE = MERIDIAN_DIR / "meridian_agentcore" / "agentcore" / "agentcore.template.json"
OUTPUT = MERIDIAN_DIR / ".local" / "gateway-harness"
LEFTOVER_HINT = (
    "Teardown ran in the run's finally block; if TEARDOWN INCOMPLETE was printed above, "
    f"resources remain: re-run with --teardown {OUTPUT}/<name>/ledger.json "
    f"{runner.CONFIRM_FLAG}."
)


def make_minter(env: dict[str, Any], *,
                session_factory: Callable[..., Any] = boto3.Session) -> Callable[[str], str]:
    """A token minter that builds its Secrets Manager and Cognito clients only when called.

    The runner calls it after the caller guard has passed. Tokens stay in memory.
    """
    def mint(user_key: str) -> str:
        session = session_factory(region_name=env["MERIDIAN_COGNITO_REGION"])
        return mint_access_token(
            user_key, sm=session.client("secretsmanager"), idp=session.client("cognito-idp"),
            pool_id=env["MERIDIAN_COGNITO_USER_POOL_ID"],
            client_id=env["MERIDIAN_COGNITO_APP_CLIENT_ID"])
    return mint


class _Parser(argparse.ArgumentParser):
    """A parser whose usage errors exit 3 (refused), never 2 (resources left behind)."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(runner.EXIT_REFUSED, f"{self.prog}: error: {message}\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        description=(__doc__ or "").split("\n\n")[0], epilog=__doc__, allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="create, probe and delete (live)")
    mode.add_argument("--teardown", type=Path, metavar="LEDGER",
                      help="delete what an earlier run left behind (live)")
    parser.add_argument(runner.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply or --teardown; they create or delete AWS "
                             "resources")
    return parser


def _resolve_ledger(path: Path) -> Path | None:
    """The ledger's real path when it is inside the harness output folder, else ``None``."""
    resolved = path.resolve()
    return resolved if resolved.is_relative_to(OUTPUT.resolve()) else None


def _execute(args: argparse.Namespace) -> int:
    ledger = None
    if args.teardown is not None:
        ledger = _resolve_ledger(args.teardown)
        if ledger is None:
            runner.say(f"REFUSED: the ledger must be inside {OUTPUT}; {args.teardown.resolve()} "
                       "is not. Nothing was deleted.")
            return runner.EXIT_REFUSED
        runner.say(f"Ledger: {ledger}")
    env = {**dotenv_values(MERIDIAN_DIR / ".env"), **os.environ}
    if not (args.apply or ledger):
        return runner.dry_run(env, TEMPLATE)
    deps = runner.Dependencies(
        session=lambda region: boto3.Session(region_name=region), mint=make_minter(env))
    if args.apply:
        return runner.run_live(env, TEMPLATE, OUTPUT, deps)
    return runner.teardown_from_ledger(env, TEMPLATE, ledger, deps)


def main(argv: list[str] | None = None) -> int:
    """Run the dry run, the live run or a ledger teardown; see the module docstring."""
    parser = _parser()
    args = parser.parse_args(argv)
    live = args.apply or args.teardown is not None
    if args.confirmed and not live:
        parser.error(f"{runner.CONFIRM_FLAG} only goes with --apply or --teardown")
    if live and not args.confirmed:
        runner.say(f"REFUSED: --apply and --teardown change AWS and also need "
                   f"{runner.CONFIRM_FLAG}. Run without flags for the dry run.")
        return runner.EXIT_REFUSED
    try:
        return _execute(args)
    except KeyboardInterrupt:
        runner.say(f"INTERRUPTED. {LEFTOVER_HINT}")
        raise
    except Exception as exc:  # the last stop: one masked line, never a raw traceback
        text = runner.mask(str(exc))[:runner.MAX_ERROR_TEXT]
        runner.say(f"FAILED: {type(exc).__name__}: {text}")
        runner.say(LEFTOVER_HINT)
        return runner.EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
