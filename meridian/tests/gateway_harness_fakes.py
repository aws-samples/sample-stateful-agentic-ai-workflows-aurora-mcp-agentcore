"""Recording stand-ins for the boto3 clients the harness drives.

The fakes also keep the tags each create call carried and answer the tag reads, so the harness's
teardown can prove ownership before every delete.
"""

from __future__ import annotations

from botocore.exceptions import ClientError

from scripts.gateway_harness import resources as res

ACCOUNT, REGION = "123456789012", "us-east-1"
GATEWAY_ID, ENGINE_ID, TARGET_ID = "gw-abcdef1234", "pe-abcdef1234", "abcdef1234"


def client_error(code, message="x"):
    return ClientError({"Error": {"Code": code, "Message": message}}, "Op")


class Fake:
    """Records calls by name; ``answers`` maps an operation to a result or a callable."""

    def __init__(self, answers=None):
        self.calls, self.answers, self.gone = [], answers or {}, set()

    def __getattr__(self, op):
        def call(*args, **kwargs):
            self.calls.append((op, kwargs))
            answer = self.answers.get(op, {})
            return answer(*args, **kwargs) if callable(answer) else answer
        return call

    def names(self):
        return [op for op, _ in self.calls]

    def args(self, op):
        return [kwargs for name, kwargs in self.calls if name == op]

    def answer(self, op, kwargs):
        """Record ``op`` and return its configured answer (calling it when it is a callable)."""
        self.calls.append((op, kwargs))
        answer = self.answers.get(op, {})
        return answer(**kwargs) if callable(answer) else answer


class FakeIam(Fake):
    """IAM whose roles carry the tags they were created with."""

    def __init__(self):
        super().__init__({
            "create_role": {"Role": {"Arn": "arn:role"}},
            "list_attached_role_policies": {
                "AttachedPolicies": [{"PolicyArn": "arn:aws:iam::aws:policy/throwaway"}]},
            "list_role_policies": {"PolicyNames": ["inline"]},
        })
        self.tags = {}

    def create_role(self, **kwargs):
        result = self.answer("create_role", kwargs)
        self.tags[kwargs["RoleName"]] = list(kwargs.get("Tags", []))
        return result

    def list_role_tags(self, **kwargs):
        self.calls.append(("list_role_tags", kwargs))
        if kwargs["RoleName"] not in self.tags:
            raise client_error("NoSuchEntity")
        return {"Tags": self.tags[kwargs["RoleName"]]}

    def delete_role(self, **kwargs):
        self.calls.append(("delete_role", kwargs))
        self.tags.pop(kwargs["RoleName"], None)


class FakeWaiter:
    def wait(self, **kwargs):
        pass


class FakeLambda(Fake):
    """Lambda whose functions carry the tags they were created with."""

    def __init__(self):
        super().__init__()
        self.tags = {}

    def get_waiter(self, name):
        return FakeWaiter()

    def create_function(self, **kwargs):
        result = self.answer("create_function", kwargs)
        self.tags[kwargs["FunctionName"]] = dict(kwargs.get("Tags", {}))
        return result

    def list_tags(self, **kwargs):
        self.calls.append(("list_tags", kwargs))
        name = kwargs["Resource"].rsplit(":", 1)[-1]
        if name not in self.tags:
            raise client_error("ResourceNotFoundException")
        return {"Tags": self.tags[name]}

    def delete_function(self, **kwargs):
        result = self.answer("delete_function", kwargs)
        self.tags.pop(kwargs["FunctionName"], None)
        return result


class FakeControl(Fake):
    """Gateway APIs whose get_* calls report READY/ACTIVE until the object is deleted."""

    def __init__(self, binding_status="ACTIVE"):
        super().__init__({
            "create_gateway_target": {"targetId": TARGET_ID},
            "create_policy_engine": {
                "policyEngineId": ENGINE_ID,
                "policyEngineArn": (
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:policy-engine/{ENGINE_ID}"),
            },
        })
        self.binding_status, self.deleted = binding_status, set()
        self.tags = {}

    def create_gateway(self, **kwargs):
        self.calls.append(("create_gateway", kwargs))
        self.tags[f"gateway/{GATEWAY_ID}"] = dict(kwargs.get("tags", {}))
        return {"gatewayId": GATEWAY_ID, "gatewayUrl": "https://gw/mcp",
                "gatewayArn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/{GATEWAY_ID}"}

    def create_policy_engine(self, **kwargs):
        result = self.answer("create_policy_engine", kwargs)
        self.tags[f"policy-engine/{ENGINE_ID}"] = dict(kwargs.get("tags", {}))
        return result

    def list_tags_for_resource(self, **kwargs):
        self.calls.append(("list_tags_for_resource", kwargs))
        key = kwargs["resourceArn"].rsplit(":", 1)[-1]
        gone = "gateway" if key.startswith("gateway/") else "engine"
        if key not in self.tags or gone in self.deleted:
            raise client_error("ResourceNotFoundException")
        return {"tags": self.tags[key]}

    def create_policy(self, **kwargs):
        self.calls.append(("create_policy", kwargs))
        return {"policyId": f"{kwargs['name']}-abcdef1234"}

    def _get(self, op, key, ready, kwargs):
        self.calls.append((op, kwargs))
        if key in self.deleted:
            raise client_error("ResourceNotFoundException")
        return {"status": ready}

    def get_gateway(self, **kw):
        reply = self._get("get_gateway", "gateway", "READY", kw)
        detached = any(name == "update_gateway" and "interceptorConfigurations" not in kwargs
                       for name, kwargs in self.calls)
        if not detached:
            reply["interceptorConfigurations"] = [{"interceptionPoints": ["REQUEST"]}]
        return reply

    def get_gateway_target(self, **kw):
        return self._get("get_gateway_target", "target", "READY", kw)

    def get_policy_engine(self, **kw):
        return self._get("get_policy_engine", "engine", "ACTIVE", kw)

    def get_policy(self, **kw):
        self.calls.append(("get_policy", kw))
        if "policy" in self.deleted:
            raise client_error("ResourceNotFoundException")
        if kw["policyId"].startswith(res.BINDING_POLICY) and self.binding_status != "ACTIVE":
            return {"status": self.binding_status, "statusReasons": ["unknown tag"]}
        return {"status": "ACTIVE"}

    def delete_gateway_target(self, **kw):
        self.calls.append(("delete_gateway_target", kw))
        self.deleted.add("target")

    def delete_gateway(self, **kw):
        self.calls.append(("delete_gateway", kw))
        self.deleted.add("gateway")

    def delete_policy(self, **kw):
        self.calls.append(("delete_policy", kw))
        self.deleted.add("policy")

    def delete_policy_engine(self, **kw):
        self.calls.append(("delete_policy_engine", kw))
        self.deleted.add("engine")


class Pager:
    """A paginator whose pages are fixed."""

    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        return iter(self.pages)
