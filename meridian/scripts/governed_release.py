"""Give back holds through the governed Gateway release tool; never delete a row.

Every proof, validator and reset in ``scripts/`` that leaves a hold behind uses this module, so
there is one rule for giving a hold back and one place that tests it:

1. The hold is read back first, pinned to its traveler and the booking agent (``bookings``
   forces row level security, so an unpinned read can match nothing without an error).
2. A hold that reads inactive (released, confirmed or expired) needs no call.
3. A second call goes out only after a successful read-back shows the hold still held.
4. Each hold gets at most two calls, counted in a ledger that survives a crash.
5. A state that cannot be read, or a row that cannot be seen, is unknown. Unknown is never
   treated as released.

The booking, hold request, journey and ``booking_action_audit`` rows stay: they are the evidence
of what the run did.
"""

from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from scripts import agentcore_caller
from scripts.agentcore_caller import user_for_traveler

BOOKING_AGENT = "booking_agent"
RELEASE_CALLS_PER_HOLD = 2
RUN_HOLDS_SQL = """
SELECT hr.booking_id, hr.hold_request_id, hr.journey_id
  FROM journey_threads jt
  JOIN hold_requests hr ON hr.journey_id = jt.journey_id
 WHERE jt.thread_id = %s
 ORDER BY hr.booking_id
"""
THREADS_OF_JOURNEY_SQL = "SELECT COUNT(*) AS n FROM journey_threads WHERE journey_id = %s"
JOURNEY_HOLDS_SQL = (
    "SELECT booking_id, hold_request_id, journey_id FROM hold_requests "
    "WHERE journey_id = %s ORDER BY booking_id")
BOOKING_HOLD_SQL = (
    "SELECT booking_id, hold_request_id, journey_id FROM hold_requests WHERE booking_id = %s")
ACTIVE_SQL = ("SELECT (status = 'held' AND hold_expires_at > CURRENT_TIMESTAMP) AS active "
              "FROM bookings WHERE booking_id = %s")

Release = Callable[[str, dict], dict]


@asynccontextmanager
async def pinned_to_traveler(client, traveler_id: str):
    """A Data API transaction pinned to one traveler and the booking agent.

    ``bookings`` and ``booking_lines`` force row level security on these two settings, so a
    statement outside such a transaction can match zero rows without any error. Commits when
    the block ends, rolls back when it raises.
    """
    transaction = client.begin_transaction()
    try:
        await client.execute(
            "SELECT set_config('app.current_traveler_id', %s, true), "
            "set_config('app.agent_type', %s, true)",
            (traveler_id, BOOKING_AGENT), transaction_id=transaction)
        yield transaction
    except BaseException:
        client.rollback_transaction(transaction)
        raise
    client.commit_transaction(transaction)


def final_code(code: int, clean: bool) -> int:
    """The exit code of a run whose cleanup may have failed.

    A run keeps its own result: a failing run stays failing whatever the cleanup did. A passing
    run whose cleanup left a hold held or unknown exits 1.
    """
    return code if clean or code else 1


def cause(exc: BaseException) -> str:
    """A cause that is safe to print: the exception type, never its text."""
    return type(exc).__name__


async def hold_state(client, traveler: str, booking: str) -> tuple[Optional[bool], str]:
    """Whether the hold still reserves places, read pinned to the traveler.

    A row that cannot be seen in that scope is unknown (None), never a released hold.
    """
    try:
        async with pinned_to_traveler(client, traveler) as transaction:
            rows = await client.execute(ACTIVE_SQL, (booking,), transaction_id=transaction)
    except Exception as exc:  # noqa: BLE001 - an unknown state is reported, never assumed
        return None, f"read-back failed ({cause(exc)})"
    if not rows or rows[0]["active"] is None:
        return None, "booking row not visible or its state unknown"
    return bool(rows[0]["active"]), ""


class GatewayUnavailable(Exception):
    """The Gateway client or the caller's token could not be set up for a release call."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LazyGateway:
    """The Gateway's ``call_tool`` as ``user_key``, set up only when the first call is made.

    A cleanup whose holds all read back inactive never calls it, so it needs no Gateway
    settings and no Cognito secret.
    """

    def __init__(self, user_key: str) -> None:
        self.user_key = user_key
        self._stack: Optional[ExitStack] = None
        self._call: Optional[Release] = None

    def __call__(self, name: str, args: dict) -> dict:
        if self._call is None:
            self._open()
        return self._call(name, args)

    def _open(self) -> None:
        from backend.agentcore.gateway import get_agentcore_gateway

        stack = ExitStack()
        try:
            stack.enter_context(agentcore_caller.caller_scope(self.user_key))
            self._call = get_agentcore_gateway().call_tool
        except Exception as exc:  # noqa: BLE001 - reported per hold, with the type only
            self._close_quietly(stack)
            raise GatewayUnavailable(cause(exc)) from None
        self._stack = stack

    @staticmethod
    def _close_quietly(stack: ExitStack) -> None:
        try:
            stack.close()
        except Exception:  # noqa: BLE001 - the setup failure is what gets reported
            pass

    def close(self) -> str:
        """Leave the caller scope; '' on success, else a sanitized cause."""
        stack, self._stack = self._stack, None
        if stack is None:
            return ""
        try:
            stack.close()
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return f"closing the caller token scope failed ({cause(exc)})"
        return ""


def call_release(call_tool: Release, args: dict) -> str:
    """One governed release call: '' when the Gateway answered released, else a fixed cause."""
    from backend.agents.phase_05_workflow.governed_hold import release_governed_hold

    try:
        outcome = release_governed_hold(call_tool, args)
    except GatewayUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - an ambiguous reply: the next read-back decides
        return f"release reply lost ({cause(exc)})"
    return "" if outcome.released else f"release not confirmed ({outcome.error})"


class CallLedger:
    """Release calls already sent per booking: in memory, or in a file other processes share."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path, self.sent = path, {}

    def made(self, booking: str) -> int:
        """How many release calls this booking has had."""
        if self.path is None:
            return self.sent.get(booking, 0)
        return self.path.read_text().split().count(booking) if self.path.exists() else 0

    def record(self, booking: str) -> None:
        """Count the call before it is sent, so a call that dies on the way still counts."""
        if self.path is None:
            self.sent[booking] = self.sent.get(booking, 0) + 1
            return
        with open(self.path, "a", encoding="utf-8") as ledger:
            ledger.write(booking + "\n")


async def release_one(client, traveler: str, call_tool: Release, args: dict, booking: str,
                      ledger: CallLedger) -> str:
    """Release one hold with at most two calls in all; '' once it is inactive, else the causes.

    It is read back before each call, and a call after the first goes out only when that
    read-back succeeded and showed the hold still held, so an unconfirmed release is never
    repeated blind. Calls an earlier process recorded in the ledger count toward the two.
    """
    causes: list[str] = []
    sent_here = 0
    while True:
        made = max(ledger.made(booking), sent_here)
        active, read_cause = await hold_state(client, traveler, booking)
        if active is False:
            return ""
        causes.append(read_cause)
        if made >= RELEASE_CALLS_PER_HOLD or (made and active is None):
            break
        ledger.record(booking)
        sent_here += 1
        causes.append(call_release(call_tool, args))
    return "; ".join(dict.fromkeys(cause_ for cause_ in causes if cause_)) or "still held"


async def _release_each(client, traveler: str, holds: list[dict], ledger: CallLedger) -> list[str]:
    """Release each hold, reading it back first; the Gateway is set up on the first real call."""
    from backend.agents.phase_05_workflow.governed_hold import release_arguments

    problems = []
    call_tool = LazyGateway(user_for_traveler(traveler))
    for hold in holds:
        booking = hold["booking_id"]
        calls_before = ledger.made(booking)
        try:
            args = release_arguments(traveler_id=traveler,
                                     hold_request_id=hold["hold_request_id"],
                                     journey_id=hold["journey_id"])
            reason = await release_one(client, traveler, call_tool, args, booking, ledger)
        except GatewayUnavailable as exc:
            problems.append(f"booking {booking} not released: no governed release possible "
                            f"({exc.reason})")
            continue
        except Exception as exc:  # noqa: BLE001 - one bad hold never stops the others
            reason = f"cleanup error ({cause(exc)})"
        if reason:
            problems.append(f"booking {booking} still held or unknown ({reason})")
        elif ledger.made(booking) > calls_before:
            print(f"released: booking {booking} request {hold['hold_request_id']} "
                  f"journey {hold['journey_id']}")
        else:
            print(f"already inactive, no release call: booking {booking}")
    closing = call_tool.close()
    return problems + ([closing] if closing else [])


async def release_holds(client, traveler: str, holds: list[dict],
                        ledger: Optional[CallLedger] = None) -> list[str]:
    """Give back each hold through the Gateway as ``traveler``; every hold is attempted.

    Args:
        client: The Aurora client used for the read-backs.
        traveler: The traveler that owns the holds. In ``jwt`` mode the call carries that
            seeded user's token.
        holds: Rows with ``booking_id``, ``hold_request_id`` and ``journey_id``.
        ledger: The call ledger; a new in-memory one when omitted.

    Returns:
        One problem per hold that is still held or of unknown state, each with a sanitized
        cause. Empty when every hold is inactive.
    """
    if not holds:
        return []
    try:
        return await _release_each(client, traveler, holds, ledger or CallLedger())
    except Exception as exc:  # noqa: BLE001 - no token or gateway: report every hold
        return [f"booking {hold['booking_id']} not released: no governed release possible "
                f"({cause(exc)})" for hold in holds]


async def thread_holds(client, thread: str) -> tuple[list[dict], list[str]]:
    """The holds of ``thread``'s journey, and why any of them must not be touched.

    A journey that another thread also uses is never touched: its holds are not this run's.
    """
    try:
        holds = [dict(row) for row in await client.execute(RUN_HOLDS_SQL, (thread,))]
        shared = []
        for journey in sorted({hold["journey_id"] for hold in holds}):
            rows = await client.execute(THREADS_OF_JOURNEY_SQL, (journey,))
            if int(rows[0]["n"]) != 1:
                shared.append(journey)
    except Exception as exc:  # noqa: BLE001 - reported; nothing is released blind
        return [], [f"reading the holds of {thread} failed ({cause(exc)})"]
    problems = [f"journey {journey} has other threads; its holds were not released"
                for journey in shared]
    return [hold for hold in holds if hold["journey_id"] not in shared], problems


async def journey_holds(client, journey_id: str) -> list[dict]:
    """The hold requests of one journey."""
    return [dict(row) for row in await client.execute(JOURNEY_HOLDS_SQL, (journey_id,))]


async def booking_holds(client, traveler: str, bookings: list[str]) -> tuple[list[dict], list[str]]:
    """The hold identity of each booking, and the bookings whose identity cannot be found.

    A booking with no hold request row is a problem only while its state is active or unknown;
    one that reads inactive has nothing to give back.
    """
    holds, problems = [], []
    for booking in bookings:
        try:
            rows = await client.execute(BOOKING_HOLD_SQL, (booking,))
        except Exception as exc:  # noqa: BLE001 - reported; nothing is released blind
            problems.append(f"booking {booking} not released: its hold request could not be "
                            f"read ({cause(exc)})")
            continue
        if rows:
            holds.append(dict(rows[0]))
            continue
        active, _ = await hold_state(client, traveler, booking)
        if active is not False:
            problems.append(f"booking {booking} has no hold request and is not known to be "
                            "inactive; it was not released")
    return holds, problems


async def release_bookings(client, traveler: str, bookings: list[str],
                           ledger: Optional[CallLedger] = None) -> list[str]:
    """Give back the active holds among ``bookings`` (exact ids, all owned by ``traveler``)."""
    holds, problems = await booking_holds(client, traveler, bookings)
    return problems + await release_holds(client, traveler, holds, ledger)


async def release_journey(client, traveler: str, journey_id: str,
                          ledger: Optional[CallLedger] = None) -> list[str]:
    """Give back the active holds of one journey the caller created."""
    try:
        holds = await journey_holds(client, journey_id)
    except Exception as exc:  # noqa: BLE001 - reported; nothing is released blind
        return [f"reading the holds of journey {journey_id} failed ({cause(exc)})"]
    return await release_holds(client, traveler, holds, ledger)
