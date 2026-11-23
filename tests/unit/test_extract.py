from __future__ import annotations

import pytest

from key_extract.core.extract import extract, extract_from_tree
from key_extract.models import Polarity
from key_extract.syntax.parser import parse

from ..conftest import generated_expressions, truth_table


def polarities(expression: str) -> dict[str, Polarity]:
    return {keyword.surface: keyword.polarity for keyword in extract(expression).keywords}


class TestTheFourPolarities:
    """One case per cell of the (negated, certain) table."""

    def test_conjunction_makes_terms_required(self):
        assert polarities("a AND b") == {
            "a": Polarity.REQUIRED,
            "b": Polarity.REQUIRED,
        }

    def test_disjunction_makes_terms_optional(self):
        """
        Neither branch of an OR is required. Labelling both `required` overstates the
        rule, and it is what a parity-only walk does.
        """
        assert polarities("a OR b") == {
            "a": Polarity.OPTIONAL,
            "b": Polarity.OPTIONAL,
        }

    def test_a_plain_negation_excludes(self):
        assert polarities("NOT a") == {"a": Polarity.EXCLUDED}

    def test_negated_conjunction_is_conditional(self):
        assert polarities("NOT (a AND b)") == {
            "a": Polarity.CONDITIONAL,
            "b": Polarity.CONDITIONAL,
        }

    def test_a_required_term_beside_a_disjunction(self):
        assert polarities("a AND (b OR c)") == {
            "a": Polarity.REQUIRED,
            "b": Polarity.OPTIONAL,
            "c": Polarity.OPTIONAL,
        }


class TestDeMorgan:
    """
    The regression that motivated the second implementation.

    A walk that tracks only negation parity gives `NOT (a OR b)` and `NOT (a AND b)`
    byte-identical output. They mean opposite things.
    """

    def test_negation_over_or_excludes_both_terms(self):
        """NOT (a OR b) == NOT a AND NOT b, so each term is individually forbidden."""
        assert polarities("NOT (a OR b)") == {
            "a": Polarity.EXCLUDED,
            "b": Polarity.EXCLUDED,
        }

    def test_negation_over_and_excludes_only_the_combination(self):
        """
        NOT (a AND b) == NOT a OR NOT b. An item with `a` but not `b` still matches, so
        calling `a` excluded is the opposite of the truth.
        """
        assert polarities("NOT (a AND b)") == {
            "a": Polarity.CONDITIONAL,
            "b": Polarity.CONDITIONAL,
        }

    def test_the_two_forms_no_longer_produce_the_same_answer(self):
        assert polarities("NOT (a OR b)") != polarities("NOT (a AND b)")

    def test_double_negation_returns_to_positive(self):
        assert polarities("NOT NOT a") == {"a": Polarity.REQUIRED}

    def test_negation_nested_twice_swaps_back(self):
        """NOT (a AND NOT (b OR c)) -- the inner OR is under two NOTs, so it acts as OR."""
        found = polarities("NOT (a AND NOT (b OR c))")
        assert found["a"] is Polarity.CONDITIONAL
        assert found["b"] is Polarity.OPTIONAL
        assert found["c"] is Polarity.OPTIONAL

    def test_a_negated_or_inside_a_conjunction_still_excludes(self):
        found = polarities("knife AND NOT (fork OR spoon)")
        assert found["knife"] is Polarity.REQUIRED
        assert found["fork"] is Polarity.EXCLUDED
        assert found["spoon"] is Polarity.EXCLUDED


class TestAgainstTheTruthTable:
    """
    The structural rule checked against brute-force semantics.

    `extract` is linear in the size of the expression; the oracle is exponential in the
    number of distinct keywords. Agreement on generated input is what justifies trusting
    the cheap one.
    """

    @pytest.mark.parametrize("expression", generated_expressions(600, seed=20260916))
    def test_strong_claims_are_never_wrong(self, expression):
        """
        Soundness. A REQUIRED label must mean every satisfying assignment contains the
        keyword, and EXCLUDED that none does. The rule may be weaker than the truth; it
        may never contradict it.
        """
        tree = parse(expression)
        verdicts, _ = truth_table(tree)
        if verdicts is None:
            pytest.skip("unsatisfiable: no claim about any keyword is meaningful")

        for keyword in extract_from_tree(tree):
            truth = verdicts[keyword.surface]
            if keyword.polarity is Polarity.REQUIRED:
                assert truth == "REQUIRED", f"{expression}: {keyword.surface} is not required"
            elif keyword.polarity is Polarity.EXCLUDED:
                assert truth == "EXCLUDED", f"{expression}: {keyword.surface} is not excluded"

    @pytest.mark.parametrize("expression", generated_expressions(200, seed=4242))
    def test_weak_labels_are_used_where_the_truth_varies(self, expression):
        """
        The other direction, as far as it can be asserted: where the truth table says a
        keyword varies, the label must not be one of the strong ones.
        """
        tree = parse(expression)
        verdicts, _ = truth_table(tree)
        if verdicts is None:
            pytest.skip("unsatisfiable")

        for keyword in extract_from_tree(tree):
            if verdicts[keyword.surface] == "VARIES":
                assert keyword.polarity in (Polarity.OPTIONAL, Polarity.CONDITIONAL), (
                    f"{expression}: {keyword.surface} labelled {keyword.polarity.value} "
                    "but the truth table says it varies"
                )

    def test_the_rule_is_conservative_not_exact(self):
        """
        A documented limitation, pinned so it is a known property rather than a surprise.

        `(a AND b) OR a` is satisfied only when `a` is present, so `a` is genuinely
        required. The structural rule sees an OR and reports OPTIONAL. Under-claiming is
        the safe direction for output that people act on.
        """
        verdicts, _ = truth_table(parse("(a AND b) OR a"))
        assert verdicts["a"] == "REQUIRED"
        assert polarities("(a AND b) OR a")["a"] is Polarity.OPTIONAL


class TestExtractionShape:
    def test_keywords_come_back_in_source_order(self):
        found = extract("zebra AND apple AND mango").keywords
        assert [keyword.surface for keyword in found] == ["zebra", "apple", "mango"]

    def test_fielded_terms_keep_their_field(self):
        keyword = extract("category:kitchen").keywords[0]
        assert keyword.field == "category"
        assert keyword.surface == "kitchen"
        assert keyword.key == ("category", "kitchen")

    def test_the_same_word_in_different_fields_is_two_keywords(self):
        found = extract("category:steel AND material:steel")
        assert len(found.polarities()) == 2

    def test_quoting_and_wildcards_are_preserved(self):
        found = extract('"butter knife" AND tit*').keywords
        assert found[0].quoted is True
        assert found[1].wildcard is True
        assert found[1].surface == "tit"

    def test_the_tree_and_depth_are_reported(self):
        result = extract("a AND (b OR c)")
        assert result.tree == "(a AND (b OR c))"
        assert result.depth == 3

    def test_normalised_form_is_computed(self):
        assert extract("KNIFE").keywords[0].normalized == "knife"


class TestContradictions:
    def test_a_word_both_required_and_excluded_is_flagged(self):
        result = extract("knife AND NOT knife")
        assert result.contradictions == ["knife"]

    def test_both_occurrences_are_kept(self):
        """
        Grouping by identity must not drop one. A dict comprehension keyed on identity
        silently keeps the last occurrence, which hides the contradiction entirely.
        """
        result = extract("knife AND NOT knife")
        assert len(result.keywords) == 2
        assert result.polarities()[("", "knife")] == frozenset(
            {Polarity.REQUIRED, Polarity.EXCLUDED}
        )

    def test_a_contradiction_inside_a_group_is_still_found(self):
        assert extract("c AND NOT (b OR c)").contradictions == ["c"]

    def test_optional_plus_conditional_is_not_a_contradiction(self):
        """`a OR NOT a` is a tautology, not a contradiction. It is always satisfiable."""
        result = extract("a OR NOT a")
        assert result.contradictions == []

    def test_a_fielded_contradiction_names_the_field(self):
        assert extract("category:x AND NOT category:x").contradictions == ["category:x"]

    def test_a_clean_expression_reports_none(self):
        assert extract('knife AND NOT "butter knife"').contradictions == []


class TestParseFailuresBecomeResults:
    def test_a_syntax_error_is_carried_not_raised(self):
        result = extract("a AND")
        assert result.ok is False
        assert result.keywords == []
        assert "expected" in result.error or "needs" in result.error

    def test_the_error_carries_a_caret(self):
        assert "^" in extract("a AND (b").error

    def test_an_empty_expression_is_an_error(self):
        assert extract("   ").ok is False

    def test_the_source_is_recorded_even_on_failure(self):
        assert extract("(((").source == "parser"
