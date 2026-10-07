"""Build access tokens that look like Cognito's, for tests that never verify a signature."""

import base64
import json
import time


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def access_token(traveler_id: str | None = "trv_meridian_demo", *, expires_in: float = 3600,
                 **claims) -> str:
    """An unsigned JWT-shaped access token for ``traveler_id`` that expires in ``expires_in`` s."""
    payload = {"sub": "sub-test", "token_use": "access", "client_id": "client-web",
               "exp": int(time.time() + expires_in), "iat": int(time.time()), **claims}
    if traveler_id is not None:
        payload["traveler_id"] = traveler_id
    header = _b64(json.dumps({"alg": "RS256", "kid": "test"}).encode())
    return f"{header}.{_b64(json.dumps(payload).encode())}.signature"
