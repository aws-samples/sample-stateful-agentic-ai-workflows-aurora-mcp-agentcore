"""Fakes for the two AWS boundaries the login rotation Lambda touches.

FakeSecrets follows Secrets Manager's version and stage rules (a stage lives on one version, a
token names one value). FakeDataApi holds role state and verifies a secret's password against the
SCRAM verifier the rotation stored, so a wrong password fails a connection as it would in Aurora.
"""

import base64
import hashlib
import hmac
import json
import re

from botocore.exceptions import ClientError

ACCOUNT = "123456789012"
CLUSTER = f"arn:aws:rds:us-east-1:{ACCOUNT}:cluster:meridian"
SECRET_PREFIX = f"arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:meridian/aurora"
LOGIN_SECRET = f"{SECRET_PREFIX}/backend-login-AbC123"
ROTATOR_SECRET = f"{SECRET_PREFIX}/rotator-login-XyZ789"
LOGIN = "meridian_backend"
CLONE = "meridian_backend_clone"
CONFIG = "idle_in_transaction_session_timeout=120s,lock_timeout=10s,statement_timeout=30s"
COLUMNS = ["rolname", "rolcanlogin", "rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb",
           "rolreplication", "rolinherit", "rolconnlimit", "config", "clone_is_member"]


def client_error(code, operation="Op"):
    return ClientError({"Error": {"Code": code, "Message": "not for logs"}}, operation)


class FakeSecrets:
    """One rotating secret, as Secrets Manager keeps its versions and stages."""

    def __init__(self, username=LOGIN, password="original-password"):
        value = json.dumps({"username": username, "password": password, "note": "kept"})
        self.versions = {"v1": {"value": value, "stages": {"AWSCURRENT"}}}
        self.rotation_enabled = True
        self.fail = {}

    def _maybe_fail(self, name):
        queue = self.fail.get(name)
        if queue:
            raise queue.pop(0)

    def begin(self, token):
        """RotateSecret stages the new token as AWSPENDING with no value before createSecret."""
        self.versions.setdefault(token, {"value": None, "stages": {"AWSPENDING"}})

    def holder(self, stage):
        return next((v for v, d in self.versions.items() if stage in d["stages"]), None)

    def describe_secret(self, SecretId):
        self._maybe_fail("describe_secret")
        return {"RotationEnabled": self.rotation_enabled,
                "VersionIdsToStages": {v: sorted(d["stages"]) for v, d in self.versions.items()}}

    def get_secret_value(self, SecretId, VersionId=None, VersionStage=None):
        self._maybe_fail("get_secret_value")
        for version, data in self.versions.items():
            if VersionId and version != VersionId:
                continue
            if VersionStage and VersionStage not in data["stages"]:
                continue
            if data["value"] is None:
                continue
            return {"SecretString": data["value"], "VersionId": version}
        raise client_error("ResourceNotFoundException")

    def put_secret_value(self, SecretId, ClientRequestToken, SecretString, VersionStages):
        self._maybe_fail("put_secret_value")
        existing = self.versions.get(ClientRequestToken)
        if existing and existing["value"] not in (None, SecretString):
            raise client_error("ResourceExistsException")
        stages = set(VersionStages)
        for data in self.versions.values():
            data["stages"] -= stages
        self.versions[ClientRequestToken] = {"value": SecretString, "stages": stages}

    def update_secret_version_stage(self, SecretId, VersionStage, MoveToVersionId,
                                    RemoveFromVersionId):
        self._maybe_fail("update_secret_version_stage")
        assert VersionStage in self.versions[RemoveFromVersionId]["stages"]
        self.versions[RemoveFromVersionId]["stages"].discard(VersionStage)
        if VersionStage == "AWSCURRENT":
            for data in self.versions.values():
                data["stages"].discard("AWSPREVIOUS")
            self.versions[RemoveFromVersionId]["stages"].add("AWSPREVIOUS")
        self.versions[MoveToVersionId]["stages"].add(VersionStage)

    def value(self, stage):
        return json.loads(self.versions[self.holder(stage)]["value"])


def verifier_matches(password, verifier):
    match = re.fullmatch(r"SCRAM-SHA-256\$(\d+):([^$]+)\$([^:]+):(.+)", verifier)
    iterations, salt, stored, _server = match.groups()
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt),
                                 int(iterations))
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    return base64.b64encode(hashlib.sha256(client_key).digest()).decode() == stored


def role_row(name, **overrides):
    row = {"rolname": name, "rolcanlogin": False, "rolsuper": False, "rolbypassrls": False,
           "rolcreaterole": False, "rolcreatedb": False, "rolreplication": False,
           "rolinherit": False, "rolconnlimit": -1, "config": CONFIG, "clone_is_member": True}
    return {**row, **overrides}


def _cell(value):
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    return {"stringValue": value}


class FakeDataApi:
    """The Data API for one cluster: role rows, password verifiers, and secret-based connects."""

    def __init__(self, secrets_by_arn):
        self.secrets = secrets_by_arn
        self.roles = {LOGIN: role_row(LOGIN, rolcanlogin=True), CLONE: role_row(CLONE)}
        self.verifiers = {}
        self.statements = []
        self.fail = {}

    def _maybe_fail(self, kind):
        queue = self.fail.get(kind)
        if queue:
            raise queue.pop(0)

    def _alter(self, secret_arn, sql):
        assert secret_arn == ROTATOR_SECRET
        self._maybe_fail("alter")
        role, verifier = re.fullmatch(
            r"ALTER ROLE (\w+) LOGIN PASSWORD '(SCRAM-SHA-256[^']+)'", sql).groups()
        self.verifiers[role] = verifier
        self.roles[role]["rolcanlogin"] = True
        return {"numberOfRecordsUpdated": 0}

    def _roles(self, secret_arn, parameters):
        assert secret_arn == ROTATOR_SECRET
        self._maybe_fail("roles")
        names = sorted(p["value"]["stringValue"] for p in parameters)
        return {"records": [[_cell(self.roles[n][c]) for c in COLUMNS]
                            for n in names if n in self.roles]}

    def _connect(self, secret_arn):
        self._maybe_fail("connect")
        secret = self.secrets[secret_arn]
        creds = secret.value("AWSCURRENT")
        verifier = self.verifiers.get(creds["username"])
        if verifier is None or not verifier_matches(creds["password"], verifier):
            raise client_error("BadRequestException")
        bypass = self.roles[creds["username"]]["rolbypassrls"]
        return {"records": [[{"stringValue": creds["username"]}, {"booleanValue": bypass}]]}

    def execute_statement(self, resourceArn, secretArn, database, sql, parameters=None):
        assert resourceArn == CLUSTER and database == "meridian"
        self.statements.append((secretArn, sql))
        if sql.startswith("ALTER ROLE"):
            return self._alter(secretArn, sql)
        if "FROM pg_roles r WHERE r.rolname IN" in sql:
            return self._roles(secretArn, parameters)
        if sql.startswith("SELECT current_user"):
            return self._connect(secretArn)
        raise AssertionError(f"unexpected SQL: {sql}")
