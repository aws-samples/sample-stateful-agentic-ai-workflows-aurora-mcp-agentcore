"""Who a command-line script calls AgentCore as.

In ``iam`` mode (the default) a script signs with the AWS credentials it already has, and
``caller_scope`` does nothing. In ``jwt`` mode (``MERIDIAN_AGENTCORE_AUTH=jwt``) the Runtimes and
the Gateway accept only a Cognito access token, so the script signs in as a seeded user
(``scripts/cognito_tokens.py``, password from Secrets Manager) and binds that token for the clients.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from backend.agentcore.auth_mode import jwt_mode
from backend.agentcore.caller_credential import caller_token_scope
from scripts.cognito_tokens import mint_access_token

TRAVELER_USERS = {"trv_meridian_demo": "jordan", "trv_demo_decoy": "decoy"}


def user_for_traveler(traveler_id: str) -> str:
    """The seeded Cognito user whose token proves ``traveler_id``."""
    return TRAVELER_USERS.get(traveler_id, "jordan")


@contextmanager
def caller_scope(
    user_key: str = "jordan", *, mint: Optional[Callable[[str], str]] = None
) -> Iterator[None]:
    """Bind the seeded user's access token for the block, in ``jwt`` mode only."""
    if not jwt_mode():
        yield
        return
    with caller_token_scope((mint or mint_access_token)(user_key)):
        yield
