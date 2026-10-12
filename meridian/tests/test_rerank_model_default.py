"""The rerank default must name a model id Bedrock actually serves.

`us.cohere.rerank-v3-5:0` is not a real inference profile in this account -
`GetInferenceProfile` returns `ResourceNotFoundException` for it (confirmed
live). The bare id `cohere.rerank-v3-5:0` is the one Bedrock actually serves
in us-east-1 (`GetFoundationModel` -> `modelLifecycle.status: ACTIVE`,
`inferenceTypesSupported: ["ON_DEMAND"]`). A fresh clone with no RERANK_MODEL
override must resolve to the working id, not the broken one.
"""

from backend.db.embedding_service import EmbeddingService


def test_default_rerank_model_is_the_bare_id_bedrock_actually_serves():
    assert EmbeddingService.DEFAULT_RERANK_MODEL == "cohere.rerank-v3-5:0"
    assert not EmbeddingService.DEFAULT_RERANK_MODEL.startswith("us.")


def test_a_fresh_instance_with_no_override_resolves_to_the_working_id(monkeypatch):
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    service = EmbeddingService()
    assert service.rerank_model_id == "cohere.rerank-v3-5:0"
