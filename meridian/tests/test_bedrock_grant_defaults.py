"""The IAM grants name the same default Amazon Bedrock models the code calls.

The App Runner role (``infra/lib/bedrock-model-access.ts``) and the Runtime execution roles
(``meridian_agentcore/agentcore/cdk/lib/model-access.ts``) grant exactly the inventoried
models. Each repeats the code's defaults, so a default changed on one side only would be denied
live; these tests fail first.
"""

import re
from pathlib import Path

from backend.config import BedrockConfig
from backend.db.embedding_service import EmbeddingService

MERIDIAN = Path(__file__).resolve().parents[1]
APP_RUNNER_GRANTS = MERIDIAN / "infra" / "lib" / "bedrock-model-access.ts"
RUNTIME_GRANTS = MERIDIAN / "meridian_agentcore" / "agentcore" / "cdk" / "lib" / "model-access.ts"


def granted_default(path: Path, name: str) -> str:
    """The string value of one exported TypeScript constant."""
    found = re.findall(rf"^export const {name} = '([^']+)';$", path.read_text(), re.MULTILINE)
    assert len(found) == 1, f"{path.name} must export {name} exactly once"
    return found[0]


def test_the_app_runner_grant_defaults_are_the_models_the_backend_calls():
    assert granted_default(APP_RUNNER_GRANTS, "DEFAULT_BEDROCK_MODEL_ID") == (
        BedrockConfig.DEFAULT_MODEL_ID)
    assert granted_default(APP_RUNNER_GRANTS, "DEFAULT_EMBEDDING_MODEL_ID") == (
        EmbeddingService.PRIMARY_MODEL)
    assert granted_default(APP_RUNNER_GRANTS, "DEFAULT_RERANK_MODEL_ID") == (
        EmbeddingService.DEFAULT_RERANK_MODEL)


def test_the_workflow_grant_defaults_are_the_models_its_retrieval_calls():
    assert granted_default(RUNTIME_GRANTS, "DEFAULT_EMBEDDING_MODEL_ID") == (
        EmbeddingService.PRIMARY_MODEL)
    assert granted_default(RUNTIME_GRANTS, "DEFAULT_RERANK_MODEL_ID") == (
        EmbeddingService.DEFAULT_RERANK_MODEL)
