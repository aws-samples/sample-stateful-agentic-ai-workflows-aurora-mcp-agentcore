"""The teardown guides remove stacks in the order their definitions require."""

import re
from pathlib import Path

MERIDIAN = Path(__file__).resolve().parents[1]
README = (MERIDIAN.parent / "README.md").read_text(encoding="utf-8")
OPERATIONS = (MERIDIAN / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
WEB_APP = (MERIDIAN / "infra" / "lib" / "meridian-web-app.ts").read_text(encoding="utf-8")
SECTION = OPERATIONS.split("### Tear down the Express backend")[1].split("\n## ")[0]
CLEAN_UP = README.split("### Clean up")[1].split("\n## ")[0]


def destroys(text: str) -> list[str]:
    return re.findall(r"npx cdk destroy (\w+) (.*)", text)


def test_the_express_only_teardown_removes_the_site_then_the_service_then_the_roles():
    commands = destroys(SECTION.split("```bash")[1].split("```")[0])

    assert [name for name, _ in commands] == [
        "MeridianWeb", "MeridianWebExpress", "MeridianWebExpressRoles"]
    assert all("--exclusively" in rest and "-c backendHost=express" in rest
               for _, rest in commands)


def test_that_order_is_the_dependency_order_of_the_stack_definitions():
    assert "taskRole: roles.taskRole" in WEB_APP
    assert "infrastructureRole: roles.infrastructureRole" in WEB_APP
    assert "return express.endpoint" in WEB_APP
    assert "backendHost: origin" in WEB_APP


def test_the_express_teardown_verifies_the_service_and_load_balancer_are_gone():
    assert "aws ecs list-services --cluster default" in SECTION
    assert "aws elbv2 describe-load-balancers" in SECTION
    assert "not been rehearsed" in SECTION


def test_the_app_runner_teardown_is_not_the_express_teardown():
    assert "MeridianWebBackend MeridianWebRoles" not in SECTION
    assert "[Tear down the Express backend](#tear-down-the-express-backend)" in OPERATIONS


def test_the_clean_up_list_retires_agentcore_before_logins_and_audit_export_before_aurora():
    order = [CLEAN_UP.index(marker) for marker in (
        "tear-down-the-express-backend", "AGENTCORE_DEPLOY_RUNBOOK.md#tear-down",
        "roll-back-the-service-logins-and-sign-in", "OPERATIONS.md#audit-export",
        "OPERATIONS.md#provision-aurora", "CDK bootstrap")]

    assert order == sorted(order)
    assert "BucketName" in CLEAN_UP and "KMS key" in CLEAN_UP
