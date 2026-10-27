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

Cedar compares the total with the ``budgetCeilingCents`` the caller sent, which a
caller holding a valid token can set to anything. So a new hold or a confirmation is
also checked here against the ceiling derived from the traveler's saved preferences,
read under the traveler's own row-level security scope. A retry of a request that
already committed is reconciled first and replays without new price or budget checks,
because it reserves nothing new.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
    "durations, package_places_left(package_id) AS availability, highlights FROM trip_packages "
    "WHERE package_id = :package_id"
)
PRICE_SQL = (
    "SELECT ROUND(price_per_person * 100)::BIGINT AS unit_price_cents "
    "FROM trip_packages WHERE package_id = :package_id"
)
HOLD_REQUEST_SQL = (
    "SELECT hr.booking_id, b.status, (b.hold_expires_at <= CURRENT_TIMESTAMP) AS lapsed "
    "FROM hold_requests hr JOIN bookings b ON b.booking_id = hr.booking_id "
    "WHERE hr.journey_id = :journey AND hr.hold_request_id = :request_id"
)
REQUEST_FINGERPRINT_SQL = (
    "SELECT fingerprint FROM hold_requests "
    "WHERE journey_id = :journey AND hold_request_id = :request_id"
)
PREFERENCES_SQL = (
    "SELECT preference_key, preference_value FROM traveler_preferences "
    "WHERE traveler_id = :traveler ORDER BY confidence DESC, last_seen_at DESC LIMIT 50"
)
BOOKED_PARTY_SQL = (
    "SELECT bl.travelers_count, b.status FROM booking_lines bl "
    "JOIN bookings b ON b.booking_id = bl.booking_id "
    "WHERE bl.booking_id = :b AND b.traveler_id = :t"
)
MAX_REQUEST_CHAIN = 50
# The ceiling derivation in backend/agents/budget.py, repeated here because this
# function deploys alone. tests/test_holds_lambda.py keeps the two in step.
BUDGET_KEYS = ("budget_cap", "budget", "budget_ceiling", "budget_range", "per_person_range")
AMOUNT = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*(k)?", re.I)
DEFAULT_BUDGET_ENV = "MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS"
DEFAULT_BUDGET_CENTS = 400000
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
    "hold_price_mismatch",
    "hold_total_mismatch",
    "budget_ceiling_exceeded",
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
    """The same canonical form as backend/agents/phase_05_workflow/hold_intent.py."""
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
    open_slots = ", ".join(f"{k}: {v} places left" for k, v in availability.items())
    return {
        "package": package,
        "summary": f"{package.get('name')}, {open_slots or 'no published durations'}",
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


def _catalog_unit_price_cents(args: dict, tx: str) -> int | None:
    """The catalog price, read before the step-down to meridian_app, which has no catalog grant."""
    rows = query(PRICE_SQL, {"package_id": str(args["packageId"]).strip()}, tx)
    return int(rows[0]["unit_price_cents"]) if rows else None


def _verify_catalog_terms(args: dict, catalog_cents: int | None) -> None:
    """Refuse a hold whose price is not the catalog price or whose total is not price x travelers.

    Cedar bounds ``totalCents`` by the budget ceiling but cannot know the catalog, so a direct
    gateway caller could otherwise book at any price.
    """
    if catalog_cents is None:
        raise RuntimeError("invalid_package_inventory")
    unit_price_cents = int(args["unitPriceCents"])
    if unit_price_cents != catalog_cents:
        raise RuntimeError("hold_price_mismatch")
    if int(args["totalCents"]) != unit_price_cents * int(args["travelers"]):
        raise RuntimeError("hold_total_mismatch")


def _dollars(text: str) -> list[float]:
    values = []
    for number, thousands in AMOUNT.findall(text.replace(",", "")):
        value = float(number) * (1000 if thousands else 1)
        if value >= 100:
            values.append(value)
    return values


def saved_ceiling_cents(facts: list[dict], travelers: int) -> int:
    """The whole-party ceiling the traveler's saved budget allows, in cents.

    Args:
        facts: Preference facts as ``{"key": ..., "value": ...}``, highest confidence first.
        travelers: Party size; the saved budget is per person.

    Returns:
        The largest dollar figure in the first budget fact present, times the party. Without
        a budget fact, ``MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS`` for the whole trip.
    """
    by_key = {str(fact.get("key", "")).lower(): str(fact.get("value", "")) for fact in facts}
    for key in BUDGET_KEYS:
        amounts = _dollars(by_key.get(key, ""))
        if amounts:
            return int(round(max(amounts) * 100)) * max(1, int(travelers))
    return int(os.getenv(DEFAULT_BUDGET_ENV, str(DEFAULT_BUDGET_CENTS)))


def _within_saved_budget(traveler_id: str, total_cents: int, travelers: int, tx: str) -> None:
    """Refuse a total above the traveler's saved budget, whatever ceiling the caller sent."""
    rows = query(PREFERENCES_SQL, {"traveler": traveler_id}, tx)
    facts = [{"key": row["preference_key"], "value": row["preference_value"]} for row in rows]
    if total_cents > saved_ceiling_cents(facts, travelers):
        raise RuntimeError("budget_ceiling_exceeded")


def _is_replay(journey_id: str, hold: dict, tx: str) -> bool:
    """This journey already committed a request with this id and these exact terms."""
    rows = query(
        REQUEST_FINGERPRINT_SQL, {"journey": journey_id, "request_id": hold["request_id"]}, tx
    )
    return bool(rows) and rows[0]["fingerprint"] == hold["fingerprint"]


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
        "hold_expires_at::TEXT AS hold_expires_at, CURRENT_TIMESTAMP::TEXT AS observed_at, "
        "(hold_expires_at <= CURRENT_TIMESTAMP) AS lapsed "
        "FROM bookings WHERE booking_id = :b AND traveler_id = :t",
        {"b": booking_id, "t": traveler_id},
        tx,
    )
    if not rows or not rows[0].get("hold_expires_at"):
        raise RuntimeError("persisted hold receipt unavailable")
    return rows[0]


def _next_request_id(previous: str, booking_id: str) -> str:
    digest = hashlib.sha256(f"{previous}|{booking_id}".encode()).hexdigest()[:12]
    return f"hrq_{digest}"


def _is_dead_request(row: dict) -> bool:
    """A request whose booking can no longer be held: lapsed, released or expired."""
    if row["status"] == "held":
        return bool(row.get("lapsed"))
    return row["status"] != "confirmed"


def _live_request_id(request_id: str, journey_id: str, tx: str) -> str:
    """Advance a derived request id past requests whose booking is dead.

    The default id is a hash of the journey and the terms, so without this a
    second attempt after the first hold lapsed would replay the dead booking
    forever. Each step hashes the previous id with the dead booking id, so
    concurrent retries (serialized by the journey advisory lock) converge on
    the same new request and later retries replay it.
    """
    for _ in range(MAX_REQUEST_CHAIN):
        rows = query(HOLD_REQUEST_SQL, {"journey": journey_id, "request_id": request_id}, tx)
        if not rows or not _is_dead_request(rows[0]):
            return request_id
        request_id = _next_request_id(request_id, str(rows[0]["booking_id"]))
    raise RuntimeError(f"hold request chain exceeds {MAX_REQUEST_CHAIN} dead requests")


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


def _held_row(args: dict, hold: dict, expires_at: datetime, tx: str) -> tuple[str, dict, dict]:
    """Inside the authorized transaction: reconcile a committed request, or check and hold.

    Returns the journey id, the hold row and the persisted receipt.
    """
    traveler_id = str(args["travelerId"])
    catalog_cents = _catalog_unit_price_cents(args, tx)
    _scope(traveler_id, tx)
    journey_ref = str(args["journeyRef"])
    query("SELECT set_config('app.thread_id', :t, true)", {"t": journey_ref}, tx)
    if args.get("executionId"):
        _claim_execution(str(args["executionId"]), tx)
    journey_id = _journey(traveler_id, journey_ref, tx)
    if not args.get("holdRequestId"):
        hold["request_id"] = _live_request_id(hold["request_id"], journey_id, tx)
    if not _is_replay(journey_id, hold, tx):
        _verify_catalog_terms(args, catalog_cents)
        _within_saved_budget(traveler_id, int(args["totalCents"]), int(args["travelers"]), tx)
    row = _hold_row(args, hold, journey_id, expires_at, tx)
    return journey_id, row, _receipt(str(row["booking_id"]), traveler_id, tx)


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
            return _grant_refusal(governance, traveler_id)
        journey_id, row, receipt = _held_row(args, hold, expires_at, tx)
        _transaction("commit", tx)
    except Exception as error:  # noqa: BLE001 - the SQL function raises named business errors
        _transaction("rollback", tx)
        return {"error": _named_error(error)}
    lapsed = receipt["status"] == "held" and bool(receipt.get("lapsed"))
    result = {
        "bookingId": row["booking_id"],
        "status": "expired" if lapsed else str(receipt["status"]),
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


def _confirmation_within_saved_budget(
    args: dict, traveler_id: str, booking_id: str, tx: str
) -> None:
    """Check a first confirmation against the saved budget for the party the hold booked.

    An already confirmed booking replays unchecked: confirming again commits nothing new.
    """
    rows = query(BOOKED_PARTY_SQL, {"b": booking_id, "t": traveler_id}, tx)
    if not rows:
        raise RuntimeError("booking_not_found")
    if rows[0]["status"] != "confirmed":
        _within_saved_budget(
            traveler_id, int(args["totalCents"]), int(rows[0]["travelers_count"]), tx
        )


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
            return _grant_refusal(governance, traveler_id)
        _scope(traveler_id, tx)
        query(
            "SELECT set_config('app.thread_id', :t, true)", {"t": str(args["journeyRef"])}, tx
        )
        _confirmation_within_saved_budget(args, traveler_id, booking_id, tx)
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


def _record_denied_audit(traveler_id: str) -> str:
    """A DENY is evidence: keep the audit row even though the hold transaction rolled back.

    Returns:
        The kept row's ``audit_id``.
    """
    provider, subject_id, principal = SUBJECT
    audit_id = f"authz_{uuid.uuid4().hex[:12]}"
    query(AUDIT_SQL, {
        "audit_id": audit_id,
        "provider": provider,
        "subject": subject_id,
        "principal": principal,
        "traveler": traveler_id,
        "decision": "deny",
        "reason": "no active identity binding",
    })
    return audit_id


def _grant_refusal(governance: dict, traveler_id: str) -> dict:
    """The answer to a call this workload may not make for the traveler.

    It names the kept deny row, so a caller can prove the grant refused rather than infer it.
    """
    access_audit_id = _record_denied_audit(traveler_id)
    return {
        "error": "traveler_not_authorized",
        "governance": {**governance, "access_audit_id": access_audit_id},
    }


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
