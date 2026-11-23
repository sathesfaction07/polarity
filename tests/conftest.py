from __future__ import annotations

import random
from itertools import product

import pytest

from key_extract.models import Polarity
from key_extract.syntax.nodes import And, Node, Not, Or, Term, terms
from key_extract.syntax.parser import parse


def evaluate(node: Node, present: set[str]) -> bool:
    """Reference semantics: is the expression satisfied by this set of keywords?"""
    if isinstance(node, Term):
        return node.surface in present
    if isinstance(node, Not):
        return not evaluate(node.operand, present)
    if isinstance(node, And):
        return all(evaluate(child, present) for child in node.operands)
    if isinstance(node, Or):
        return any(evaluate(child, present) for child in node.operands)
    raise TypeError(f"unexpected node: {node!r}")


def truth_table(node: Node) -> tuple[dict[str, str] | None, int]:
    """
    Brute-force ground truth for every keyword in an expression.

    Enumerates all 2^n presence combinations, keeps the satisfying ones, and asks of each
    keyword whether it is present in all of them (genuinely required), absent from all of
    them (genuinely excluded), or neither.

    This is the oracle the structural rule in `core/extract.py` is checked against. It is
    exponential and therefore useless in production, which is exactly why the structural
    rule exists -- but it is cheap enough on four keywords to validate against.

    Returns `(None, 0)` for an unsatisfiable expression, where no claim about any keyword
    is meaningful.
    """
    names = sorted({term.surface for term in terms(node)})
    satisfying = [
        {name for name, flag in zip(names, bits, strict=True) if flag}
        for bits in product([False, True], repeat=len(names))
        if evaluate(node, {name for name, flag in zip(names, bits, strict=True) if flag})
    ]
    if not satisfying:
        return None, 0

    verdicts = {}
    for name in names:
        if all(name in case for case in satisfying):
            verdicts[name] = "REQUIRED"
        elif all(name not in case for case in satisfying):
            verdicts[name] = "EXCLUDED"
        else:
            verdicts[name] = "VARIES"
    return verdicts, len(satisfying)


def random_expression(rng: random.Random, names: list[str], depth: int = 0) -> str:
    """
    Generate a random expression. Biased toward nesting, since that is where the
    interesting failures live.
    """
    if depth >= 3 or rng.random() < 0.32:
        return rng.choice(names)
    roll = rng.random()
    if roll < 0.30:
        return f"NOT {random_expression(rng, names, depth + 1)}"
    operator = "AND" if roll < 0.65 else "OR"
    count = rng.choice([2, 2, 3])
    parts = [random_expression(rng, names, depth + 1) for _ in range(count)]
    return "(" + f" {operator} ".join(parts) + ")"


def generated_expressions(count: int, seed: int, names: list[str] | None = None) -> list[str]:
    rng = random.Random(seed)
    pool = names or ["a", "b", "c", "d"]
    return [random_expression(rng, pool) for _ in range(count)]


@pytest.fixture
def oracle():
    """Parse an expression and return the brute-force verdicts for its keywords."""

    def run(expression: str) -> dict[str, str] | None:
        verdicts, _ = truth_table(parse(expression))
        return verdicts

    return run


STRONG = {Polarity.REQUIRED, Polarity.EXCLUDED}


__all__ = [
    "STRONG",
    "evaluate",
    "generated_expressions",
    "random_expression",
    "truth_table",
]
