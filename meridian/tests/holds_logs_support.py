"""Stand-ins for the log group and the holds function the stage 1 to 2 hand-over touches."""

from __future__ import annotations

from tests import release_support as rs
from tests.aws_recorders import Recorder, client_error

FUNCTION = "meridianv2-MeridianHolds"
GROUP = f"/aws/lambda/{FUNCTION}"
NEIGHBOURS = (f"{GROUP}-old", f"{GROUP}2", "/aws/lambda/meridianv2-MeridianHold",
              "/aws/lambda/meridian-gateway-traveler-pin")


class FakeLogs(Recorder):
    """describe_log_groups by prefix (two per page) and delete_log_group, like CloudWatch Logs."""

    def __init__(self, *groups, stubborn=False, page=2, failures=None):
        super().__init__(failures=failures)
        self.groups = list(groups)
        self.stubborn, self.page = stubborn, page

    def describe_log_groups(self, logGroupNamePrefix="", nextToken=None, **kwargs):
        self.calls.append(("describe_log_groups",
                           {"logGroupNamePrefix": logGroupNamePrefix, "nextToken": nextToken}))
        found = [name for name in self.groups if name.startswith(logGroupNamePrefix)]
        start = int(nextToken or 0)
        reply = {"logGroups": [{"logGroupName": n} for n in found[start:start + self.page]]}
        if start + self.page < len(found):
            reply["nextToken"] = str(start + self.page)
        return reply

    def delete_log_group(self, logGroupName):
        self.calls.append(("delete_log_group", {"logGroupName": logGroupName}))
        pending = self.failures.get("delete_log_group")
        if pending:
            raise pending.pop(0)
        if not self.stubborn:
            self.groups.remove(logGroupName)
        return {}

    def deleted(self):
        return [kwargs["logGroupName"] for name, kwargs in self.calls
                if name == "delete_log_group"]


class FakeFunctions(Recorder):
    """get_function_configuration for the holds function: present or ResourceNotFound."""

    def __init__(self, exists=False):
        super().__init__()
        self.exists = exists

    def get_function_configuration(self, FunctionName):
        self.calls.append(("get_function_configuration", {"FunctionName": FunctionName}))
        if FunctionName == FUNCTION and self.exists:
            return {"FunctionName": FUNCTION}
        raise client_error("ResourceNotFoundException", operation="GetFunctionConfiguration")


def account_check(account=rs.ACCOUNT):
    """An sts stand-in that reports the credentials' account."""
    return Recorder(answers={"get_caller_identity": {"Account": account}})
