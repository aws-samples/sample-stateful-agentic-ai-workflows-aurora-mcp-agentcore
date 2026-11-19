"""setup.sh follows the documented migration route and never applies a bare command."""

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup.sh"
SOURCE = SCRIPT.read_text(encoding="utf-8")
START = "  pending=$(python scripts/apply_migrations.py --pending)"
END = "  fi\n  python scripts/seed_data.py"


def migration_block() -> str:
    return SOURCE[SOURCE.index(START):SOURCE.index(END) + len("  fi\n")]


def run_block(pending: str, tmp_path: Path) -> list[str]:
    """Run the script's migration step with a recording `python`; return its calls."""
    log = tmp_path / "calls"
    shim = (f'python() {{ echo "$*" >> {log}; '
            f'if [ "$2" = "--pending" ]; then printf "%s" "$PENDING"; fi; }}\n')
    subprocess.run(["bash", "-c", "set -e\n" + shim + migration_block()],
                   check=True, env={"PENDING": pending, "PATH": "/usr/bin:/bin"},
                   capture_output=True, text=True)
    return [line.removeprefix("scripts/apply_migrations.py ")
            for line in log.read_text().splitlines()]


def test_every_migration_command_in_setup_names_a_mode():
    commands = re.findall(r"python scripts/apply_migrations\.py([^\n]*)", SOURCE)

    assert commands
    assert all("--pending" in c or "--only" in c for c in commands), commands
    assert not any(c.strip() == "" for c in commands)


def test_each_pending_migration_is_dry_run_then_applied_by_name_in_order(tmp_path):
    calls = run_block("001_a.sql\n002_b.sql", tmp_path)

    assert calls == ["--pending", "--dry-run --only 001_a.sql", "--only 001_a.sql",
                     "--dry-run --only 002_b.sql", "--only 002_b.sql"]


def test_nothing_is_applied_when_nothing_is_pending(tmp_path):
    assert run_block("No pending migrations.", tmp_path) == ["--pending"]


def test_the_script_points_to_backend_login_provisioning_before_the_launcher():
    assert "provision_service_logins.py --login backend" in SOURCE
    assert SOURCE.index("provision_service_logins.py --login backend") < SOURCE.index(
        'echo "  python scripts/run_local.py"')


def test_no_line_runs_the_migration_command_bare():
    bare = [line for line in SOURCE.splitlines()
            if re.search(r"apply_migrations\.py\s*(\)|$|;|&&)", line)]

    assert bare == []
