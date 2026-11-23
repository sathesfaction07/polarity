from __future__ import annotations

from .diff import diff_expressions
from .extract import extract, extract_from_tree
from .normalize import is_cosmetic_change, normalize

__all__ = [
    "diff_expressions",
    "extract",
    "extract_from_tree",
    "is_cosmetic_change",
    "normalize",
]
