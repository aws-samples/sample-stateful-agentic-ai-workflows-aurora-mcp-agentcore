"""Who a command-line script calls AgentCore as.

In ``iam`` mode (the default) a script signs with the AWS credentials it already has, and
``caller_scope`` does nothing. In ``jwt`` mode (``MERIDIAN_AGENTCORE_AUTH=jwt``) the Runtimes and
the Gateway accept only a Cognito access token, so the script signs in as a seeded user
(``scripts/cognito_tokens.py``, password from Secrets Manager) and binds that token for the clients.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator, Optional
from urllib.parse import urlparse

from backend.agentcore.auth_mode import jwt_mode
from backend.agentcore.caller_credential import caller_token_scope
from scripts.cognito_tokens import mint_access_token

TRAVELER_USERS = {"trv_meridian_demo": "jordan", "trv_demo_decoy": "decoy"}
USER_TRAVELERS = {user: traveler for traveler, user in TRAVELER_USERS.items()}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def user_for_traveler(traveler_id: str) -> str:
    """The seeded Cognito user whose token proves ``traveler_id``."""
    return TRAVELER_USERS.get(traveler_id, "jordan")


def traveler_for_user(user_key: str) -> str:
    """The traveler a seeded Cognito user signs in as (the inverse of ``TRAVELER_USERS``)."""
    return USER_TRAVELERS[user_key]


def require_token_safe_url(url: str, *, always: bool = False) -> None:
    """Exit unless ``url`` may receive a credential: HTTPS, or plain HTTP on loopback.

    Applies in ``jwt`` mode, where a token is minted, and whenever ``always`` is set, for a
    script that sends another credential.
    """
    if not (always or jwt_mode()):
        return
    target = urlparse(url)
    if target.scheme == "https" or (target.scheme == "http" and target.hostname in LOOPBACK_HOSTS):
        return
    raise SystemExit(
        f"Refusing to send a credential to {target.scheme}://{target.hostname}: "
        "use an https URL, or http on localhost."
    )


def bearer_headers(
    user_key: str = "jordan", *, mint: Optional[Callable[[str], str]] = None
) -> dict[str, str]:
    """The ``Authorization`` header for calls to the backend as a seeded user (jwt mode only).

    In ``iam`` mode the backend needs no credential from a local script, so the header is empty
    and nobody is signed in.
    """
    if not jwt_mode():
        return {}
    return {"Authorization": "Bearer " + (mint or mint_access_token)(user_key)}


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
