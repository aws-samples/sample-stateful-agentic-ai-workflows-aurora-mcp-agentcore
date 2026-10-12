"""Recording stand-ins for the boto3 clients the release tools drive, and a model validator."""

from __future__ import annotations

import botocore.session
from botocore import xform_name
from botocore.exceptions import ClientError
from botocore.validate import ParamValidator


def client_error(code: str, message: str = "x", operation: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


class Recorder:
    """Records every call by operation name; ``answers`` maps an operation to a result or callable.

    An operation listed in ``failures`` raises the exception (or each one in turn if a list).
    """

    def __init__(self, answers=None, failures=None):
        self.calls: list[tuple[str, dict]] = []
        self.answers = answers or {}
        self.failures = {k: (list(v) if isinstance(v, list) else [v])
                         for k, v in (failures or {}).items()}

    def __getattr__(self, operation):
        def call(**kwargs):
            self.calls.append((operation, kwargs))
            pending = self.failures.get(operation)
            if pending:
                raise pending.pop(0)
            answer = self.answers.get(operation, {})
            return answer(**kwargs) if callable(answer) else answer
        return call

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def args(self, operation: str) -> list[dict]:
        return [kwargs for name, kwargs in self.calls if name == operation]


class Waiters:
    """A client with waiters that never wait."""

    def __init__(self):
        self.waited: list[tuple[str, dict]] = []

    def get_waiter(self, name):
        outer = self

        class Waiter:
            def wait(self, **kwargs):
                outer.waited.append((name, kwargs))

        return Waiter()


def violations(service: str, calls: list[tuple[str, dict]]) -> list[str]:
    """Calls whose arguments the installed botocore model for ``service`` rejects."""
    model = botocore.session.get_session().get_service_model(service)
    found = []
    for operation, kwargs in calls:
        if operation.startswith("get_waiter"):
            continue
        pascal = next((op for op in model.operation_names if xform_name(op) == operation), None)
        if pascal is None:
            found.append(f"{service}.{operation}: no such operation")
            continue
        report = ParamValidator().validate(kwargs, model.operation_model(pascal).input_shape)
        if report.has_errors():
            found.append(f"{service}.{operation}: {report.generate_report()}")
    return found
