"""HTML pages for a recorded receipt: a four-row summary for the slide and a full listing.

The pages are self-contained (inline style, no script, no external request), so a screenshot of
one is a faithful picture of the receipt and of nothing else.
"""

from __future__ import annotations

from html import escape
from typing import Any

from scripts.identity_probes.receipt import ALLOWED, REFUSED, REFUSER_LABELS, leaks

LAYER_NAMES = {
    "backend": "Backend", "runtime": "Runtimes", "gateway": "Gateway", "database": "AWS Aurora",
}
RESULT_WORDS = {
    "refused": "Refused", "allowed": "Allowed", "FAILED": "Failed", "not run": "Not run",
    "error": "Error", "unproven": "Unproven",
}
STYLE = """
html, body { margin: 0; background: #111214; color: #F5F6F8;
  font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; }
#receipt { box-sizing: border-box; width: 1920px; padding: 14px 24px 12px; }
table { border-collapse: collapse; width: 100%; }
th { text-align: left; color: #8E939B; font-size: 21px; font-weight: 600;
  letter-spacing: 0.06em; text-transform: uppercase; padding: 4px 14px 8px;
  border-bottom: 2px solid #5A5E66; }
td { font-size: 28px; padding: 8px 14px; border-bottom: 1px solid #474B52; }
td.layer { font-weight: 700; }
td.ok { color: #5FD34A; font-weight: 700; }
td.bad { color: #FF6B6B; font-weight: 700; }
.caption { margin-top: 10px; color: #8E939B; font-size: 20px; }
.verdict { margin-top: 10px; font-size: 24px; font-weight: 700; }
.verdict.pass, .banner.pass { color: #5FD34A; }
.verdict.fail, .banner.fail { color: #FF6B6B; }
.banner { font-size: 30px; font-weight: 700; padding: 6px 10px 12px; }
.note { margin-top: 6px; color: #C9CCD2; font-size: 17px; }
.full td { font-size: 18px; padding: 5px 10px; }
.full th { font-size: 15px; }
"""


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
            f"<body>{body}</body></html>")


def _cell(value: str, wanted: str) -> str:
    tone = "ok" if value == wanted else "bad"
    return f'<td class="{tone}">{escape(RESULT_WORDS.get(value, value))}</td>'


def _caption(data: dict[str, Any]) -> str:
    return (f'<div class="caption">Recorded run of {escape(str(data["at"])[:10])}, '
            f'commit {escape(str(data["git_sha"])[:8])}</div>')


def _passed(data: dict[str, Any]) -> bool:
    return data.get("ok") is True


def summary_html(data: dict[str, Any]) -> str:
    """The four-row page: for each layer, the decoy's result, Jordan's result and the refuser."""
    rows = []
    for row in data["summary"]:
        name = LAYER_NAMES.get(row["layer"], row["layer"])
        rows.append(
            f'<tr><td class="layer">{escape(name)}</td>{_cell(row["decoy"], REFUSED)}'
            f'{_cell(row["jordan"], ALLOWED)}<td>{escape(row["refused_by"])}</td></tr>')
    head = "<tr><th>Layer</th><th>Decoy user</th><th>Jordan</th><th>Refused by</th></tr>"
    table = f"<table>{head}{''.join(rows)}</table>"
    tone, word = ("pass", "Pass") if _passed(data) else ("fail", "Fail")
    verdict = f'<div class="verdict {tone}">Overall: {word}</div>'
    return _page("Identity receipt", f'<div id="receipt">{table}{verdict}{_caption(data)}</div>')


def _outcome_row(outcome: dict[str, Any]) -> str:
    tone = "ok" if outcome["passed"] else "bad"
    label = REFUSER_LABELS.get(outcome["refused_by"] or "", "")
    layer = LAYER_NAMES.get(outcome["layer"], outcome["layer"])
    return (
        f'<tr><td class="layer">{escape(layer)}</td><td>{escape(outcome["probe"])}</td>'
        f'<td>{escape(outcome["actor"])}</td><td>{escape(outcome["expected"])}</td>'
        f'<td class="{tone}">{escape(outcome["result"])}</td><td>{escape(label)}</td>'
        f'<td>{escape(outcome["detail"])}</td></tr>')


def _notes(data: dict[str, Any]) -> str:
    cleanup = data.get("cleanup", {})
    line = (f"Cleanup: {cleanup.get('threads_purged', 0)} threads purged, "
            f"{cleanup.get('bookings_released', 0)} bookings released, "
            f"leftovers {cleanup.get('leftovers', 0)}")
    lines = [line]
    lines += [f"Gap: {gap}" for gap in data.get("coverage_gaps", [])]
    lines += [f"Cleanup problem: {problem}" for problem in cleanup.get("problems", [])]
    lines += [f"Leftover: {item}" for item in cleanup.get("leftover_items", [])]
    lines += list(data.get("notes", []))
    return "".join(f'<div class="note">{escape(str(text))}</div>' for text in lines)


def full_html(data: dict[str, Any]) -> str:
    """Every probe with its result and refuser, the overall result, gaps, cleanup and residue."""
    head = ("<tr><th>Layer</th><th>Probe</th><th>Actor</th><th>Expected</th><th>Result</th>"
            "<th>Refused by</th><th>Detail</th></tr>")
    rows = "".join(_outcome_row(outcome) for outcome in data["outcomes"])
    tone, word = ("pass", "PASS") if _passed(data) else ("fail", "FAIL")
    banner = f'<div class="banner {tone}">Result: {word}</div>'
    body = (f'<div id="receipt" class="full">{banner}<table>{head}{rows}</table>'
            f"{_notes(data)}{_caption(data)}</div>")
    return _page("Identity receipt, all probes", body)


def pages(data: dict[str, Any]) -> dict[str, str]:
    """The summary and the full page, keyed by file suffix.

    Raises:
        ValueError: When a page would hold a secret or an identifier that ``leaks`` finds.
    """
    built = {".summary.html": summary_html(data), ".full.html": full_html(data)}
    for suffix, text in built.items():
        found = leaks(text)
        if found:
            raise ValueError(f"the {suffix} page would contain {', '.join(found)}; not written")
    return built
