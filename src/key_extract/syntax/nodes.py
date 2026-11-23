from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field


class Node:
    """
    Base for expression AST nodes.

    Deliberately a small closed set -- Term, Not, And, Or -- rather than a generic
    `BinaryOp(op, left, right)`. Extraction has to treat AND and OR differently (see
    `core/extract.py`), and a generic node would push that distinction into a string
    comparison at every use site.
    """

    def children(self) -> tuple[Node, ...]:
        return ()

    def walk(self) -> Iterator[Node]:
        yield self
        for child in self.children():
            yield from child.walk()


@dataclass(frozen=True)
class Term(Node):
    """
    A leaf: a keyword, phrase, or fielded value.

    `surface` keeps the text exactly as written so a report can show what the author
    typed. Comparison uses a normalised form computed separately, because normalising
    in place would lose the ability to say "you wrote 'Knife', the other version has
    'knife'".
    """

    surface: str
    field: str = ""
    quoted: bool = False
    wildcard: bool = False
    position: int = 0

    def __str__(self) -> str:
        body = f'"{self.surface}"' if self.quoted else self.surface
        if self.wildcard:
            body += "*"
        return f"{self.field}:{body}" if self.field else body


@dataclass(frozen=True)
class Not(Node):
    operand: Node

    def children(self) -> tuple[Node, ...]:
        return (self.operand,)

    def __str__(self) -> str:
        return f"NOT {self.operand}"


@dataclass(frozen=True)
class And(Node):
    """
    N-ary rather than binary.

    `a AND b AND c` is one And with three operands, not nested pairs. Nesting would
    make the AST shape depend on parse order, which then leaks into the diff: two
    expressions that mean the same thing would compare as different.
    """

    operands: tuple[Node, ...] = field(default_factory=tuple)

    def children(self) -> tuple[Node, ...]:
        return self.operands

    def __str__(self) -> str:
        return "(" + " AND ".join(str(operand) for operand in self.operands) + ")"


@dataclass(frozen=True)
class Or(Node):
    operands: tuple[Node, ...] = field(default_factory=tuple)

    def children(self) -> tuple[Node, ...]:
        return self.operands

    def __str__(self) -> str:
        return "(" + " OR ".join(str(operand) for operand in self.operands) + ")"


def terms(node: Node) -> list[Term]:
    """Every Term leaf, in source order."""
    return [found for found in node.walk() if isinstance(found, Term)]


def depth(node: Node) -> int:
    """
    Maximum nesting depth. Used to report how complex an expression is.

    Depth is what separates the expressions a regex extractor gets right from the ones
    it silently gets wrong, so it is worth surfacing rather than merely computing.
    """
    children = node.children()
    if not children:
        return 1
    return 1 + max(depth(child) for child in children)
