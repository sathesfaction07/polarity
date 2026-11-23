from __future__ import annotations

import pytest

from key_extract.syntax.nodes import And, Not, Or, Term, depth, terms
from key_extract.syntax.parser import ParseError, parse
from key_extract.syntax.tokens import TokenizeError, TokenKind, tokenize


def kinds(expression: str) -> list[TokenKind]:
    return [token.kind for token in tokenize(expression)[:-1]]


class TestTokenizer:
    def test_bare_terms_and_operators(self):
        assert kinds("a AND b") == [TokenKind.TERM, TokenKind.AND, TokenKind.TERM]

    @pytest.mark.parametrize("text", ["and", "AND", "And", "aNd"])
    def test_operators_are_case_insensitive(self, text):
        assert kinds(f"a {text} b")[1] is TokenKind.AND

    @pytest.mark.parametrize(
        "word", ["corkscrew", "android", "nothing", "notebook", "organ", "sandal"]
    )
    def test_an_operator_inside_a_word_is_not_an_operator(self, word):
        """
        The classic hand-rolled-tokenizer bug. `corkscrew` contains `or`, `android`
        contains `and`, `notebook` contains `not`. Substring matching turns each of these
        into a syntax error or, worse, a silently different rule.
        """
        assert kinds(word) == [TokenKind.TERM]
        assert tokenize(word)[0].value == word

    def test_symbolic_operator_aliases(self):
        assert kinds("a && b || c") == [
            TokenKind.TERM,
            TokenKind.AND,
            TokenKind.TERM,
            TokenKind.OR,
            TokenKind.TERM,
        ]

    def test_bang_is_not(self):
        assert kinds("!a")[0] is TokenKind.NOT

    def test_quoted_phrases_become_one_token(self):
        tokens = tokenize('"butter knife"')
        assert tokens[0].value == "butter knife"
        assert tokens[0].quoted is True

    def test_single_quotes_work_too(self):
        assert tokenize("'butter knife'")[0].value == "butter knife"

    def test_escapes_inside_quotes(self):
        assert tokenize(r'"say \"hi\""')[0].value == 'say "hi"'

    def test_an_unterminated_quote_is_an_error(self):
        """
        Reading to end of string instead would turn a typo into a rule matching almost
        nothing, quietly.
        """
        with pytest.raises(TokenizeError, match="unterminated quote"):
            tokenize('knife AND "butter')

    def test_fielded_terms_split_on_the_colon(self):
        token = tokenize("category:kitchen")[0]
        assert token.kind is TokenKind.FIELD
        assert token.field == "category"
        assert token.value == "kitchen"

    def test_a_fielded_quoted_value(self):
        token = tokenize('category:"small appliance"')[0]
        assert token.field == "category"
        assert token.value == "small appliance"
        assert token.quoted is True

    def test_a_leading_colon_is_an_error(self):
        with pytest.raises(TokenizeError, match="field name is missing"):
            tokenize(":kitchen")

    def test_a_field_with_no_value_is_an_error(self):
        with pytest.raises(TokenizeError, match="no value"):
            tokenize("category: AND b")

    def test_wildcards_are_recorded_and_stripped(self):
        token = tokenize("tit*")[0]
        assert token.value == "tit"
        assert token.wildcard is True

    def test_a_quoted_wildcard(self):
        token = tokenize('"butter kni"*')[0]
        assert token.value == "butter kni"
        assert token.wildcard is True

    def test_punctuation_survives_inside_terms(self):
        for text in ["multi-tool", "3.5mm", "and/or-ish", "l'outil"]:
            assert tokenize(text)[0].value == text

    def test_positions_are_recorded(self):
        tokens = tokenize("aa AND bb")
        assert tokens[0].position == 0
        assert tokens[1].position == 3
        assert tokens[2].position == 7

    def test_whitespace_is_irrelevant(self):
        assert kinds("a    AND\tb") == kinds("a AND b")

    def test_an_empty_string_is_just_eof(self):
        assert tokenize("")[0].kind is TokenKind.EOF


class TestPrecedence:
    def test_and_binds_tighter_than_or(self):
        assert str(parse("a AND b OR c")) == "((a AND b) OR c)"

    def test_parentheses_override(self):
        assert str(parse("a AND (b OR c)")) == "(a AND (b OR c))"

    def test_the_two_groupings_are_different_trees(self):
        """
        The reason a regex extractor is insufficient: identical keyword lists, different
        rules. `a AND b OR c` matches an item with only `c`; `a AND (b OR c)` does not.
        """
        assert parse("a AND b OR c") != parse("a AND (b OR c)")

    def test_not_binds_tightest(self):
        assert str(parse("NOT a AND b")) == "(NOT a AND b)"

    def test_not_applies_to_a_group(self):
        assert str(parse("NOT (a AND b)")) == "NOT (a AND b)"

    def test_repeated_not(self):
        assert str(parse("NOT NOT a")) == "NOT NOT a"

    def test_adjacent_terms_are_an_implicit_and(self):
        assert str(parse("a b")) == "(a AND b)"

    def test_implicit_and_binds_at_and_precedence(self):
        assert str(parse("a b OR c")) == "((a AND b) OR c)"

    def test_implicit_and_before_a_group(self):
        assert str(parse("a (b OR c)")) == "(a AND (b OR c))"

    def test_implicit_and_before_a_negation(self):
        assert str(parse("a NOT b")) == "(a AND NOT b)"


class TestAssociativityFlattening:
    """
    N-ary nodes with same-type children spliced in, so grouping that does not change
    meaning does not change the tree. Without this the diff reports equivalent
    expressions as re-grouped.
    """

    @pytest.mark.parametrize(
        "expression",
        ["a AND b AND c", "a AND (b AND c)", "(a AND b) AND c", "((a AND b)) AND c"],
    )
    def test_conjunctions_flatten_to_one_node(self, expression):
        assert str(parse(expression)) == "(a AND b AND c)"

    @pytest.mark.parametrize("expression", ["a OR b OR c", "a OR (b OR c)", "(a OR b) OR c"])
    def test_disjunctions_flatten_too(self, expression):
        assert str(parse(expression)) == "(a OR b OR c)"

    def test_mixed_operators_keep_their_shape(self):
        """Flattening across different connectives would change the meaning."""
        assert str(parse("a AND (b OR c)")) == "(a AND (b OR c))"

    def test_a_negated_group_is_not_flattened_into_its_parent(self):
        assert str(parse("a AND NOT (b AND c)")) == "(a AND NOT (b AND c))"

    def test_equivalent_groupings_render_identically(self):
        """
        Rendering is what the diff compares, so this is the property that matters.

        Node equality is deliberately *not* asserted: `Term` carries its source position,
        so the same keyword at a different offset is a different Term. That is wanted for
        error reporting and irrelevant to equivalence.
        """
        assert str(parse("a AND (b AND c)")) == str(parse("(a AND b) AND c"))

    def test_positions_still_differ_between_the_two_forms(self):
        left = terms(parse("a AND (b AND c)"))
        right = terms(parse("(a AND b) AND c"))
        assert [term.surface for term in left] == [term.surface for term in right]
        assert [term.position for term in left] != [term.position for term in right]


class TestParseErrors:
    @pytest.mark.parametrize(
        ("expression", "match"),
        [
            ("", "empty"),
            ("   ", "empty"),
            ("a AND", "expected|needs|end of expression"),
            ("AND b", "needs something on its left"),
            ("OR b", "needs something on its left"),
            ("a AND (b", "unclosed"),
            ("a) AND b", "unmatched"),
            ("()", "empty group"),
            ("a AND ()", "empty group"),
            ("NOT", "nothing to negate"),
            ("a AND NOT", "nothing to negate"),
            ("(a AND NOT)", "nothing to negate"),
        ],
    )
    def test_bad_input_is_rejected_with_a_reason(self, expression, match):
        with pytest.raises(ParseError, match=match):
            parse(expression)

    def test_the_error_points_at_a_column(self):
        with pytest.raises(ParseError) as caught:
            parse("knife AND (blade")
        message = str(caught.value)
        assert "knife AND (blade" in message
        assert "^" in message

    def test_the_caret_lands_under_the_offending_token(self):
        with pytest.raises(ParseError) as caught:
            parse("knife AND (blade")
        lines = str(caught.value).splitlines()
        assert lines[-1].index("^") == lines[-2].index("(")


class TestNodes:
    def test_terms_are_collected_in_order(self):
        found = terms(parse("z AND (y OR x)"))
        assert [term.surface for term in found] == ["z", "y", "x"]

    def test_depth_of_a_single_term_is_one(self):
        assert depth(parse("a")) == 1

    def test_depth_grows_with_nesting(self):
        assert depth(parse("a AND b")) == 2
        assert depth(parse("a AND (b OR c)")) == 3
        assert depth(parse("a AND (b OR (c AND d))")) == 4

    def test_term_renders_its_source_form(self):
        assert str(Term("kitchen", field="category")) == "category:kitchen"
        assert str(Term("butter knife", quoted=True)) == '"butter knife"'
        assert str(Term("tit", wildcard=True)) == "tit*"

    def test_nodes_are_comparable_by_value(self):
        assert And((Term("a"), Term("b"))) == And((Term("a"), Term("b")))
        assert And((Term("a"),)) != Or((Term("a"),))

    def test_not_exposes_its_operand_as_a_child(self):
        node = Not(Term("a"))
        assert node.children() == (Term("a"),)

    def test_walk_visits_every_node(self):
        assert len(list(parse("a AND (b OR c)").walk())) == 5
