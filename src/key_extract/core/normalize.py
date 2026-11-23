from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """
    Comparison key for a keyword.

    Three steps, each earning its place:

    **NFKC** folds compatibility characters, so the fullwidth `ｋｎｉｆｅ` and the
    ligature `ﬁle` compare equal to their ASCII forms. Classification expressions get
    pasted from spreadsheets, PDFs and other locales, and a rule that reads identically
    to a human should compare identically here.

    **casefold** rather than `lower()`, because `lower()` leaves the German ß alone
    while `casefold` maps it to `ss`. For keyword matching, `STRASSE` and `Straße` are
    the same word.

    **whitespace collapse** so `"butter  knife"` and `"butter knife"` are one keyword.
    A double space inside a quoted phrase is invisible in a diff and is never intended.

    The original surface form is kept separately on the Term, so a report can still show
    exactly what the author typed. Normalising in place would destroy the ability to say
    "these two versions differ only cosmetically", which is a genuinely useful finding.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _WHITESPACE.sub(" ", folded).strip()


def is_cosmetic_change(left: str, right: str) -> bool:
    """
    Whether two surface forms differ only in ways `normalize` erases.

    Used by the diff to separate "somebody retyped this keyword with different casing"
    from "somebody changed what this rule matches". Reporting both as a change buries
    the second kind under the first.
    """
    return left != right and normalize(left) == normalize(right)
