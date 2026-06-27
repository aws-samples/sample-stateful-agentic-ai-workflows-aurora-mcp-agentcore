"""
Structured logging configuration for Meridian backend.

Set LOG_LEVEL=DEBUG for maximum detail. Agent/tool/skill lines are emitted at
INFO when LOG_AGENT_VERBOSE=true (default for workshop demos).
"""

from __future__ import annotations

import logging
import os
import sys
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def agent_verbose_enabled() -> bool:
    return _env_bool("LOG_AGENT_VERBOSE", True)


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key in (
            "phase", "query", "results_count", "execution_time_ms",
            "product_id", "order_id", "error", "context",
            "agent", "agent_file", "tool", "skill", "skills",
            "specialists", "event", "activity_type", "traveler_id",
            "conversation_id", "orchestration",
        ):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class ReadableFormatter(logging.Formatter):
    """Human-readable formatter for development."""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        reset = self.COLORS["RESET"]

        extras: list[str] = []
        if hasattr(record, "phase"):
            extras.append(f"phase={record.phase}")
        if hasattr(record, "event"):
            extras.append(f"event={record.event}")
        if hasattr(record, "agent"):
            extras.append(f"agent={record.agent}")
        if hasattr(record, "tool"):
            extras.append(f"tool={record.tool}")
        if hasattr(record, "skill"):
            extras.append(f"skill={record.skill}")
        if hasattr(record, "activity_type"):
            extras.append(f"type={record.activity_type}")
        if hasattr(record, "specialists"):
            extras.append(f"specialists=[{record.specialists}]")
        if hasattr(record, "skills"):
            extras.append(f"skills=[{record.skills}]")
        if hasattr(record, "query"):
            query = record.query
            if len(query) > 60:
                query = query[:60] + "…"
            extras.append(f'query="{query}"')
        if hasattr(record, "results_count"):
            extras.append(f"results={record.results_count}")
        if hasattr(record, "execution_time_ms"):
            extras.append(f"time={record.execution_time_ms}ms")
        if hasattr(record, "traveler_id"):
            extras.append(f"traveler={record.traveler_id}")
        if hasattr(record, "orchestration"):
            extras.append(f"orchestration={record.orchestration}")

        extra_str = f" [{', '.join(extras)}]" if extras else ""
        return f"{color}[{record.levelname}]{reset} {record.name}: {record.getMessage()}{extra_str}"


def setup_logging(
    level: Optional[str] = None,
    json_output: Optional[bool] = None,
) -> logging.Logger:
    """Configure the meridian logger from environment."""
    level_name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)
    use_json = json_output if json_output is not None else _env_bool("LOG_JSON", False)

    meridian_logger = logging.getLogger("meridian")
    meridian_logger.setLevel(log_level)
    meridian_logger.handlers.clear()
    meridian_logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.setFormatter(StructuredFormatter() if use_json else ReadableFormatter())
    meridian_logger.addHandler(handler)

    return meridian_logger


logger = setup_logging()


def log_startup_banner() -> None:
    """Log the five-phase agent catalog once at startup."""
    if not agent_verbose_enabled():
        return

    from backend.agent_catalog import PHASE_CATALOG

    logger.info("Meridian agent catalog loaded (5 phases)")
    for phase in sorted(PHASE_CATALOG):
        spec = PHASE_CATALOG[phase]
        skills = ", ".join(s.name for s in spec.skills)
        specialists = ", ".join(spec.specialists) if spec.specialists else "—"
        logger.info(
            f"  Phase {phase} · {spec.label}: {spec.primary_agent} → {spec.method}",
            extra={
                "phase": phase,
                "agent": spec.primary_agent,
                "specialists": specialists,
                "skills": skills,
                "event": "catalog",
            },
        )


def log_turn_start(
    phase: int,
    message: str,
    *,
    traveler_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> float:
    """Log incoming chat turn with phase agent + available skills. Returns t0."""
    from backend.agent_catalog import get_phase_spec, format_skills_summary, format_specialists_summary

    spec = get_phase_spec(phase)
    extra: Dict[str, Any] = {
        "phase": phase,
        "query": message,
        "event": "turn_start",
        "orchestration": os.getenv("STRANDS_ORCHESTRATION", "full"),
    }
    if traveler_id:
        extra["traveler_id"] = traveler_id
    if conversation_id:
        extra["conversation_id"] = conversation_id

    if spec and agent_verbose_enabled():
        extra.update({
            "agent": spec.primary_agent,
            "agent_file": spec.agent_file,
            "specialists": format_specialists_summary(phase),
            "skills": format_skills_summary(phase),
        })
        logger.info(
            f"▶ Turn start · Phase {phase} ({spec.label}) · {spec.primary_agent}",
            extra=extra,
        )
        logger.info(
            f"  specialists: {extra['specialists']}",
            extra={**extra, "event": "specialists"},
        )
        logger.info(
            f"  skills: {extra['skills']}",
            extra={**extra, "event": "skills"},
        )
    else:
        logger.info(f"▶ Turn start · phase={phase}", extra=extra)

    return time.perf_counter()


def log_turn_complete(
    phase: int,
    *,
    products_count: int = 0,
    activities_count: int = 0,
    started_at: Optional[float] = None,
    error: Optional[str] = None,
) -> None:
    """Log chat turn completion summary."""
    elapsed_ms: Optional[int] = None
    if started_at is not None:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

    extra: Dict[str, Any] = {
        "phase": phase,
        "results_count": products_count,
        "event": "turn_complete",
    }
    if elapsed_ms is not None:
        extra["execution_time_ms"] = elapsed_ms

    if error:
        logger.error(
            f"✗ Turn failed · phase={phase} · {error}",
            extra={**extra, "error": error},
        )
        return

    logger.info(
        f"✓ Turn complete · phase={phase} · {products_count} trips · {activities_count} trace spans",
        extra=extra,
    )


def log_agent_activity(
    *,
    activity_type: str,
    title: str,
    agent_name: Optional[str] = None,
    agent_file: Optional[str] = None,
    details: Optional[str] = None,
    execution_time_ms: Optional[int] = None,
    sql_query: Optional[str] = None,
) -> None:
    """Log a single agent/tool span (delegation, search, memory recall, etc.)."""
    if not agent_verbose_enabled():
        return

    detail_snip = ""
    if details:
        detail_snip = details if len(details) <= 120 else details[:120] + "…"

    msg_parts = [f"  ↳ {activity_type}: {title}"]
    if agent_name:
        msg_parts.append(f"({agent_name})")
    if detail_snip:
        msg_parts.append(f"— {detail_snip}")
    if execution_time_ms is not None:
        msg_parts.append(f"[{execution_time_ms}ms]")

    extra: Dict[str, Any] = {
        "event": "agent_activity",
        "activity_type": activity_type,
        "agent": agent_name,
        "agent_file": agent_file,
        "execution_time_ms": execution_time_ms,
    }
    if sql_query:
        extra["tool"] = "sql"
        logger.debug(
            " ".join(msg_parts),
            extra={**extra, "skill": sql_query[:80]},
        )
        return

    logger.info(" ".join(msg_parts), extra=extra)


def log_activity_entry(entry: Any) -> None:
    """Log from an ActivityEntry model or compatible dict."""
    if entry is None:
        return
    if hasattr(entry, "model_dump"):
        data = entry.model_dump()
    elif isinstance(entry, dict):
        data = entry
    else:
        data = {
            "activity_type": getattr(entry, "activity_type", "unknown"),
            "title": getattr(entry, "title", str(entry)),
            "details": getattr(entry, "details", None),
            "agent_name": getattr(entry, "agent_name", None),
            "agent_file": getattr(entry, "agent_file", None),
            "execution_time_ms": getattr(entry, "execution_time_ms", None),
            "sql_query": getattr(entry, "sql_query", None),
        }

    log_agent_activity(
        activity_type=data.get("activity_type") or "unknown",
        title=data.get("title") or "(unnamed)",
        agent_name=data.get("agent_name"),
        agent_file=data.get("agent_file"),
        details=data.get("details"),
        execution_time_ms=data.get("execution_time_ms"),
        sql_query=data.get("sql_query"),
    )


def log_search(
    phase: int,
    query: str,
    results_count: int,
    execution_time_ms: int,
    search_type: str = "keyword",
) -> None:
    """Log a search operation."""
    logger.info(
        f"Search completed: {search_type}",
        extra={
            "phase": phase,
            "query": query,
            "results_count": results_count,
            "execution_time_ms": execution_time_ms,
            "event": "search",
        },
    )


def log_order(
    phase: int,
    product_id: str,
    order_id: Optional[str] = None,
    total: Optional[float] = None,
    status: str = "started",
    error: Optional[str] = None,
) -> None:
    """Log an order operation."""
    extra: Dict[str, Any] = {
        "phase": phase,
        "product_id": product_id,
        "event": "order",
    }
    if order_id:
        extra["order_id"] = order_id
    if total is not None:
        extra["total"] = total
    if error:
        extra["error"] = error
        logger.error(f"Order {status}", extra=extra)
    else:
        logger.info(f"Order {status}", extra=extra)


def log_error(context: str, error: str, **kwargs: Any) -> None:
    """Log an error with context."""
    logger.error(
        f"Error in {context}: {error}",
        extra={"error": error, "context": context, "event": "error", **kwargs},
    )
