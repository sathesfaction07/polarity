# Polarity

Parses classification expressions into an AST and reports, for each keyword, what its presence actually means for the rule — required, optional, excluded, or conditional. Also diffs two versions of an expression and names the changes that a keyword-list comparison cannot see.

Runs with no credentials. AWS Bedrock or Google Vertex can be enabled for expressions the grammar rejects.

---

## The Problem

A classification rule is a boolean expression over keywords:

```
category:kitchen AND (knife OR blade) AND NOT "butter knife"
```

The usual way to answer "which keywords does this rule use" is a regex over the text. That gives you four keywords and it is wrong in a way that does not announce itself.

**Precedence is invisible.** `a AND b OR c` means `(a AND b) OR c`. A regex returns the same three keywords for that as for `a AND (b OR c)`. The first rule matches an item containing only `c`; the second does not. Nothing in the flat list distinguishes them.

**Negation is invisible.** `"butter knife"` in the example above is *excluded* — its presence stops the rule matching. A flat list reports it alongside `knife` as though both were things you were looking for. That is not an imprecision, it is the opposite of the truth.

**Comparing two versions is worse.** A set difference over keyword lists reports a keyword moving from excluded to required as one removal plus one addition. That reads like churn and gets skimmed. It is the single most consequential edit a rule can receive.

And there is a subtler failure that this project spent most of its time on. Once you decide to track negation, the obvious implementation counts how many `NOT`s sit above each keyword and calls the odd ones excluded. That is also wrong, and a truth table proves it — see below.

---

## How It Works

```
expression -> tokenize -> parse -> AST -> walk -> keywords with polarity
                                          |
                                          two AST walks -> diff
```

**Tokenize** handles quoted phrases, `field:value`, wildcards, `AND`/`OR`/`NOT` with `&&`/`||`/`!` aliases. Operators are matched as whole words, so `corkscrew` is a keyword rather than a syntax error.

**Parse** is recursive descent with `OR` loosest, then `AND` (including implicit AND between adjacent terms), then `NOT`, then atoms. Same-type nodes are flattened, so `a AND (b AND c)` and `(a AND b) AND c` build one identical tree — associativity cannot change the meaning, so it must not change the output.

**Walk** carries two flags down the tree: whether an odd number of `NOT`s is above this node, and whether reaching it is unavoidable. The combination gives four polarities.

**Diff** compares polarity *sets* per keyword, so a flip is one change with a note explaining why the direction matters, and a keyword that is both required and excluded is reported as a contradiction.

---

## Polarity

| Polarity | Meaning | Example |
|:--|:--|:--|
| `required` | the rule cannot match without it | `a` in `a AND b` |
| `optional` | one of several alternatives | `a` in `a OR b` |
| `excluded` | the rule cannot match with it | `a` in `NOT a` |
| `conditional` | negated, but only as part of a combination | `a` in `NOT (a AND b)` |

`conditional` is the one that looks like over-engineering and is not. By De Morgan:

```
NOT (a OR b)   ==  NOT a AND NOT b     both terms individually forbidden
NOT (a AND b)  ==  NOT a OR  NOT b     only the combination is forbidden
```

An item containing `a` but not `b` still satisfies `NOT (a AND b)`. Calling `a` excluded there is not approximately right, it is backwards.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CLI (polarity)                               │
│                                                                         │
│   extract                    diff                    doctor             │
│   one expression or a file   two versions, with      providers,         │
│   exits 1 on a parse error   severity notes per       the grammar,      │
│   or a contradiction, so a   change. Exits 1 if       all four          │
│   hook can gate on it        anything changed         polarities        │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        SYNTAX (syntax/)                                  │
│                                                                         │
│  tokens.py    whole-word operators, so `corkscrew` is not `or`          │
│               quoted phrases with escapes; unterminated quote is a      │
│                 hard error, never "read to end of string"               │
│               field:value, field:"two words", trailing wildcard          │
│               every token carries its source column                     │
│                                                                         │
│  parser.py    recursive descent: OR < AND < NOT < atom                  │
│               implicit AND between adjacent atoms                        │
│               _flatten() splices same-type children -> associativity    │
│                 cannot show up as a difference                          │
│               ParseError renders a caret under the offending column     │
│                                                                         │
│  nodes.py     Term / Not / And / Or, closed set, N-ary And and Or       │
│               Term keeps `surface` and `position` for reporting          │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          CORE (core/)                                    │
│                                                                         │
│  extract.py   walk carrying (negated, certain)                          │
│                 And acts as AND unless negated  ─┐  De Morgan           │
│                 Or  acts as AND when   negated  ─┘                      │
│               (negated, certain) -> one of four polarities              │
│               sound, not exact: never claims a strong polarity wrongly  │
│                                                                         │
│  normalize.py NFKC + casefold + whitespace collapse                     │
│               surface form kept separately, so a cosmetic retype can    │
│                 be told apart from a change in meaning                  │
│                                                                         │
│  diff.py      compares polarity SETS per keyword, not single values     │
│               POLARITY_FLIPPED is one change, with a severity note      │
│               contradictions reported per side                          │
│               structural_only for pure re-grouping                      │
└───────┬─────────────────────────────────────┬───────────────────────────┘
        │                                     │
        ▼                                     ▼
┌────────────────────────────┐   ┌────────────────────────────────────────┐
│  PROVIDERS (providers/)     │   │  IO (io.py)                             │
│                             │   │                                         │
│  rules.py   the parser,     │   │  load_expressions: CSV/JSON, or a bare  │
│             always available│   │    list of strings                      │
│                             │   │  write_results(results, dir, fmt,       │
│  llm.py     Bedrock, Vertex │   │    *, emit=None)                        │
│             DELIBERATELY    │   │    complete set always reaches the      │
│             NARROW: only    │   │    JSON report; emit narrows only the   │
│             for input the   │   │    tabular output                       │
│             grammar rejects │   │  CSV / JSON / Excel share ROW_FIELDS    │
│             marked          │   │                                         │
│             "interpreted,   │   └────────────────────────────────────────┘
│             not parsed"     │
└────────────────────────────┘
```

---

## Running

```bash
bash setup.sh                 # asserts the polarity rule against a truth table
bash setup.sh --bedrock       # + boto3
bash setup.sh --all
source .venv/bin/activate

# What can this machine use, and what does the grammar accept?
polarity doctor

# One expression
polarity extract 'category:kitchen AND (knife OR blade) AND NOT "butter knife"'

# A file of rules; exits 1 on any parse error or contradiction
polarity extract -i data/sample_expressions.csv -o out/

# Compare two versions
polarity diff 'knife AND NOT "butter knife"' 'knife AND "butter knife"'

# Excel, or only the rows that parsed (the report keeps everything)
polarity extract -i rules.csv -o out/ --format excel --ok-only
```

Observed output for the single expression above:

```
tree          (category:kitchen AND (knife OR blade) AND NOT "butter knife")
depth         3
source        parser

  category:kitchen                  required
  knife                             optional
  blade                             optional
  "butter knife"                    excluded
```

Four keywords, four different meanings. A regex extractor returns four strings.

And the diff:

```
  polarity_flipped butter knife  excluded -> required
                   was forbidden, is now mandatory
```

One change, not a removal plus an addition.

---

## Usage Reference

### `extract [EXPRESSION]`

| Option | Purpose |
|:--|:--|
| `-i, --input` | CSV or JSON file instead of a single expression |
| `-o, --output` | Results directory. Default `output/` |
| `-f, --format` | `csv`, `json`, `excel` |
| `--provider` | `parser` (default), `bedrock`, `vertex` |
| `--tree` | Show the parsed tree for each row |
| `--ok-only` | Trim the tabular output. The report keeps everything |

Exits non-zero if anything failed to parse **or is self-contradictory**, so a pre-commit hook or pipeline step can gate on rule health rather than only on syntax.

### `diff BEFORE AFTER`

Reports `added`, `removed`, `polarity_flipped` and `cosmetic` changes, plus `regrouped` when the keywords and polarities are identical but the structure differs. Exits non-zero if anything changed, so it works as a review gate.

### `doctor`

Provider availability with install commands, the grammar and its precedence, all four polarities with examples, and the De Morgan rule stated explicitly — it is the thing most likely to surprise someone reading the output.

---

## Testing

```bash
pytest                      # 988 tests, ~0.6s
pytest tests/unit -v
pytest tests/integration -v
pytest --cov=key_extract --cov-report=term-missing
```

800 of those tests are generated. `tests/conftest.py` contains a brute-force oracle: it enumerates all 2^n keyword-presence combinations, keeps the satisfying ones, and asks of each keyword whether it appears in all of them, none of them, or some. That is the ground truth the linear-time structural rule is checked against, over 800 randomly generated expressions.

The property asserted is **soundness**: a `required` label must mean every satisfying assignment contains the keyword, and `excluded` that none does. The rule is allowed to be weaker than the truth, never to contradict it. Measured on generated input, it is weaker about 16% of the time and wrong 0% of the time.

Unsatisfiable expressions are skipped, since no claim about any keyword is meaningful there — that is what the 43 skips are.

---

## Configuration

```bash
KE_PROVIDER=parser            # parser | bedrock | vertex
KE_AWS_REGION=us-east-1
KE_BEDROCK_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0
KE_GCP_PROJECT=
KE_GCP_LOCATION=us-central1
KE_GEMINI_MODEL=gemini-1.5-flash
KE_OUTPUT_DIR=output
KE_OUTPUT_FORMAT=csv
```

---

## Tradeoffs and Decisions I Made Along the Way

**Counting `NOT`s is not enough, and a truth table is what showed it.** My first extractor tracked negation parity: odd number of `NOT`s above a term meant excluded, even meant required. It looked obviously correct. I wrote a brute-force truth table to check it and it disagreed on **four of seven** hand-picked cases.

The clearest failure: `NOT (a OR b)` and `NOT (a AND b)` produced byte-identical output. They mean opposite things. The first forbids both keywords individually; the second forbids only the combination, so an item with `a` alone still matches.

The fix was to carry a second flag — whether reaching this node is unavoidable — and to **swap the roles of AND and OR under a negation**, which is De Morgan applied structurally rather than by rewriting the tree. That produces four polarities instead of two, and `conditional` exists precisely to name the case parity got backwards.

I also got the diagnosis wrong on the first attempt, which is worth recording. I predicted the parity failure would show at a particular point for a particular reason, wrote it out, and the trace disagreed. Verifying instead of reasoning is what found the real shape of it — and that happened twice more in this project.

**The structural rule is sound rather than exact, and I chose that deliberately.** `(a AND b) OR a` is satisfied only when `a` is present, so `a` is genuinely required; the rule sees an OR and says `optional`. Being *weaker* than the truth is the safe direction for output people act on, and the alternative is a satisfiability check that is exponential in the number of keywords. There is a test that pins this specific under-claim so it is a known property rather than a lurking surprise.

**A feature I suspected was dead code turned out to be hiding a bug.** `structural_only` fires when keywords and polarities match but the trees differ. Nothing I wrote by hand triggered it, so rather than ship code I could not exercise I searched generated expressions for a case. It found four — and every one was wrong. `(c AND NOT (b OR c))` and `(b AND NOT (b OR c))` were reported as having no keyword changes, despite requiring different terms.

The cause was a dict comprehension keyed on keyword identity. `c` appears twice in the first expression with *different polarities*, and the dict silently kept the last one. So `knife AND NOT knife` — a rule that can never match anything — looked entirely ordinary. Polarities are now compared as sets, every occurrence is preserved, and a keyword that is both required and excluded is reported as a contradiction that exits non-zero. That went from a bug to one of the more useful things the tool does.

The general lesson: **an unreachable branch is worth investigating rather than deleting.** I was about to remove it as speculative.

**Flattening came from thinking about the diff, not the parser.** `a AND (b AND c)` and `(a AND b) AND c` are the same rule. Without flattening they build different trees, and the diff would report equivalent expressions as re-grouped — a false positive on the one signal a keyword diff cannot otherwise provide. Only same-type nodes are spliced; `a AND (b OR c)` keeps its shape because there the grouping genuinely matters.

**Surface forms are kept alongside normalised ones.** Normalising in place would be simpler and would destroy the ability to report `'Blade' -> 'blade'` as cosmetic rather than as a change in meaning. `casefold` rather than `lower` because the German ß maps to `ss` and `STRASSE` should match `Straße`. NFKC rather than NFKD because accents are meaningful — `coûteau` and `couteau` are different words and must not merge.

**The LLM path is narrow on purpose, and I want to be honest about why.** Four sibling projects already have a local-plus-hosted-model backend, so that pattern is not the novelty here. Once an expression parses, extraction is exact — there is no judgement left for a model to contribute. The hosted path exists for input the grammar rejects: a dialect with an operator this parser does not know, or syntax mangled by a round trip through a spreadsheet. Its output is labelled `interpreted, not parsed`, and a response with an unrecognised polarity label is discarded whole rather than coerced, because coercing turns a model's confusion into a confident wrong answer.

**No GUI, which is a deliberate departure from the project brief.** The tool this replaces was a desktop application. A GUI here would be a large untestable surface, and the sibling projects already carry a known gap for 340 lines of untested extension JavaScript — repeating that would be a choice, not an oversight. The library and CLI carry the whole capability, `extract` and `diff` both exit non-zero on a problem so they compose into hooks and pipelines, and that is more useful for the actual job than a window.

**Implicit AND is supported and it is a compromise.** Real expressions get pasted from search bars where adjacency means AND, so `knife blade` parses as a conjunction. The cost is that a typo like `knife AN blade` becomes three keywords silently ANDed rather than a syntax error. Rejecting adjacency would catch that and break a large fraction of real input. The grammar prints in `doctor` so the rule is at least discoverable.

**Order within a connective is not normalised.** `a OR b` and `b OR a` are the same rule, and the diff reports them as re-grouped rather than identical. Sorting operands would fix it and would also destroy source order in the output, which is what makes a report line up with the expression someone is reading. Reporting a harmless difference seemed better than scrambling every report to avoid it.

---

## Stack

| Concern | Choice |
|:--|:--|
| Language | Python 3.11+ |
| Parsing | hand-written tokenizer and recursive-descent parser, no dependency |
| Models + config | Pydantic, Pydantic Settings |
| CLI | Click |
| LLM | boto3 (Bedrock), google-cloud-aiplatform (Vertex), both optional |
| Output | stdlib csv/json, openpyxl |
| Tests | pytest, a brute-force truth-table oracle, Click's CliRunner |
| Lint | ruff |
