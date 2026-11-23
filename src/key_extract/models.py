from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Polarity(StrEnum):
    """
    What a keyword's presence means for whether the rule matches.

    Two values would be the obvious choice -- included and excluded -- and that is what
    this started as. It is not sufficient; see `core/extract.py`.
    """

    REQUIRED = "required"
    OPTIONAL = "optional"
    EXCLUDED = "excluded"
    CONDITIONAL = "conditional"


class Keyword(BaseModel):
    """
    One extracted keyword.

    `surface` is what the author typed, `normalized` is the comparison key. Both are
    kept so a diff can distinguish a cosmetic retype from a change in meaning.
    """

    surface: str
    normalized: str
    field: str = ""
    polarity: Polarity = Polarity.REQUIRED
    quoted: bool = False
    wildcard: bool = False
    position: int = 0

    @property
    def key(self) -> tuple[str, str]:
        """Identity for diffing: the field plus the normalised value."""
        return (self.field, self.normalized)

    def render(self) -> str:
        body = f'"{self.surface}"' if self.quoted else self.surface
        if self.wildcard:
            body += "*"
        return f"{self.field}:{body}" if self.field else body


class Extraction(BaseModel):
    """Result of extracting from one expression."""

    expression: str
    keywords: list[Keyword] = Field(default_factory=list)
    tree: str = ""
    depth: int = 0
    source: str = "parser"
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def by_polarity(self, polarity: Polarity) -> list[Keyword]:
        return [keyword for keyword in self.keywords if keyword.polarity is polarity]

    def counts(self) -> dict[str, int]:
        return {polarity.value: len(self.by_polarity(polarity)) for polarity in Polarity}

    def grouped(self) -> dict[tuple[str, str], list[Keyword]]:
        """
        Occurrences grouped by identity, preserving every one.

        A dict keyed on identity is the obvious structure and it silently drops
        duplicates. `knife AND NOT knife` produces two occurrences of one keyword with
        opposite polarities, and keeping only the last makes a self-contradictory rule
        look ordinary.
        """
        groups: dict[tuple[str, str], list[Keyword]] = {}
        for keyword in self.keywords:
            groups.setdefault(keyword.key, []).append(keyword)
        return groups

    def polarities(self) -> dict[tuple[str, str], frozenset[Polarity]]:
        """Every polarity each keyword carries. More than one means it appears twice."""
        return {
            key: frozenset(occurrence.polarity for occurrence in occurrences)
            for key, occurrences in self.grouped().items()
        }

    @property
    def contradictions(self) -> list[str]:
        """
        Keywords that are both required and excluded, so the rule can never match.

        Almost always an authoring mistake -- a term added to one branch and forgotten in
        a negated one. Worth reporting loudly, because such a rule silently matches
        nothing, and nobody investigates a filter returning no results as eagerly as one
        returning the wrong results.
        """
        found = []
        for key, polarities in sorted(self.polarities().items()):
            if Polarity.REQUIRED in polarities and Polarity.EXCLUDED in polarities:
                field, value = key
                found.append(f"{field}:{value}" if field else value)
        return found


class ChangeKind(StrEnum):
    """
    How a keyword differs between two versions of an expression.

    `POLARITY_FLIPPED` exists because a set difference reports it as one removal plus
    one addition, which buries the most dangerous change a rule can undergo.
    """

    ADDED = "added"
    REMOVED = "removed"
    POLARITY_FLIPPED = "polarity_flipped"
    COSMETIC = "cosmetic"


class Change(BaseModel):
    kind: ChangeKind
    keyword: str
    field: str = ""
    before: str = ""
    after: str = ""
    note: str = ""

    def render(self) -> str:
        target = f"{self.field}:{self.keyword}" if self.field else self.keyword
        if self.kind is ChangeKind.POLARITY_FLIPPED:
            return f"{target}  {self.before} -> {self.after}"
        if self.kind is ChangeKind.COSMETIC:
            return f"{target}  {self.before!r} -> {self.after!r}"
        return target


class Diff(BaseModel):
    """
    Comparison of two expression versions.

    `structural_only` is true when the keyword sets and polarities are identical but the
    trees differ -- somebody re-parenthesised. That is a real change in meaning with no
    change in keywords, and it is exactly what a keyword-list diff cannot see.
    """

    left: str
    right: str
    changes: list[Change] = Field(default_factory=list)
    left_tree: str = ""
    right_tree: str = ""
    structural_only: bool = False
    left_contradictions: list[str] = Field(default_factory=list)
    right_contradictions: list[str] = Field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def unchanged(self) -> bool:
        return not self.changes and not self.structural_only

    def of_kind(self, kind: ChangeKind) -> list[Change]:
        return [change for change in self.changes if change.kind is kind]

    def counts(self) -> dict[str, int]:
        return {kind.value: len(self.of_kind(kind)) for kind in ChangeKind}


ROW_FIELDS = (
    "expression",
    "status",
    "depth",
    "required",
    "optional",
    "excluded",
    "conditional",
    "keywords",
    "source",
    "error",
)


def as_row(extraction: Extraction) -> dict[str, object]:
    """Flat dict used by every writer, so CSV, JSON and Excel cannot drift apart."""
    counts = extraction.counts()
    return {
        "expression": extraction.expression,
        "status": "ok" if extraction.ok else "error",
        "depth": extraction.depth,
        "required": counts["required"],
        "optional": counts["optional"],
        "excluded": counts["excluded"],
        "conditional": counts["conditional"],
        "keywords": "; ".join(
            f"{keyword.render()}[{keyword.polarity.value}]" for keyword in extraction.keywords
        ),
        "source": extraction.source,
        "error": extraction.error,
    }
