"""The request headers the local Runtime harness sends, as the Runtime receives them."""

from typing import Callable, Dict, Optional

from scripts.agentcore_caller import bearer_headers, user_for_traveler


def runtime_headers(
    traveler_id: str, *, mint: Optional[Callable[[str], str]] = None
) -> Optional[Dict[str, str]]:
    """The ``Authorization`` header of the seeded user who proves ``traveler_id``.

    In ``jwt`` mode the token is minted into memory by ``scripts.cognito_tokens`` (``mint``
    replaces it in tests) and returned for the ``headers`` argument of ``workflow_turn``, which is
    where the Runtime entry reads the caller. In ``iam`` mode there is no caller token: None.
    """
    headers = bearer_headers(user_for_traveler(traveler_id), mint=mint)
    return headers or None
