from __future__ import annotations

import pytest

from key_extract.core.diff import diff_expressions
from key_extract.models import ChangeKind


def kinds(left: str, right: str) -> list[ChangeKind]:
    return [change.kind for change in diff_expressions(left, right).changes]


class TestNoChange:
    def test_identical_expressions(self):
        assert diff_expressions("a AND b", "a AND b").unchanged is True

    def test_whitespace_is_irrelevant(self):
        assert diff_expressions("a AND b", "a    AND   b").unchanged is True

    def test_equivalent_grouping_is_silent(self):
        """
        Associativity is normalised by the parser, so a change that cannot alter the
        meaning must not appear in the report. Reporting it would train people to skim.
        """
        assert diff_expressions("a AND (b AND c)", "(a AND b) AND c").unchanged is True

    def test_operator_aliases_are_silent(self):
        assert diff_expressions("a AND b", "a && b").unchanged is True

    def test_reordering_an_or_is_reported_as_regrouped_not_as_churn(self):
        """
        Order within a connective is not normalised, so this shows as a structural note
        rather than as four keyword changes.
        """
        result = diff_expressions("a OR b", "b OR a")
        assert result.changes == []
        assert result.structural_only is True


class TestAddedAndRemoved:
    def test_an_added_keyword(self):
        result = diff_expressions("a OR b", "a OR b OR c")
        assert kinds("a OR b", "a OR b OR c") == [ChangeKind.ADDED]
        assert result.changes[0].keyword == "c"
        assert "optional" in result.changes[0].note

    def test_a_removed_keyword(self):
        result = diff_expressions("a OR b OR c", "a OR b")
        assert [change.kind for change in result.changes] == [ChangeKind.REMOVED]
        assert result.changes[0].keyword == "c"

    def test_the_note_states_the_polarity_it_had(self):
        result = diff_expressions('a AND NOT "butter knife"', "a")
        assert "excluded" in result.changes[0].note

    def test_a_fielded_keyword_keeps_its_field(self):
        result = diff_expressions("a", "a AND category:kitchen")
        assert result.changes[0].field == "category"
        assert "category:kitchen" in result.changes[0].render()

    def test_the_same_word_in_a_different_field_is_a_different_keyword(self):
        result = diff_expressions("material:steel", "category:steel")
        assert {change.kind for change in result.changes} == {
            ChangeKind.ADDED,
            ChangeKind.REMOVED,
        }


class TestPolarityFlips:
    """
    The reason this is not a set difference.

    A set diff reports a flip as one removal plus one addition, which reads as churn.
    """

    def test_excluded_becoming_required_is_one_change(self):
        result = diff_expressions('a AND NOT "bk"', 'a AND "bk"')
        assert len(result.changes) == 1
        assert result.changes[0].kind is ChangeKind.POLARITY_FLIPPED

    def test_the_direction_is_reported(self):
        change = diff_expressions('a AND NOT "bk"', 'a AND "bk"').changes[0]
        assert change.before == "excluded"
        assert change.after == "required"

    def test_forbidden_to_mandatory_is_called_out_as_severe(self):
        change = diff_expressions("a AND NOT b", "a AND b").changes[0]
        assert change.note == "was forbidden, is now mandatory"

    def test_mandatory_to_forbidden_is_called_out_as_severe(self):
        change = diff_expressions("a AND b", "a AND NOT b").changes[0]
        assert change.note == "was mandatory, is now forbidden"

    def test_loosening_reads_differently_from_inverting(self):
        """
        Required becoming optional weakens the rule; required becoming excluded inverts
        it. A reviewer needs those to read differently.
        """
        loosened = diff_expressions("a AND b", "a OR b").changes[0]
        assert "looser" in loosened.note
        assert "forbidden" not in loosened.note

    def test_tightening_is_named(self):
        assert "stricter" in diff_expressions("a OR b", "a AND b").changes[0].note

    def test_a_regrouping_that_changes_meaning_shows_as_a_flip(self):
        """
        `a AND (b OR c)` requires a; `(a AND b) OR c` does not. Same three keywords, and
        the difference surfaces because polarity is derived from structure.
        """
        result = diff_expressions("a AND (b OR c)", "(a AND b) OR c")
        assert [change.kind for change in result.changes] == [ChangeKind.POLARITY_FLIPPED]
        assert result.changes[0].keyword == "a"

    def test_de_morgan_forms_differ(self):
        result = diff_expressions("NOT (a OR b)", "NOT (a AND b)")
        assert len(result.changes) == 2
        assert all(change.kind is ChangeKind.POLARITY_FLIPPED for change in result.changes)


class TestContradictionsInADiff:
    def test_becoming_self_contradictory_is_named(self):
        result = diff_expressions("NOT knife", "knife AND NOT knife")
        flip = next(
            change for change in result.changes if change.kind is ChangeKind.POLARITY_FLIPPED
        )
        assert flip.note == "now both required and excluded: the rule cannot match"

    def test_a_contradiction_is_reported_per_side(self):
        result = diff_expressions("knife", "knife AND NOT knife")
        assert result.left_contradictions == []
        assert result.right_contradictions == ["knife"]

    def test_resolving_a_contradiction_is_named(self):
        result = diff_expressions("knife AND NOT knife", "knife")
        flip = next(
            change for change in result.changes if change.kind is ChangeKind.POLARITY_FLIPPED
        )
        assert flip.note == "no longer self-contradictory"

    def test_a_duplicate_keyword_does_not_collapse(self):
        """
        Regression. Both sides here require a different term while excluding the same
        pair, and keying a dict on identity made this compare as no keyword changes.
        """
        result = diff_expressions("c AND NOT (b OR c)", "b AND NOT (b OR c)")
        assert result.changes, "the difference in what is required was lost"
        assert result.structural_only is False

    def test_multiple_polarities_are_rendered_together(self):
        result = diff_expressions("knife", "knife AND NOT knife")
        flip = next(
            change for change in result.changes if change.kind is ChangeKind.POLARITY_FLIPPED
        )
        assert flip.after == "excluded+required"


class TestCosmeticChanges:
    def test_case_only_change(self):
        result = diff_expressions("a AND Blade", "a AND blade")
        assert [change.kind for change in result.changes] == [ChangeKind.COSMETIC]

    def test_the_note_says_it_normalises_the_same(self):
        change = diff_expressions("a AND Blade", "a AND blade").changes[0]
        assert "normalises" in change.note
        assert change.before == "Blade"
        assert change.after == "blade"

    def test_a_fullwidth_variant_is_cosmetic(self):
        result = diff_expressions("a AND ｋｎｉｆｅ", "a AND knife")
        assert [change.kind for change in result.changes] == [ChangeKind.COSMETIC]

    def test_internal_whitespace_in_a_phrase_is_cosmetic(self):
        result = diff_expressions('a AND "butter  knife"', 'a AND "butter knife"')
        assert [change.kind for change in result.changes] == [ChangeKind.COSMETIC]

    def test_a_cosmetic_change_is_not_reported_as_add_plus_remove(self):
        assert len(diff_expressions("a AND Blade", "a AND blade").changes) == 1


class TestDiffErrors:
    def test_a_bad_left_expression_is_reported_as_left(self):
        result = diff_expressions("a AND", "a AND b")
        assert result.ok is False
        assert result.error.startswith("left:")

    def test_a_bad_right_expression_is_reported_as_right(self):
        result = diff_expressions("a AND b", "a AND")
        assert result.ok is False
        assert result.error.startswith("right:")

    def test_no_changes_are_produced_when_parsing_failed(self):
        assert diff_expressions("(((", "a").changes == []

    def test_counts_cover_every_kind(self):
        counts = diff_expressions("a OR b", "a OR c").counts()
        assert set(counts) == {kind.value for kind in ChangeKind}


class TestChangeRendering:
    def test_a_flip_renders_before_and_after(self):
        change = diff_expressions("a AND NOT b", "a AND b").changes[0]
        assert change.render() == "b  excluded -> required"

    def test_a_cosmetic_change_renders_quoted(self):
        change = diff_expressions("a AND Blade", "a AND blade").changes[0]
        assert change.render() == "blade  'Blade' -> 'blade'"

    def test_an_addition_renders_the_keyword(self):
        assert diff_expressions("a", "a AND b").changes[0].render() == "b"

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("a AND b", "a AND b AND c"),
            ("a AND b", "a"),
            ("a AND NOT b", "a AND b"),
            ("a AND Blade", "a AND blade"),
        ],
    )
    def test_every_change_carries_a_note(self, left, right):
        for change in diff_expressions(left, right).changes:
            assert change.note
