from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from .config import Settings
from .core.diff import diff_expressions
from .core.extract import extract as parser_extract
from .io import load_expressions, write_results
from .models import ChangeKind, Extraction, Polarity
from .providers import availability, get_provider

_POLARITY_COLOUR = {
    Polarity.REQUIRED: "green",
    Polarity.OPTIONAL: "cyan",
    Polarity.EXCLUDED: "red",
    Polarity.CONDITIONAL: "yellow",
}

_CHANGE_COLOUR = {
    ChangeKind.ADDED: "green",
    ChangeKind.REMOVED: "red",
    ChangeKind.POLARITY_FLIPPED: "yellow",
    ChangeKind.COSMETIC: "white",
}


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("botocore", "boto3", "urllib3", "google"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _settings(**overrides) -> Settings:
    return Settings(**{k: v for k, v in overrides.items() if v is not None})


def _run(expression: str, settings: Settings) -> Extraction:
    """
    Extract with the configured provider, falling back to the parser.

    The fallback is one-directional on purpose: a provider that cannot interpret an
    expression yields to the parser's error, which names a column. A model's guess about
    why something failed to parse is less useful than the position of the problem.
    """
    provider = get_provider(settings.provider, settings)
    result = provider.extract(expression)
    if result is None:
        return parser_extract(expression)
    return result


@click.group()
@click.version_option(package_name="key-extract")
def main() -> None:
    """Parse classification expressions and extract keywords with their polarity."""


@main.command()
@click.argument("expression", required=False)
@click.option("--input", "-i", "input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path))
@click.option("--format", "-f", "fmt", type=click.Choice(["csv", "json", "excel"]))
@click.option("--provider", type=click.Choice(["parser", "bedrock", "vertex"]))
@click.option("--tree", is_flag=True, help="Show the parsed tree.")
@click.option("--ok-only", is_flag=True, help="Emit only parsed rows. The report keeps all.")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]))
def extract(
    expression: str | None,
    input_path: Path | None,
    output: Path | None,
    fmt: str | None,
    provider: str | None,
    tree: bool,
    ok_only: bool,
    log_level: str | None,
) -> None:
    """
    Extract keywords from one EXPRESSION, or from a file with --input.

    Exits non-zero if anything failed to parse or is self-contradictory, so a
    pre-commit hook or pipeline step can gate on rule health.
    """
    settings = _settings(
        output_dir=output, output_format=fmt, provider=provider, log_level=log_level
    )
    _configure_logging(settings.log_level)

    if not expression and not input_path:
        raise click.UsageError("give an EXPRESSION or use --input")
    if expression and input_path:
        raise click.UsageError("give an EXPRESSION or --input, not both")

    try:
        if expression:
            result = _run(expression, settings)
            _print_one(result, show_tree=True)
            if not result.ok or result.contradictions:
                sys.exit(1)
            return

        pairs = load_expressions(input_path)  # type: ignore[arg-type]
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if not pairs:
        raise click.ClickException(f"no expressions found in {input_path}")

    results = [_run(text, settings) for _, text in pairs]

    for (identifier, _), result in zip(pairs, results, strict=True):
        _print_row(identifier, result, show_tree=tree)

    click.echo("")
    ok = sum(1 for result in results if result.ok)
    bad = len(results) - ok
    contradictory = sum(1 for result in results if result.contradictions)
    click.echo(f"{'total':<16}{len(results)}")
    click.echo(f"{'parsed':<16}{ok}")
    click.echo(f"{'errors':<16}{bad}")
    click.echo(f"{'contradictory':<16}{contradictory}")

    emit = [result for result in results if result.ok] if ok_only else None
    try:
        written = write_results(results, settings.output_dir, settings.output_format, emit=emit)
    except (ImportError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("")
    for path in written:
        click.echo(f"wrote {path}")

    if bad or contradictory:
        sys.exit(1)


def _print_one(result: Extraction, *, show_tree: bool) -> None:
    if not result.ok:
        click.echo(click.style("could not parse", fg="red"))
        click.echo(result.error)
        return

    if show_tree:
        click.echo(f"{'tree':<14}{result.tree}")
        click.echo(f"{'depth':<14}{result.depth}")
        click.echo(f"{'source':<14}{result.source}")
        click.echo("")

    for keyword in result.keywords:
        label = click.style(keyword.polarity.value, fg=_POLARITY_COLOUR[keyword.polarity])
        click.echo(f"  {keyword.render():<34}{label}")

    if result.contradictions:
        click.echo("")
        for keyword in result.contradictions:
            click.echo(
                click.style("contradiction  ", fg="red")
                + f"{keyword} is both required and excluded; the rule cannot match"
            )


def _print_row(identifier: str, result: Extraction, *, show_tree: bool) -> None:
    if not result.ok:
        first = result.error.splitlines()[0]
        click.echo(f"{identifier:<8}" + click.style("error   ", fg="red") + first)
        return

    counts = result.counts()
    summary = " ".join(
        click.style(f"{counts[polarity.value]}{polarity.value[:3]}", fg=_POLARITY_COLOUR[polarity])
        for polarity in Polarity
        if counts[polarity.value]
    )
    flag = click.style("  CONTRADICTORY", fg="red") if result.contradictions else ""
    click.echo(f"{identifier:<8}ok      d{result.depth} {summary}{flag}")
    if show_tree:
        click.echo(f"{'':<8}        {result.tree}")


@main.command()
@click.argument("before")
@click.argument("after")
@click.option("--tree", is_flag=True, help="Show both parsed trees.")
def diff(before: str, after: str, tree: bool) -> None:
    """
    Compare two versions of an expression, BEFORE and AFTER.

    Reports polarity flips as single changes rather than as a removal plus an addition,
    because a keyword moving from excluded to required is the most consequential edit a
    rule can receive and a set difference buries it.
    """
    result = diff_expressions(before, after)

    if not result.ok:
        raise click.ClickException(result.error)

    if tree:
        click.echo(f"{'before':<10}{result.left_tree}")
        click.echo(f"{'after':<10}{result.right_tree}")
        click.echo("")

    if result.unchanged:
        click.echo("no change")
    elif result.structural_only:
        click.echo(click.style("regrouped", fg="yellow") + "  same keywords and polarities")
        click.echo(f"  before  {result.left_tree}")
        click.echo(f"  after   {result.right_tree}")

    for change in result.changes:
        label = click.style(f"{change.kind.value:<17}", fg=_CHANGE_COLOUR[change.kind])
        click.echo(f"  {label}{change.render()}")
        if change.note:
            click.echo(f"  {'':<17}{click.style(change.note, dim=True)}")

    for side, found in (
        ("before", result.left_contradictions),
        ("after", result.right_contradictions),
    ):
        for keyword in found:
            click.echo(
                click.style("  contradiction    ", fg="red")
                + f"{keyword} is both required and excluded in the {side} expression"
            )

    if result.changes or result.structural_only:
        sys.exit(1)


@main.command()
def doctor() -> None:
    """Report which providers this machine can use, and what the grammar accepts."""
    settings = Settings()

    click.echo("providers")
    for name, (ready, reason) in sorted(availability().items()):
        if ready:
            click.echo(f"  {name:<10}" + click.style("available", fg="green"))
        else:
            click.echo(f"  {name:<10}" + click.style("unavailable", fg="yellow") + f"  {reason}")

    click.echo("")
    click.echo("grammar, loosest precedence first")
    click.echo("  OR         a OR b, a || b")
    click.echo("  AND        a AND b, a && b, and adjacent terms (implicit AND)")
    click.echo("  NOT        NOT a, !a")
    click.echo('  atom       knife, "butter knife", category:kitchen, tit*, ( ... )')

    click.echo("")
    click.echo("polarity")
    for polarity, meaning in (
        (Polarity.REQUIRED, "must be present   e.g. a in `a AND b`"),
        (Polarity.OPTIONAL, "one alternative   e.g. a in `a OR b`"),
        (Polarity.EXCLUDED, "must be absent    e.g. a in `NOT a`"),
        (Polarity.CONDITIONAL, "negated in a combination only, e.g. a in `NOT (a AND b)`"),
    ):
        click.echo(f"  {click.style(polarity.value, fg=_POLARITY_COLOUR[polarity]):<22}{meaning}")

    click.echo("")
    click.echo(f"{'settings':<12}provider={settings.provider} format={settings.output_format}")
    click.echo("")
    click.echo("NOT distributes by De Morgan: NOT (a OR b) excludes both terms,")
    click.echo("NOT (a AND b) excludes only the combination.")


__all__ = ["main"]
