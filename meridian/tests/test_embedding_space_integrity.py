"""Query vectors must come from the model the corpus was embedded with.

The service used to walk a fallback chain and accept whichever model answered,
gated only on dimension count. Cohere v3 and Titan v2 both emit 1024 floats, so
either could stand in for v4 against a v4-embedded catalog. Equal dimensions do
not imply a shared vector space: the resulting cosine distances rank
confidently and mean nothing, and `model_id` still named v4, so the trace said
the search was something it was not.
"""

from __future__ import annotations

import pytest

from backend.db.embedding_service import EmbeddingService, EmbeddingUnavailable


def test_v4_truncate_matches_the_documented_contract():
    """Embed v4 accepts NONE, LEFT or RIGHT. "END" is a v3 value."""
    assert EmbeddingService._truncate_for("cohere.embed-v4:0") == "RIGHT"

    service = EmbeddingService.__new__(EmbeddingService)
    service.dimensions = 1024
    body = service._build_request("cohere.embed-v4:0", "a query", "search_query")
    assert body["truncate"] == "RIGHT"
    assert body["output_dimension"] == 1024


def test_v3_keeps_its_own_truncate_value():
    assert EmbeddingService._truncate_for("cohere.embed-english-v3") == "END"


def test_queries_and_the_seed_path_agree():
    """The corpus was embedded with RIGHT; queries must not send something else."""
    assert EmbeddingService._truncate_for("cohere.embed-v4:0") == "RIGHT"


def test_there_is_no_cross_model_fallback_chain():
    """The mechanism that allowed a v3 vector to answer a v4 query is gone."""
    assert not hasattr(EmbeddingService, "FALLBACK_MODELS")
    service = EmbeddingService.__new__(EmbeddingService)
    assert not hasattr(service, "model_candidates")


def test_failure_raises_rather_than_substituting_a_model():
    """Callers degrade to lexical retrieval; they never get a foreign vector."""
    service = EmbeddingService.__new__(EmbeddingService)
    service.corpus_model_id = "cohere.embed-v4:0"
    service.dimensions = 1024
    service.EMBEDDING_ATTEMPTS = 2
    service._last_model_used = None
    service.MAX_TEXT_LENGTH = 2048

    def always_fails(*_args, **_kwargs):
        raise RuntimeError("throttled")

    service._invoke_model = always_fails
    with pytest.raises(EmbeddingUnavailable):
        service.generate_text_embedding("a query")


def test_a_wrong_dimension_is_refused_immediately():
    """It cannot address the pgvector column, so retrying cannot help."""
    service = EmbeddingService.__new__(EmbeddingService)
    service.corpus_model_id = "cohere.embed-v4:0"
    service.dimensions = 1024
    service.EMBEDDING_ATTEMPTS = 2
    service._last_model_used = None
    service.MAX_TEXT_LENGTH = 2048
    service._invoke_model = lambda *_a, **_k: [0.0] * 512

    with pytest.raises(EmbeddingUnavailable) as caught:
        service.generate_text_embedding("a query")
    assert "512" in str(caught.value)


def test_evidence_reports_the_model_that_actually_ran():
    service = EmbeddingService.__new__(EmbeddingService)
    service.corpus_model_id = "cohere.embed-v4:0"
    service.dimensions = 4
    service.EMBEDDING_ATTEMPTS = 2
    service._last_model_used = None
    service.MAX_TEXT_LENGTH = 2048
    service._invoke_model = lambda *_a, **_k: [0.1, 0.2, 0.3, 0.4]

    service.generate_text_embedding("a query")
    assert service.last_model_used == "cohere.embed-v4:0"
