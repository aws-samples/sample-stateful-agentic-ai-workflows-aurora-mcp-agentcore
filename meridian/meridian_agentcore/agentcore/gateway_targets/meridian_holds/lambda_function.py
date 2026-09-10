"""Gateway target: get_package_details, create_courtesy_hold and confirm_booking.

The gateway passes tool arguments as the event and the tool name in the client
context. Before any traveler-scoped write, this function authorizes its own
execution role against traveler_identity_bindings, records the decision in
traveler_access_audit, pins the RLS scope and steps down to meridian_app, all
inside one Data API transaction. The hold itself is the SQL function
create_courtesy_hold from migration 008, so a retried tool call replays the
same booking instead of taking a second one. Confirmation is confirm_booking
from migration 010: it books catalog inventory in this database, takes no
payment, and a retry reports the original confirmation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

PARAMETERS = (
    "/meridian/aurora/cluster_arn",
    "/meridian/aurora/secret_arn",
    "/meridian/aurora/database",
)
APP_ROLE = "meridian_app"
AGENT_TYPE = "concierge_agent"
HOLD_BACKEND = "gateway"
PACKAGE_SQL = (
    "SELECT package_id, name, operator, destination, region, price_per_person, "
    "durations, availability, highlights FROM trip_packages WHERE package_id = :package_id"
)
BINDING_SQL = (
    "SELECT binding_id FROM traveler_identity_bindings WHERE identity_provider = :provider "
    "AND subject_id = :subject AND traveler_id = :traveler AND status = 'active' "
    "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP) LIMIT 1"
)
AUDIT_SQL = (
    "INSERT INTO traveler_access_audit (audit_id, identity_provider, subject_id, principal, "
    "requested_traveler_id, decision, reason) VALUES (:audit_id, :provider, :subject, "
    ":principal, :traveler, :decision, :reason)"
)
HOLD_SQL = (
    "SELECT booking_id, status, replayed, seats_available, seats_reserved, seats_remaining "
    "FROM create_courtesy_hold(:booking_id::TEXT, :traveler::TEXT, :journey::TEXT, "
    ":request_id::TEXT, :fingerprint::TEXT, :package_id::TEXT, :duration::TEXT, "
    ":quantity::INTEGER, :unit_price::NUMERIC, :total::NUMERIC, :expires_at::TIMESTAMPTZ)"
)
CONFIRM_SQL = (
    "SELECT booking_id, status, replayed, confirmed_at::TEXT AS confirmed_at, "
    "hold_expires_at::TEXT AS hold_expires_at, total_amount::TEXT AS total_amount "
    "FROM confirm_booking(:booking_id::TEXT, :traveler::TEXT, :total::NUMERIC)"
)
BOOKING_RECEIPT_SQL = (
    "SELECT b.status, b.created_at::TIMESTAMPTZ::TEXT AS created_at, "
    "b.confirmed_at::TIMESTAMPTZ::TEXT AS confirmed_at, "
    "b.hold_expires_at::TEXT AS hold_expires_at, b.total_amount::TEXT AS total_amount, "
    "bl.package_id, bl.duration, bl.travelers_count, CURRENT_TIMESTAMP::TEXT AS observed_at "
    "FROM bookings b LEFT JOIN booking_lines bl ON bl.booking_id = b.booking_id "
    "WHERE b.booking_id = :b AND b.traveler_id = :t"
)
BUSINESS_ERRORS = (
    "execution_lease_lost",
    "insufficient_inventory",
    "invalid_package_inventory",
    "journey_not_owned",
    "traveler_scope_mismatch",
    "booking_agent_not_authorized",
    "invalid_hold_quantity",
    "hold_request_parameter_mismatch",
    "booking_not_found",
    "booking_amount_mismatch",
    "booking_not_held",
    "hold_expired",
)


@dataclass(frozen=True)
class AuroraConfig:
    cluster_arn: str
    secret_arn: str
    database: str


CONFIG: AuroraConfig | None = None
SUBJECT: tuple[str, str, str] | None = None
RDS = None


def _load_config() -> AuroraConfig:
    response = boto3.client("ssm").get_parameters(Names=list(PARAMETERS))
    values = {p["Name"]: p["Value"] for p in response.get("Parameters", [])}
    missing = [name for name in PARAMETERS if name not in values]
    if missing:
        raise RuntimeError(
            f"Missing SSM parameters {missing}; run scripts/publish_gateway_parameters.py"
        )
    return AuroraConfig(values[PARAMETERS[0]], values[PARAMETERS[1]], values[PARAMETERS[2]])


def _load_subject() -> tuple[str, str, str]:
    caller = boto3.client("sts").get_caller_identity()
    return "aws_iam", caller.get("UserId", "").split(":", 1)[0], caller.get("Arn", "unknown")


def _boot() -> None:
    global CONFIG, SUBJECT, RDS
    if CONFIG is None:
        CONFIG = _load_config()
    if SUBJECT is None:
        SUBJECT = _load_subject()
    if RDS is None:
        RDS = boto3.client("rds-data")


def _parameters(values: dict) -> list[dict]:
    params = []
    for key, value in values.items():
        if isinstance(value, bool):
            field = {"booleanValue": value}
        elif isinstance(value, int):
            field = {"longValue": value}
        else:
            field = {"stringValue": str(value)}
        params.append({"name": key, "value": field})
    return params


def query(sql: str, values: dict | None = None, tx: str | None = None) -> list[dict]:
    kwargs = {
        "resourceArn": CONFIG.cluster_arn,
        "secretArn": CONFIG.secret_arn,
        "database": CONFIG.database,
        "sql": sql,
        "formatRecordsAs": "JSON",
        "parameters": _parameters(values or {}),
    }
    if tx:
        kwargs["transactionId"] = tx
    response = RDS.execute_statement(**kwargs)
    rows = json.loads(response.get("formattedRecords") or "[]")
    for row in rows:
        for key in ("durations", "availability", "highlights"):
            if isinstance(row.get(key), str):
                row[key] = json.loads(row[key])
    return rows


def _transaction(operation: str, tx: str) -> None:
    method = getattr(RDS, f"{operation}_transaction")
    method(resourceArn=CONFIG.cluster_arn, secretArn=CONFIG.secret_arn, transactionId=tx)


def normalize_terms(package_id: str, duration: str, quantity: int, unit_price: Decimal) -> dict:
    """The same canonical form as backend/agents/orchestration_05/hold_intent.py."""
    price = Decimal(unit_price).quantize(Decimal("0.01"))
    return {
        "package_id": package_id.strip().lower(),
        "duration": " ".join(duration.split()).lower(),
        "quantity": int(quantity),
        "unit_price": str(price),
        "total_amount": str(price * int(quantity)),
    }


def fingerprint(terms: dict) -> str:
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def get_package_details(args: dict) -> dict:
    package_id = str(args.get("packageId", "")).strip()
    rows = query(PACKAGE_SQL, {"package_id": package_id})
    if not rows:
        return {"error": f"Unknown package {package_id}"}
    package = rows[0]
    availability = package.get("availability") or {}
    open_slots = ", ".join(f"{k}: {v} places" for k, v in availability.items())
    return {
        "package": package,
        "summary": f"{package.get('name')} · {open_slots or 'no published durations'}",
    }


def _authorize(traveler_id: str, tx: str) -> dict:
    provider, subject_id, principal = SUBJECT
    binding = query(
        BINDING_SQL, {"provider": provider, "subject": subject_id, "traveler": traveler_id}, tx
    )
    allowed = bool(binding)
    decision = "allow" if allowed else "deny"
    query(AUDIT_SQL, {
        "audit_id": f"authz_{uuid.uuid4().hex[:12]}",
        "provider": provider,
        "subject": subject_id,
        "principal": principal,
        "traveler": traveler_id,
        "decision": decision,
        "reason": "active identity binding" if allowed else "no active identity binding",
    }, tx)
    return {"allowed": allowed, "decision": decision, "subject": subject_id, "principal": principal}


def _scope(traveler_id: str, tx: str) -> None:
    query(
        "SELECT set_config('app.current_traveler_id', :traveler, true)",
        {"traveler": traveler_id},
        tx,
    )
    query("SELECT set_config('app.agent_type', :agent, true)", {"agent": AGENT_TYPE}, tx)
    query(f"SET LOCAL ROLE {APP_ROLE}", None, tx)


def _journey(traveler_id: str, journey_ref: str, tx: str) -> str:
    query("SELECT pg_advisory_xact_lock(hashtextextended(:thread, 0))", {"thread": journey_ref}, tx)
    rows = query(
        "SELECT journey_id FROM journey_threads WHERE thread_id = :thread", {"thread": journey_ref}, tx
    )
    if rows:
        return str(rows[0]["journey_id"])
    journey_id = f"jrn_{uuid.uuid4().hex[:12]}"
    query(
        "INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) VALUES (:j, :t, :b)",
        {"j": journey_id, "t": traveler_id, "b": HOLD_BACKEND},
        tx,
    )
    query(
        "INSERT INTO journey_threads (thread_id, journey_id) VALUES (:thread, :j) "
        "ON CONFLICT (thread_id) DO NOTHING",
        {"thread": journey_ref, "j": journey_id},
        tx,
    )
    query(
        "UPDATE journeys SET active_thread_id = :thread, updated_at = CURRENT_TIMESTAMP "
        "WHERE journey_id = :j",
        {"thread": journey_ref, "j": journey_id},
        tx,
    )
    return journey_id


def _hold_terms(args: dict) -> dict:
    """Terms, identity and fingerprint for the hold.

    A caller that checkpointed its own intent (the Phase 5 workflow) passes
    ``holdRequestId`` and ``bookingId`` so a resumed run replays the same
    booking; otherwise both derive from the journey and the terms.
    """
    unit_price = Decimal(int(args["unitPriceCents"])) / Decimal(100)
    terms = normalize_terms(
        str(args["packageId"]), str(args["duration"]), int(args["travelers"]), unit_price
    )
    key = f"{args['journeyRef']}|{terms['package_id']}|{terms['duration']}|{terms['quantity']}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return {
        "terms": terms,
        "unit_price": str(unit_price.quantize(Decimal("0.01"))),
        "request_id": str(args.get("holdRequestId") or f"hrq_{digest}"),
        "fingerprint": fingerprint(terms),
        "booking_id": str(args.get("bookingId") or f"HLD-{uuid.uuid4().hex[:8].upper()}"),
    }


def _claim_execution(execution_id: str, tx: str) -> None:
    """A workflow worker must still own its lease when the hold is written."""
    rows = query(
        "SELECT execution_id FROM journey_executions WHERE execution_id = :e "
        "AND status = 'running' AND lease_expires_at > CURRENT_TIMESTAMP FOR UPDATE",
        {"e": execution_id},
        tx,
    )
    if not rows:
        raise RuntimeError("execution_lease_lost")
    query("SELECT set_config('app.execution_id', :e, true)", {"e": execution_id}, tx)


def _receipt(booking_id: str, traveler_id: str, tx: str) -> dict:
    """The persisted receipt, so a replay reports the original expiry, not a new one."""
    rows = query(
        "SELECT status, created_at::TIMESTAMPTZ::TEXT AS created_at, "
        "hold_expires_at::TEXT AS hold_expires_at, CURRENT_TIMESTAMP::TEXT AS observed_at "
        "FROM bookings WHERE booking_id = :b AND traveler_id = :t",
        {"b": booking_id, "t": traveler_id},
        tx,
    )
    if not rows or not rows[0].get("hold_expires_at"):
        raise RuntimeError("persisted hold receipt unavailable")
    return rows[0]


def _hold_row(args: dict, hold: dict, journey_id: str, expires_at: datetime, tx: str) -> dict:
    rows = query(HOLD_SQL, {
        "booking_id": hold["booking_id"],
        "traveler": str(args["travelerId"]),
        "journey": journey_id,
        "request_id": hold["request_id"],
        "fingerprint": hold["fingerprint"],
        "package_id": str(args["packageId"]).strip(),
        "duration": " ".join(str(args["duration"]).split()),
        "quantity": int(args["travelers"]),
        "unit_price": hold["unit_price"],
        "total": hold["terms"]["total_amount"],
        "expires_at": expires_at.isoformat(),
    }, tx)
    return rows[0]


def create_courtesy_hold(args: dict) -> dict:
    traveler_id = str(args["travelerId"])
    hold = _hold_terms(args)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=int(args["holdMinutes"]))
    tx = RDS.begin_transaction(
        resourceArn=CONFIG.cluster_arn, secretArn=CONFIG.secret_arn, database=CONFIG.database
    )["transactionId"]
    try:
        governance = _authorize(traveler_id, tx)
        if not governance["allowed"]:
            _transaction("rollback", tx)
            _record_denied_audit(governance, traveler_id)
            return {"error": "traveler_not_authorized", "governance": governance}
        _scope(traveler_id, tx)
        journey_ref = str(args["journeyRef"])
        query("SELECT set_config('app.thread_id', :t, true)", {"t": journey_ref}, tx)
        if args.get("executionId"):
            _claim_execution(str(args["executionId"]), tx)
        journey_id = _journey(traveler_id, journey_ref, tx)
        row = _hold_row(args, hold, journey_id, expires_at, tx)
        receipt = _receipt(str(row["booking_id"]), traveler_id, tx)
        _transaction("commit", tx)
    except Exception as error:  # noqa: BLE001 - the SQL function raises named business errors
        _transaction("rollback", tx)
        return {"error": _named_error(error)}
    result = {
        "bookingId": row["booking_id"],
        "status": str(receipt["status"]),
        "replayed": bool(row["replayed"]),
        "journeyId": journey_id,
        "holdRequestId": hold["request_id"],
        "expiresAt": str(receipt["hold_expires_at"]),
        "createdAt": str(receipt["created_at"]),
        "observedAt": str(receipt["observed_at"]),
        "seatsAvailable": row.get("seats_available"),
        "seatsRemaining": row.get("seats_remaining"),
        "totalAmount": hold["terms"]["total_amount"],
        "packageId": str(args["packageId"]).strip(),
        "duration": " ".join(str(args["duration"]).split()),
        "travelers": int(args["travelers"]),
    }
    verb = "Replayed" if result["replayed"] else "Held"
    return {
        "hold": result,
        "governance": governance,
        "summary": (
            f"{verb} {result['packageId']} ({result['duration']}) for {result['travelers']} "
            f"traveler(s) as {result['bookingId']}, expires {result['expiresAt']}"
        ),
    }


def _booking_receipt(booking_id: str, traveler_id: str, tx: str) -> dict:
    """The persisted booking and its line, read under the traveler's scope after confirmation."""
    rows = query(BOOKING_RECEIPT_SQL, {"b": booking_id, "t": traveler_id}, tx)
    if not rows or rows[0].get("status") != "confirmed":
        raise RuntimeError("persisted booking receipt unavailable")
    return rows[0]


def confirm_booking(args: dict) -> dict:
    """Confirm the held booking the traveler approved. Cedar decided on the arguments already."""
    traveler_id = str(args["travelerId"])
    booking_id = str(args["bookingId"]).strip()
    total = str((Decimal(int(args["totalCents"])) / Decimal(100)).quantize(Decimal("0.01")))
    tx = RDS.begin_transaction(
        resourceArn=CONFIG.cluster_arn, secretArn=CONFIG.secret_arn, database=CONFIG.database
    )["transactionId"]
    try:
        governance = _authorize(traveler_id, tx)
        if not governance["allowed"]:
            _transaction("rollback", tx)
            _record_denied_audit(governance, traveler_id)
            return {"error": "traveler_not_authorized", "governance": governance}
        _scope(traveler_id, tx)
        query(
            "SELECT set_config('app.thread_id', :t, true)", {"t": str(args["journeyRef"])}, tx
        )
        row = query(
            CONFIRM_SQL, {"booking_id": booking_id, "traveler": traveler_id, "total": total}, tx
        )[0]
        receipt = _booking_receipt(booking_id, traveler_id, tx)
        _transaction("commit", tx)
    except Exception as error:  # noqa: BLE001 - the SQL function raises named business errors
        _transaction("rollback", tx)
        return {"error": _named_error(error)}
    result = {
        "bookingId": booking_id,
        "status": str(receipt["status"]),
        "replayed": bool(row["replayed"]),
        "confirmedAt": str(receipt["confirmed_at"]),
        "createdAt": str(receipt["created_at"]),
        "expiresAt": str(receipt["hold_expires_at"]),
        "observedAt": str(receipt["observed_at"]),
        "totalAmount": str(receipt["total_amount"]),
        "packageId": receipt.get("package_id"),
        "duration": receipt.get("duration"),
        "travelers": receipt.get("travelers_count"),
    }
    verb = "Already confirmed" if result["replayed"] else "Confirmed"
    return {
        "booking": result,
        "governance": governance,
        "summary": (
            f"{verb} booking {booking_id} for {result['packageId']} ({result['duration']}, "
            f"{result['travelers']} traveler(s)), total ${result['totalAmount']}, "
            f"confirmed {result['confirmedAt']}"
        ),
    }


def _record_denied_audit(governance: dict, traveler_id: str) -> None:
    """A DENY is evidence: keep the audit row even though the hold transaction rolled back."""
    provider, subject_id, principal = SUBJECT
    query(AUDIT_SQL, {
        "audit_id": f"authz_{uuid.uuid4().hex[:12]}",
        "provider": provider,
        "subject": subject_id,
        "principal": principal,
        "traveler": traveler_id,
        "decision": "deny",
        "reason": "no active identity binding",
    })


def _named_error(error: Exception) -> str:
    text = str(error)
    for name in BUSINESS_ERRORS:
        if name in text:
            return name
    return text[:300]


TOOLS = {
    "get_package_details": get_package_details,
    "create_courtesy_hold": create_courtesy_hold,
    "confirm_booking": confirm_booking,
}


def lambda_handler(event, context):
    _boot()
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    tool = custom.get("bedrockAgentCoreToolName", "")
    name = tool.split("___")[-1]
    if name not in TOOLS:
        raise ValueError(f"Unknown Meridian tool: {tool or '(none)'}")
    result = TOOLS[name](event or {})
    print(json.dumps({
        "meridian_tool": name,
        "ok": "error" not in result,
        "gatewayRequestId": custom.get("bedrockAgentCoreAwsRequestId"),
    }), flush=True)
    return result
