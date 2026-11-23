from __future__ import annotations

from ..models import Change, ChangeKind, Diff, Extraction, Polarity
from .extract import extract
from .normalize import is_cosmetic_change


def diff_expressions(left: str, right: str) -> Diff:
    """
    Compare two versions of a classification expression.

    A set difference over keyword lists is the obvious implementation and it loses the
    findings that matter most.

    **A polarity flip reads as a removal plus an addition.** Moving `"butter knife"`
    from excluded to required is the most consequential single-token edit a rule can
    undergo -- it inverts what the rule does with that term. A set diff reports
    `- butter knife` and `+ butter knife`, which looks like churn and gets skimmed past.
    Here it is one `POLARITY_FLIPPED` change stating the before, the after, and why the
    particular direction matters.

    **A keyword can carry more than one polarity.** `c AND NOT (b OR c)` has `c` both
    required and excluded. Keying a dict on identity silently keeps whichever occurrence
    came last, which made `(c AND NOT (b OR c))` and `(b AND NOT (b OR c))` compare as
    having no keyword changes at all -- despite requiring different terms. Polarities are
    therefore compared as sets, and a keyword that is both required and excluded is
    reported as a contradiction.

    **Re-grouping changes nothing in the keyword list.** `structural_only` covers the
    residual case where keywords and polarity sets are identical but the trees differ.
    Associativity is already normalised away by the parser, so this fires only for
    genuinely different groupings.

    Keywords are matched on `(field, normalized)`, so a change in casing or a fullwidth
    character is reported as `COSMETIC` rather than as a removal plus an addition.
    """
    left_result = extract(left)
    right_result = extract(right)

    if not left_result.ok:
        return Diff(left=left, right=right, error=f"left: {left_result.error}")
    if not right_result.ok:
        return Diff(left=left, right=right, error=f"right: {right_result.error}")

    before = left_result.polarities()
    after = right_result.polarities()
    left_surface = _surfaces(left_result)
    right_surface = _surfaces(right_result)

    changes: list[Change] = []

    for key in sorted(before.keys() - after.keys()):
        field, _ = key
        changes.append(
            Change(
                kind=ChangeKind.REMOVED,
                keyword=left_surface[key],
                field=field,
                before=_render(before[key]),
                note=f"was {_render(before[key])}",
            )
        )

    for key in sorted(after.keys() - before.keys()):
        field, _ = key
        changes.append(
            Change(
                kind=ChangeKind.ADDED,
                keyword=right_surface[key],
                field=field,
                after=_render(after[key]),
                note=f"now {_render(after[key])}",
            )
        )

    for key in sorted(before.keys() & after.keys()):
        field, _ = key
        old_set, new_set = before[key], after[key]
        old_text, new_text = left_surface[key], right_surface[key]

        if old_set != new_set:
            changes.append(
                Change(
                    kind=ChangeKind.POLARITY_FLIPPED,
                    keyword=new_text,
                    field=field,
                    before=_render(old_set),
                    after=_render(new_set),
                    note=_flip_note(old_set, new_set),
                )
            )
        elif is_cosmetic_change(old_text, new_text):
            changes.append(
                Change(
                    kind=ChangeKind.COSMETIC,
                    keyword=new_text,
                    field=field,
                    before=old_text,
                    after=new_text,
                    note="normalises to the same keyword",
                )
            )

    structural_only = not changes and left_result.tree != right_result.tree

    return Diff(
        left=left,
        right=right,
        changes=changes,
        left_tree=left_result.tree,
        right_tree=right_result.tree,
        structural_only=structural_only,
        left_contradictions=left_result.contradictions,
        right_contradictions=right_result.contradictions,
    )


def _surfaces(result: Extraction) -> dict[tuple[str, str], str]:
    """First surface form seen for each identity, for display."""
    return {key: occurrences[0].surface for key, occurrences in result.grouped().items()}


def _render(polarities: frozenset[Polarity]) -> str:
    return "+".join(sorted(polarity.value for polarity in polarities))


def _flip_note(old: frozenset[Polarity], new: frozenset[Polarity]) -> str:
    """
    Spell out why a particular flip matters.

    The direction is not symmetric. Required becoming excluded inverts the rule for that
    keyword; required becoming optional merely loosens it. A reviewer skimming a list of
    flips needs the severe ones to read differently from the routine ones.
    """
    gained = new - old
    lost = old - new
    contradictory = {Polarity.REQUIRED, Polarity.EXCLUDED}

    # Becoming self-contradictory is the most important transition to name, and a
    # set-versus-set report of "excluded -> excluded+required" does not say it.
    if contradictory <= new and not contradictory <= old:
        return "now both required and excluded: the rule cannot match"
    if contradictory <= old and not contradictory <= new:
        return "no longer self-contradictory"

    if Polarity.EXCLUDED in gained and Polarity.REQUIRED in lost:
        return "was mandatory, is now forbidden"
    if Polarity.REQUIRED in gained and Polarity.EXCLUDED in lost:
        return "was forbidden, is now mandatory"
    if Polarity.EXCLUDED in gained and Polarity.OPTIONAL in lost:
        return "was allowed, is now forbidden"
    if Polarity.OPTIONAL in gained and Polarity.EXCLUDED in lost:
        return "was forbidden, is now allowed"
    if Polarity.REQUIRED in lost and Polarity.OPTIONAL in gained:
        return "no longer mandatory, the rule is looser"
    if Polarity.OPTIONAL in lost and Polarity.REQUIRED in gained:
        return "now mandatory, the rule is stricter"
    if Polarity.EXCLUDED in gained:
        return "now forbidden as well"
    if Polarity.EXCLUDED in lost:
        return "no longer forbidden"
    if Polarity.CONDITIONAL in gained:
        return "now negated only as part of a combination"
    if Polarity.CONDITIONAL in lost:
        return "no longer merely part of a negated combination"
    return f"{_render(old)} -> {_render(new)}"
