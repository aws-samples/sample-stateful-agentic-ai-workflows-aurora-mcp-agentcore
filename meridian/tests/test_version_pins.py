"""One version set everywhere: CI, the container, the Lambdas, the Runtimes and the locks.

`.python-version` and `.nvmrc` hold the Python and Node versions. Every other place that names a
Python version must agree with `.python-version`, and CI must take its versions from these files,
pin every action to a commit and install tools only from hashed locks.
"""

import json
import pathlib
import re

MERIDIAN = pathlib.Path(__file__).resolve().parents[1]
REPO = MERIDIAN.parent
WORKFLOW = REPO / ".github" / "workflows" / "application-ci.yml"
EXACT = re.compile(r"^\d+\.\d+\.\d+$")
HASHED_LOCKS = [
    "requirements.txt",
    "requirements-runtime-tests.txt",
    "requirements-ci.txt",
    "examples/langgraph/requirements.txt",
]


def python_version() -> str:
    version = (MERIDIAN / ".python-version").read_text().strip()
    assert EXACT.match(version), f".python-version must be exact (X.Y.Z), got {version!r}"
    return version


def python_minor() -> str:
    return python_version().rsplit(".", 1)[0]


def test_node_version_is_exact():
    version = (MERIDIAN / ".nvmrc").read_text().strip()
    assert EXACT.match(version), f".nvmrc must be exact (X.Y.Z), got {version!r}"


def test_container_runs_the_pinned_python_by_digest():
    froms = re.findall(r"^FROM (\S+)", (MERIDIAN / "Dockerfile").read_text(), re.MULTILINE)
    assert froms, "Dockerfile has no FROM line"
    for image in froms:
        assert re.fullmatch(
            rf"python:{re.escape(python_version())}-slim-[a-z]+@sha256:[0-9a-f]{{64}}", image
        ), f"Dockerfile FROM {image} must be python:{python_version()}-slim-<debian>@sha256:..."


def test_locks_are_compiled_for_the_pinned_python():
    for name in HASHED_LOCKS:
        text = (MERIDIAN / name).read_text()
        compiled_for = f"--python-version {python_minor()} "
        assert compiled_for in text, f"{name} was compiled for another Python"
        assert "--hash=sha256:" in text, f"{name} must carry hashes"


def test_type_checker_targets_the_pinned_python():
    config = json.loads((MERIDIAN / "pyrightconfig.json").read_text())
    assert config["pythonVersion"] == python_minor()


def test_lambdas_run_the_pinned_python():
    lambda_name = "python" + python_minor()
    cdk_name = "PYTHON_" + python_minor().replace(".", "_")
    sources = {
        "infra/lib/meridian-identity-stack.ts": re.compile(r"Runtime\.(PYTHON_\d+_\d+)"),
        "scripts/gateway_harness/resources.py": re.compile(r'"Runtime": "(python\d+\.\d+)"'),
        "scripts/identity_release/interceptor_lambda.py": re.compile(
            r'RUNTIME = "(python\d+\.\d+)"'
        ),
    }
    for name, pattern in sources.items():
        found = pattern.findall((MERIDIAN / name).read_text())
        assert found, f"no Lambda runtime found in {name}"
        assert set(found) <= {lambda_name, cdk_name}, f"{name} names {sorted(set(found))}"


def _values(node, keys):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys:
                yield value
            yield from _values(value, keys)
    elif isinstance(node, list):
        for item in node:
            yield from _values(item, keys)


def test_agentcore_runtimes_and_targets_run_the_pinned_python():
    template = json.loads(
        (MERIDIAN / "meridian_agentcore" / "agentcore" / "agentcore.template.json").read_text()
    )
    found = set(_values(template, {"runtimeVersion", "pythonVersion"}))
    assert found == {"PYTHON_" + python_minor().replace(".", "_")}, sorted(found)


CDK_APPS = ["infra", "meridian_agentcore/agentcore/cdk"]
# The CDK toolkit inside the pinned AgentCore CLI reads cloud assembly schema 54 only.
AGENTCORE_CLI_SCHEMA_MAJOR = 54


def test_cdk_apps_pin_one_exact_aws_cdk_lib():
    manifests = {app: json.loads((MERIDIAN / app / "package.json").read_text()) for app in CDK_APPS}
    specs = {app: manifest["dependencies"]["aws-cdk-lib"] for app, manifest in manifests.items()}
    assert len(set(specs.values())) == 1, specs
    assert EXACT.match(specs[CDK_APPS[0]]), specs


def test_agentcore_app_writes_the_schema_the_pinned_cli_reads():
    lock = MERIDIAN / "meridian_agentcore" / "agentcore" / "cdk" / "package-lock.json"
    packages = json.loads(lock.read_text())["packages"]
    schemas = {
        path: entry["version"]
        for path, entry in packages.items()
        if path.endswith("node_modules/@aws-cdk/cloud-assembly-schema")
    }
    assert schemas, "no cloud assembly schema in the AgentCore CDK lock"
    majors = {int(version.split(".")[0]) for version in schemas.values()}
    assert majors == {AGENTCORE_CLI_SCHEMA_MAJOR}, schemas


def test_ci_takes_versions_from_the_pin_files():
    text = WORKFLOW.read_text()
    assert "ubuntu-latest" not in text, "pin the runner image, for example ubuntu-24.04"
    assert re.search(r"^\s*python-version:", text, re.MULTILINE) is None
    assert re.search(r"^\s*node-version:", text, re.MULTILINE) is None
    for setting in re.findall(r"python-version-file: (\S+)", text):
        assert setting == "meridian/.python-version"
    for setting in re.findall(r"node-version-file: (\S+)", text):
        assert setting == "meridian/.nvmrc"


def test_ci_pins_every_action_to_a_commit():
    for action in re.findall(r"uses: (\S+)", WORKFLOW.read_text()):
        pinned = re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action)
        assert pinned, f"{action} is not pinned to a commit"


def test_ci_installs_only_from_hashed_locks_or_exact_pins():
    text = WORKFLOW.read_text()
    assert re.search(r"setup-uv@\S+[^\n]*\n\s+with:\n\s+version: '\d+\.\d+\.\d+'", text)
    dockerfile_pip = re.search(r"pip==(\d+\.\d+\.\d+)", (MERIDIAN / "Dockerfile").read_text())
    assert dockerfile_pip, "the Dockerfile pins pip"
    for command in re.findall(r"pip install ([^\n]+)", text):
        if command.startswith("pip=="):
            assert command == f"pip=={dockerfile_pip.group(1)}", "CI and the image use one pip"
        else:
            words = command.split()
            assert "--require-hashes" in words and "-r" in words, (
                f"unhashed install: pip install {command}"
            )
