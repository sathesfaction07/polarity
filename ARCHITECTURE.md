# Polarity — Technical Architecture

## Overview

Polarity parses classification expressions — boolean rules over keywords — into an AST, then reports for each keyword what its presence means for whether the rule matches. It also compares two versions of an expression and names the changes a keyword-list diff cannot see.

**Design principle:** polarity is a property of a keyword's position in the tree, not of the text around it. Everything interesting in this codebase follows from taking that seriously, including the two bugs that came from not taking it seriously enough at first.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            CLI (polarity)                                  │
│                                                                              │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────────┐ │
│  │ extract            │  │ diff               │  │ doctor                 │ │
│  │ one EXPRESSION or  │  │ BEFORE AFTER       │  │ provider availability, │ │
│  │ -i file            │  │ per-change         │  │ the grammar and its    │ │
│  │ colour-coded       │  │ severity notes,    │  │ precedence, all four   │ │
│  │ polarity, tree     │  │ contradictions     │  │ polarities, De Morgan  │ │
│  │                    │  │ per side           │  │                        │ │
│  │ exit 1 on a parse  │  │ exit 1 if anything │  │ no input, no network   │ │
│  │ error OR a         │  │ changed            │  │                        │ │
│  │ contradiction      │  │                    │  │                        │ │
│  └─────────┬──────────┘  └─────────┬──────────┘  └────────────────────────┘ │
└────────────┼───────────────────────┼────────────────────────────────────────┘
             │                       │
             ▼                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              SYNTAX (syntax/)                                 │
│                                                                              │
│  tokens.py                                                                   │
│    operators matched as WHOLE WORDS -> `corkscrew` is a keyword, not `or`     │
│    quoted phrases with backslash escapes                                     │
│    unterminated quote is a hard error, never "read to end of string"          │
│    field:value and field:"two words"                                         │
│    trailing wildcard recorded and stripped from the value                     │
│    every token carries its source column, for the caret in errors            │
│    adjacency left as adjacent tokens -- the parser decides what it means      │
│                                                                              │
│  parser.py     recursive descent                                             │
│    OR   (loosest)                                                            │
│    AND  incl. implicit AND between adjacent atoms                            │
│    NOT  prefix                                                               │
│    atom  term | ( ... )                                                      │
│    _flatten(): splices same-type children into the parent                    │
│      -> a AND (b AND c) and (a AND b) AND c build ONE tree                    │
│      -> associativity cannot surface as a difference in the diff             │
│    ParseError renders the expression with a caret under the column           │
│                                                                              │
│  nodes.py      Term / Not / And / Or -- a closed set, not BinaryOp(op,l,r)    │
│    And and Or are N-ARY, so parse order does not shape the tree              │
│    Term keeps `surface` (as typed) and `position` (for reporting)             │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                                CORE (core/)                                   │
│                                                                              │
│  extract.py    walk carrying (negated, certain)                              │
│                                                                              │
│      negated   parity of NOTs above this node                                │
│      certain   is reaching this node unavoidable for satisfaction?           │
│                                                                              │
│      acts_as_and = isinstance(node, And) != negated      <- De Morgan        │
│      child_certain = certain if acts_as_and else False                       │
│                                                                              │
│      (no,  yes) -> REQUIRED       a in `a AND b`                             │
│      (no,  no ) -> OPTIONAL       a in `a OR b`                              │
│      (yes, yes) -> EXCLUDED       a in `NOT a`                               │
│      (yes, no ) -> CONDITIONAL    a in `NOT (a AND b)`                       │
│                                                                              │
│      SOUND, not exact: never claims REQUIRED/EXCLUDED wrongly, may claim     │
│      a weaker polarity than the truth. Validated against a truth table.      │
│                                                                              │
│  normalize.py  NFKC + casefold + whitespace collapse                         │
│      casefold not lower(), so STRASSE == Straße                              │
│      NFKC not NFKD, so coûteau != couteau (accents are meaningful)           │
│      surface form kept on the Keyword, so a cosmetic retype is reportable    │
│                                                                              │
│  diff.py       compares polarity SETS per (field, normalized)                 │
│      grouped()/polarities() preserve EVERY occurrence                        │
│      POLARITY_FLIPPED: one change + a note whose severity varies             │
│      contradictions reported per side                                        │
│      structural_only: identical keywords and polarities, different tree      │
└────────┬────────────────────────────────────────┬────────────────────────────┘
         │                                        │
         ▼                                        ▼
┌───────────────────────────────────┐  ┌──────────────────────────────────────┐
│  PROVIDERS (providers/)            │  │  IO (io.py)                           │
│                                    │  │                                       │
│  base.py   Provider ABC            │  │  load_expressions()                   │
│    extract() -> Extraction | None  │  │    CSV, JSON objects, or a bare JSON  │
│                                    │  │      list of strings                  │
│  rules.py  ParserProvider          │  │    blank rows skipped, missing id     │
│    always available, the default   │  │      filled from the row number       │
│                                    │  │                                       │
│  llm.py    Bedrock / Vertex        │  │  write_results(results, dir, fmt,     │
│    DELIBERATELY NARROW: only for   │  │                *, emit=None)          │
│    input the grammar rejects       │  │    complete set ALWAYS reaches the    │
│    tree marked "interpreted,       │  │      JSON report                      │
│      not parsed"                   │  │    emit narrows only csv/excel        │
│    unknown polarity -> discard the │  │                                       │
│      whole response, never coerce  │  │  CSV / JSON / Excel share ROW_FIELDS  │
│    any failure -> None -> fall     │  │                                       │
│      back to the parser's error    │  └──────────────────────────────────────┘
└───────────────────────────────────┘
```

---

## Components

### 1. CLI (`cli.py`)

Three commands. Entry point `polarity`.

| Command | Purpose |
|:--|:--|
| `extract [EXPRESSION]` | One expression, or a file with `-i`. Prints polarity per keyword, colour-coded. Exits 1 on a parse error **or a contradiction** |
| `diff BEFORE AFTER` | Named changes with severity notes. Exits 1 if anything changed |
| `doctor` | Providers, grammar, all four polarities, and the De Morgan rule |

Both working commands exit non-zero on a problem, so they compose into pre-commit hooks and pipeline steps. Gating on *contradictions* and not only on syntax is the part that makes this useful in CI: a rule that parses fine and can never match is invisible to a syntax check.

### 2. Syntax (`syntax/`)

**`tokens.py`** — operators are matched as whole words. This is the single most common way a hand-rolled boolean tokenizer breaks: `corkscrew` contains `or`, `android` contains `and`, `notebook` contains `not`. Substring matching turns each into a syntax error or, worse, a silently different rule. There is a parametrized test over exactly those words.

An unterminated quote raises. The alternative — read to end of string — turns a typo into a rule that matches almost nothing, quietly.

Adjacency is left as adjacent tokens rather than resolved here, so the implicit-AND rule is stated in exactly one place.

**`parser.py`** — recursive descent, `OR` loosest, then `AND`, then `NOT`, then atoms.

Precedence is the reason this is a parser and not a regex. `a AND b OR c` means `(a AND b) OR c`; a regex extractor returns the same three keywords for that as for `a AND (b OR c)`. The first matches an item containing only `c`, the second does not, and nothing in the flat keyword list distinguishes them.

`_flatten()` splices same-type children into the parent so `a AND (b AND c)` and `(a AND b) AND c` build one identical tree. This exists for the diff's benefit rather than the parser's — without it, equivalent expressions report as re-grouped, which is a false positive on the one signal a keyword diff cannot otherwise provide. Only same-type nodes are spliced; `a AND (b OR c)` keeps its shape because there the grouping genuinely changes the meaning.

`ParseError` renders the expression with a caret under the offending column. Classification expressions are long, human-written, and often pasted; "syntax error" is useless and the column is the whole message.

**`nodes.py`** — `Term`, `Not`, `And`, `Or`. A closed set rather than a generic `BinaryOp(op, left, right)`, because extraction has to treat `And` and `Or` differently and a generic node would push that into a string comparison at every use site.

`And` and `Or` are N-ary, so `a AND b AND c` is one node with three operands. Nesting would make the tree shape depend on parse order, which then leaks into the diff.

`Term.surface` is the text as typed and `Term.position` its column. Both are kept because a report should be able to say "you wrote `Knife`, the other version has `knife`".

### 3. Extraction (`core/extract.py`)

The centre of the project. See Polarity Semantics below and ADR-001.

### 4. Normalisation (`core/normalize.py`)

`NFKC` + `casefold` + whitespace collapse, producing the comparison key. Each step earns its place:

- **NFKC** folds compatibility characters, so fullwidth `ｋｎｉｆｅ` and the ligature `ﬁle` compare equal to their ASCII forms. Expressions get pasted from spreadsheets, PDFs and other locales.
- **casefold** rather than `lower()`, because `lower()` leaves the German ß alone while `casefold` maps it to `ss`. `STRASSE` and `Straße` are the same word for matching purposes.
- **whitespace collapse** so `"butter  knife"` and `"butter knife"` are one keyword. A double space inside a quoted phrase is invisible in a diff and never intended.

NFKC rather than NFKD deliberately: accents are meaningful. `coûteau` and `couteau` are different words and must not merge.

`is_cosmetic_change()` is what lets the diff separate "somebody retyped this with different casing" from "somebody changed what this rule matches".

### 5. Diff (`core/diff.py`)

See Comparing Two Versions below and ADR-002.

### 6. Providers (`providers/`)

`Provider` ABC with `extract() -> Extraction | None`. `None` means "I could not do this", which triggers a fallback to the parser rather than an exception.

**`ParserProvider`** is the default and is not a fallback for something better. Once an expression parses, extraction is exact — there is no judgement left for a model to contribute.

**`BedrockProvider` / `VertexProvider`** are deliberately narrow. Four sibling projects already have a local-plus-hosted-model backend, so that pattern is not the contribution here. The hosted path exists for input the grammar rejects: a dialect with an unknown operator, or syntax mangled by a round trip through a spreadsheet.

Three safeguards on that path:

- output is labelled `interpreted, not parsed`, so nobody mistakes a reading for a parse
- an unrecognised polarity label discards the **whole** response rather than coercing to `required`, because coercing turns a model's confusion into a confident wrong answer
- any failure returns `None`, so the caller falls back to the parser's error, which names a column — more useful than a model's guess about why something failed

SDK imports are deferred so `doctor` and the entire parser path work on a machine with neither installed.

### 7. IO (`io.py`)

`load_expressions()` reads CSV (`utf-8-sig`, since Excel writes a BOM), JSON objects, or a bare JSON list of strings. Blank rows are skipped rather than failing the file; a missing id is filled from the row number so every result traces back.

`write_results(results, directory, fmt, *, emit=None)` — the complete set is the required positional argument and always reaches the JSON report; `emit` narrows only the tabular output. The arguments are this way round so that the worst a forgetful caller can do is fail to filter, rather than quietly drop rows out of the audit trail.

---

## Polarity Semantics

### Counting negations is not enough

The first extractor tracked negation parity: odd number of `NOT`s above a term meant excluded, even meant required. Two polarities, one flag, and it looks obviously correct.

I checked it against a brute-force oracle — enumerate every combination of keyword presence, keep the satisfying assignments, ask of each keyword whether it appears in all, none, or some. On seven hand-picked expressions it disagreed on **four**:

| Expression | Truth table | Parity walk | |
|:--|:--|:--|:--|
| `a AND b` | both REQUIRED | both required | correct |
| `a OR b` | both *varies* | both required | **overstates** |
| `NOT a` | EXCLUDED | excluded | correct |
| `NOT (a OR b)` | both EXCLUDED | both excluded | correct |
| `NOT (a AND b)` | both *varies* | both excluded | **inverted** |
| `a AND b OR c` | all *varies* | all required | **overstates** |
| `a AND (b OR c)` | a REQUIRED, b/c *varies* | all required | **overstates** |

Two distinct defects. Disjunction does not make terms required — `a OR b` is satisfied by `a` alone. And negation does not distribute over conjunction the way parity assumes:

```
NOT (a OR b)   ==  NOT a AND NOT b     each term individually forbidden
NOT (a AND b)  ==  NOT a OR  NOT b     only the combination forbidden
```

An item containing `a` but not `b` satisfies `NOT (a AND b)`. Calling `a` excluded there is backwards, and the two expressions produced **byte-identical output** despite meaning opposite things.

### The fix

Carry a second flag. `certain` is whether reaching this node is unavoidable for satisfaction; it starts true and an OR clears it for its children. Negation swaps the roles of AND and OR, which is De Morgan applied structurally rather than by rewriting the tree:

```python
acts_as_and = isinstance(node, And) != negated
child_certain = certain if acts_as_and else False
```

Four polarities result, and `CONDITIONAL` names precisely the case parity got backwards.

### Sound, not exact

The rule never claims `REQUIRED` unless every satisfying assignment contains the keyword, nor `EXCLUDED` unless none does — but redundancy defeats it in the safe direction. `(a AND b) OR a` is satisfied only when `a` is present, so `a` is genuinely required; the rule sees an OR and reports `OPTIONAL`.

Exactness needs a satisfiability check, exponential in the number of distinct keywords. Under-claiming is the right direction for output people act on. `test_the_rule_is_conservative_not_exact` pins this specific case so it is a known property rather than a lurking surprise.

Measured against the oracle: **0 soundness violations** across 3,801 satisfiable expressions and 12,997 keyword claims; strictly weaker than the truth about **16%** of the time.

---

## Comparing Two Versions

A set difference over keyword lists loses three things.

**A polarity flip reads as churn.** Moving `"butter knife"` from excluded to required inverts what the rule does with that term. A set diff reports `- butter knife` and `+ butter knife`, which looks like a no-op. Here it is one `POLARITY_FLIPPED` with a note whose wording depends on severity: `was mandatory, is now forbidden` reads differently from `no longer mandatory, the rule is looser`, because those are different situations.

**Equivalent expressions can look different.** Handled upstream by flattening, so associativity never reaches the diff.

**A keyword can carry more than one polarity.** This was found by chasing what looked like dead code. `structural_only` was never triggered by anything hand-written, so rather than ship an unexercised branch I searched generated expressions for a case. Four turned up and every one was wrong:

```
(c AND NOT (b OR c))   vs   (b AND NOT (b OR c))
  -> reported: 0 keyword changes
```

The left requires `c`, the right requires `b`. The cause:

```python
before = {keyword.key: keyword for keyword in left_result.keywords}
```

`c` occurs twice in the left expression — once required, once excluded — and a dict keyed on identity silently keeps the last. The same defect made `knife AND NOT knife`, a rule that can never match, look ordinary.

Polarities are now compared as `frozenset`s, every occurrence is preserved, and a keyword holding both `REQUIRED` and `EXCLUDED` is reported as a contradiction that exits non-zero. `OPTIONAL` plus `CONDITIONAL` is *not* a contradiction — `a OR NOT a` is a tautology.

The lesson worth keeping: **an unreachable branch is worth probing before deleting.** Removing it as speculative would have left the collapse in place and unnoticed.

### Limitations

- Order within a connective is not normalised, so `a OR b` versus `b OR a` reports as re-grouped. Sorting operands would fix it and destroy source order in the output, which is what makes a report line up with the expression being read
- Contradiction detection is structural, so a rule contradictory through a longer chain — `a AND (NOT a OR b) AND NOT b` — is not caught
- Cosmetic detection inherits whatever `normalize` folds, so a homoglyph substitution (Cyrillic `а` for Latin `a`) reads as a genuine add plus remove. Catching that needs a confusables table

---

## Data Flow

```
1.  polarity extract -i rules.csv -o out/
2.  CLI drops None overrides -> Settings(output_dir=out/, provider="parser", ...)
3.  load_expressions() -> [(id, expression)]; blank rows skipped
4.  per expression: get_provider("parser").extract(text)
5.    tokenize   'category:kitchen AND (knife OR blade) AND NOT "butter knife"'
         FIELD(category,kitchen) AND LPAREN TERM(knife) OR TERM(blade) RPAREN
         AND NOT TERM(butter knife, quoted)
6.    parse      And( Term(category:kitchen),
                      Or( Term(knife), Term(blade) ),
                      Not( Term("butter knife") ) )
7.    walk       root: negated=False, certain=True
                   Term(category:kitchen)  (no, yes)  -> REQUIRED
                   Or -> acts_as_and=False -> children certain=False
                     Term(knife)           (no, no)   -> OPTIONAL
                     Term(blade)           (no, no)   -> OPTIONAL
                   Not -> negated=True, certain unchanged
                     Term("butter knife")  (yes, yes) -> EXCLUDED
8.    contradictions: no keyword holds both REQUIRED and EXCLUDED -> []
9.  per-row line printed: `R-1  ok  d3 1req 2opt 1exc`
10. write_results(results, out/, "csv")   <- complete set, no emit
      out/keywords.csv      one row per expression, keywords + polarities
      out/full_report.json  summary {total, ok, errors, contradictions} + all rows
11. exit 1 if any row failed to parse or is contradictory
```

Observed on the shipped sample file:

```
E-4     ok      d3 2exc            NOT (knife OR blade)
E-5     ok      d3 2con            NOT (knife AND blade)
E-9     ok      d3 1req 1exc  CONTRADICTORY
total           10
parsed          10
errors          0
contradictory   1
```

E-4 and E-5 differing is the De Morgan distinction working end to end; the parity implementation printed them identically.

---

## Configuration

`KE_`-prefixed environment variables or a `.env` file. No list-typed fields, since Pydantic Settings parses those as JSON and `KE_FIELDS=a,b` produces a confusing decode error.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KE_", env_file=".env", extra="ignore")

    provider: str = "parser"

    aws_region: str = "us-east-1"
    bedrock_model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    gcp_project: str = ""
    gcp_location: str = "us-central1"
    gemini_model: str = "gemini-1.5-flash"

    output_dir: Path = Path("output")
    output_format: str = "csv"
    log_level: str = "INFO"
```

---

## Running

```bash
bash setup.sh                 # asserts polarity against a truth table before finishing
bash setup.sh --bedrock --excel
bash setup.sh --all
source .venv/bin/activate

polarity doctor
polarity extract 'category:kitchen AND (knife OR blade) AND NOT "butter knife"'
polarity extract -i data/sample_expressions.csv -o out/ --tree
polarity diff 'knife AND NOT "bk"' 'knife AND "bk"'
polarity extract -i rules.csv -o out/ --format excel --ok-only
```

`setup.sh` does more than install. It reports provider availability, then asserts: six fixed polarity cases including both De Morgan forms; agreement with a brute-force truth table over ~1,400 generated expressions; that a flip is one change and not two; that equivalent re-grouping is silent; that a duplicate keyword does not collapse; that a contradiction is reported; that a cosmetic retype is classified as such; and that the shipped sample data parses with exactly one contradiction and a differing De Morgan pair.

---

## Testing

```bash
pytest                          # 988 tests, 43 skipped, ~0.6s
pytest tests/unit -v
pytest tests/integration -v
pytest --cov=key_extract --cov-report=term-missing
```

| File | Covers |
|:--|:--|
| `conftest.py` | the brute-force oracle, `evaluate`/`truth_table`, and the expression generator |
| `test_extract.py` | all four polarities; `TestDeMorgan` incl. an assertion that the two forms differ; `TestAgainstTheTruthTable` — 800 generated soundness and weakness properties; contradictions; parse failures carried as results |
| `test_syntax.py` | whole-word operators over `corkscrew`/`android`/`notebook`; quotes, escapes, unterminated quotes; fields and wildcards; full precedence table; associativity flattening; 12 parse-error cases; caret position |
| `test_diff.py` | no-change cases incl. equivalent grouping; added/removed; every flip severity note; the duplicate-collapse regression; contradictions per side; cosmetic detection; rendering |
| `test_support.py` | normalisation incl. ß and accents; `Keyword`/`Extraction` models; provider registry and availability messages; the LLM JSON path with a stubbed `_complete`, including that an unknown polarity discards the response |
| `integration/test_cli.py` | all three commands, exit codes for parse errors and contradictions, `--ok-only` keeping the report whole, and the shipped sample file |

800 of the tests are generated. The property asserted is soundness: a strong label must match the truth table; the rule may be weaker, never wrong. Unsatisfiable expressions are skipped, since no claim about any keyword is meaningful there — that is what the 43 skips are.

The repo's own sample data is covered by both a test and a CI step, so editing it and breaking it fails the build rather than somebody's first run.

Not covered: real Bedrock and Vertex calls. Prompt construction, JSON extraction, polarity validation and `None`-on-failure are unit-tested with a stubbed completion; the network path is not exercised.
