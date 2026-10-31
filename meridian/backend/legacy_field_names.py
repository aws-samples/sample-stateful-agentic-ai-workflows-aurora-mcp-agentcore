"""Response fields under their names from before the snapshot rename, for one release.

The backend image and the static site publish separately, so for one release each side also
speaks the other's field names: the backend sends both, and the frontend accepts either
(``frontend/src/api/legacyFieldNames.ts``). The commit that adds migration 025 removes this
module and its call sites.
"""

from typing import Any

# The name each response field had before the rename, keyed by its name now.
LEGACY_NAMES = {
    "snapshot_store": "checkpoint_backend",
    "snapshot_durable": "checkpoint_durable",
    "snapshot_required": "checkpoint_required",
    "snapshot": "checkpoint",
    "snapshot_id": "checkpoint_id",
    "parent_snapshot_id": "parent_checkpoint_id",
    "resumed_from_snapshot": "resumed_from_checkpoint",
    "durable_snapshots": "durable_checkpoints",
    "snapshot_store_durable": "checkpoint_backend_durable",
}


def with_legacy_names(value: Any) -> Any:
    """Add each renamed key's old name beside it, in place, through nested dicts and lists.

    Args:
        value: A JSON-shaped response body.

    Returns:
        The same object, with every renamed key also present under its old name. A key that
        already exists under the old name is left as it is.
    """
    if isinstance(value, dict):
        for item in list(value.values()):
            with_legacy_names(item)
        for name, old in LEGACY_NAMES.items():
            if name in value and old not in value:
                value[old] = value[name]
    elif isinstance(value, list):
        for item in value:
            with_legacy_names(item)
    return value
