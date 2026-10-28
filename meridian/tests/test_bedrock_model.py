"""Claude Sonnet 5.5 is built one way, in the backend and the Runtime, with nothing it rejects."""

import ast
import importlib.util
import inspect
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend import bedrock_model
from backend.config import BedrockConfig

MERIDIAN = Path(__file__).resolve().parents[1]
RUNTIME = MERIDIAN / "meridian_agentcore" / "app" / "MeridianConcierge"
SONNET = "global.anthropic.claude-sonnet-5-5"
spec = importlib.util.spec_from_file_location(
    "concierge_model_loader_parity", RUNTIME / "model" / "load.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

# Sonnet 5.5 rejects each of these: a temperature, thinking disabled, a forced tool choice.
REJECTED_NAMES = {"temperature", "tool_choice", "toolChoice"}
KEYED_METHODS = {"pop", "get", "setdefault", "__getitem__"}
BEDROCK_MAX_TOKENS_FILES = (
    MERIDIAN / "meridian_agentcore" / "agentcore" / "agentcore.template.json",
    MERIDIAN / "meridian_agentcore" / "agentcore" / "cdk" / "test" / "fixtures" / "jwt-spec.json",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("BEDROCK_MODEL_ID", "BEDROCK_MAX_TOKENS", "AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bedrock_model.config.bedrock, "region", "us-east-1")


def test_every_default_names_sonnet_5_5():
    assert bedrock_model.DEFAULT_MODEL_ID == SONNET
    assert BedrockConfig.DEFAULT_MODEL_ID == SONNET
    assert loader.DEFAULT_MODEL_ID == SONNET


def test_the_real_model_has_no_temperature_and_adaptive_retries():
    model = bedrock_model.bedrock_model(SONNET)

    assert model.config["model_id"] == SONNET and model.config["max_tokens"] == 16000
    assert "temperature" not in model.config
    assert model.config.get("additional_request_fields", {}) == bedrock_model.request_fields()
    assert model.client.meta.config.retries["mode"] == "adaptive"


def test_the_configured_model_id_is_the_default(monkeypatch):
    monkeypatch.setattr(bedrock_model.config.bedrock, "model_id", "global.anthropic.claude-test")
    assert bedrock_model.bedrock_model().config["model_id"] == "global.anthropic.claude-test"


def test_the_thinking_setting_comes_from_the_one_constant(monkeypatch):
    monkeypatch.setattr(bedrock_model, "REQUEST_FIELDS", {"thinking": {"type": "adaptive"}})
    model = bedrock_model.bedrock_model(SONNET)
    assert model.config["additional_request_fields"] == {"thinking": {"type": "adaptive"}}

    monkeypatch.setattr(bedrock_model, "REQUEST_FIELDS", {})
    assert "additional_request_fields" not in bedrock_model.bedrock_model(SONNET).config


def test_the_accessor_returns_a_copy():
    bedrock_model.request_fields()["thinking"] = {"type": "enabled"}
    assert "thinking" not in bedrock_model.REQUEST_FIELDS


def test_thinking_is_never_set_to_disabled():
    assert "disabled" not in json.dumps(bedrock_model.REQUEST_FIELDS)
    assert "disabled" not in json.dumps(loader.REQUEST_FIELDS)


def test_the_runtime_builds_the_same_model_as_the_backend(monkeypatch):
    backend_model, runtime_model = Mock(), Mock()
    monkeypatch.setattr(bedrock_model, "BedrockModel", backend_model)
    monkeypatch.setattr(loader, "BedrockModel", runtime_model)

    bedrock_model.bedrock_model(SONNET)
    loader.load_model(SONNET, "us-east-1")

    backend_kwargs = dict(backend_model.call_args.kwargs)
    runtime_kwargs = dict(runtime_model.call_args.kwargs)
    backend_retries = backend_kwargs.pop("boto_client_config").retries
    assert backend_retries == runtime_kwargs.pop("boto_client_config").retries
    assert backend_kwargs == runtime_kwargs
    assert bedrock_model.REQUEST_FIELDS == loader.REQUEST_FIELDS


def is_disabled(node: ast.expr) -> bool:
    """Whether the node is the string constant "disabled"."""
    return isinstance(node, ast.Constant) and node.value == "disabled"


def names_rejected_first_argument(call: ast.Call) -> bool:
    """Whether a keyed access such as ``d.pop(name)`` takes a rejected name."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr in KEYED_METHODS):
        return False
    first = call.args[0] if call.args else None
    return isinstance(first, ast.Constant) and first.value in REJECTED_NAMES


def rejected_in_dict(node: ast.Dict) -> list[int]:
    """Line numbers of dict keys that name a rejected parameter or set a type to disabled."""
    lines = []
    for key, value in zip(node.keys, node.values, strict=True):
        if not isinstance(key, ast.Constant):
            continue
        if key.value in REJECTED_NAMES or (key.value == "type" and is_disabled(value)):
            lines.append(key.lineno)
    return lines


def rejected_parameters(tree: ast.AST) -> list[int]:
    """Line numbers of code (never docstrings or comments) that sends a rejected parameter."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in REJECTED_NAMES:
            lines.append(node.value.lineno)
        elif isinstance(node, ast.keyword) and node.arg == "type" and is_disabled(node.value):
            lines.append(node.value.lineno)
        elif isinstance(node, ast.Call) and names_rejected_first_argument(node):
            lines.append(node.lineno)
        elif isinstance(node, ast.Dict):
            lines += rejected_in_dict(node)
        elif isinstance(node, ast.Subscript):
            index = node.slice
            if isinstance(index, ast.Constant) and index.value in REJECTED_NAMES:
                lines.append(index.lineno)
    return sorted(lines)


def scan(root: Path) -> dict[str, list[int]]:
    """Rejected-parameter lines per file under ``root``, keyed by path relative to Meridian."""
    found = {}
    for path in root.rglob("*.py"):
        lines = rejected_parameters(ast.parse(path.read_text()))
        if lines:
            found[path.relative_to(MERIDIAN).as_posix()] = lines
    return found


def test_nothing_in_the_backend_or_the_runtime_sends_a_parameter_sonnet_5_5_rejects():
    found = {**scan(MERIDIAN / "backend"), **scan(RUNTIME)}
    offenders = [f"{path}:{n}" for path, lines in found.items() for n in lines]
    assert offenders == []


def uses_bedrock_model(tree: ast.AST) -> list[int]:
    """Line numbers that import ``BedrockModel`` or reach it as an attribute of a module."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "BedrockModel" for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name.rsplit(".", 1)[-1] == "BedrockModel" for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "BedrockModel":
            lines.append(node.lineno)
    return sorted(lines)


def test_only_the_factory_uses_bedrock_model_in_the_backend():
    users = []
    for path in (MERIDIAN / "backend").rglob("*.py"):
        relative = path.relative_to(MERIDIAN).as_posix()
        if relative != "backend/bedrock_model.py":
            found = uses_bedrock_model(ast.parse(path.read_text()))
            users += [f"{relative}:{n}" for n in found]
    assert users == []


def test_the_guard_sees_every_import_and_attribute_form():
    code = (
        "from strands.models import BedrockModel\n"
        "from strands.models.bedrock import BedrockModel as Model\n"
        "import strands.models.BedrockModel\n"
        "import strands.models\n"
        "from strands import models\n"
        "models.BedrockModel(model_id='x')\n"
        "import strands.models as m\n"
        "m.BedrockModel()\n"
    )
    assert uses_bedrock_model(ast.parse(code)) == [1, 2, 3, 6, 8]


def configured_token_caps(node) -> list[str]:
    """Values of every ``BEDROCK_MAX_TOKENS`` environment entry in parsed JSON."""
    if isinstance(node, dict):
        own = [node.get("value")] if node.get("name") == "BEDROCK_MAX_TOKENS" else []
        return own + [v for child in node.values() for v in configured_token_caps(child)]
    if isinstance(node, list):
        return [v for child in node for v in configured_token_caps(child)]
    return []


@pytest.mark.parametrize("path", BEDROCK_MAX_TOKENS_FILES, ids=lambda p: p.name)
def test_the_deployed_runtime_environment_sets_the_16000_cap(path):
    assert configured_token_caps(json.loads(path.read_text())) == ["16000"]


def test_the_output_budget_is_16000_in_the_backend_and_the_runtime(monkeypatch):
    runtime_model = Mock()
    monkeypatch.setattr(loader, "BedrockModel", runtime_model)
    loader.load_model()

    parameters = inspect.signature(bedrock_model.bedrock_model).parameters
    backend_default = parameters["max_tokens"].default
    assert backend_default == runtime_model.call_args.kwargs["max_tokens"] == 16000


def test_the_scan_reads_code_and_not_prose():
    code = (
        'def f():\n    """temperature toolChoice"""\n    # tool_choice\n'
        '    call(temperature=0.2)\n    x = {"toolChoice": 1}\n    y = z["tool_choice"]\n'
        '    w = {"thinking": {"type": "disabled"}}\n'
        '    v = dict(type="disabled")\n'
        '    d.setdefault("temperature", 1)\n    d.pop("toolChoice")\n'
    )
    assert rejected_parameters(ast.parse(code)) == [4, 5, 6, 7, 8, 9, 10]
    assert rejected_parameters(ast.parse('"""temperature"""\n# toolChoice\n')) == []


def test_unrelated_calls_naming_a_rejected_word_are_not_flagged():
    code = 'os.getenv("temperature")\nlog("tool_choice")\nconfig.get("note")\n'
    assert rejected_parameters(ast.parse(code)) == []


def test_max_attempts_counts_every_attempt_in_the_backend_and_the_runtime():
    backend_retries = bedrock_model.bedrock_model(SONNET).client.meta.config.retries
    runtime_retries = loader.load_model(SONNET, "us-east-1").client.meta.config.retries

    assert backend_retries == {
        "mode": "adaptive", "total_max_attempts": bedrock_model.MAX_ATTEMPTS}
    assert runtime_retries == backend_retries
    assert bedrock_model.MAX_ATTEMPTS == loader.MAX_ATTEMPTS == 5
