"""Check the audit export in S3 for edits, deletions and breaks in its hash chain.

Read-only by design: it lists and reads objects, and with --compare-aurora it reads the audit
tables as the meridian_audit_export login (SELECT only). It writes nothing to S3 or Aurora and
has no flag that could. It still calls AWS, so it needs the owner's approval for that run.

What it checks, per table:
1. every object key has one version and no delete marker (a second version or a marker means a
   write over the key or a delete was attempted);
2. sequence numbers run 1, 2, 3 without a gap;
3. each header names its table and sequence, and its prev_sha256 equals the SHA-256 of the
   previous object's bytes;
4. each header's row count and first and last row match the lines below it, and no row id
   repeats in the chain;
5. the object's recorded sha256 metadata matches its bytes;
6. with --head TABLE=SEQ:SHA256, the latest object is the one you recorded elsewhere. The chain
   alone cannot show that the newest objects were removed, so keep that value off this bucket;
7. with --compare-aurora, every exported row is still in Aurora and unchanged (an edit or a
   deletion made after the export), and no Aurora row at or before the export's watermark is
   missing from it. Rows after the watermark are only counted as pending.

Exit codes: 0 verified, 1 findings, 2 could not run or read, 3 usage error.

Usage:
    python scripts/verify_audit_export.py --bucket NAME --dry-run
    python scripts/verify_audit_export.py --bucket NAME [--table agent_audit_log] \
        [--compare-aurora] [--head agent_audit_log=12:<sha256>]

--bucket defaults to AUDIT_EXPORT_BUCKET. --compare-aurora needs AURORA_CLUSTER_ARN and
AURORA_AUDIT_EXPORT_SECRET_ARN in meridian/.env. Run from meridian/ with AWS_PROFILE set.
"""

import hashlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.identity_release.usage import UsageParser  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / "infra" / "functions" / "audit_export" / "audit_export.py"
SEQ_IN_KEY = re.compile(r"/(\d{12})\.jsonl$")
HEAD_ARG = re.compile(r"^(\w+)=(\d+):([0-9a-f]{64})$")
EXIT_OK, EXIT_FINDINGS, EXIT_FAILED, EXIT_USAGE = 0, 1, 2, 3
COMPARE_BATCH_ROWS = 200


def load_exporter():
    """The exporter module, loaded by path so the tables, SQL and chain format are shared."""
    spec = importlib.util.spec_from_file_location("audit_export_for_verifier", EXPORTER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Finding:
    """One thing the verifier found wrong."""

    table: str
    code: str
    detail: str


@dataclass
class Walk:
    """The chain of one table, as read from S3."""

    table: str
    objects: int = 0
    rows: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    head_seq: int = 0
    head_sha256: str = ""
    watermark_ts: str = ""
    findings: List[Finding] = field(default_factory=list)


def redact(text: str) -> str:
    """Hide every 12-digit account id in ``text``."""
    return re.sub(r"(?<!\d)\d{12}(?!\d)", "<acct>", text)


def parse_ts(text: str) -> datetime:
    """Parse a PostgreSQL timestamp text, with or without an offset."""
    return datetime.fromisoformat(text)


def list_versions(s3, bucket: str, prefix: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """Return each key's version ids (newest first) and the keys that carry a delete marker."""
    versions: Dict[str, List[str]] = {}
    markers: List[str] = []
    args = {"Bucket": bucket, "Prefix": prefix}
    while True:
        page = s3.list_object_versions(**args)
        for item in page.get("Versions", []):
            versions.setdefault(item["Key"], []).append(item["VersionId"])
        markers.extend(item["Key"] for item in page.get("DeleteMarkers", []))
        if not page.get("IsTruncated"):
            return versions, markers
        args["KeyMarker"] = page["NextKeyMarker"]
        args["VersionIdMarker"] = page["NextVersionIdMarker"]


def parse_object(body: bytes) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Split an object into its header and its row lines; a malformed one raises ValueError."""
    parsed = [json.loads(line) for line in body.decode().splitlines()]
    if not parsed or parsed[0].get("type") != "header":
        raise ValueError("no header line")
    if any(line.get("type") != "row" for line in parsed[1:]):
        raise ValueError("a line after the header is not a row")
    return parsed[0], parsed[1:]


def check_object(walk: Walk, key: str, header: Dict[str, Any],
                 rows: List[Dict[str, Any]], previous: str) -> None:
    """Check one object's header against its key, its rows and the previous object's hash."""
    seq = int(SEQ_IN_KEY.search(key).group(1))
    problems = []
    if header.get("table") != walk.table or header.get("seq") != seq:
        problems.append(("header-mismatch",
                         f"seq {seq}: header names another table or sequence"))
    if header.get("prev_sha256") != previous:
        problems.append(("chain-broken",
                         f"seq {seq}: prev_sha256 is not the hash of seq {seq - 1}"))
    if header.get("row_count") != len(rows):
        problems.append(("row-count", f"seq {seq}: header says {header.get('row_count')} rows, "
                                      f"object holds {len(rows)}"))
    if rows and ({"ts": rows[0]["ts"], "id": rows[0]["id"]} != header.get("first")
                 or {"ts": rows[-1]["ts"], "id": rows[-1]["id"]} != header.get("last")):
        problems.append(("range-mismatch", f"seq {seq}: first or last row differs from header"))
    walk.findings.extend(Finding(walk.table, code, detail) for code, detail in problems)


def _record_rows(walk: Walk, seq: int, rows: List[Dict[str, Any]]) -> None:
    for row in rows:
        if row["id"] in walk.rows:
            walk.findings.append(Finding(walk.table, "duplicate-row",
                                         f"seq {seq}: row {row['id']} was exported before"))
        walk.rows[row["id"]] = {"ts": row["ts"], "row": row["row"]}


def _check_key_state(walk: Walk, versions: Dict[str, List[str]], markers: List[str]) -> None:
    for key in sorted(set(markers)):
        walk.findings.append(Finding(walk.table, "delete-marker", f"{key} has a delete marker"))
    for key, ids in sorted(versions.items()):
        if len(ids) > 1:
            walk.findings.append(Finding(walk.table, "extra-version",
                                         f"{key} has {len(ids)} versions"))


def walk_chain(s3, bucket: str, table: str, prefix: str) -> Walk:
    """Read every object of the table in sequence order and check the chain."""
    walk = Walk(table)
    versions, markers = list_versions(s3, bucket, f"{prefix}/{table}/")
    _check_key_state(walk, versions, markers)
    previous, expected = "0" * 64, 1
    for key in sorted(versions):
        seq = int(SEQ_IN_KEY.search(key).group(1))
        if seq != expected:
            walk.findings.append(Finding(
                table, "chain-gap", f"expected seq {expected}, found {seq}"))
        expected = seq + 1
        response = s3.get_object(Bucket=bucket, Key=key, VersionId=versions[key][0])
        body = response["Body"].read()
        digest = hashlib.sha256(body).hexdigest()
        recorded = response.get("Metadata", {}).get("sha256")
        if recorded and recorded != digest:
            walk.findings.append(Finding(table, "metadata-mismatch",
                                         f"seq {seq}: recorded sha256 differs from the bytes"))
        try:
            header, rows = parse_object(body)
        except ValueError as error:
            walk.findings.append(Finding(table, "object-malformed", f"seq {seq}: {error}"))
        else:
            check_object(walk, key, header, rows, previous)
            _record_rows(walk, seq, rows)
            walk.watermark_ts = header["last"]["ts"]
        previous, walk.head_seq, walk.head_sha256 = digest, seq, digest
        walk.objects += 1
    return walk


def check_expected_head(walk: Walk, expected: Optional[Tuple[int, str]]) -> None:
    """Compare the chain's latest object with a head recorded elsewhere."""
    if expected and (walk.head_seq, walk.head_sha256) != expected:
        walk.findings.append(Finding(
            walk.table, "head-mismatch",
            f"latest object is seq {walk.head_seq}, not the recorded seq {expected[0]}"
            " with that hash"))


def aurora_rows(rds, exporter, settings, table) -> List[Dict[str, str]]:
    """Every row of the table now, in export order, read as the audit export login."""
    head = exporter.GENESIS
    out: List[Dict[str, str]] = []
    while True:
        rows, _ = exporter.fetch_rows(rds, settings, table, head, COMPARE_BATCH_ROWS)
        if not rows:
            return out
        out.extend(rows)
        head = exporter.ChainHead(0, "", rows[-1]["wm_ts"], rows[-1]["wm_id"])


def differing_columns(exported: Dict[str, Any], current: Dict[str, Any]) -> List[str]:
    """Columns whose value differs; a column added after the export reads as null in it."""
    return sorted(k for k in set(exported) | set(current) if exported.get(k) != current.get(k))


def compare_with_aurora(walk: Walk, rows: List[Dict[str, str]]) -> Tuple[List[Finding], int]:
    """Find exported rows that were edited or deleted, and rows missing behind the watermark.

    Returns the findings and the count of rows after the watermark, which are only pending.
    """
    findings: List[Finding] = []
    current = {row["wm_id"]: row for row in rows}
    for ident, exported in walk.rows.items():
        now = current.get(ident)
        if now is None:
            findings.append(Finding(walk.table, "row-deleted",
                                    f"row {ident} was exported and is not in Aurora"))
            continue
        columns = differing_columns(exported["row"], json.loads(now["row_json"]))
        if columns:
            findings.append(Finding(walk.table, "row-edited",
                                    f"row {ident} changed after export: {', '.join(columns)}"))
    pending = 0
    for ident, now in current.items():
        if ident in walk.rows:
            continue
        if walk.watermark_ts and parse_ts(now["wm_ts"]) < parse_ts(walk.watermark_ts):
            findings.append(Finding(walk.table, "row-unexported",
                                    f"row {ident} is behind the watermark and not exported"))
        else:
            pending += 1
    return findings, pending


def parse_heads(values: List[str]) -> Dict[str, Tuple[int, str]]:
    """Read repeated --head TABLE=SEQ:SHA256 values."""
    heads = {}
    for value in values:
        match = HEAD_ARG.match(value)
        if not match:
            raise ValueError(f"--head must look like TABLE=SEQ:SHA256, got {value!r}")
        heads[match.group(1)] = (int(match.group(2)), match.group(3))
    return heads


def build_parser() -> UsageParser:
    """The command line."""
    parser = UsageParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bucket", default=os.environ.get("AUDIT_EXPORT_BUCKET"),
                        help="the export bucket (default: AUDIT_EXPORT_BUCKET)")
    parser.add_argument("--table", action="append", default=[],
                        help="a table to check; repeat for several (default: all)")
    parser.add_argument("--compare-aurora", action="store_true",
                        help="also read the tables as the audit export login and compare")
    parser.add_argument("--head", action="append", default=[], metavar="TABLE=SEQ:SHA256",
                        help="a chain head recorded elsewhere; the latest object must match it")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be read and call nothing")
    return parser


def _plan(args, exporter, tables) -> None:
    print(f"bucket: {redact(args.bucket)}")
    print(f"tables: {', '.join(t.name for t in tables)}")
    print("reads:  S3 ListObjectVersions and GetObject on the export prefix")
    if args.compare_aurora:
        print("reads:  Aurora SELECT through the Data API as the audit export login")
    print(f"expected heads: {len(args.head)}")
    print("dry run: nothing was called; run again without --dry-run after approval")


def _aurora_clients(exporter):
    load_dotenv(ROOT / ".env")
    cluster = os.environ.get("AURORA_CLUSTER_ARN")
    secret = os.environ.get("AURORA_AUDIT_EXPORT_SECRET_ARN")
    if not cluster or not secret:
        raise SystemExit("--compare-aurora needs AURORA_CLUSTER_ARN and "
                         "AURORA_AUDIT_EXPORT_SECRET_ARN in meridian/.env")
    settings = exporter.Settings(
        cluster_arn=cluster, secret_arn=secret, bucket="", batch_rows=COMPARE_BATCH_ROWS,
        database=os.environ.get("AURORA_DATABASE", "meridian"), settle_seconds=0, max_batches=1)
    return boto3.client("rds-data", region_name=cluster.split(":")[3]), settings


def verify(args, exporter, tables) -> List[Finding]:
    """Walk each table's chain and, when asked, compare it with Aurora. Prints a summary."""
    s3 = boto3.client("s3")
    heads = parse_heads(args.head)
    rds, settings = _aurora_clients(exporter) if args.compare_aurora else (None, None)
    findings: List[Finding] = []
    for table in tables:
        walk = walk_chain(s3, args.bucket, table.name, exporter.KEY_PREFIX)
        check_expected_head(walk, heads.get(table.name))
        pending = None
        if rds:
            extra, pending = compare_with_aurora(
                walk, aurora_rows(rds, exporter, settings, table))
            walk.findings.extend(extra)
        suffix = f", {pending} pending" if pending is not None else ""
        print(f"{table.name}: {walk.objects} objects, {len(walk.rows)} rows{suffix}, "
              f"{len(walk.findings)} findings")
        findings.extend(walk.findings)
    return findings


def main(argv: Optional[List[str]] = None) -> int:
    """Run the verification and return the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    exporter = load_exporter()
    names = {t.name: t for t in exporter.TABLES}
    unknown = [name for name in args.table if name not in names]
    if unknown or not args.bucket:
        parser.error(f"unknown table {unknown}" if unknown else "--bucket is required")
    try:
        parse_heads(args.head)
    except ValueError as error:
        parser.error(str(error))
    tables = [names[n] for n in args.table] or list(exporter.TABLES)
    if args.dry_run:
        _plan(args, exporter, tables)
        return EXIT_OK
    try:
        findings = verify(args, exporter, tables)
    except (ClientError, BotoCoreError) as error:
        code = error.response.get("Error", {}).get("Code", "unknown") if isinstance(
            error, ClientError) else type(error).__name__
        print(f"could not read the export ({code}); check AWS_PROFILE and the profile's "
              "S3, KMS and Data API read permissions", file=sys.stderr)
        return EXIT_FAILED
    except exporter.ExportError as error:
        print(f"could not read Aurora: {error}", file=sys.stderr)
        return EXIT_FAILED
    for finding in findings:
        print(f"FINDING {finding.table} {finding.code}: {redact(finding.detail)}")
    return EXIT_FINDINGS if findings else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
