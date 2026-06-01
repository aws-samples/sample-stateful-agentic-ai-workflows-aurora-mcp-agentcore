"""
Embedding Service for Meridian.

Generates text embeddings via Amazon Bedrock (Cohere Embed v4 by default).

AWS docs:
  - Bedrock embeddings overview:
    https://docs.aws.amazon.com/bedrock/latest/userguide/embeddings.html
  - Cohere Embed v4 model parameters (1024-d output for Aurora pgvector):
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
  - InvokeModel API (bedrock-runtime):
    https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModel.html
  - Aurora PostgreSQL pgvector extension:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector
"""

import json
import os
from typing import Any, Dict, List, Optional

import boto3


class EmbeddingService:
    """Bedrock embeddings — Cohere Embed v4 by default (1024d for Aurora pgvector)."""

    PRIMARY_MODEL = "cohere.embed-v4:0"
    FALLBACK_MODELS = (
        "cohere.embed-english-v3",
        "amazon.titan-embed-text-v2:0",
    )
    DEFAULT_DIMENSIONS = 1024
    MAX_TEXT_LENGTH = 2048
    # Cohere Rerank 3.5 is not directly invokable in us-east-1 with the bare
    # model ID — Bedrock requires the US cross-region inference profile, which
    # routes traffic to whichever Cohere region has capacity. The plain ID
    # ("cohere.rerank-v3-5:0") only resolves in us-west-2 / a few other
    # regions, so calling it from us-east-1 returns AccessDeniedException
    # / ValidationException ("model not available"), and we silently fall
    # back to semantic-only ranking on every Phase 3 turn.
    # Override via RERANK_MODEL env var if running in a region where the
    # bare model ID works.
    # Docs: https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html
    DEFAULT_RERANK_MODEL = "us.cohere.rerank-v3-5:0"

    def __init__(self, region: Optional[str] = None, dimensions: Optional[int] = None):
        self.region = region or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self.dimensions = dimensions or int(os.getenv("EMBEDDING_DIMENSION", self.DEFAULT_DIMENSIONS))
        self.rerank_model_id = os.getenv("RERANK_MODEL", self.DEFAULT_RERANK_MODEL)
        self._bedrock_client = None
        configured = os.getenv("EMBEDDING_MODEL", self.PRIMARY_MODEL)
        self.model_candidates: List[str] = [configured]
        for model_id in (self.PRIMARY_MODEL, *self.FALLBACK_MODELS):
            if model_id not in self.model_candidates:
                self.model_candidates.append(model_id)

    @property
    def bedrock_client(self):
        if self._bedrock_client is None:
            self._bedrock_client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._bedrock_client

    @property
    def model_id(self) -> str:
        return self.model_candidates[0]

    def _build_request(self, model_id: str, text: str, input_type: str) -> dict:
        if "titan" in model_id:
            return {"inputText": text}
        body = {
            "texts": [text],
            "input_type": input_type,
            "truncate": "END",
        }
        if "embed-v4" in model_id or "embed-v4:" in model_id:
            body["embedding_types"] = ["float"]
            body["output_dimension"] = self.dimensions
        return body

    @staticmethod
    def _parse_response(model_id: str, response_body: dict) -> List[float]:
        if "titan" in model_id:
            return response_body["embedding"]
        embeddings = response_body.get("embeddings")
        if isinstance(embeddings, dict):
            for key in ("float", "int8"):
                if key in embeddings and embeddings[key]:
                    return embeddings[key][0]
        if isinstance(embeddings, list) and embeddings:
            first = embeddings[0]
            if isinstance(first, list):
                return first
        raise ValueError(f"Unexpected embedding response shape for {model_id}")

    def _invoke_model(self, model_id: str, text: str, input_type: str) -> List[float]:
        request_body = self._build_request(model_id, text, input_type)
        response = self.bedrock_client.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )
        response_body = json.loads(response["body"].read())
        return self._parse_response(model_id, response_body)

    def generate_text_embedding(self, text: str, input_type: str = "search_document") -> List[float]:
        """Embed text with Cohere Embed v4 (1024-dim) via Bedrock.

        The query arm of hybrid search and the write path for semantic recall
        both call this. Tries the configured model then the fallback chain,
        and rejects any result whose dimension count doesn't match the
        pgvector column. ``input_type`` is "search_query" for queries,
        "search_document" for stored content (Cohere asymmetric embeddings).
        """
        if len(text) > self.MAX_TEXT_LENGTH:
            text = text[: self.MAX_TEXT_LENGTH]

        last_error: Optional[Exception] = None
        for model_id in self.model_candidates:
            try:
                embedding = self._invoke_model(model_id, text, input_type)
                if len(embedding) != self.dimensions:
                    last_error = ValueError(
                        f"{model_id} returned {len(embedding)} dimensions, expected {self.dimensions}"
                    )
                    continue
                return embedding
            except Exception as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        raise RuntimeError("No embedding models configured")

    def rerank_documents(
        self,
        query: str,
        documents: List[str],
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """
        Rerank candidate documents using Cohere Rerank on Bedrock.

        Returns a list of dicts containing:
            {"index": int, "score": float}
        """
        if not documents:
            return []

        # Cohere Rerank on Bedrock expects `documents` as an array of plain
        # strings. Sending objects ({"text": ...}) triggers a ValidationException:
        #   "#/documents/0: expected type: String, found: JSONObject".
        request_body = {
            "api_version": 2,
            "query": query,
            "documents": [d[: self.MAX_TEXT_LENGTH] for d in documents],
            "top_n": max(1, min(top_n, len(documents))),
        }
        response = self.bedrock_client.invoke_model(
            modelId=self.rerank_model_id,
            body=json.dumps(request_body),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(response["body"].read())
        raw_results = payload.get("results") or payload.get("rerank_results") or []

        ranked: List[Dict[str, Any]] = []
        for item in raw_results:
            idx = item.get("index")
            score = item.get("relevance_score", item.get("score", 0.0))
            if isinstance(idx, int):
                ranked.append({"index": idx, "score": float(score)})
        return ranked


_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
