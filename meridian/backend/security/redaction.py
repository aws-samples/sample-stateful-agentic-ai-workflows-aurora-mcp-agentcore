"""Mask AWS account ids in the text the application shows, returns or keeps as error text.

An account id is a run of exactly 12 digits that no letter or digit touches. Hyphens,
colons, slashes, quotes and spaces bound it, and so does a percent-encoded colon or
slash before it (``%3A``, ``%3a``, ``%2F``, ``%2f``), as in the Runtime ARN of a
botocore request URL: ``arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A<acct>%3Aruntime``.
The last group of a canonical UUID is never masked. Each id becomes ``<acct>``.

Identity columns such as ``agent_audit_log.iam_identity`` keep the full ARN so an
investigation can still use it; the mask is applied when that text leaves the backend:
the System evidence Authorization tab, the journey document, the Phase 4 trace fields
and the RLS probe response. Error text is masked before it is stored or shown:
``agent_audit_log.turn_error``, tool setup errors, and retrieval and polish failure
notes. The Concierge Runtime repeats the pattern in ``tool_errors.py``.
"""

import re
from typing import Any

# An account id is a 12-digit run that no letter or digit touches, so hyphens, colons,
# slashes and quotes bound it (``fn-123456789012`` is masked). A percent-encoded colon or
# slash (``%3A``, ``%3a``, ``%2F``, ``%2f``) bounds it too, although it ends in a letter:
# botocore names the request URL in a transport error, and that URL holds the Runtime ARN
# percent-encoded. After the run, the ``%`` already bounds it. The final group of a
# canonical UUID is also 12 hex characters and can be all digits; the lookbehind
# keeps it, because span, thread and journey ids are UUIDs.
_BOUNDARY_BEFORE = r"(?:(?<![0-9A-Za-z])|(?<=%3[Aa]|%2[Ff]))"
_UUID_PREFIX = r"(?<![0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-)"
ACCOUNT_ID_PATTERN = re.compile(rf"{_BOUNDARY_BEFORE}{_UUID_PREFIX}\d{{12}}(?![0-9A-Za-z])")
PLACEHOLDER = "<acct>"


def mask_account_ids(text: str) -> str:
    """Replace each free-standing run of exactly 12 digits with ``<acct>``.

    Args:
        text: Any text, such as an IAM role ARN.

    Returns:
        The text with every 12-digit run masked unless a letter or digit touches
        it or it is the last group of a canonical UUID. A percent-encoded colon or
        slash before the run (``%3A``, ``%2F``, in either case) bounds it like the
        character it encodes. Masking a masked string changes nothing.
    """
    return ACCOUNT_ID_PATTERN.sub(PLACEHOLDER, text)


def mask_payload(value: Any) -> Any:
    """Return a deep copy of ``value`` with every string masked.

    Args:
        value: A string, or dicts, lists and tuples nested to any depth. Dict keys
            that are strings are masked too.

    Returns:
        A new structure. Non-string scalars come back unchanged.
    """
    if isinstance(value, str):
        return mask_account_ids(value)
    if isinstance(value, dict):
        return {mask_payload(key): mask_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_payload(item) for item in value)
    return value
