from __future__ import annotations

from ..models import Extraction, Keyword, Polarity
from ..syntax.nodes import And, Node, Not, Or, Term, depth
from ..syntax.parser import ParseError, parse
from ..syntax.tokens import TokenizeError
from .normalize import normalize

# Polarity for each (negated, certain) combination. Written as a table because the four
# cases are the whole idea and a nest of ifs hides it.
_POLARITY = {
    (False, True): Polarity.REQUIRED,
    (False, False): Polarity.OPTIONAL,
    (True, True): Polarity.EXCLUDED,
    (True, False): Polarity.CONDITIONAL,
}


def extract_from_tree(node: Node) -> list[Keyword]:
    """
    Walk the AST and label every keyword with what its presence actually means.

    Two pieces of context descend with the walk.

    **`negated`** is the parity of NOTs above this node.

    **`certain`** is whether reaching this node is unavoidable for the rule to be
    satisfied. It starts true at the root, and an OR turns it off for its children
    because only one branch has to hold.

    The combination gives four polarities, and all four are needed:

        negated  certain   polarity      example
        -------  -------   -----------   -------------------------
        no       yes       REQUIRED      a in `a AND b`
        no       no        OPTIONAL      a in `a OR b`
        yes      yes       EXCLUDED      a in `NOT a`
        yes      no        CONDITIONAL   a in `NOT (a AND b)`

    **Negation swaps the roles of AND and OR**, which is the part that a parity-only
    walk gets wrong. By De Morgan:

        NOT (a OR b)   ==  NOT a AND NOT b    both terms individually excluded
        NOT (a AND b)  ==  NOT a OR  NOT b    only the *combination* is excluded

    An earlier version tracked parity alone and returned `excluded` for both. That is
    not a rounding error, it is the opposite of the truth for the second case: an item
    containing `a` but not `b` still satisfies `NOT (a AND b)`. The two expressions
    produced byte-identical output despite meaning opposite things.

    **Soundness.** This is a structural rule, so it is conservative rather than exact.
    It never labels a keyword REQUIRED unless every satisfying assignment contains it,
    and never EXCLUDED unless every satisfying assignment omits it -- but redundancy
    defeats it in the safe direction. `(a AND b) OR a` is satisfied only when `a` is
    present, so `a` is genuinely required; this returns OPTIONAL. Erring toward the
    weaker claim is the right way round for a tool whose output people act on, and
    `tests/unit/test_extract.py` property-tests exactly this against a brute-force
    truth-table oracle over generated expressions.
    """
    found: list[Keyword] = []
    _visit(node, negated=False, certain=True, out=found)
    found.sort(key=lambda keyword: keyword.position)
    return found


def _visit(node: Node, *, negated: bool, certain: bool, out: list[Keyword]) -> None:
    if isinstance(node, Term):
        out.append(
            Keyword(
                surface=node.surface,
                normalized=normalize(node.surface),
                field=node.field,
                polarity=_POLARITY[(negated, certain)],
                quoted=node.quoted,
                wildcard=node.wildcard,
                position=node.position,
            )
        )
        return

    if isinstance(node, Not):
        _visit(node.operand, negated=not negated, certain=certain, out=out)
        return

    if isinstance(node, And | Or):
        # Under a negation the connective behaves as its dual, so what matters is
        # whether this node acts conjunctively *after* De Morgan is applied.
        acts_as_and = isinstance(node, And) != negated
        child_certain = certain if acts_as_and else False
        for child in node.operands:
            _visit(child, negated=negated, certain=child_certain, out=out)
        return

    for child in node.children():  # pragma: no cover - no other node types exist
        _visit(child, negated=negated, certain=certain, out=out)


def extract(expression: str) -> Extraction:
    """Parse and extract, returning a populated Extraction or one carrying the error."""
    try:
        tree = parse(expression)
    except (ParseError, TokenizeError) as exc:
        return Extraction(expression=expression, error=str(exc), source="parser")

    return Extraction(
        expression=expression,
        keywords=extract_from_tree(tree),
        tree=str(tree),
        depth=depth(tree),
        source="parser",
    )


__all__ = ["extract", "extract_from_tree"]
