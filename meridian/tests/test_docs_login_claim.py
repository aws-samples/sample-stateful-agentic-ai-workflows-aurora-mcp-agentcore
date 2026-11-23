"""The README says both hosted releases run as non-owner logins, and what --tighten adds."""

from __future__ import annotations

import re
from pathlib import Path

README = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
CLAIM = "non-owner, `NOBYPASSRLS`, `NOINHERIT` logins"


def test_readme_says_both_hosted_releases_use_the_non_owner_logins():
    flat = re.sub(r"\s+", " ", README)
    assert CLAIM in flat
    qualifier = flat.split(CLAIM, 1)[1][:360]
    assert "`iam` and `jwt` releases" in qualifier
    assert "`meridian_backend`" in qualifier
    assert "`--tighten`" in qualifier
    assert "master secret" in qualifier
    assert "runs its backend with the master" not in flat
