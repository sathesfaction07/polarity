# ADR 002: The diff compares polarity sets per keyword, not single values

## Status

Accepted

## Context

Comparing two versions of a classification expression has an obvious implementation: extract keywords from each side, put them in sets, report the difference. It loses three things, and I only found the third by chasing what looked like dead code.

### A polarity flip reads as churn

Moving `"butter knife"` from excluded to required inverts what the rule does with that term. It is the most consequential single-token edit a rule can receive. A set difference reports:

```
- butter knife
+ butter knife
```

which looks like a no-op and gets skimmed past. The keyword is in both sets; only its meaning changed.

### Equivalent expressions can look different

`a AND (b AND c)` and `(a AND b) AND c` are the same rule. Without normalisation they build different trees, so any comparison that looks at structure reports a change that cannot possibly matter. False positives on a review tool are worse than useless — they train the reader to skim, which is exactly when the real change slips through.

### A keyword can carry more than one polarity, and a dict hides it

This one was found indirectly. `Diff.structural_only` — keywords and polarities identical, trees different — was never triggered by anything I wrote by hand. Rather than ship a branch I could not exercise, I searched generated expressions for a case that would fire it. Four turned up, and every one was wrong:

```
(c AND NOT (b OR c))   vs   (b AND NOT (b OR c))
  -> reported: 0 keyword changes, structural_only
```

The left requires `c`; the right requires `b`. That is a real difference in what the rule matches, reported as no keyword changes at all.

The cause was this:

```python
before = {keyword.key: keyword for keyword in left_result.keywords}
```

In `c AND NOT (b OR c)`, the keyword `c` occurs **twice** — once required, once excluded. A dict keyed on identity silently keeps whichever came last. So the difference in what each side required was overwritten before the comparison started.

The same defect made `knife AND NOT knife` — a rule that can never match anything — look entirely ordinary.

## Decision

Three changes.

**1. Compare polarity sets, not single polarities.** `Extraction.grouped()` preserves every occurrence; `Extraction.polarities()` returns a `frozenset` per keyword identity. The diff compares those sets, so a keyword carrying two polarities is compared as such and rendered as `excluded+required`.

**2. Report a flip as one change with a severity note.** `POLARITY_FLIPPED` carries `before`, `after`, and a note that distinguishes the severe transitions from the routine ones, because the direction is not symmetric:

| Transition | Note |
|:--|:--|
| required → excluded | was mandatory, is now forbidden |
| excluded → required | was forbidden, is now mandatory |
| required → optional | no longer mandatory, the rule is looser |
| gains both required and excluded | now both required and excluded: the rule cannot match |

**3. Surface contradictions.** `Extraction.contradictions` lists keywords holding both `REQUIRED` and `EXCLUDED` — the rule cannot match, and it is almost always an authoring mistake: a term added to one branch and forgotten in a negated one. The CLI exits non-zero on it, so a hook can gate on rule health rather than only on syntax.

Note that `OPTIONAL` plus `CONDITIONAL` on the same keyword is *not* a contradiction. `a OR NOT a` is a tautology; it is always satisfiable.

Supporting this, the parser flattens same-type nodes so associativity never reaches the diff. Only same-type children are spliced — `a AND (b OR c)` keeps its shape, because there the grouping genuinely changes the meaning.

Keyword identity is `(field, normalized)`. That makes `material:steel` and `category:steel` distinct, and makes a change of casing or a fullwidth character a `COSMETIC` change rather than a removal plus an addition.

## Consequences

**Good**

- A flip is one line with a note, not two lines that look like churn
- `knife AND NOT knife` is reported instead of passing silently. A rule that matches nothing is not investigated as urgently as one matching the wrong things, so catching it statically is worth a lot
- Equivalent re-grouping is silent, so the report only contains changes that can matter
- `structural_only` now means what it says, and is reachable for the right reason
- Both commands exit non-zero on a problem, so they compose into pre-commit hooks and pipeline steps

**Bad**

- `excluded+required` is not a friendly rendering. It is accurate and the note explains it, but a reader meets the set notation before the explanation
- Contradiction detection only covers `REQUIRED` + `EXCLUDED`. A rule contradictory through a longer chain — `a AND (NOT a OR b) AND NOT b` — is not caught, because that needs satisfiability rather than a structural check
- Three ways for a keyword to "change" (added, removed, flipped) plus two structural signals is more vocabulary than a plain diff

**Neutral**

- Order within a connective is not normalised, so `a OR b` versus `b OR a` reports as re-grouped. Sorting operands would fix it and would destroy source order in the output, which is what makes a report line up with the expression being read. Reporting a harmless difference beat scrambling every report
- `_surfaces()` keeps the *first* surface form seen for each identity. With `Knife AND NOT knife` the report shows `Knife`; either choice is arbitrary
- Cosmetic detection is exactly "differs before normalisation, matches after", so it inherits whatever `normalize` folds. A homoglyph substitution — Cyrillic `а` for Latin `a` — is reported as a genuine add plus remove rather than as suspicious, which is honest but not helpful. Catching that would need a confusables table and is out of scope here

## Postscript

The generalisable lesson is about the investigation rather than the fix: **an unreachable branch is worth probing before deleting.** I was about to remove `structural_only` as speculative code, which would have left the dict collapse in place and unnoticed — the branch was not dead, it was the only visible symptom of a bug in the comparison beneath it.
