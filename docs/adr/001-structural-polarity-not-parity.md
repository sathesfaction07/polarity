# ADR 001: Polarity is derived from structure, with four values, not from negation parity

## Status

Accepted

## Context

The point of this tool is to answer "what does this keyword mean in this rule". The obvious implementation walks the AST counting negations:

```python
def _visit(node, *, negated, out):
    if isinstance(node, Term):
        out.append(Keyword(..., polarity=EXCLUDED if negated else REQUIRED))
    elif isinstance(node, Not):
        _visit(node.operand, negated=not negated, out=out)
    else:
        for child in node.children():
            _visit(child, negated=negated, out=out)
```

Two polarities, one flag, obviously correct. It is not.

I checked it against a brute-force oracle — enumerate every combination of keyword presence, keep the assignments that satisfy the expression, and ask of each keyword whether it appears in all of them, none of them, or some. On seven hand-picked expressions the parity walk disagreed with the truth table on **four**:

| Expression | Truth table | Parity walk | |
|:--|:--|:--|:--|
| `a AND b` | both REQUIRED | both required | correct |
| `a OR b` | both *varies* | both required | **overstates** |
| `NOT a` | EXCLUDED | excluded | correct |
| `NOT (a OR b)` | both EXCLUDED | both excluded | correct |
| `NOT (a AND b)` | both *varies* | both excluded | **inverted** |
| `a AND b OR c` | all *varies* | all required | **overstates** |
| `a AND (b OR c)` | a REQUIRED, b/c *varies* | all required | **overstates** |

Two distinct defects.

**Disjunction does not make terms required.** `a OR b` is satisfied by `a` alone, so neither term is required. Reporting both as required tells a reader the rule is stricter than it is.

**Negation does not distribute over conjunction the way parity assumes.** By De Morgan:

```
NOT (a OR b)   ==  NOT a AND NOT b     each term individually forbidden
NOT (a AND b)  ==  NOT a OR  NOT b     only the combination forbidden
```

An item containing `a` but not `b` satisfies `NOT (a AND b)`. Calling `a` excluded there is not imprecise, it is backwards. And the two expressions produced byte-identical output despite meaning opposite things — the starkest possible demonstration that the model was wrong rather than merely coarse.

Worth recording: my first hypothesis about *why* parity failed was also wrong. I predicted a specific failure point and mechanism, wrote out the trace, and the actual behaviour disagreed. The oracle found the real shape.

## Decision

Carry two flags down the walk instead of one.

- **`negated`** — parity of `NOT`s above this node, as before.
- **`certain`** — whether reaching this node is unavoidable for the rule to be satisfied. True at the root; an OR turns it off for its children, since only one branch has to hold.

**Negation swaps the roles of AND and OR**, which is De Morgan applied structurally rather than by rewriting the tree:

```python
acts_as_and = isinstance(node, And) != negated
child_certain = certain if acts_as_and else False
```

The two flags give four polarities:

| negated | certain | polarity | example |
|:--|:--|:--|:--|
| no | yes | `REQUIRED` | `a` in `a AND b` |
| no | no | `OPTIONAL` | `a` in `a OR b` |
| yes | yes | `EXCLUDED` | `a` in `NOT a` |
| yes | no | `CONDITIONAL` | `a` in `NOT (a AND b)` |

`CONDITIONAL` exists specifically to name the case parity got backwards: negated, but only as part of a combination, so the keyword's presence alone does not prevent a match.

### Soundness, not exactness

The rule is structural, so it is conservative. It never labels a keyword `REQUIRED` unless every satisfying assignment contains it, and never `EXCLUDED` unless none does — but redundancy defeats it in the safe direction. `(a AND b) OR a` is satisfied only when `a` is present, so `a` is genuinely required; the rule sees an OR and reports `OPTIONAL`.

That trade is deliberate. Exactness requires a satisfiability check, which is exponential in the number of distinct keywords. Under-claiming is the right direction for output people act on: a reader who is told `optional` and checks will find it is required, whereas a reader told `required` about something that is not will build on a false premise.

## Validation

`tests/conftest.py` holds the oracle. `tests/unit/test_extract.py::TestAgainstTheTruthTable` runs it over 800 generated expressions in two parametrized properties:

- **soundness** — a `REQUIRED` or `EXCLUDED` label must match the truth table
- **weakness where warranted** — where the truth table says a keyword varies, the label must be `OPTIONAL` or `CONDITIONAL`

Unsatisfiable expressions are skipped, since no claim about any keyword is meaningful there.

Measured on generated input: **0 soundness violations** across 3,801 satisfiable expressions and 12,997 keyword claims, and the rule is strictly weaker than the truth about **16%** of the time.

`TestDeMorgan` pins the specific regression, including an explicit assertion that `NOT (a OR b)` and `NOT (a AND b)` no longer produce the same answer.

## Consequences

**Good**

- `required` and `excluded` mean what a reader assumes, and are checked against brute-force semantics on every test run
- The De Morgan distinction is expressible at all, which two polarities could not do
- The whole rule is a four-entry lookup table plus one line of De Morgan, so it is auditable by reading
- Linear in the size of the expression, so it works on rules with hundreds of keywords where the oracle would not finish
- The polarity distinction is what makes the diff able to report a flip rather than a removal plus an addition; see ADR-002

**Bad**

- Four polarities is more vocabulary than users want, and `conditional` needs the De Morgan explanation to make sense. `doctor` prints it for that reason
- Conservatism produces `optional` on expressions where a human can see the keyword is required. Explaining "sound but not exact" is harder than explaining a wrong answer
- The `certain` flag is not locally obvious. Reading `_visit` in isolation does not reveal why an OR clears it; the docstring carries that weight

**Neutral**

- The oracle is test-only code that is itself untested. It is simple enough to read in one sitting, and it disagreeing with the implementation is exactly the signal wanted
- 800 generated cases at one seed is not exhaustive. It found the real bug, which is the evidence available
- Nothing distinguishes "conditional because negated-over-AND" from other routes to the same label. A reader wanting to know why must look at the tree, which `--tree` prints
