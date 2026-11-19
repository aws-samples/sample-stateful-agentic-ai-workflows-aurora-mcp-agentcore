"""The Runtime V1 check reads each fact from the place that holds it."""

from pathlib import Path

from botocore.session import get_session

RUNBOOK = (Path(__file__).resolve().parents[1] / "docs" / "AGENTCORE_DEPLOY_RUNBOOK.md").read_text(
    encoding="utf-8")
STEP = RUNBOOK.split("### Deploy order for the typed recovery intent")[1].split("\n## ")[0]


def get_agent_runtime_members() -> set[str]:
    model = get_session().get_service_model("bedrock-agentcore-control")
    return set(model.operation_model("GetAgentRuntime").output_shape.members)


def test_the_pinned_botocore_returns_status_and_a_revision_but_no_platform_version():
    members = get_agent_runtime_members()

    assert {"status", "agentRuntimeVersion"} <= members
    assert "platformVersion" not in members, (
        "botocore now returns platformVersion: update the runbook step that says it does not")


def test_the_guide_does_not_wait_for_a_platform_version_the_call_does_not_return():
    assert "`get-agent-runtime` does not\n   report the platform version" in STEP
    assert "must report the new code and platform version" not in RUNBOOK


def test_the_guide_reads_readiness_and_revision_from_the_service():
    assert "aws bedrock-agentcore-control get-agent-runtime" in STEP
    assert "status: status, revision: agentRuntimeVersion" in STEP
    assert "`READY`" in STEP


def test_the_guide_reads_the_platform_version_from_the_deployed_template_for_both_runtimes():
    assert "aws cloudformation get-template" in STEP
    assert "AWS::BedrockAgentCore::Runtime" in STEP
    assert "Properties.PlatformVersion" in STEP
    assert '"PlatformVersion": "V1"' in STEP
    assert "Both Runtime resources" in STEP
    assert "A revision number\n   alone does not say which platform version runs" in STEP
