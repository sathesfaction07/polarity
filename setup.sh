#!/usr/bin/env bash
set -euo pipefail

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${BLUE}==>${NC} $1"; }
ok()   { echo -e "${GREEN} ok ${NC} $1"; }
warn() { echo -e "${YELLOW}warn${NC} $1"; }
fail() { echo -e "${RED}fail${NC} $1"; exit 1; }

WITH_BEDROCK=0
WITH_VERTEX=0
WITH_EXCEL=0
for arg in "$@"; do
    case "$arg" in
        --bedrock) WITH_BEDROCK=1 ;;
        --vertex)  WITH_VERTEX=1 ;;
        --excel)   WITH_EXCEL=1 ;;
        --all)     WITH_BEDROCK=1; WITH_VERTEX=1; WITH_EXCEL=1 ;;
        -h|--help)
            cat <<'USAGE'
Usage: bash setup.sh [options]

  --bedrock   also install boto3, for the AWS Bedrock provider
  --vertex    also install google-cloud-aiplatform, for the Vertex provider
  --excel     also install openpyxl, for .xlsx output
  --all       everything
  -h          this message

With no options you get a fully working install. The parser needs no credentials and
no network; the hosted providers exist only for expressions the grammar rejects.
USAGE
            exit 0 ;;
        *) fail "unknown option: $arg (try --help)" ;;
    esac
done

info "Checking Python"
command -v python3 >/dev/null 2>&1 || {
    echo "python3 was not found. Install it first:"
    echo "  macOS:         brew install python@3.12"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv"
    echo "  RHEL/Fedora:   sudo dnf install python3"
    fail "python3 missing"
}

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')" != "1" ]; then
    echo "Python 3.11 or newer is required (StrEnum). Found $PY_VER."
    echo "  macOS:         brew install python@3.12"
    echo "  Ubuntu/Debian: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv"
    fail "python $PY_VER too old"
fi
ok "Python $PY_VER"

info "Creating virtual environment"
[ -d .venv ] || python3 -m venv .venv || fail "could not create .venv (Debian/Ubuntu: sudo apt install python3-venv)"
ok ".venv ready"

EXTRAS="dev"
[ "$WITH_BEDROCK" = "1" ] && EXTRAS="$EXTRAS,bedrock"
[ "$WITH_VERTEX" = "1" ] && EXTRAS="$EXTRAS,vertex"
[ "$WITH_EXCEL" = "1" ] && EXTRAS="$EXTRAS,excel"

info "Installing dependencies [$EXTRAS]"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[$EXTRAS]" || fail "pip install failed"
ok "dependencies installed"

info "Verifying imports"
.venv/bin/python - <<'PYCHECK' || fail "core imports failed"
import click, yaml                                       # noqa: F401
from pydantic_settings import BaseSettings               # noqa: F401
from key_extract.core.diff import diff_expressions       # noqa: F401
from key_extract.core.extract import extract             # noqa: F401
from key_extract.providers import availability           # noqa: F401
from key_extract.syntax.parser import parse              # noqa: F401
PYCHECK
ok "imports resolve"

info "Reporting provider availability"
.venv/bin/python - <<'PYCHECK'
from key_extract.providers import availability

for name, (ready, reason) in sorted(availability().items()):
    print(f"     {name:<10} {'available' if ready else 'unavailable'}  {reason}")
PYCHECK

info "Checking polarity against a brute-force truth table"
.venv/bin/python - <<'PYCHECK' || fail "polarity disagreed with the truth table"
import random
from itertools import product

from key_extract.core.extract import extract_from_tree
from key_extract.models import Polarity
from key_extract.syntax.nodes import And, Not, Or, Term, terms
from key_extract.syntax.parser import parse


def evaluate(node, present):
    if isinstance(node, Term):
        return node.surface in present
    if isinstance(node, Not):
        return not evaluate(node.operand, present)
    if isinstance(node, And):
        return all(evaluate(child, present) for child in node.operands)
    if isinstance(node, Or):
        return any(evaluate(child, present) for child in node.operands)
    raise TypeError(node)


def truth(node):
    names = sorted({term.surface for term in terms(node)})
    satisfying = []
    for bits in product([False, True], repeat=len(names)):
        present = {n for n, flag in zip(names, bits) if flag}
        if evaluate(node, present):
            satisfying.append(present)
    if not satisfying:
        return None
    return {
        name: (
            "REQUIRED" if all(name in case for case in satisfying)
            else "EXCLUDED" if all(name not in case for case in satisfying)
            else "VARIES"
        )
        for name in names
    }


def generate(rng, names, depth=0):
    if depth >= 3 or rng.random() < 0.32:
        return rng.choice(names)
    roll = rng.random()
    if roll < 0.30:
        return f"NOT {generate(rng, names, depth + 1)}"
    operator = "AND" if roll < 0.65 else "OR"
    parts = [generate(rng, names, depth + 1) for _ in range(rng.choice([2, 2, 3]))]
    return "(" + f" {operator} ".join(parts) + ")"


# The regression this guards: an earlier version tracked negation parity alone and
# reported `NOT (a OR b)` and `NOT (a AND b)` identically, despite meaning opposites.
fixed = {
    "a AND b": {"a": "REQUIRED", "b": "REQUIRED"},
    "a OR b": {"a": "OPTIONAL", "b": "OPTIONAL"},
    "NOT a": {"a": "EXCLUDED"},
    "NOT (a OR b)": {"a": "EXCLUDED", "b": "EXCLUDED"},
    "NOT (a AND b)": {"a": "CONDITIONAL", "b": "CONDITIONAL"},
    "a AND (b OR c)": {"a": "REQUIRED", "b": "OPTIONAL", "c": "OPTIONAL"},
}
for expression, expected in fixed.items():
    found = {
        keyword.surface: keyword.polarity.value.upper()
        for keyword in extract_from_tree(parse(expression))
    }
    assert found == expected, f"{expression}: expected {expected}, got {found}"

rng = random.Random(20260916)
checked = skipped = claims = weaker = 0
for _ in range(1500):
    expression = generate(rng, ["a", "b", "c", "d"])
    tree = parse(expression)
    verdicts = truth(tree)
    if verdicts is None:
        skipped += 1
        continue
    checked += 1
    for keyword in extract_from_tree(tree):
        actual = verdicts[keyword.surface]
        if keyword.polarity is Polarity.REQUIRED:
            assert actual == "REQUIRED", f"{expression}: {keyword.surface} is not required"
            claims += 1
        elif keyword.polarity is Polarity.EXCLUDED:
            assert actual == "EXCLUDED", f"{expression}: {keyword.surface} is not excluded"
            claims += 1
        elif actual in ("REQUIRED", "EXCLUDED"):
            weaker += 1  # sound but conservative; documented in ADR-001

print(
    f"     6 fixed cases + {checked} generated expressions agree "
    f"({claims} strong claims verified, {weaker} conservative, "
    f"{skipped} unsatisfiable skipped)"
)
PYCHECK
ok "polarity is sound"

info "Checking the diff reports what a set difference cannot"
.venv/bin/python - <<'PYCHECK' || fail "the diff did not behave"
from key_extract.core.diff import diff_expressions
from key_extract.models import ChangeKind

# 1. a flip is one change, not a removal plus an addition
flip = diff_expressions('knife AND NOT "bk"', 'knife AND "bk"')
assert len(flip.changes) == 1, [c.render() for c in flip.changes]
assert flip.changes[0].kind is ChangeKind.POLARITY_FLIPPED
assert flip.changes[0].note == "was forbidden, is now mandatory"

# 2. equivalent regrouping is silent
assert diff_expressions("a AND (b AND c)", "(a AND b) AND c").unchanged, (
    "associativity leaked into the report"
)

# 3. a duplicate keyword must not collapse -- this pair used to report zero changes
collapse = diff_expressions("c AND NOT (b OR c)", "b AND NOT (b OR c)")
assert collapse.changes, "the difference in what is required was lost"

# 4. a self-contradictory rule is reported
assert diff_expressions("knife", "knife AND NOT knife").right_contradictions == ["knife"]

# 5. a cosmetic retype is not a change in meaning
cosmetic = diff_expressions("a AND Blade", "a AND blade")
assert [c.kind for c in cosmetic.changes] == [ChangeKind.COSMETIC]

print("     flip / regroup-silent / no-collapse / contradiction / cosmetic")
PYCHECK
ok "diff verified"

info "Checking the shipped sample data"
.venv/bin/python - <<'PYCHECK' || fail "the sample data did not behave as documented"
from pathlib import Path

from key_extract.core.extract import extract
from key_extract.io import load_expressions

pairs = load_expressions(Path("data/sample_expressions.csv"))
results = {identifier: extract(text) for identifier, text in pairs}

broken = [key for key, result in results.items() if not result.ok]
assert not broken, f"these samples do not parse: {broken}"

contradictory = sorted(key for key, result in results.items() if result.contradictions)
assert contradictory == ["E-9"], f"expected only E-9 to be contradictory, got {contradictory}"

# E-4 and E-5 are the De Morgan pair and must not agree.
de_morgan_or = {k.polarity.value for k in results["E-4"].keywords}
de_morgan_and = {k.polarity.value for k in results["E-5"].keywords}
assert de_morgan_or == {"excluded"}, de_morgan_or
assert de_morgan_and == {"conditional"}, de_morgan_and

print(f"     {len(pairs)} expressions parse, E-9 contradictory, E-4/E-5 differ as they must")
PYCHECK
ok "sample data valid"

info "Running the test suite"
.venv/bin/python -m pytest -q || fail "tests failed"
ok "tests pass"

if [ "$WITH_BEDROCK" = "1" ]; then
    info "Checking AWS credentials"
    if .venv/bin/python -c "import boto3; boto3.client('sts').get_caller_identity()" >/dev/null 2>&1; then
        ok "AWS credentials resolve"
    else
        warn "boto3 installed but no usable AWS credentials found."
        warn "  Configure with: aws configure   (or set AWS_PROFILE)"
        warn "  The parser works without this."
    fi
fi

if [ "$WITH_VERTEX" = "1" ] && [ -z "${KE_GCP_PROJECT:-}" ]; then
    warn "KE_GCP_PROJECT is not set, so the vertex provider will report unavailable."
    warn "  Set it and run: gcloud auth application-default login"
fi

cat <<'DONE'

Setup complete.

  source .venv/bin/activate

  polarity doctor
  polarity extract 'category:kitchen AND (knife OR blade) AND NOT "butter knife"'
  polarity diff 'knife AND NOT "bk"' 'knife AND "bk"'
  polarity extract -i data/sample_expressions.csv -o out/

Both commands exit non-zero on a problem, so they work as pre-commit hooks.
NOT distributes by De Morgan: NOT (a OR b) forbids both terms, NOT (a AND b) only the pair.
DONE
