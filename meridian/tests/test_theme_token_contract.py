"""Guard the showcase theme token layer.

Accent tints used to be written as raw ``rgba(47, 140, 255, .12)`` in 169
places across 48 different alphas. A literal like that cannot follow the
theme, which is how the tooltip ended up rendering near-black text on a
near-black ground in light mode - the theme the presenter guide recommends for
low-contrast projectors.

Every tint now resolves from a themed hue triple, so the light theme follows
automatically. These tests stop a raw one creeping back in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SHOWCASE = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "showcase"
)

SHEETS = (
    "meridianShowcase.css",
    "recoveryWorkspace.css",
    "discoveryWorkspace.css",
    "recoveryDecisionRefresh.css",
)

# Hues that must only ever be referenced through their themed triple.
THEMED_HUES = {
    "blue accent": r"rgba?\(\s*47\s*,\s*140\s*,\s*255\s*[,)]",
    "green": r"rgba?\(\s*55\s*,\s*210\s*,\s*157\s*[,)]",
    "recovery yellow": r"rgba?\(\s*246\s*,\s*183\s*,\s*60\s*[,)]",
    "recovery green": r"rgba?\(\s*50\s*,\s*207\s*,\s*134\s*[,)]",
    "teal": r"rgba?\(\s*67\s*,\s*206\s*,\s*171\s*[,)]",
}

HUE_TRIPLES = (
    "--mds-blue-rgb",
    "--mds-green-rgb",
    "--mds-recovery-yellow-rgb",
    "--mds-recovery-green-rgb",
    "--mds-teal-rgb",
)


def _sheet(name: str) -> str:
    return (SHOWCASE / name).read_text(encoding="utf-8")


def _without_token_definitions(css: str) -> str:
    """Drop the ``:root`` declarations, where the literals legitimately live."""
    return "\n".join(
        line
        for line in css.splitlines()
        if not re.match(r"\s*--mds-[a-z-]+\s*:", line)
    )


@pytest.mark.parametrize("name", SHEETS)
@pytest.mark.parametrize("label,pattern", sorted(THEMED_HUES.items()))
def test_no_raw_accent_literals(name: str, label: str, pattern: str) -> None:
    css = _without_token_definitions(_sheet(name))
    hits = re.findall(pattern, css)
    assert not hits, (
        f"{name} has {len(hits)} raw {label} literal(s). Use "
        f"rgb(var(--mds-...-rgb) / <alpha>) so the tint follows the theme."
    )


@pytest.mark.parametrize("token", HUE_TRIPLES)
def test_hue_triple_defined_for_both_themes(token: str) -> None:
    css = _sheet("meridianShowcase.css")
    definitions = re.findall(rf"{re.escape(token)}\s*:", css)
    assert len(definitions) == 2, (
        f"{token} must be defined once for dark and once for light; "
        f"found {len(definitions)}"
    )


def test_tooltip_colours_come_from_tokens() -> None:
    """The original bug: themed text over a hard-coded dark ground."""
    css = _sheet("meridianShowcase.css")
    match = re.search(r"\.mds-tooltip\s*\{[^}]*\}", css)
    assert match, ".mds-tooltip rule not found"
    rule = match.group(0)
    assert "var(--mds-tooltip-bg)" in rule
    assert "var(--mds-tooltip-fg)" in rule
    assert not re.search(r"#[0-9a-fA-F]{6}", rule), (
        "tooltip still carries a hard-coded colour"
    )
