"""Test helpers that read SQL the way the RDS Data API does."""

import re


def names_the_data_api_binds(sql: str) -> list[str]:
    """The :pN names outside string literals, reading a backslash in a literal as an escape.

    That is how the RDS Data API scans a statement for parameters: in ``ESCAPE '\\'`` it
    reads ``\\'`` as an escaped quote, so the literal runs on and swallows the next name.
    """
    names, quoted, i = [], False, 0
    while i < len(sql):
        if quoted and sql[i] == "\\":
            i += 2
            continue
        if sql[i] == "'":
            quoted = not quoted
        elif not quoted and (match := re.match(r":(p\d+)", sql[i:])):
            names.append(match.group(1))
        i += 1
    return names
