"""Gateway target: get_package_details, loyalty_balance, create_courtesy_hold, confirm_booking
and release_hold.

The gateway passes tool arguments as the event and the tool name in the client
context. Before any traveler-scoped write, this function authorizes its own
execution role against traveler_identity_bindings, records the decision in
traveler_access_audit, pins the RLS scope and steps down to meridian_app, all
inside one Data API transaction. The hold itself is the SQL function
create_courtesy_hold from migration 008, so a retried tool call replays the
same booking instead of taking a second one. Confirmation is confirm_booking
from migration 010: it books catalog inventory in this database, takes no
payment, and a retry reports the original confirmation. loyalty_balance reads the
traveler's own saved loyalty programs under the same grant and scope.

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
import math
import os
import re
import ssl
import time
import uuid
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from botocore.config import Config
from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError, SSLError

PARAMETERS = (
    "/meridian/aurora/cluster_arn",
    "/meridian/aurora/secret_arn",
    "/meridian/aurora/database",
)
APP_ROLE = "meridian_app"
WRITE_ROLE = "meridian_gateway"
AGENT_TYPE = "concierge_agent"
HOLD_BACKEND = "gateway"
INVOCATION = ContextVar("meridian_hold_invocation", default=None)
ACTION_AUDIT_SQL = """
INSERT INTO booking_action_audit (
    action_audit_id, traveler_id, booking_id, journey_id, hold_request_id,
    execution_id, operation, replayed, identity_provider, verified_subject,
    workload_subject, access_audit_id, authorization_ref, gateway_request_id, lambda_request_id
) VALUES (
    :action_id, :traveler, :booking_id::TEXT,
    (SELECT journey_id FROM hold_requests WHERE booking_id = :booking_id::TEXT),
    (SELECT hold_request_id FROM hold_requests WHERE booking_id = :booking_id::TEXT),
    :execution_id, :operation, :replayed, :provider, :verified_subject,
    :workload, :access_audit_id, :authorization_ref, :gateway_request_id, :lambda_request_id
)
"""
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
    "WHERE traveler_id = :traveler ORDER BY confidence DESC NULLS LAST, last_seen_at DESC LIMIT 50"
)
LOYALTY_SQL = "SELECT loyalty_programs FROM traveler_profiles WHERE traveler_id = :traveler"
BOOKED_PARTY_SQL = (
    "SELECT bl.travelers_count, b.status FROM booking_lines bl "
    "JOIN bookings b ON b.booking_id = bl.booking_id "
    "WHERE bl.booking_id = :b AND b.traveler_id = :t"
)
MAX_REQUEST_CHAIN = 50
# One attempt in all: a re-sent statement or commit would run twice inside one Data API
# transaction. The read timeout stays under the Lambda's 30 s timeout.
DATA_API_CONFIG = Config(
    retries={"mode": "standard", "total_max_attempts": 1}, connect_timeout=5, read_timeout=25
)
# A call that provably never reached the Data API is sent again after these pauses: no
# connection, a connect timeout, or a TLS session that ended before the request went out (an
# SSLError caused by ssl.SSLEOFError; Python reports an EOF while reading a reply as a closed
# connection). Nothing else is repeated. The same rule as never_sent in
# backend/db/rds_data_client.py, repeated here because this function deploys alone;
# tests/test_never_sent_parity.py holds both to one table of errors.
UNSENT_RETRY_DELAYS = (0.2, 0.4)
# The ceiling derivation in backend/agents/budget.py, repeated here because this
# function deploys alone. tests/test_budget_parity.py runs one table of cases against both.
BUDGET_KEYS = ("budget_cap", "budget", "budget_ceiling", "budget_range", "per_person_range")
NUMBER = re.compile(r"(\d+(?:\.\d+)?)(k\b)?", re.I)
THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d{3}\b)")
US_PREFIX = re.compile(r"(?<![A-Za-z])US\s?\$", re.I)
# Another currency, written as a symbol, an ISO code or a word, makes a budget unclear.
# "won" and "real" are also English words, so they count only straight after a figure.
OTHER_CURRENCY = re.compile(
    r"[A-Za-z]\$|[\u20a0-\u20bf\u00a2-\u00a5]"
    r"|\b(?:EUR|GBP|JPY|CNY|CAD|AUD|NZD|CHF|INR|MXN|BRL|SGD|HKD|KRW|SEK|NOK|DKK|AED|ZAR"
    r"|PLN|THB|TWD|CZK|HUF|ILS|IDR|MYR|VND|PHP)\b"
    r"|\b(?:euros?|pounds?|sterling|yen|yuan|renminbi|rupees?|rupiah|pesos?|baht|francs?"
    r"|reais|rand|liras?|lire|krona|kronor|krone|kroner|kronur|zlotys?|zloty|forints?"
    r"|shekels?|dirhams?|ringgit|dong|rubles?|roubles?|dinars?|riyals?|rials?"
    r"|(?:canadian|australian|new zealand|hong kong|singapore|singaporean|taiwan|taiwanese"
    r"|mexican|nz|hk)\s+dollars?)\b"
    r"|(?:(?<=\d)|(?<=\dk))\s*(?:won|reals?)\b",
    re.I,
)
# ".5k" is not read as 5k, and nothing is read from a figure that no budget could be.
LEADING_DOT = re.compile(r"(?<!\d)\.\d")
MAX_BUDGET_USD = 10_000_000
USD_SUFFIX = re.compile(r"\s*USD\b", re.I)
USD_BEFORE = re.compile(r"(?<![A-Za-z])USD\s*$", re.I)
RANGE_JOIN = re.compile(
    r"\s*(?:USD)?\s*(?:-|\u2013|\u2014|to)\s*\$?\s*(?:USD)?\s*", re.I
)
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
OWNED_HOLD_SQL = (
    "SELECT hr.booking_id, b.status FROM hold_requests hr "
    "JOIN bookings b ON b.booking_id = hr.booking_id "
    "WHERE hr.journey_id = :journey AND hr.hold_request_id = :request_id "
    "AND b.traveler_id = :traveler"
)
RELEASE_SQL = (
    "SELECT booking_id, replayed FROM release_courtesy_hold("
    ":journey::TEXT, :request_id::TEXT, :traveler::TEXT)"
)
BUSINESS_ERRORS = (
    "caller_identity_missing",
    "execution_lease_lost",
    "execution_lease_held",
    "insufficient_inventory",
    "invalid_package_inventory",
    "journey_not_owned",
    "traveler_scope_mismatch",
    "booking_agent_not_authorized",
    "invalid_hold_quantity",
    "invalid_hold_minutes",
    "hold_request_parameter_mismatch",
    "booking_not_found",
    "booking_amount_mismatch",
    "booking_not_held",
    "hold_expired",
    "hold_price_mismatch",
    "hold_total_mismatch",
    "budget_ceiling_exceeded",
    "budget_unclear",
    "hold_not_found",
)


# The Cedar permit for create_courtesy_hold bounds both (agentcore.template.json); the Lambda
# repeats the bounds so a caller that skips the Gateway schema and policy cannot exceed them.
MAX_HOLD_MINUTES = 720
MAX_HOLD_TRAVELERS = 6


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
        RDS = boto3.client("rds-data", config=DATA_API_CONFIG)


def _parameters(values: dict) -> list[dict]:
    params = []
    for key, value in values.items():
        if value is None:
            field = {"isNull": True}
        elif isinstance(value, bool):
            field = {"booleanValue": value}
        elif isinstance(value, int):
            field = {"longValue": value}
        else:
            field = {"stringValue": str(value)}
        params.append({"name": key, "value": field})
    return params


def _ended_by_tls_eof(error: BaseException) -> bool:
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLEOFError):
            return True
        linked = [current.__cause__, current.__context__, *current.args,
                  *(getattr(current, "kwargs", None) or {}).values()]
        pending.extend(item for item in linked if isinstance(item, BaseException))
    return False


def _never_sent(error: BaseException) -> bool:
    """Whether the request provably never reached the Data API, so a repeat cannot run twice."""
    if isinstance(error, (EndpointConnectionError, ConnectTimeoutError)):
        return True
    return isinstance(error, SSLError) and _ended_by_tls_eof(error)


def _unless_sent(method, **kwargs):
    """Call a Data API method, sending it again only when the request never arrived.

    Safe inside a transaction and for a commit: a request that never arrived ran nothing.
    """
    for delay in UNSENT_RETRY_DELAYS:
        try:
            return method(**kwargs)
        except (EndpointConnectionError, ConnectTimeoutError, SSLError) as error:
            if not _never_sent(error):
                raise
            time.sleep(delay)
    return method(**kwargs)


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
    response = _unless_sent(RDS.execute_statement, **kwargs)
    rows = json.loads(response.get("formattedRecords") or "[]")
    for row in rows:
        for key in ("durations", "availability", "highlights", "loyalty_programs"):
            if isinstance(row.get(key), str):
                row[key] = json.loads(row[key])
    return rows


def _transaction(operation: str, tx: str) -> None:
    method = getattr(RDS, f"{operation}_transaction")
    _unless_sent(method, resourceArn=CONFIG.cluster_arn, secretArn=CONFIG.secret_arn, transactionId=tx)


def _business_query(statement: str, values: dict, tx: str) -> list[dict]:
    """Use the Gateway's EXECUTE grant, then return to the scoped read role.

    The common app role cannot mutate business tables or call these functions.
    Only this service login can assume WRITE_ROLE; scope stays transaction-local.
    A SQL error aborts the transaction and its caller rolls back the role change.
    """
    query(f"SET LOCAL ROLE {WRITE_ROLE}", None, tx)
    rows = query(statement, values, tx)
    query(f"SET LOCAL ROLE {APP_ROLE}", None, tx)
    return rows


class ActionOutcomeUnknown(RuntimeError):
    """The commit was sent, but its acknowledgement did not establish the result."""


def _commit_action(tx: str, kind: str) -> None:
    try:
        _transaction("commit", tx)
    except Exception as error:
        # A later rollback cannot undo a commit the server has already accepted.
        raise ActionOutcomeUnknown(f"{kind}_outcome_unknown") from error


def _begin() -> str:
    return _unless_sent(
        RDS.begin_transaction,
        resourceArn=CONFIG.cluster_arn, secretArn=CONFIG.secret_arn, database=CONFIG.database,
    )["transactionId"]


def _rollback_quietly(tx: str) -> None:
    """Roll back, tolerating a transaction that already ended; the caller still answers."""
    try:
        _transaction("rollback", tx)
    except Exception as error:  # noqa: BLE001 - a second rollback must never mask the answer
        print(json.dumps({
            "meridian_tool": "rollback", "ok": False, "error_type": type(error).__name__,
        }), flush=True)


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
        return {"error": "unknown_package", "package_id": package_id}
    package = rows[0]
    availability = package.get("availability") or {}
    open_slots = ", ".join(f"{k}: {v} places left" for k, v in availability.items())
    return {
        "package": package,
        "summary": f"{package.get('name')}, {open_slots or 'no published durations'}",
    }


def _check_traveler_grant(traveler_id: str, tx: str) -> dict:
    provider, subject_id, principal = SUBJECT
    binding = query(
        BINDING_SQL, {"provider": provider, "subject": subject_id, "traveler": traveler_id}, tx
    )
    allowed = bool(binding)
    decision = "allow" if allowed else "deny"
    audit_id = f"authz_{uuid.uuid4().hex[:12]}"
    query(AUDIT_SQL, {
        "audit_id": audit_id,
        "provider": provider,
        "subject": subject_id,
        "principal": principal,
        "traveler": traveler_id,
        "decision": decision,
        "reason": "active identity binding" if allowed else "no active identity binding",
    }, tx)
    return {
        "allowed": allowed,
        "decision": decision,
        "subject": subject_id,
        "principal": ACCOUNT.sub("<acct>", principal),
        "access_audit_id": audit_id,
    }


def _record_action(args: dict, operation: str, row: dict, governance: dict, tx: str) -> None:
    """Commit attribution with the action, including when the worker loses its reply."""
    context = INVOCATION.get() or {}
    action_id = f"act_{uuid.uuid4().hex}"
    _business_query(ACTION_AUDIT_SQL, {
        "action_id": action_id, "traveler": str(args["travelerId"]),
        "booking_id": str(row["booking_id"]), "execution_id": args.get("executionId"),
        "operation": operation, "replayed": bool(row["replayed"]),
        "provider": "cognito" if context.get("verified_subject") else "aws_iam",
        "verified_subject": context.get("verified_subject"), "workload": SUBJECT[1],
        "access_audit_id": governance["access_audit_id"],
        "authorization_ref": args.get("authorizationRef"),
        "gateway_request_id": context.get("gateway_request_id"),
        "lambda_request_id": context.get("lambda_request_id"),
    }, tx)
    governance.update(action_audit_id=action_id,
                      gateway_request_id=context.get("gateway_request_id"))


def _pin_traveler_scope(traveler_id: str, tx: str) -> None:
    query(
        "SELECT set_config('app.current_traveler_id', :traveler, true)",
        {"traveler": traveler_id},
        tx,
    )
    query("SELECT set_config('app.agent_type', :agent, true)", {"agent": AGENT_TYPE}, tx)
    query(f"SET LOCAL ROLE {APP_ROLE}", None, tx)


def _thread_journey(journey_ref: str, tx: str) -> str | None:
    """The journey this thread already belongs to, or None. Serialized per thread."""
    query("SELECT pg_advisory_xact_lock(hashtextextended(:thread, 0))", {"thread": journey_ref}, tx)
    rows = query(
        "SELECT journey_id FROM journey_threads WHERE thread_id = :thread", {"thread": journey_ref}, tx
    )
    return str(rows[0]["journey_id"]) if rows else None


def _journey(traveler_id: str, journey_ref: str, tx: str) -> str:
    existing = _thread_journey(journey_ref, tx)
    if existing:
        return existing
    journey_id = f"jrn_{uuid.uuid4().hex[:12]}"
    query(
        "INSERT INTO journeys (journey_id, traveler_id, snapshot_store) VALUES (:j, :t, :b)",
        {"j": journey_id, "t": traveler_id, "b": HOLD_BACKEND},
        tx,
    )
    claimed = query(
        "INSERT INTO journey_threads (thread_id, journey_id) VALUES (:thread, :j) "
        "ON CONFLICT (thread_id) DO NOTHING RETURNING thread_id",
        {"thread": journey_ref, "j": journey_id},
        tx,
    )
    if not claimed:
        raise RuntimeError("journey_not_owned")
    query(
        "UPDATE journeys SET active_thread_id = :thread, updated_at = CURRENT_TIMESTAMP "
        "WHERE journey_id = :j",
        {"thread": journey_ref, "j": journey_id},
        tx,
    )
    return journey_id


def _hold_terms(args: dict) -> dict:
    """Terms, identity and fingerprint for the hold.

    A caller that saved its own intent (the Phase 5 workflow) passes
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


def _is_marked(text: str, match: re.Match) -> bool:
    """Whether a figure carries ``$`` or ``USD`` before or after it."""
    before = text[:match.start()]
    return (
        before.rstrip().endswith("$")
        or USD_BEFORE.search(before) is not None
        or USD_SUFFIX.match(text, match.end()) is not None
    )


def _spread_marks(marked: list[bool], joined: list[bool]) -> list[bool]:
    """A figure joined to a marked one is marked too: the later figure of ``$100 to 200``."""
    spread = list(marked)
    for i in range(1, len(spread)):
        spread[i] = spread[i] or (joined[i - 1] and spread[i - 1])
    for i in reversed(range(len(spread) - 1)):
        spread[i] = spread[i] or (joined[i] and spread[i + 1])
    return spread


def parse_usd_amounts(text: str) -> list[float] | None:
    """The one US dollar amount or one joined range in ``text``, or None when it is not clear.

    Figures that carry ``$``, ``US$`` or ``USD`` (before or after) are the only ones used when
    any exist. Otherwise a single bare figure, or a single bare joined range, counts as dollars.
    A range is two figures joined by ``-``, an en dash or ``to``. A ``k`` suffix means thousands.
    More than one amount or range, a figure of zero, another currency (a symbol, an ISO code
    or a word such as ``yen``), a figure written ``.5k``, a figure above ``MAX_BUDGET_USD`` or
    one too large to be finite, or no figure is None.

    Returns:
        ``[amount]`` or ``[low, high]`` in dollars, or None.
    """
    text = US_PREFIX.sub("$", THOUSANDS_COMMA.sub("", text))
    found = list(NUMBER.finditer(text))
    if not found or OTHER_CURRENCY.search(text) or LEADING_DOT.search(text):
        return None
    joined = [
        RANGE_JOIN.fullmatch(text[a.end():b.start()]) is not None
        for a, b in zip(found, found[1:], strict=False)
    ]
    marked = [_is_marked(text, match) for match in found]
    chosen = _spread_marks(marked, joined) if any(marked) else [True] * len(found)
    picked = [i for i, keep in enumerate(chosen) if keep]
    amounts = [
        float(found[i].group(1)) * (1000 if found[i].group(2) else 1) for i in picked
    ]
    one_range = len(picked) == 2 and picked[1] == picked[0] + 1 and joined[picked[0]]
    if min(amounts) <= 0 or not (len(picked) == 1 or one_range):
        return None
    if not all(math.isfinite(amount) and amount <= MAX_BUDGET_USD for amount in amounts):
        return None
    return amounts


def _first_budget_text(facts: list[dict]) -> str | None:
    """The value of the highest-priority budget fact present, or None when there is none."""
    by_key: dict[str, str] = {}
    for fact in facts:
        by_key.setdefault(str(fact.get("key", "")).lower(), str(fact.get("value", "")))
    return next((by_key[key] for key in BUDGET_KEYS if key in by_key), None)


def _cap_cents(text: str) -> int | None:
    """The largest US dollar figure in ``text`` in cents, or None when it is not clear.

    A non-finite figure is not clear, so no caller can raise on it.
    """
    amounts = parse_usd_amounts(text)
    if not amounts or not math.isfinite(max(amounts)):
        return None
    return int(round(max(amounts) * 100))


def budget_is_unclear(facts: list[dict]) -> bool:
    """The first budget fact present has no unambiguous US dollar amount.

    A lower-priority fact or the default never stands in for it.
    """
    text = _first_budget_text(facts)
    return text is not None and _cap_cents(text) is None


def saved_ceiling_cents(facts: list[dict], travelers: int) -> int:
    """The whole-party ceiling the traveler's saved budget allows, in cents.

    Args:
        facts: Preference facts as ``{"key": ..., "value": ...}``, highest confidence first.
        travelers: Party size; the saved budget is per person.

    Returns:
        The largest US dollar figure in the first budget fact present, times the party; ``0``
        when that fact is unclear. Without a budget fact,
        ``MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS`` for the whole trip.
    """
    text = _first_budget_text(facts)
    if text is None:
        return int(os.getenv(DEFAULT_BUDGET_ENV, str(DEFAULT_BUDGET_CENTS)))
    cap = _cap_cents(text)
    return cap * max(1, int(travelers)) if cap is not None else 0


def _within_saved_budget(traveler_id: str, total_cents: int, travelers: int, tx: str) -> None:
    """Refuse a total above the traveler's saved budget, whatever ceiling the caller sent.

    A saved budget with no unambiguous US dollar amount is refused as ``budget_unclear``.
    """
    rows = query(PREFERENCES_SQL, {"traveler": traveler_id}, tx)
    facts = [{"key": row["preference_key"], "value": row["preference_value"]} for row in rows]
    if budget_is_unclear(facts):
        raise RuntimeError("budget_unclear")
    if total_cents > saved_ceiling_cents(facts, travelers):
        raise RuntimeError("budget_ceiling_exceeded")


def _is_replay(journey_id: str, hold: dict, tx: str) -> bool:
    """This journey already committed a request with this id and these exact terms."""
    rows = query(
        REQUEST_FINGERPRINT_SQL, {"journey": journey_id, "request_id": hold["request_id"]}, tx
    )
    return bool(rows) and rows[0]["fingerprint"] == hold["fingerprint"]


def _claim_execution(execution_id: str, tx: str, journey_id: str | None = None) -> str:
    """A workflow worker must still own its lease when the hold is written.

    Locks the execution row and returns the journey it belongs to. When the call names a
    journey, the execution must belong to that journey too; a live lease on another journey is
    refused as ``journey_not_owned``.
    """
    rows = query(
        "SELECT execution_id, journey_id FROM journey_executions WHERE execution_id = :e "
        "AND status = 'running' AND lease_expires_at > CURRENT_TIMESTAMP FOR UPDATE",
        {"e": execution_id},
        tx,
    )
    if not rows:
        raise RuntimeError("execution_lease_lost")
    owner = str(rows[0].get("journey_id"))
    if journey_id is not None and owner != journey_id:
        raise RuntimeError("journey_not_owned")
    query("SELECT set_config('app.execution_id', :e, true)", {"e": execution_id}, tx)
    return owner


def _refuse_live_lease(journey_id: str, tx: str) -> None:
    """A release that names no execution must not run while a worker owns the journey.

    A worker that is still running may be about to reuse the hold, so only a release with its
    own ``executionId`` (checked under the lease lock) may act then. Once the run ended, or its
    lease lapsed, a release without one is allowed, as the presenter's cleanup scripts do.
    Runs under the traveler's row security scope, like the lease claim.
    """
    rows = query(
        "SELECT execution_id FROM journey_executions WHERE journey_id = :j "
        "AND status = 'running' AND lease_expires_at > CURRENT_TIMESTAMP LIMIT 1",
        {"j": journey_id},
        tx,
    )
    if rows:
        raise RuntimeError("execution_lease_held")


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
    the same new request and later retries replay it. A caller-supplied
    ``holdRequestId`` is never advanced: its replay reports the booking it made.
    """
    for _ in range(MAX_REQUEST_CHAIN):
        rows = query(HOLD_REQUEST_SQL, {"journey": journey_id, "request_id": request_id}, tx)
        if not rows or not _is_dead_request(rows[0]):
            return request_id
        request_id = _next_request_id(request_id, str(rows[0]["booking_id"]))
    raise RuntimeError(f"hold request chain exceeds {MAX_REQUEST_CHAIN} dead requests")


def _hold_row(args: dict, hold: dict, journey_id: str, expires_at: datetime, tx: str) -> dict:
    rows = _business_query(HOLD_SQL, {
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


def _hold_journey(args: dict, traveler_id: str, journey_ref: str, tx: str) -> str:
    """The journey the hold belongs to, with a worker's lease bound to it.

    A call with an ``executionId`` is a workflow worker, whose journey already exists. The
    execution row is locked first, as the workflow does, then the thread's journey is resolved
    without creating one: an unknown thread, or a journey other than the execution's, is
    ``journey_not_owned``; a lapsed lease is ``execution_lease_lost``. A call without one
    (Phase 4) resolves or creates its journey as before.
    """
    if not args.get("executionId"):
        return _journey(traveler_id, journey_ref, tx)
    owner = _claim_execution(str(args["executionId"]), tx)
    journey_id = _thread_journey(journey_ref, tx)
    if journey_id is None or journey_id != owner:
        raise RuntimeError("journey_not_owned")
    return journey_id


def _held_row(args: dict, hold: dict, expires_at: datetime, tx: str) -> tuple[str, dict, dict]:
    """Inside the authorized transaction: reconcile a committed request, or check and hold.

    Returns the journey id, the hold row and the persisted receipt.
    """
    traveler_id = str(args["travelerId"])
    catalog_cents = _catalog_unit_price_cents(args, tx)
    _pin_traveler_scope(traveler_id, tx)
    journey_ref = str(args["journeyRef"])
    query("SELECT set_config('app.thread_id', :t, true)", {"t": journey_ref}, tx)
    journey_id = _hold_journey(args, traveler_id, journey_ref, tx)
    if not args.get("holdRequestId"):
        hold["request_id"] = _live_request_id(hold["request_id"], journey_id, tx)
    if not _is_replay(journey_id, hold, tx):
        _verify_catalog_terms(args, catalog_cents)
        _within_saved_budget(traveler_id, int(args["totalCents"]), int(args["travelers"]), tx)
    row = _hold_row(args, hold, journey_id, expires_at, tx)
    return journey_id, row, _receipt(str(row["booking_id"]), traveler_id, tx)


def _hold_limit_error(args: dict) -> str | None:
    """The error code when the hold's duration or party size is outside the permitted range."""
    minutes, travelers = args.get("holdMinutes"), args.get("travelers")
    if not _is_whole_number(minutes) or not 1 <= minutes <= MAX_HOLD_MINUTES:
        return "invalid_hold_minutes"
    if not _is_whole_number(travelers) or not 1 <= travelers <= MAX_HOLD_TRAVELERS:
        return "invalid_hold_quantity"
    return None


def _is_whole_number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def create_courtesy_hold(args: dict) -> dict:
    limit_error = _hold_limit_error(args)
    if limit_error:
        return {"error": limit_error}
    traveler_id = str(args["travelerId"])
    hold = _hold_terms(args)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=int(args["holdMinutes"]))
    tx = _begin()
    try:
        governance = _check_traveler_grant(traveler_id, tx)
        if not governance["allowed"]:
            _transaction("rollback", tx)
            return _grant_refusal(governance, traveler_id)
        journey_id, row, receipt = _held_row(args, hold, expires_at, tx)
        _record_action(args, "hold", row, governance, tx)
        _commit_action(tx, "hold")
    except Exception as error:  # noqa: BLE001 - the SQL function raises named business errors
        _rollback_quietly(tx)
        return _failure("create_courtesy_hold", error, "hold_failed")
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
    tx = _begin()
    try:
        governance = _check_traveler_grant(traveler_id, tx)
        if not governance["allowed"]:
            _transaction("rollback", tx)
            return _grant_refusal(governance, traveler_id)
        _pin_traveler_scope(traveler_id, tx)
        query(
            "SELECT set_config('app.thread_id', :t, true)", {"t": str(args["journeyRef"])}, tx
        )
        _confirmation_within_saved_budget(args, traveler_id, booking_id, tx)
        row = _business_query(
            CONFIRM_SQL, {"booking_id": booking_id, "traveler": traveler_id, "total": total}, tx
        )[0]
        receipt = _booking_receipt(booking_id, traveler_id, tx)
        _record_action(args, "confirm", row, governance, tx)
        _commit_action(tx, "booking")
    except Exception as error:  # noqa: BLE001 - the SQL function raises named business errors
        _rollback_quietly(tx)
        return _failure("confirm_booking", error, "booking_failed")
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


def _release_row(args: dict, tx: str) -> dict:
    """Inside the authorized transaction: find the traveler's own hold and release it.

    A workflow worker passes its ``executionId``; the lease is then locked and checked in this
    transaction, so a worker whose lease lapsed cannot release a hold another worker took over.
    A release without one is refused as ``execution_lease_held`` while the journey has a running
    execution with an unexpired lease, and allowed once the run ended.
    The ownership read runs under the traveler's row security scope and also names the
    traveler, so another traveler's hold is invisible and reads as ``hold_not_found``. Cedar
    sees only the arguments; this is the check that the hold exists and belongs to the pinned
    traveler.
    """
    traveler_id = str(args["travelerId"])
    _pin_traveler_scope(traveler_id, tx)
    if args.get("executionId"):
        _claim_execution(str(args["executionId"]), tx, str(args["journeyId"]))
    rows = query(OWNED_HOLD_SQL, {
        "journey": str(args["journeyId"]),
        "request_id": str(args["holdRequestId"]),
        "traveler": traveler_id,
    }, tx)
    if not rows:
        raise RuntimeError("hold_not_found")
    booking_id, status = str(rows[0]["booking_id"]), str(rows[0]["status"])
    if status == "released":
        return {"booking_id": booking_id, "replayed": True}
    if status != "held":
        raise RuntimeError("booking_not_held")
    if not args.get("executionId"):
        _refuse_live_lease(str(args["journeyId"]), tx)
    rows = _business_query(RELEASE_SQL, {
        "journey": str(args["journeyId"]), "request_id": str(args["holdRequestId"]),
        "traveler": traveler_id,
    }, tx)
    if not rows:
        raise RuntimeError("booking_not_held")
    return {"booking_id": booking_id, "replayed": bool(rows[0]["replayed"])}


def release_hold(args: dict) -> dict:
    """Give back one hold of the pinned traveler. Cedar decided on the arguments already."""
    traveler_id = str(args["travelerId"])
    tx = _begin()
    try:
        governance = _check_traveler_grant(traveler_id, tx)
        if not governance["allowed"]:
            _transaction("rollback", tx)
            return _grant_refusal(governance, traveler_id)
        released = _release_row(args, tx)
        _record_action(args, "release", released, governance, tx)
        _commit_action(tx, "release")
    except Exception as error:  # noqa: BLE001 - the SQL and the checks raise named errors
        _rollback_quietly(tx)
        return _failure("release_hold", error, "release_failed")
    result = {
        "bookingId": released["booking_id"],
        "status": "released",
        "replayed": released["replayed"],
        "holdRequestId": str(args["holdRequestId"]),
        "journeyId": str(args["journeyId"]),
    }
    verb = "Already released" if result["replayed"] else "Released"
    return {
        "release": result,
        "governance": governance,
        "summary": f"{verb} hold {result['bookingId']}",
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


REFUSAL_MESSAGE = (
    "This traveler is not authorized for this request. Asking again will not change the answer."
)


def _grant_refusal(governance: dict, traveler_id: str) -> dict:
    """The answer to a call this workload may not make for the traveler.

    It names the kept deny row, so a caller can prove the grant refused rather than infer it.
    """
    access_audit_id = _record_denied_audit(traveler_id)
    return {
        "error": "traveler_not_authorized",
        "final": True,
        "message": REFUSAL_MESSAGE,
        "governance": {**governance, "access_audit_id": access_audit_id},
    }


def _business_error(error: Exception) -> str | None:
    text = str(error)
    return next((name for name in BUSINESS_ERRORS if name in text), None)


def _failure(tool: str, error: Exception, fixed_code: str) -> dict:
    """The answer for a raised error: its business name, else ``fixed_code`` and no error text.

    A business answer is expected and adds no log record (the handler's own line records the
    call). Any other failure logs its type and a masked, bounded detail, never in the result.
    """
    business = _business_error(error)
    if business is None:
        print(json.dumps({
            "meridian_tool": tool, "ok": False,
            "error_type": type(error).__name__, "detail": _masked_detail(error),
        }), flush=True)
    code = str(error) if isinstance(error, ActionOutcomeUnknown) else business or fixed_code
    return {"error": code}


ARN = re.compile(r"arn:[a-z0-9-]+:[^\s\"',)]*")
ACCOUNT = re.compile(r"(?<![0-9A-Za-z])\d{12}(?![0-9A-Za-z])")
_TOKEN_CHARS = r"[A-Za-z0-9._~+/=-]"
CREDENTIAL = re.compile(
    r"""((?<![A-Za-z])key|(?:access|secret|private|api|auth|signing)key|"""
    r"""password|secret|token|authorization)("""
    r"""\\?["']\s*[:=]\s*|\s*=\s*|"""
    rf"""\s*:\s*(?=["']?(?:eyJ[\w-]{{5,}}\.[\w-]{{5,}}\.|{_TOKEN_CHARS}{{16,}}(?!{_TOKEN_CHARS}))))"""
    r"""(?!["']?(?:Bearer\b|<masked>|<token>))("[^"]*"|'[^']*'|\S+)""",
    re.IGNORECASE,
)
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
BEARER = re.compile(r"""Bearer:?\s+['"]?[A-Za-z0-9._~+/=-]{8,}""", re.IGNORECASE)


def _mask_credentials(text: str) -> str:
    """Bearer tokens, credentials and JWTs masked, as backend/security/redaction.py does."""
    bearer_masked = BEARER.sub("Bearer <token>", text)
    return JWT.sub("<token>", CREDENTIAL.sub(r"\1\2<masked>", bearer_masked))
DETAIL_CHARS = 120
LOYALTY_FAILED = "loyalty_balance_failed"


def _masked_detail(error: Exception) -> str:
    """The exception text for the log only: ARNs, account ids and credentials masked, cut short."""
    text = _mask_credentials(str(error))
    text = ACCOUNT.sub("<acct>", ARN.sub("<arn>", text))
    return text[:DETAIL_CHARS]


def _loyalty_unavailable(traveler_id: str, program: str) -> dict:
    return {
        "traveler_id": traveler_id,
        "program": program,
        "error": "loyalty_balance_unavailable",
        "reason": "No recorded balance for this program in the traveler's profile.",
        "source": "traveler_profiles.loyalty_programs",
    }


def loyalty_from_programs(traveler_id: str, program: str, programs) -> dict:
    """Pick the requested program from the saved programs. Port of the MCP server's matcher
    (backend/mcp/concierge_server.py); tests/test_holds_loyalty.py keeps the two in step."""
    requested = (program or "").strip().lower()
    if not isinstance(programs, dict):
        return _loyalty_unavailable(traveler_id, program)
    for value in programs.values():
        if not isinstance(value, dict):
            continue
        configured = str(value.get("program") or "")
        if not (requested and configured and value.get("points_balance") is not None):
            continue
        if requested in configured.lower() or configured.lower() in requested:
            return {
                "traveler_id": traveler_id,
                "program": configured,
                "member_id": value.get("member_id"),
                "points_balance": int(value.get("points_balance") or 0),
                "tier": value.get("tier") or "Member",
                "next_tier_threshold": None,
                "points_to_next_tier": 0,
                "source": "traveler_profiles.loyalty_programs",
            }
    return _loyalty_unavailable(traveler_id, program)


def loyalty_balance(args: dict) -> dict:
    """The authorized traveler's own balance, read under this role's grant and the traveler's RLS.

    A traveler this workload has no grant for is refused with ``_grant_refusal``, which keeps the
    deny row and names it, as the hold and the booking do.
    """
    traveler_id = str(args["travelerId"])
    program = str(args.get("program", ""))
    tx = _begin()
    try:
        governance = _check_traveler_grant(traveler_id, tx)
        if not governance["allowed"]:
            _transaction("rollback", tx)
            return _grant_refusal(governance, traveler_id)
        _pin_traveler_scope(traveler_id, tx)
        rows = query(LOYALTY_SQL, {"traveler": traveler_id}, tx)
        _transaction("commit", tx)
    except Exception as error:  # noqa: BLE001 - the caller gets a fixed code, the log the type
        _rollback_quietly(tx)
        return _failure("loyalty_balance", error, LOYALTY_FAILED)
    result = loyalty_from_programs(
        traveler_id, program, (rows[0] if rows else {}).get("loyalty_programs"))
    result["governance"] = governance
    result["summary"] = (
        f"{result['program']}: {result['tier']}, {result['points_balance']} points"
        if "points_balance" in result
        else f"No recorded balance for {program or 'that program'}"
    )
    return result


TOOLS = {
    "get_package_details": get_package_details,
    "loyalty_balance": loyalty_balance,
    "create_courtesy_hold": create_courtesy_hold,
    "confirm_booking": confirm_booking,
    "release_hold": release_hold,
}


def lambda_handler(event, context):
    _boot()
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    tool = custom.get("bedrockAgentCoreToolName", "")
    name = tool.split("___")[-1]
    if name not in TOOLS:
        raise ValueError(f"Unknown Meridian tool: {tool or '(none)'}")
    args = event or {}
    mode = os.getenv("MERIDIAN_AGENTCORE_AUTH", "iam")
    subject = args.get("verifiedSubject") if mode == "jwt" else None
    if mode not in {"iam", "jwt"} or (mode == "jwt" and name != "get_package_details" and (
        not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", subject)
    )):
        return {"error": "caller_identity_missing"}
    token = INVOCATION.set({
        "verified_subject": subject,
        "gateway_request_id": custom.get("bedrockAgentCoreAwsRequestId"),
        "lambda_request_id": getattr(context, "aws_request_id", None),
    })
    try:
        result = TOOLS[name](args)
    finally:
        INVOCATION.reset(token)
    print(json.dumps({
        "meridian_tool": name,
        "ok": "error" not in result,
        "gatewayRequestId": custom.get("bedrockAgentCoreAwsRequestId"),
    }), flush=True)
    return result
