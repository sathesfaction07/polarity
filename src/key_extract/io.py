from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path

from .models import ROW_FIELDS, Extraction, as_row

KNOWN_FIELDS = frozenset({"id", "expression", "note"})


def load_expressions(path: Path) -> list[tuple[str, str]]:
    """
    Read `(id, expression)` pairs from CSV or JSON.

    Rows without an expression are skipped rather than failing the file, because one
    blank line in a 900-row export should not stop the run before anything happens.
    A missing id is filled from the row number so every result can be traced back.
    """
    if not path.exists():
        raise FileNotFoundError(f"input not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("expressions", [])
        rows = list(raw)
    elif suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    else:
        raise ValueError(f"unsupported input type '{suffix}'; use .csv or .json")

    out: list[tuple[str, str]] = []
    for index, row in enumerate(rows, start=1):
        if isinstance(row, str):
            # A bare list of expression strings is a reasonable thing to hand this.
            expression = row.strip()
            if expression:
                out.append((f"row-{index}", expression))
            continue
        if not isinstance(row, dict):
            continue
        expression = str(row.get("expression", "") or "").strip()
        if not expression:
            continue
        identifier = str(row.get("id", "") or "").strip() or f"row-{index}"
        out.append((identifier, expression))

    return out


def write_csv(results: Sequence[Extraction], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROW_FIELDS))
        writer.writeheader()
        for result in results:
            writer.writerow(as_row(result))


def write_json(results: Sequence[Extraction], path: Path) -> None:
    """
    Write the full report, always including every row.

    The JSON report is the audit artifact and it is never filtered, even when the
    tabular output is trimmed. `write_results` takes the complete set as its required
    argument for that reason.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "total": len(results),
            "ok": sum(1 for result in results if result.ok),
            "errors": sum(1 for result in results if not result.ok),
            "contradictions": sum(1 for result in results if result.contradictions),
        },
        "results": [result.model_dump() for result in results],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_excel(results: Sequence[Extraction], path: Path) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as exc:
        raise ImportError(
            "Excel output needs openpyxl - run: pip install 'polarity[excel]'"
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "keywords"

    sheet.append(list(ROW_FIELDS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    status_column = ROW_FIELDS.index("status") + 1
    for result in results:
        row = as_row(result)
        sheet.append([row[field] for field in ROW_FIELDS])
        colour = "C6EFCE" if result.ok else "FFC7CE"
        sheet.cell(row=sheet.max_row, column=status_column).fill = PatternFill(
            start_color=colour, end_color=colour, fill_type="solid"
        )

    widths = {
        "expression": 46,
        "status": 8,
        "depth": 7,
        "required": 9,
        "optional": 9,
        "excluded": 9,
        "conditional": 12,
        "keywords": 60,
        "source": 9,
        "error": 40,
    }
    for index, field in enumerate(ROW_FIELDS, start=1):
        letter = sheet.cell(row=1, column=index).column_letter
        sheet.column_dimensions[letter].width = widths.get(field, 14)

    for name in ("keywords", "error", "expression"):
        column = ROW_FIELDS.index(name) + 1
        for row_index in range(2, sheet.max_row + 1):
            sheet.cell(row=row_index, column=column).alignment = Alignment(wrap_text=True)

    sheet.freeze_panes = "A2"
    workbook.save(path)


def write_results(
    results: Sequence[Extraction],
    directory: Path,
    fmt: str,
    *,
    emit: Sequence[Extraction] | None = None,
) -> list[Path]:
    """
    Write the chosen format plus the always-complete JSON report.

    `results` is the full set and is what the JSON report always receives; `emit`
    optionally narrows the tabular output. The arguments are this way round so that the
    worst a forgetful caller can do is fail to filter, rather than quietly drop rows out
    of the audit trail.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    rows = results if emit is None else emit

    fmt = (fmt or "csv").lower()
    if fmt == "csv":
        target = directory / "keywords.csv"
        write_csv(rows, target)
        written.append(target)
    elif fmt == "excel":
        target = directory / "keywords.xlsx"
        write_excel(rows, target)
        written.append(target)
    elif fmt == "json":
        pass
    else:
        raise ValueError(f"unsupported format '{fmt}'; use csv, json or excel")

    report = directory / "full_report.json"
    write_json(results, report)
    written.append(report)
    return written
