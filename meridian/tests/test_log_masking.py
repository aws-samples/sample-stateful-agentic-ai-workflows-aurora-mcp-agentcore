"""Every log record is masked on every handler before a formatter sees it.

The tests read the stream a handler writes (``capsys``), not ``caplog``: caplog's handler sits
on the root logger beside the real ones and sees a record before or after the mask by chance.
"""

from contextlib import contextmanager
import importlib.util
import io
import json
import logging
import sys
import traceback
from pathlib import Path
from types import ModuleType

import pytest

from backend.logging_config import setup_logging
from backend.mcp import concierge_server, memory_server
from backend.security import redaction
from scripts import stage_workflow_runtime as stage_workflow
from tests.runtime_support import load_runtime_main

ACCOUNT = "123456789012"
SECRET = "hunter2"
LEAKS = (ACCOUNT, SECRET)
RUNTIME = (Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app"
           / "MeridianConcierge")
spec = importlib.util.spec_from_file_location(
    "concierge_tool_errors_log", RUNTIME / "tool_errors.py")
runtime_copy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_copy)

LOGGERS = ["backend.agents.example", "meridian", "strands.tools.mcp.mcp_client", "mcp.server",
           "botocore.endpoint", ""]


@pytest.fixture(autouse=True)
def restore_logging():
    factory = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(factory)
    setup_logging()
    root = logging.getLogger()
    for each in [h for h in root.handlers if getattr(h, "meridian_runtime_log_handler", False)]:
        root.removeHandler(each)


@contextmanager
def alone_on_root():
    """A stream handler as the root logger's only handler, so no other handler masks first.

    A filter changes the record in place, so a handler that sits after a masking one would see
    masked text whether or not it masks itself.
    """
    root = logging.getLogger()
    saved, stream = root.handlers[:], io.StringIO()
    handler = logging.StreamHandler(stream)
    root.handlers = [handler]
    try:
        yield stream
    finally:
        root.handlers = saved


def log_failure(name):
    try:
        raise RuntimeError(f"denied on arn:aws:iam::{ACCOUNT}:role/Task password={SECRET}")
    except RuntimeError:
        logging.getLogger(name).exception("call for %s failed token=%s", ACCOUNT, SECRET)


def written(capsys):
    captured = capsys.readouterr()
    return captured.out + captured.err


@pytest.mark.parametrize("name", LOGGERS, ids=lambda name: name or "root")
def test_a_logged_exception_is_masked_in_the_readable_format(capsys, name):
    setup_logging(level="INFO", json_output=False)

    log_failure(name)

    text = written(capsys)
    assert "Traceback" in text and "test_log_masking.py" in text, "the frames stay"
    assert "<acct>" in text and "password=<masked>" in text and "token=<masked>" in text
    assert not [leak for leak in LEAKS if leak in text]


@pytest.mark.parametrize("name", LOGGERS, ids=lambda name: name or "root")
def test_a_logged_exception_is_masked_in_the_json_format(capsys, name):
    setup_logging(level="INFO", json_output=True)

    log_failure(name)

    text = written(capsys)
    [record] = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert "Traceback" in record["exception"] and "<acct>" in record["exception"]
    assert "password=<masked>" in record["exception"]
    assert "<acct>" in record["message"] and "token=<masked>" in record["message"]
    assert not [leak for leak in LEAKS if leak in text]


def test_a_stack_trace_is_masked(capsys):
    setup_logging(level="INFO", json_output=False)

    logging.getLogger("strands").warning("where password=%s", SECRET, stack_info=True)

    text = written(capsys)
    assert "Stack (most recent call last)" in text and SECRET not in text


def test_library_info_records_stay_out_of_the_log(capsys):
    setup_logging(level="INFO", json_output=False)

    logging.getLogger("strands").info("Processing request of type ListToolsRequest")

    assert written(capsys) == ""


def test_the_filter_masks_in_place_and_is_idempotent():
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "id %s", (ACCOUNT,), None)
    mask = redaction.MaskingFilter()

    assert mask.filter(record) and mask.filter(record)

    assert record.getMessage() == "id <acct>"


def test_the_filter_survives_a_message_that_does_not_format():
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, f"{ACCOUNT} %s %s", ("a",), None)

    assert redaction.MaskingFilter().filter(record)

    assert ACCOUNT not in record.getMessage()


def test_install_masking_covers_every_root_handler_once():
    with alone_on_root() as stream:
        redaction.install_masking()
        redaction.install_masking()
        logging.getLogger().warning("token=%s", SECRET)
        [handler] = logging.getLogger().handlers
        assert len([f for f in handler.filters if isinstance(f, redaction.MaskingFilter)]) == 1
        assert SECRET not in stream.getvalue() and "token=<masked>" in stream.getvalue()


@pytest.mark.parametrize("server", [concierge_server, memory_server], ids=["concierge", "memory"])
def test_the_stdio_servers_mask_what_their_handlers_write(server):
    with alone_on_root() as stream:
        server.configure_logging()
        logging.getLogger("mcp.server").warning("role %s password=%s", ACCOUNT, SECRET)
        assert "<acct>" in stream.getvalue() and "password=<masked>" in stream.getvalue()
        assert not [leak for leak in LEAKS if leak in stream.getvalue()]
        for each in logging.getLogger().handlers:
            assert any(isinstance(f, redaction.MaskingFilter) for f in each.filters), each


def test_the_workflow_runtime_stages_the_logging_setup_that_installs_the_mask():
    staged = {path.relative_to(stage_workflow.MERIDIAN).as_posix()
              for path in stage_workflow.closure()}

    assert {"backend/logging_config.py", "backend/security/redaction.py"} <= staged


def test_the_workflow_runtime_entrypoint_masks_its_handlers(monkeypatch):
    app_dir = RUNTIME.parent / "MeridianWorkflow"

    class FakeApp:
        def entrypoint(self, handler):
            return handler

    sdk = ModuleType("bedrock_agentcore.runtime")
    sdk.BedrockAgentCoreApp = FakeApp
    monkeypatch.setitem(sys.modules, "bedrock_agentcore", ModuleType("bedrock_agentcore"))
    monkeypatch.setitem(sys.modules, "bedrock_agentcore.runtime", sdk)
    monkeypatch.setenv("AGENTCORE_GATEWAY_URL", "https://gw-1.gateway.example/mcp")
    monkeypatch.syspath_prepend(str(app_dir))
    with alone_on_root() as stream:
        spec = importlib.util.spec_from_file_location("workflow_main_log_masking",
                                                      app_dir / "main.py")
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        logging.getLogger("strands").error("role %s password=%s", ACCOUNT, SECRET)
        assert "<acct>" in stream.getvalue()
        assert not [leak for leak in LEAKS if leak in stream.getvalue()]


class TestConciergeRuntimeFilter:
    """The Runtime cannot import ``backend``, so it repeats the filter."""

    SAMPLES = [
        f"role arn:aws:iam::{ACCOUNT}:role/x password={SECRET}",
        f"https://x/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A{ACCOUNT}%3Aruntime",
        "uuid 11111111-2222-3333-4444-123456789012 key=abc",
        "nothing to hide",
    ]

    @pytest.mark.parametrize("text", SAMPLES)
    def test_it_masks_the_way_the_backend_filter_does(self, text):
        def run(mask_filter):
            record = logging.LogRecord("x", logging.ERROR, __file__, 1, text, None, None)
            mask_filter.filter(record)
            return record.getMessage()

        assert run(runtime_copy.MaskingFilter()) == run(redaction.MaskingFilter())

    def test_it_masks_the_traceback_and_the_library_loggers_of_the_runtime(self):
        with alone_on_root() as stream:
            runtime_copy.install_log_masking()
            log_failure("strands.tools.mcp.mcp_client")
            text = stream.getvalue()
            assert "Traceback" in text and "<acct>" in text and "password=<masked>" in text
            assert not [leak for leak in LEAKS if leak in text]

    def test_the_runtime_entrypoint_installs_it(self, monkeypatch):
        import tool_errors

        installed = []

        monkeypatch.setattr(tool_errors, "install_log_masking", lambda: installed.append(True))
        load_runtime_main(monkeypatch, "concierge_main_log_masking")

        assert installed == [True]


# ------------------------------- handlers a library adds after the mask is installed


class RenderingHandler(logging.Handler):
    """A handler that renders ``exc_info`` itself, as a Rich handler does."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        text = record.getMessage()
        if record.exc_info:
            text += "".join(traceback.format_exception(*record.exc_info))
        self.lines.append(text)


@contextmanager
def late_handler(name, handler):
    """``handler`` on logger ``name`` only (no propagation), as a library adds it after setup."""
    target = logging.getLogger(name)
    saved = target.propagate
    target.addHandler(handler)
    target.propagate = False
    try:
        yield
    finally:
        target.removeHandler(handler)
        target.propagate = saved


@pytest.mark.parametrize("name", ["bedrock_agentcore.app", "uvicorn.error"])
def test_a_plain_handler_added_after_setup_prints_a_masked_traceback(name):
    setup_logging(level="INFO", json_output=False)
    stream = io.StringIO()

    with late_handler(name, logging.StreamHandler(stream)):
        log_failure(name)

    text = stream.getvalue()
    assert "Traceback" in text and "test_log_masking.py" in text, "the frames stay"
    assert "<acct>" in text and "password=<masked>" in text and "token=<masked>" in text
    assert not [leak for leak in LEAKS if leak in text]


ACCESS_FORMAT = '%(client_addr)s - "%(request_line)s" %(status_code)s'
ACCESS_ARGS = ("127.0.0.1:5000", "GET", f"/acct/{ACCOUNT}?token={SECRET}", "1.1", 200)


def test_uvicorns_own_access_formatter_still_prints_a_masked_access_line():
    from uvicorn.logging import AccessFormatter

    setup_logging(level="INFO", json_output=False)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(ACCESS_FORMAT, use_colors=False))

    with late_handler("uvicorn.access", handler):
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', *ACCESS_ARGS)

    assert stream.getvalue().strip() == (
        '127.0.0.1:5000 - "GET /acct/<acct>?token=<masked> HTTP/1.1" 200 OK')


def made(module, msg, args):
    logging.setLogRecordFactory(logging.LogRecord)
    module.install_record_factory()
    return logging.getLogger("shape").makeRecord("shape", logging.INFO, __file__, 1, msg, args,
                                                 None)


@pytest.mark.parametrize("module", [redaction, runtime_copy], ids=["backend", "runtime"])
class TestArgumentsKeepTheirShape:
    def test_a_tuple_keeps_its_length_and_its_non_strings(self, module):
        record = made(module, 'id %s n %d path %s', (ACCOUNT, 7, f"/x/{ACCOUNT}"))

        assert record.args == ("<acct>", 7, "/x/<acct>")
        assert record.msg == "id %s n %d path %s"
        assert record.getMessage() == "id <acct> n 7 path /x/<acct>"

    def test_a_mapping_keeps_its_keys(self, module):
        record = made(module, "who %(who)s n %(n)d", {"who": f"role {ACCOUNT}", "n": 3})

        assert record.args == {"who": "role <acct>", "n": 3}
        assert record.getMessage() == "who role <acct> n 3"

    def test_the_template_is_masked_too(self, module):
        record = made(module, f"role {ACCOUNT} did %s", ("x",))

        assert record.msg == "role <acct> did %s" and record.getMessage() == "role <acct> did x"

    def test_a_secret_split_across_the_template_and_an_argument_is_hidden_everywhere(self, module):
        record = made(module, "call for %s failed token=%s", (ACCOUNT, SECRET))

        assert record.getMessage() == "call for <acct> failed token=<masked>"
        assert not [leak for leak in LEAKS if leak in repr(record.args) + record.msg]
        assert len(record.args) == 2

    def test_masking_twice_changes_nothing(self, module):
        record = made(module, "call for %s failed token=%s", (ACCOUNT, SECRET))
        first = (record.msg, record.args, record.getMessage())

        mask_twice = module.MaskingFilter()
        mask_twice.filter(record)
        mask_twice.filter(record)

        assert (record.msg, record.args, record.getMessage()) == first

    def test_a_percent_sign_in_an_argument_is_kept(self, module):
        record = made(module, "done %s with token=%s", ("100% of it", SECRET))

        assert record.getMessage() == "done 100% of it with token=<masked>"


def test_a_handler_that_renders_exc_info_itself_finds_no_raw_traceback():
    setup_logging(level="INFO", json_output=False)
    handler = RenderingHandler()

    with late_handler("bedrock_agentcore.app", handler):
        log_failure("bedrock_agentcore.app")

    [line] = handler.lines
    assert "<acct>" in line and "password=<masked>" not in line, "exc_info was removed"
    assert not [leak for leak in LEAKS if leak in line]


@pytest.mark.parametrize("module", [redaction, runtime_copy], ids=["backend", "runtime"])
def test_the_last_line_of_exc_text_names_the_exception_class_and_its_masked_message(module):
    logging.setLogRecordFactory(logging.LogRecord)
    module.install_record_factory()
    seen = []

    class Reader(logging.Handler):
        def emit(self, record):
            seen.append(record)

    with late_handler("some.library", Reader()):
        log_failure("some.library")

    [record] = seen
    assert record.exc_text.splitlines()[-1] == (
        "RuntimeError: denied on arn:aws:iam::<acct>:role/Task password=<masked>")


def test_the_traceback_is_still_in_exc_text_for_a_handler_that_reads_it():
    setup_logging(level="INFO", json_output=False)
    seen = []

    class Reader(logging.Handler):
        def emit(self, record):
            seen.append(record)

    with late_handler("some.library", Reader()):
        log_failure("some.library")

    [record] = seen
    assert record.exc_info is None
    assert "Traceback" in record.exc_text and "password=<masked>" in record.exc_text


def test_the_last_resort_handler_prints_masked_records(monkeypatch):
    setup_logging(level="INFO", json_output=False)
    stream = io.StringIO()
    monkeypatch.setattr(logging, "lastResort", logging.StreamHandler(stream))
    root = logging.getLogger()
    saved, root.handlers = root.handlers[:], []
    try:
        log_failure("isolated.library")
    finally:
        root.handlers = saved

    text = stream.getvalue()
    assert "Traceback" in text and "<acct>" in text
    assert not [leak for leak in LEAKS if leak in text]


def test_the_factory_runs_the_factory_a_library_installed_first():
    def library_factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.library_marker = "kept"
        return record

    previous = logging.getLogRecordFactory()
    logging.setLogRecordFactory(library_factory)

    redaction.install_record_factory()

    record = logging.getLogger("x").makeRecord("x", logging.ERROR, __file__, 1, "id %s",
                                               (ACCOUNT,), None)
    assert record.library_marker == "kept" and record.getMessage() == "id <acct>"


def test_installing_the_factory_twice_adds_one_layer():
    redaction.install_record_factory()
    first = logging.getLogRecordFactory()

    redaction.install_record_factory()

    assert logging.getLogRecordFactory() is first


@pytest.mark.parametrize("json_output", [False, True], ids=["readable", "json"])
def test_string_extras_the_formatters_print_are_masked(capsys, json_output):
    setup_logging(level="INFO", json_output=json_output)

    logging.getLogger("backend.agents.example").error(
        "failed", extra={"error": f"role {ACCOUNT} password={SECRET}", "context": f"c {ACCOUNT}",
                         "query": f"trip for {ACCOUNT}", "traveler_id": "trv_pinned"})

    text = written(capsys)
    assert "<acct>" in text and "trv_pinned" in text, "a plain traveler id is not masked away"
    assert not [leak for leak in LEAKS if leak in text]


def test_the_filter_masks_string_extras_in_place():
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "m", None, None)
    record.error, record.results_count = f"token={SECRET} {ACCOUNT}", 3

    redaction.MaskingFilter().filter(record)

    assert record.error == "token=<masked> <acct>" and record.results_count == 3


class TestFactoryParity:
    """The Runtime repeats the factory; both must write the same bytes for the same records."""

    MODULES = {"backend": redaction, "runtime": runtime_copy}

    def written_by(self, module):
        logging.setLogRecordFactory(logging.LogRecord)
        module.install_record_factory()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        with late_handler("parity.library", handler):
            log_failure("parity.library")
            logging.getLogger("parity.library").warning(
                "uuid 11111111-2222-3333-4444-%s arn%%3A%s key=%s", ACCOUNT, ACCOUNT, SECRET,
                stack_info=True)
            logging.getLogger("parity.library").warning("plain %s", "text")
        return stream.getvalue()

    def test_both_keep_the_same_message_and_arguments(self):
        shapes = []
        for module in self.MODULES.values():
            record = made(module, "call for %s failed token=%s n=%d", (ACCOUNT, SECRET, 3))
            other = made(module, "a %(a)s", ({"a": f"x{ACCOUNT}"},))
            shapes.append((record.msg, record.args, other.msg, other.args))

        assert shapes[0] == shapes[1]

    def test_both_write_the_same_masked_output(self):
        outputs = {name: self.written_by(module) for name, module in self.MODULES.items()}

        assert outputs["backend"] == outputs["runtime"]
        assert "<acct>" in outputs["backend"] and "key=<masked>" in outputs["backend"]
        assert SECRET not in outputs["backend"], "the UUID tail is the one id both keep"
