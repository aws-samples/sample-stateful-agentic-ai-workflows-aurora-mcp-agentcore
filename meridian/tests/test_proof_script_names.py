"""The proof scripts were renamed; no published file may use the old names."""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OLD_NAMES = ("kill_and_resume_demo", "lost_response_demo")
# Repo-relative paths only.
EXEMPT_FILES = {"meridian/tests/test_proof_script_names.py"}
EXEMPT_PARTS = {".superpowers", ".kiro", ".cache"}


def _tracked_public_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout.decode()
    files = []
    for name in filter(None, listing.split("\0")):
        path = Path(name)
        if name in EXEMPT_FILES:
            continue
        if EXEMPT_PARTS & set(path.parts):
            continue
        files.append(path)
    return files


def test_no_public_file_mentions_the_old_proof_script_names():
    offenders = []
    for path in _tracked_public_files():
        try:
            text = (REPO_ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        offenders.extend(f"{path}: {old}" for old in OLD_NAMES if old in text)
    assert not offenders, f"old proof script names still referenced: {offenders}"


def test_renamed_proof_scripts_exist_and_old_ones_are_gone():
    scripts = REPO_ROOT / "meridian" / "scripts"
    for stem in ("kill_and_resume", "lost_response"):
        assert (scripts / f"{stem}_proof.py").is_file()
        assert not (scripts / f"{stem}_demo.py").exists()
