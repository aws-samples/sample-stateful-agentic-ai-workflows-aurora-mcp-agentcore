"""Fakes for the two AWS boundaries the audit export and its verifier touch."""

import io
import json
import re

from botocore.exceptions import ClientError

CLUSTER = "arn:aws:rds:us-east-1:123456789012:cluster:meridian"
SECRET = "arn:aws:secretsmanager:us-east-1:123456789012:secret:audit-AbC123"


def client_error(code, message="x", operation="Op"):
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


class FakeBucket:
    """S3 as the exporter uses it: ordered keys, pages of two, and no overwrite."""

    PAGE = 2

    def __init__(self):
        self.objects = {}
        self.puts = []
        self.put_failures = []
        self.lose_response = False
        self.metadata = {}
        self.older = {}
        self.hidden = {}

    def put_object(self, **request):
        self.puts.append(request)
        if self.put_failures:
            raise self.put_failures.pop(0)
        if request["Key"] in self.objects:
            raise client_error("PreconditionFailed")
        assert request["IfNoneMatch"] == "*"
        assert request["Bucket"] == "audit-bucket"
        self.objects[request["Key"]] = request["Body"]
        self.metadata[request["Key"]] = request["Metadata"]
        if self.lose_response:
            self.lose_response = False
            raise client_error("PreconditionFailed")
        return {}

    def get_object(self, Bucket, Key, VersionId=None):
        body = self.objects.get(Key, self.hidden.get(Key))
        if VersionId == "older":
            body = self.older[Key]
        return {"Body": io.BytesIO(body), "Metadata": self.metadata.get(Key, {})}

    def overwrite(self, key, body):
        """A second version of the key, as an overwrite of a versioned bucket makes."""
        self.older[key] = self.objects[key]
        self.objects[key] = body

    def delete(self, key):
        """A delete marker over the key: the key leaves the listing, its version stays."""
        self.hidden[key] = self.objects.pop(key)

    def list_object_versions(self, Bucket, Prefix, **_paging):
        keys = sorted(k for k in {**self.objects, **self.hidden} if k.startswith(Prefix))
        versions = []
        for key in keys:
            versions.append({"Key": key, "VersionId": "current"})
            if key in self.older:
                versions.append({"Key": key, "VersionId": "older"})
        return {"Versions": versions, "IsTruncated": False,
                "DeleteMarkers": [{"Key": k} for k in sorted(self.hidden) if k.startswith(Prefix)]}

    def list_objects_v2(self, Bucket, Prefix, Delimiter=None, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if Delimiter:
            entries = sorted({k[: k.index(Delimiter, len(Prefix)) + 1] for k in keys
                              if Delimiter in k[len(Prefix):]})
        else:
            entries = keys
        start = int(ContinuationToken or 0)
        page = entries[start:start + self.PAGE]
        out = {"CommonPrefixes": [{"Prefix": e} for e in page]} if Delimiter else {
            "Contents": [{"Key": e} for e in page]}
        if start + self.PAGE < len(entries):
            out["NextContinuationToken"] = str(start + self.PAGE)
        return out


class FakeAurora:
    """The Data API over fake audit tables, applying the watermark and settle rules."""

    def __init__(self):
        self.tables = {}
        self.calls = []
        self.fail_with = None
        self.max_rows = None
        self.on_call = None
        # The Data API's own wording, "Database response exceeded size limit", from the
        # troubleshooting page https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/
        # data-api.troubleshooting.html (the 1 MiB result set limit).
        self.oversize_message = "Database response exceeded size limit"

    def add(self, table, ident, ts, age=1000.0, **columns):
        row = {"id": ident, "ts": ts, "age": age, "doc": {"id": ident, "ts": ts, **columns}}
        self.tables.setdefault(table, []).append(row)

    def execute_statement(self, **request):
        self.calls.append(request)
        if self.fail_with:
            raise self.fail_with
        if self.on_call:
            self.on_call(self, request)
        if "DESC LIMIT 1" in request["sql"]:
            return self._newest(request)
        params = {p["name"]: next(iter(p["value"].values())) for p in request["parameters"]}
        table = re.search(r"FROM (\w+) AS t", request["sql"]).group(1)
        assert request["resourceArn"] == CLUSTER and request["secretArn"] == SECRET
        assert request["formatRecordsAs"] == "JSON"
        limit = params["batch"]
        if self.max_rows is not None and limit > self.max_rows:
            raise client_error("BadRequestException", self.oversize_message)
        after = (params["wm_ts"], params["wm_id"])
        rows = [r for r in self.tables.get(table, [])
                if (r["ts"], r["id"]) > after or after[0] == "-infinity"]
        rows = [r for r in rows if r["age"] >= params["settle"]]
        rows.sort(key=lambda r: (r["ts"], r["id"]))
        return {"formattedRecords": json.dumps([
            {"wm_ts": r["ts"], "wm_id": r["id"], "row_json": json.dumps(r["doc"]),
             "age_seconds": str(r["age"])} for r in rows[:limit]])}


    def _newest(self, request):
        """The newest row's timestamp and id, as the verifier's boundary read asks for."""
        table = re.search(r"FROM (\w+) ORDER BY", request["sql"]).group(1)
        rows = sorted(self.tables.get(table, []), key=lambda r: (r["ts"], r["id"]))
        newest = [{"wm_ts": rows[-1]["ts"], "wm_id": rows[-1]["id"]}] if rows else []
        return {"formattedRecords": json.dumps(newest)}


class Context:
    def __init__(self, remaining_ms=100_000):
        self.remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self):
        return self.remaining_ms


