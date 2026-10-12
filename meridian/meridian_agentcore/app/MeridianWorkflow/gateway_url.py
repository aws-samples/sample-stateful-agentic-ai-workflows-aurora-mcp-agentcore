"""Find the Gateway URL the CDK wired into this Runtime.

The CDK names the variable after the Gateway. The IAM Gateway's is
``AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL``; the token Gateway is a different resource (an existing
Gateway's authorizer type cannot change), so its variable is
``AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL``. A deployed stack holds one Gateway, so a Runtime has
exactly one of them; either is accepted and the token one is read first.
"""

from collections.abc import MutableMapping

JWT_URL_VARIABLE = "AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL"
IAM_URL_VARIABLE = "AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL"
BACKEND_VARIABLE = "AGENTCORE_GATEWAY_URL"


def gateway_url(environ) -> str:
    """The Gateway URL in ``environ``.

    Raises:
        RuntimeError: When neither variable holds a URL.
    """
    for name in (JWT_URL_VARIABLE, IAM_URL_VARIABLE):
        value = (environ.get(name) or "").strip()
        if value:
            return value
    raise RuntimeError(
        f"neither {JWT_URL_VARIABLE} nor {IAM_URL_VARIABLE} is set; the stack wires one of them "
        "into the Runtime when it deploys the Gateway")


def map_gateway_url(environ: MutableMapping[str, str]) -> None:
    """Set the variable the staged backend modules read, unless it is already set.

    Raises:
        RuntimeError: When it is not set and the Gateway's variable is missing too.
    """
    if not (environ.get(BACKEND_VARIABLE) or "").strip():
        environ[BACKEND_VARIABLE] = gateway_url(environ)
