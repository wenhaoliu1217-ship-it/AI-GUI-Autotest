"""Deterministic, site-neutral test resource naming helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable


TEST_NAME_RE = re.compile(r"^test_([A-Z]+)$")


def alpha_name(index: int) -> str:
    """Return Excel-column spelling: 1=A, 26=Z, 27=AA."""
    if index < 1:
        raise ValueError("index must be positive")
    chars: list[str] = []
    while index:
        index, remainder = divmod(index - 1, 26)
        chars.append(chr(65 + remainder))
    return "".join(reversed(chars))


def parse_test_name_index(name: str) -> int | None:
    match = TEST_NAME_RE.fullmatch(str(name).strip())
    if not match:
        return None
    index = 0
    for char in match.group(1):
        index = index * 26 + ord(char) - 64
    return index


def next_test_name(existing: Iterable[str]) -> str:
    """Return the first free name, preserving gaps instead of renumbering."""
    occupied = {str(item).strip() for item in existing if TEST_NAME_RE.fullmatch(str(item).strip())}
    index = 1
    while f"test_{alpha_name(index)}" in occupied:
        index += 1
    return f"test_{alpha_name(index)}"
