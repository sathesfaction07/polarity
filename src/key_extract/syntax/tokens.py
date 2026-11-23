from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class TokenKind(StrEnum):
    TERM = "term"
    FIELD = "field"
    AND = "and"
    OR = "or"
    NOT = "not"
    LPAREN = "lparen"
    RPAREN = "rparen"
    EOF = "eof"


@dataclass(frozen=True)
class Token:
    """
    One lexical unit, carrying its source position.

    `position` is kept so a parse error can point at the offending character rather
    than saying "syntax error". On a 300-character classification expression that is
    the difference between a fixable report and a shrug.
    """

    kind: TokenKind
    value: str = ""
    field: str = ""
    quoted: bool = False
    wildcard: bool = False
    position: int = 0


class TokenizeError(ValueError):
    """Raised with a caret pointing at the offending column."""

    def __init__(self, message: str, expression: str, position: int) -> None:
        self.expression = expression
        self.position = position
        super().__init__(f"{message}\n  {expression}\n  {' ' * position}^")


# Operators are matched case-insensitively but must be whole words, or the `or` inside
# "corkscrew" would tokenize as an operator. This is the single most common way a
# hand-rolled boolean tokenizer goes wrong.
_OPERATORS = {
    "and": TokenKind.AND,
    "or": TokenKind.OR,
    "not": TokenKind.NOT,
}

# A bare term runs until whitespace, a paren, or a colon. Colons are excluded so
# `field:value` splits, and the rest of the punctuation is allowed because real
# classification keywords contain hyphens, dots, slashes and apostrophes.
_BARE = re.compile(r"[^\s()\:]+")

_SYMBOLIC = {
    "&&": TokenKind.AND,
    "||": TokenKind.OR,
}


def tokenize(expression: str) -> list[Token]:
    """
    Turn an expression into tokens.

    Supports:

        bare terms          knife
        quoted phrases      "butter knife"
        fielded terms       category:kitchen   category:"small appliance"
        wildcards           tit*
        operators           AND OR NOT, and && || as aliases
        grouping            ( )

    Adjacent terms with no operator between them are left as adjacent TERM tokens;
    the parser decides what that means. Keeping that decision out of the tokenizer
    means the implicit-AND rule is stated in exactly one place.
    """
    tokens: list[Token] = []
    index = 0
    length = len(expression)

    while index < length:
        char = expression[index]

        if char.isspace():
            index += 1
            continue

        if char == "(":
            tokens.append(Token(TokenKind.LPAREN, "(", position=index))
            index += 1
            continue

        if char == ")":
            tokens.append(Token(TokenKind.RPAREN, ")", position=index))
            index += 1
            continue

        if char == "!":
            tokens.append(Token(TokenKind.NOT, "!", position=index))
            index += 1
            continue

        two = expression[index : index + 2]
        if two in _SYMBOLIC:
            tokens.append(Token(_SYMBOLIC[two], two, position=index))
            index += 2
            continue

        if char == ":":
            raise TokenizeError("a field name is missing before ':'", expression, index)

        if char in "\"'":
            token, index = _read_quoted(expression, index, field="")
            tokens.append(token)
            continue

        match = _BARE.match(expression, index)
        if match is None:  # pragma: no cover - _BARE matches any non-delimiter
            raise TokenizeError(f"unexpected character {char!r}", expression, index)

        raw = match.group()
        start = index
        index = match.end()

        lowered = raw.lower()
        if lowered in _OPERATORS:
            tokens.append(Token(_OPERATORS[lowered], lowered, position=start))
            continue

        # A colon immediately after a bare word makes it a field name.
        if index < length and expression[index] == ":":
            field = raw
            index += 1
            if index >= length or expression[index].isspace() or expression[index] in "()":
                raise TokenizeError(
                    f"field '{field}' has no value after ':'", expression, min(index, length - 1)
                )
            if expression[index] in "\"'":
                token, index = _read_quoted(expression, index, field=field)
                tokens.append(Token(TokenKind.FIELD, token.value, field, True, False, start))
                continue
            value_match = _BARE.match(expression, index)
            if value_match is None:  # pragma: no cover
                raise TokenizeError(f"field '{field}' has no value", expression, index)
            value = value_match.group()
            index = value_match.end()
            tokens.append(
                Token(
                    TokenKind.FIELD,
                    value.rstrip("*"),
                    field,
                    False,
                    value.endswith("*"),
                    start,
                )
            )
            continue

        tokens.append(
            Token(
                TokenKind.TERM,
                raw.rstrip("*"),
                "",
                False,
                raw.endswith("*"),
                start,
            )
        )

    tokens.append(Token(TokenKind.EOF, position=length))
    return tokens


def _read_quoted(expression: str, index: int, *, field: str) -> tuple[Token, int]:
    """
    Read a quoted phrase, supporting backslash escapes.

    An unterminated quote is a hard error rather than "read to end of string". Silently
    swallowing the rest of the expression would turn a typo into a rule that matches
    almost nothing, and it would do it quietly.
    """
    quote = expression[index]
    start = index
    index += 1
    chars: list[str] = []

    while index < len(expression):
        char = expression[index]
        if char == "\\" and index + 1 < len(expression):
            chars.append(expression[index + 1])
            index += 2
            continue
        if char == quote:
            wildcard = False
            if index + 1 < len(expression) and expression[index + 1] == "*":
                wildcard = True
                index += 1
            return (
                Token(
                    TokenKind.FIELD if field else TokenKind.TERM,
                    "".join(chars),
                    field,
                    True,
                    wildcard,
                    start,
                ),
                index + 1,
            )
        chars.append(char)
        index += 1

    raise TokenizeError("unterminated quote", expression, start)
