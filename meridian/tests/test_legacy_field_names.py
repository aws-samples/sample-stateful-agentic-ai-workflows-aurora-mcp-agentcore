"""For one release, responses carry each renamed field under its old name too."""

from backend.legacy_field_names import LEGACY_NAMES, with_legacy_names
from backend.main import HealthResponse
from backend.routers.diagnostics import SessionReceiptResponse


def test_every_renamed_key_gains_its_old_name_through_nested_objects():
    document = {
        "snapshot_store": {"kind": "Aurora workflow_snapshots", "durable": True},
        "snapshot": {"snapshot_id": 7, "parent_snapshot_id": 6, "status": "committed"},
        "workflow": {"resumed_from_snapshot": 6},
        "journeys": [{"snapshot_store": "Aurora workflow_snapshots"}],
    }

    with_legacy_names(document)

    assert document["checkpoint_backend"] == document["snapshot_store"]
    assert document["checkpoint"]["checkpoint_id"] == 7
    assert document["checkpoint"]["parent_checkpoint_id"] == 6
    assert document["workflow"]["resumed_from_checkpoint"] == 6
    assert document["journeys"][0]["checkpoint_backend"] == "Aurora workflow_snapshots"


def test_a_key_already_present_under_its_old_name_is_left_alone():
    body = {"snapshot_store": "new", "checkpoint_backend": "kept"}

    assert with_legacy_names(body)["checkpoint_backend"] == "kept"


def test_values_without_renamed_keys_pass_through():
    assert with_legacy_names({"status": "healthy"}) == {"status": "healthy"}
    assert with_legacy_names([1, "two", None]) == [1, "two", None]
    assert with_legacy_names("text") == "text"


def test_the_map_names_only_old_spellings_of_snapshot_fields():
    assert all("snapshot" in new and "checkpoint" in old for new, old in LEGACY_NAMES.items())


def test_the_receipt_model_keeps_the_old_names_it_is_given():
    receipt = SessionReceiptResponse(
        traveler_id="trv_x", since="last 90 minutes", lines=[], durable_snapshots=True,
        snapshot_store="Aurora workflow_snapshots", snapshot_store_durable=True,
    )

    body = SessionReceiptResponse.model_validate(with_legacy_names(receipt.model_dump()))

    dumped = body.model_dump()
    assert dumped["durable_checkpoints"] is True
    assert dumped["checkpoint_backend"] == "Aurora workflow_snapshots"
    assert dumped["checkpoint_backend_durable"] is True


def test_the_health_model_keeps_the_old_names_it_is_given():
    health = HealthResponse(
        status="healthy", version="1.0.0", environment="test", bedrock_model_id="m",
        bedrock_model_label="M", embedding_model_id="e", snapshot_store="memory",
        snapshot_durable=False, snapshot_required=False, aurora_reachable=True,
        workflow_runtime_configured=True,
    )

    dumped = HealthResponse.model_validate(with_legacy_names(health.model_dump())).model_dump()

    assert dumped["checkpoint_backend"] == "memory"
    assert dumped["checkpoint_durable"] is False
    assert dumped["checkpoint_required"] is False
