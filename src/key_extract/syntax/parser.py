from __future__ import annotations

from .nodes import And, Node, Not, Or, Term
from .tokens import Token, TokenKind, tokenize


class ParseError(ValueError):
    """
    Raised with the expression and a caret under the offending token.

    Classification expressions are written by people, often long, often pasted from
    somewhere else. "Syntax error" is useless; the column is the whole message.
    """

    def __init__(self, message: str, expression: str, position: int) -> None:
        self.expression = expression
        self.position = position
        clipped = min(position, len(expression))
        super().__init__(f"{message}\n  {expression}\n  {' ' * clipped}^")


class Parser:
    """
    Recursive descent over the token stream. Precedence, loosest to tightest:

        OR      a OR b
        AND     a AND b, and adjacent terms (implicit AND)
        NOT     NOT a, !a
        atom    term, or ( ... )

    **Precedence is the reason this is a parser and not a regex.** `a AND b OR c` means
    `(a AND b) OR c`. A keyword extractor built on `re.findall` returns the same three
    keywords for that as for `a AND (b OR c)`, which are different rules -- the first
    matches anything with c alone, the second does not. Nothing about the flat keyword
    list reveals the difference.

    Implicit AND is supported because real expressions are pasted from search bars where
    it is the convention. It is bound at AND precedence, so `a b OR c` parses as
    `(a AND b) OR c`, consistent with the explicit form.
    """

    def __init__(self, expression: str) -> None:
        self.expression = expression
        self.tokens = tokenize(expression)
        self.index = 0

    # -- token access -----------------------------------------------------------

    def peek(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.tokens[self.index]
        if token.kind is not TokenKind.EOF:
            self.index += 1
        return token

    def expect(self, kind: TokenKind, description: str) -> Token:
        token = self.peek()
        if token.kind is not kind:
            raise ParseError(
                f"expected {description}, found {_describe(token)}",
                self.expression,
                token.position,
            )
        return self.advance()

    # -- grammar ----------------------------------------------------------------

    def parse(self) -> Node:
        if self.peek().kind is TokenKind.EOF:
            raise ParseError("the expression is empty", self.expression, 0)

        node = self.parse_or()

        trailing = self.peek()
        if trailing.kind is not TokenKind.EOF:
            if trailing.kind is TokenKind.RPAREN:
                raise ParseError("unmatched ')'", self.expression, trailing.position)
            raise ParseError(  # pragma: no cover - implicit AND consumes stray terms
                f"unexpected {_describe(trailing)}", self.expression, trailing.position
            )
        return node

    def parse_or(self) -> Node:
        operands = [self.parse_and()]
        while self.peek().kind is TokenKind.OR:
            self.advance()
            operands.append(self.parse_and())
        if len(operands) == 1:
            return operands[0]
        return Or(_flatten(Or, operands))

    def parse_and(self) -> Node:
        operands = [self.parse_not()]
        while True:
            token = self.peek()
            if token.kind is TokenKind.AND:
                self.advance()
                operands.append(self.parse_not())
                continue
            # Implicit AND: two atoms side by side with no operator.
            if token.kind in (TokenKind.TERM, TokenKind.FIELD, TokenKind.NOT, TokenKind.LPAREN):
                operands.append(self.parse_not())
                continue
            break
        if len(operands) == 1:
            return operands[0]
        return And(_flatten(And, operands))

    def parse_not(self) -> Node:
        if self.peek().kind is TokenKind.NOT:
            token = self.advance()
            operand_token = self.peek()
            if operand_token.kind in (TokenKind.EOF, TokenKind.RPAREN):
                raise ParseError("NOT has nothing to negate", self.expression, token.position)
            return Not(self.parse_not())
        return self.parse_atom()

    def parse_atom(self) -> Node:
        token = self.peek()

        if token.kind is TokenKind.LPAREN:
            open_token = self.advance()
            if self.peek().kind is TokenKind.RPAREN:
                raise ParseError("empty group '()'", self.expression, open_token.position)
            inner = self.parse_or()
            if self.peek().kind is not TokenKind.RPAREN:
                raise ParseError("unclosed '('", self.expression, open_token.position)
            self.advance()
            return inner

        if token.kind in (TokenKind.TERM, TokenKind.FIELD):
            self.advance()
            return Term(
                surface=token.value,
                field=token.field,
                quoted=token.quoted,
                wildcard=token.wildcard,
                position=token.position,
            )

        if token.kind in (TokenKind.AND, TokenKind.OR):
            raise ParseError(
                f"'{token.value.upper()}' needs something on its left",
                self.expression,
                token.position,
            )

        raise ParseError(  # pragma: no cover - every kind is handled above
            f"unexpected {_describe(token)}", self.expression, token.position
        )


def parse(expression: str) -> Node:
    """Parse an expression into an AST, raising ParseError with a column on failure."""
    return Parser(expression).parse()


def _flatten(kind: type[And] | type[Or], operands: list[Node]) -> tuple[Node, ...]:
    """
    Splice same-type children into the parent, so AND and OR are associative.

    `a AND (b AND c)` and `(a AND b) AND c` mean the same thing, and without flattening
    they build different trees. That difference is invisible in the keyword list but the
    diff compares trees to detect re-grouping, so it would report two equivalent
    expressions as a structural change -- a false positive on the one signal a keyword
    diff cannot otherwise provide.

    Only same-type nodes are spliced. `a AND (b OR c)` keeps its shape, because there the
    grouping genuinely changes the meaning.
    """
    flat: list[Node] = []
    for operand in operands:
        if isinstance(operand, kind):
            flat.extend(operand.operands)
        else:
            flat.append(operand)
    return tuple(flat)


def _describe(token: Token) -> str:
    if token.kind is TokenKind.EOF:
        return "end of expression"
    if token.kind in (TokenKind.TERM, TokenKind.FIELD):
        return f"term '{token.value}'"
    return f"'{token.value}'"
