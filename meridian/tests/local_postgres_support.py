"""An isolated PostgreSQL cluster for real SQL tests, with no AWS or TCP access."""

from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import uuid

import psycopg
from psycopg import sql
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


@pytest.fixture(scope="session")
def local_postgres():
    """Start our own socket-only cluster and stop it after the SQL tests finish."""
    config = os.environ.get("MERIDIAN_PG_CONFIG") or shutil.which("pg_config")
    if config is None:
        pytest.skip("Local SQL tests need PostgreSQL and pgvector installed")
    bindir = Path(_run(config, "--bindir").stdout.strip())
    sharedir = Path(_run(config, "--sharedir").stdout.strip())
    if not (bindir / "initdb").is_file() or not (sharedir / "extension/vector.control").is_file():
        pytest.skip("Local SQL tests need the PostgreSQL server and pgvector")
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="meridian-pg-", dir=cache) as directory:
        data = str(Path(directory) / "data")
        _run(
            str(bindir / "initdb"),
            "-D",
            data,
            "-U",
            "meridian_admin",
            "--auth-local=trust",
            "--auth-host=reject",
            "--no-locale",
            "-E",
            "UTF8",
        )
        control = str(bindir / "pg_ctl")
        _run(
            control,
            "-D",
            data,
            "-l",
            str(Path(directory) / "server.log"),
            "-o",
            f"-k {directory} -h '' -p 55439",
            "-w",
            "start",
        )
        try:
            yield dict(host=directory, port=55439, user="meridian_admin", dbname="postgres")
        finally:
            _run(control, "-D", data, "-m", "immediate", "-w", "stop")


class BookingDatabase:
    """Connections to a disposable database containing the repository's actual DDL."""

    def __init__(self, config):
        self.config = config

    @contextmanager
    def connect(self, traveler=None, *, login=None, agent="concierge_agent"):
        with psycopg.connect(**self.config) as connection:
            if login:
                connection.execute(
                    sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(login))
                )
            if traveler:
                connection.execute(
                    "SELECT set_config('app.current_traveler_id', %s, true)", (traveler,)
                )
                if agent is not None:
                    connection.execute("SELECT set_config('app.agent_type', %s, true)", (agent,))
            yield connection


def _install(connection):
    from scripts.init_aurora_schema import _grants_on_objects, split_sql

    connection.execute((ROOT / "backend/db/schema.sql").read_text())
    role = split_sql((ROOT / "examples/rls_app_role.sql").read_text())
    for statement in role:
        if not _grants_on_objects(statement):
            connection.execute(statement)
    connection.execute((ROOT / "examples/rls_for_agents.sql").read_text())
    for statement in role:
        if _grants_on_objects(statement):
            connection.execute(statement)
    for migration in sorted((ROOT / "scripts/migrations").glob("*.sql")):
        connection.execute(migration.read_text())


@pytest.fixture
def booking_db(local_postgres):
    name = "booking_test_" + uuid.uuid4().hex[:12]
    with psycopg.connect(**local_postgres, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    database = BookingDatabase({**local_postgres, "dbname": name})
    try:
        with database.connect() as connection:
            _install(connection)
            connection.execute(
                "INSERT INTO travelers(traveler_id, full_name, email) "
                "VALUES ('t1', 'One', 'one@example.test'), "
                "('t2', 'Two', 'two@example.test')"
            )
            connection.execute(
                "INSERT INTO trip_packages(package_id, name, trip_type, "
                "destination, price_per_person, durations, availability) "
                "VALUES ('pkg', 'Tokyo', 'City Breaks', 'Tokyo', 100, "
                "'[\"2 nights\"]', '{\"2 nights\": 1}')"
            )
            connection.execute(
                "INSERT INTO journeys(journey_id, traveler_id, snapshot_store) "
                "VALUES ('j1', 't1', 'aurora_snapshot'), "
                "('j2', 't2', 'aurora_snapshot')"
            )
        yield database
    finally:
        with psycopg.connect(**local_postgres, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
