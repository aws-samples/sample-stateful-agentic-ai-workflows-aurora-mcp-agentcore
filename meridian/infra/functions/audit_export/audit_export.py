"""Audit export Lambda: copy new audit rows to an S3 bucket with Object Lock, as a hash chain.

The audit tables live in Aurora, where an administrator can edit or delete rows. Every run
reads the rows that arrived since the last export, through the RDS Data API as the
``meridian_audit_export`` login (migration 033: SELECT on the audit tables, nothing else), and
writes one JSON Lines object per table and batch. The first line of an object is a header with
the object's sequence number, the SHA-256 of the previous object's bytes, and the range of rows
it holds. Editing or deleting a row in Aurora after its export, or an object in the bucket, then
breaks a check that ``scripts/verify_audit_export.py`` makes.

State lives only in the bucket. The latest object of a table names the last exported row (its
watermark) and, through its own bytes, the hash the next object carries. Nothing is written to
Aurora, and a retry derives the same position again, so it repeats no row and skips none.

Watermark. Rows are read in ``(timestamp, id)`` order, strictly after the watermark. A
transaction stamps its rows with its own start time, so a row can commit after a row with a later
stamp. Only rows older than ``AUDIT_EXPORT_SETTLE_SECONDS`` are read, which must exceed the
longest transaction that writes audit rows. A row that still commits behind the watermark is not
exported, and the verifier reports it as missing from the export.

Errors are fixed phrases with the AWS error code in the log, never the service's own text.
"""

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

FORMAT_VERSION = 1
KEY_PREFIX = "audit-export/v1"
OBJECTS_PER_GROUP = 1000
GENESIS_SHA256 = "0" * 64
GENESIS_TS = "-infinity"
GENESIS_ID = ""
DEFAULT_BATCH_ROWS = 200
MAX_BATCH_ROWS = 1000
DEFAULT_SETTLE_SECONDS = 180
DEFAULT_MAX_BATCHES = 20
MIN_REMAINING_MS = 20_000
CONFLICT_CODES = ("PreconditionFailed", "ConditionalRequestConflict")
METRIC_NAMESPACE = "Meridian/AuditExport"

PHRASE_READ = "Audit export failed: the Aurora read was refused."
PHRASE_WRITE = "Audit export failed: the bucket write was refused."
PHRASE_STATE = "Audit export failed: the latest export object is unreadable or inconsistent."
PHRASE_ROW = "Audit export failed: one audit row is too large to read."
PHRASE_RUN = "Audit export failed for at least one table."


class ExportError(RuntimeError):
    """A run step failed; the message is one of the fixed phrases above."""


@dataclass(frozen=True)
class AuditTable:
    """One audit table and the columns its rows are ordered by.

    Attributes:
        name: The table, read as meridian_audit_export.
        id_column: The primary key, the tie-breaker after the timestamp.
        ts_column: The column the database sets when it inserts the row.
        ts_type: ``timestamp`` or ``timestamptz``, the column's type.
    """

    name: str
    id_column: str
    ts_column: str
    ts_type: str

    @property
    def now_expression(self) -> str:
        """The database clock in the column's type, never in a session's time zone.

        A timestamp without time zone holds the writer's local time, which migration 033 and the
        live tests pin to UTC, so the clock it is compared with is UTC by name.
        """
        if self.ts_type == "timestamp":
            return "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"
        return "CURRENT_TIMESTAMP"

    @property
    def sql(self) -> str:
        """The read: rows strictly after the watermark, old enough to have settled."""
        ts, ident, now = self.ts_column, self.id_column, self.now_expression
        return (
            f"SELECT {ts}::text AS wm_ts, {ident}::text AS wm_id, "
            f"to_jsonb(t)::text AS row_json, "
            f"EXTRACT(EPOCH FROM ({now} - {ts}))::text AS age_seconds "
            f"FROM {self.name} AS t "
            f"WHERE ({ts}, {ident}) > (CAST(:wm_ts AS {self.ts_type}), CAST(:wm_id AS TEXT)) "
            f"AND {ts} <= {now} - CAST(:settle AS integer) * INTERVAL '1 second' "
            f"ORDER BY {ts}, {ident} LIMIT :batch"
        )


TABLES = (
    AuditTable("agent_audit_log", "audit_id", "ran_at", "timestamp"),
    AuditTable("traveler_access_audit", "audit_id", "decided_at", "timestamptz"),
    AuditTable("booking_action_audit", "action_audit_id", "recorded_at", "timestamptz"),
)


@dataclass(frozen=True)
class Settings:
    """The function's environment."""

    cluster_arn: str
    secret_arn: str
    database: str
    bucket: str
    batch_rows: int
    settle_seconds: int
    max_batches: int

    @classmethod
    def from_env(cls) -> "Settings":
        """Read the environment; a missing name or a bad number stops the run."""
        batch_rows = int(os.environ.get("AUDIT_EXPORT_BATCH_ROWS", DEFAULT_BATCH_ROWS))
        if not 1 <= batch_rows <= MAX_BATCH_ROWS:
            raise ValueError(f"AUDIT_EXPORT_BATCH_ROWS must be 1 to {MAX_BATCH_ROWS}")
        return cls(
            cluster_arn=os.environ["AURORA_CLUSTER_ARN"],
            secret_arn=os.environ["AURORA_SECRET_ARN"],
            database=os.environ["AURORA_DATABASE"],
            bucket=os.environ["AUDIT_EXPORT_BUCKET"],
            batch_rows=batch_rows,
            settle_seconds=int(os.environ.get(
                "AUDIT_EXPORT_SETTLE_SECONDS", DEFAULT_SETTLE_SECONDS)),
            max_batches=int(os.environ.get("AUDIT_EXPORT_MAX_BATCHES", DEFAULT_MAX_BATCHES)),
        )


@dataclass(frozen=True)
class ChainHead:
    """Where a table's chain stands: the last object and the last row it holds."""

    seq: int
    sha256: str
    wm_ts: str
    wm_id: str


GENESIS = ChainHead(0, GENESIS_SHA256, GENESIS_TS, GENESIS_ID)


@dataclass(frozen=True)
class NewObject:
    """An object ready to write, with the head it moves the chain to."""

    table: str
    body: bytes
    head: ChainHead
    prev_sha256: str


@dataclass
class TableResult:
    """What one table's run did."""

    table: str
    rows: int = 0
    objects: int = 0
    backlog_age_seconds: float = 0.0
    conflict: bool = False
    head: Optional[ChainHead] = None


def canonical(value: Any) -> str:
    """The one JSON text a line is written and compared in."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def object_key(table: str, seq: int) -> str:
    """The key of object ``seq``; sorting keys as text sorts them by sequence."""
    return f"{KEY_PREFIX}/{table}/{seq // OBJECTS_PER_GROUP:08d}/{seq:012d}.jsonl"


def _error_code(error: Exception) -> str:
    if isinstance(error, ClientError):
        return error.response.get("Error", {}).get("Code") or "ClientError"
    return type(error).__name__


def _last_group_prefix(s3, bucket: str, table: str) -> Optional[str]:
    """The highest group prefix under the table, or None when nothing was exported."""
    last = None
    token = None
    while True:
        args = {"Bucket": bucket, "Prefix": f"{KEY_PREFIX}/{table}/", "Delimiter": "/"}
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        for common in page.get("CommonPrefixes", []):
            last = common["Prefix"]
        token = page.get("NextContinuationToken")
        if not token:
            return last


def _last_key_in(s3, bucket: str, prefix: str) -> Optional[str]:
    """The highest key under one group prefix."""
    last = None
    token = None
    while True:
        args = {"Bucket": bucket, "Prefix": prefix}
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        for item in page.get("Contents", []):
            last = item["Key"]
        token = page.get("NextContinuationToken")
        if not token:
            return last


def read_head(s3, bucket: str, table: str) -> ChainHead:
    """Derive the chain's position from the latest object, or GENESIS for an empty chain."""
    group = _last_group_prefix(s3, bucket, table)
    key = _last_key_in(s3, bucket, group) if group else None
    if key is None:
        return GENESIS
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        header = json.loads(body.split(b"\n", 1)[0])
        seq = int(header["seq"])
        valid = (header["type"] == "header" and header["table"] == table
                 and key == object_key(table, seq))
        last = header["last"]
        head = ChainHead(seq, hashlib.sha256(body).hexdigest(), last["ts"], last["id"])
    except (ValueError, KeyError, TypeError):
        raise ExportError(PHRASE_STATE) from None
    if not valid:
        raise ExportError(PHRASE_STATE)
    return head


def build_object(table: str, head: ChainHead, rows: List[Dict[str, str]],
                 exported_at: str) -> NewObject:
    """Build the next object: a header line, then one line per row."""
    seq = head.seq + 1
    first, last = rows[0], rows[-1]
    header = {
        "type": "header", "format": FORMAT_VERSION, "table": table, "seq": seq,
        "prev_sha256": head.sha256, "row_count": len(rows), "exported_at": exported_at,
        "first": {"ts": first["wm_ts"], "id": first["wm_id"]},
        "last": {"ts": last["wm_ts"], "id": last["wm_id"]},
    }
    lines = [canonical(header)]
    for row in rows:
        lines.append(canonical({
            "type": "row", "ts": row["wm_ts"], "id": row["wm_id"],
            "row": json.loads(row["row_json"]),
        }))
    body = ("\n".join(lines) + "\n").encode()
    new_head = ChainHead(seq, hashlib.sha256(body).hexdigest(), last["wm_ts"], last["wm_id"])
    return NewObject(table, body, new_head, head.sha256)


def put_object(s3, bucket: str, new: NewObject) -> bool:
    """Write the object once. Returns False when another writer already holds that key."""
    digest = hashlib.sha256(new.body).digest()
    try:
        s3.put_object(
            Bucket=bucket, Key=object_key(new.table, new.head.seq), Body=new.body,
            ContentType="application/x-ndjson", IfNoneMatch="*",
            ChecksumAlgorithm="SHA256", ChecksumSHA256=base64.b64encode(digest).decode(),
            Metadata={"table": new.table, "seq": str(new.head.seq),
                      "prev-sha256": new.prev_sha256, "sha256": new.head.sha256,
                      "last-ts": new.head.wm_ts, "last-id": new.head.wm_id},
        )
    except ClientError as error:
        if _error_code(error) in CONFLICT_CODES:
            return False
        LOGGER.error("Bucket write failed: %s", _error_code(error))
        raise ExportError(PHRASE_WRITE) from None
    except BotoCoreError as error:
        LOGGER.error("Bucket write failed: %s", _error_code(error))
        raise ExportError(PHRASE_WRITE) from None
    return True


def _query(rds, settings: Settings, table: AuditTable, head: ChainHead, size: int):
    response = rds.execute_statement(
        resourceArn=settings.cluster_arn, secretArn=settings.secret_arn,
        database=settings.database, sql=table.sql, formatRecordsAs="JSON",
        parameters=[
            {"name": "wm_ts", "value": {"stringValue": head.wm_ts}},
            {"name": "wm_id", "value": {"stringValue": head.wm_id}},
            {"name": "settle", "value": {"longValue": settings.settle_seconds}},
            {"name": "batch", "value": {"longValue": size}},
        ],
    )
    return json.loads(response.get("formattedRecords") or "[]")


def _looks_oversize(error: ClientError) -> bool:
    text = str(error.response.get("Error", {}).get("Message", "")).lower()
    return error.response.get("Error", {}).get("Code") == "BadRequestException" and (
        "size" in text or "large" in text)


def fetch_rows(rds, settings: Settings, table: AuditTable, head: ChainHead,
               size: int) -> Tuple[List[Dict[str, str]], int]:
    """Read the next rows. A response over the Data API limit halves the batch and retries.

    Returns the rows and the batch size that worked, so later batches keep it.
    """
    while True:
        try:
            return _query(rds, settings, table, head, size), size
        except ClientError as error:
            if not _looks_oversize(error):
                LOGGER.error("Aurora read failed: %s", _error_code(error))
                raise ExportError(PHRASE_READ) from None
            if size == 1:
                LOGGER.error("Aurora read failed: a single row exceeds the response limit")
                raise ExportError(PHRASE_ROW) from None
            size = max(1, size // 2)
        except (BotoCoreError, ValueError) as error:
            LOGGER.error("Aurora read failed: %s", _error_code(error))
            raise ExportError(PHRASE_READ) from None


def export_table(rds, s3, settings: Settings, table: AuditTable,
                 time_left_ms: Callable[[], int]) -> TableResult:
    """Export a table's new rows as chained objects, until caught up or out of budget."""
    result = TableResult(table.name)
    try:
        head = read_head(s3, settings.bucket, table.name)
    except (ClientError, BotoCoreError) as error:
        LOGGER.error("Reading the chain head failed: %s", _error_code(error))
        raise ExportError(PHRASE_STATE) from None
    size = settings.batch_rows
    for _ in range(settings.max_batches):
        if time_left_ms() < MIN_REMAINING_MS:
            break
        rows, size = fetch_rows(rds, settings, table, head, size)
        if not rows:
            break
        exported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new = build_object(table.name, head, rows, exported_at)
        if not put_object(s3, settings.bucket, new):
            LOGGER.warning("Another writer holds object %s of %s", new.head.seq, table.name)
            result.conflict = True
            break
        result.rows += len(rows)
        result.objects += 1
        result.backlog_age_seconds = float(rows[-1]["age_seconds"]) if len(rows) == size else 0.0
        head = new.head
    result.head = head
    return result


def emit_metrics(results: List[TableResult], completed: bool) -> None:
    """Log one CloudWatch embedded-metric line, and each table's head as an external anchor."""
    for result in results:
        if result.head and result.objects:
            LOGGER.info(canonical({
                "event": "audit_export_head", "table": result.table,
                "seq": result.head.seq, "sha256": result.head.sha256,
            }))
    LOGGER.info(canonical({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE, "Dimensions": [[]],
                "Metrics": [
                    {"Name": "RowsExported", "Unit": "Count"},
                    {"Name": "ObjectsWritten", "Unit": "Count"},
                    {"Name": "BacklogAgeSeconds", "Unit": "Seconds"},
                    {"Name": "RunsCompleted", "Unit": "Count"},
                ],
            }],
        },
        "RowsExported": sum(r.rows for r in results),
        "ObjectsWritten": sum(r.objects for r in results),
        "BacklogAgeSeconds": max((r.backlog_age_seconds for r in results), default=0.0),
        "RunsCompleted": 1 if completed else 0,
    }))


def _clients():
    config = Config(connect_timeout=3, read_timeout=30,
                    retries={"mode": "standard", "total_max_attempts": 3})
    return boto3.client("rds-data", config=config), boto3.client("s3", config=config)


def lambda_handler(_event: dict, context) -> dict:
    """Export every audit table. One table's failure does not stop the others, but fails the run."""
    LOGGER.info(canonical({"event": "audit_export_start", "boto3": boto3.__version__}))
    settings = Settings.from_env()
    rds, s3 = _clients()
    results: List[TableResult] = []
    failed = False
    for table in TABLES:
        try:
            results.append(export_table(rds, s3, settings, table,
                                        context.get_remaining_time_in_millis))
        except ExportError as error:
            LOGGER.error("%s: %s", table.name, error)
            failed = True
    emit_metrics(results, completed=not failed)
    if failed:
        raise ExportError(PHRASE_RUN)
    return {"rows": sum(r.rows for r in results), "objects": sum(r.objects for r in results)}
