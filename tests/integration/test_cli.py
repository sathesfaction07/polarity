from __future__ import annotations

import csv
import json

import pytest
from click.testing import CliRunner

from key_extract.cli import main
from key_extract.core.extract import extract
from key_extract.io import load_expressions, write_csv, write_excel, write_results
from key_extract.models import ROW_FIELDS


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def expressions_csv(tmp_path):
    path = tmp_path / "rules.csv"
    path.write_text(
        "id,expression,note\n"
        "R-1,knife AND blade,both required\n"
        'R-2,"knife AND NOT ""butter knife""",an exclusion\n'
        "R-3,category:kitchen OR premium,alternatives\n",
        encoding="utf-8",
    )
    return path


class TestLoadExpressions:
    def test_reads_a_csv(self, expressions_csv):
        pairs = load_expressions(expressions_csv)
        assert [identifier for identifier, _ in pairs] == ["R-1", "R-2", "R-3"]

    def test_quoted_expressions_survive_the_csv(self, expressions_csv):
        pairs = dict(load_expressions(expressions_csv))
        assert pairs["R-2"] == 'knife AND NOT "butter knife"'

    def test_reads_a_json_list_of_objects(self, tmp_path):
        path = tmp_path / "rules.json"
        path.write_text(json.dumps([{"id": "R-1", "expression": "a AND b"}]), encoding="utf-8")
        assert load_expressions(path) == [("R-1", "a AND b")]

    def test_reads_a_json_object_with_an_expressions_key(self, tmp_path):
        path = tmp_path / "rules.json"
        path.write_text(
            json.dumps({"expressions": [{"id": "R-1", "expression": "a"}]}), encoding="utf-8"
        )
        assert len(load_expressions(path)) == 1

    def test_reads_a_bare_json_list_of_strings(self, tmp_path):
        path = tmp_path / "rules.json"
        path.write_text(json.dumps(["a AND b", "c OR d"]), encoding="utf-8")
        assert [text for _, text in load_expressions(path)] == ["a AND b", "c OR d"]

    def test_a_missing_id_is_filled_from_the_row_number(self, tmp_path):
        path = tmp_path / "rules.csv"
        path.write_text("id,expression\n,a AND b\n", encoding="utf-8")
        assert load_expressions(path)[0][0] == "row-1"

    def test_blank_expressions_are_skipped(self, tmp_path):
        path = tmp_path / "rules.csv"
        path.write_text("id,expression\nR-1,a\nR-2,\nR-3,b\n", encoding="utf-8")
        assert [identifier for identifier, _ in load_expressions(path)] == ["R-1", "R-3"]

    def test_a_bom_does_not_corrupt_the_header(self, tmp_path):
        path = tmp_path / "rules.csv"
        path.write_text("\ufeffid,expression\nR-1,a AND b\n", encoding="utf-8")
        assert load_expressions(path)[0][0] == "R-1"

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="input not found"):
            load_expressions(tmp_path / "nope.csv")

    def test_an_unsupported_extension_names_what_is_accepted(self, tmp_path):
        path = tmp_path / "rules.txt"
        path.write_text("a", encoding="utf-8")
        with pytest.raises(ValueError, match="use .csv or .json"):
            load_expressions(path)


class TestWriters:
    def test_csv_has_a_stable_column_order(self, tmp_path):
        path = tmp_path / "out" / "keywords.csv"
        write_csv([extract("a AND b")], path)
        with path.open(encoding="utf-8") as handle:
            assert next(csv.reader(handle)) == list(ROW_FIELDS)

    def test_csv_of_nothing_still_writes_a_header(self, tmp_path):
        path = tmp_path / "keywords.csv"
        write_csv([], path)
        assert path.read_text(encoding="utf-8").strip() == ",".join(ROW_FIELDS)

    def test_excel_writes_a_sheet(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        path = tmp_path / "keywords.xlsx"
        write_excel([extract("a AND b"), extract("a AND")], path)
        book = openpyxl.load_workbook(path)
        assert book.active.title == "keywords"
        assert book.active.max_row == 3

    def test_the_report_summarises_contradictions(self, tmp_path):
        written = write_results(
            [extract("a AND b"), extract("knife AND NOT knife"), extract("a AND")],
            tmp_path,
            "csv",
        )
        report = json.loads(
            next(p for p in written if p.name == "full_report.json").read_text(encoding="utf-8")
        )
        assert report["summary"] == {
            "total": 3,
            "ok": 2,
            "errors": 1,
            "contradictions": 1,
        }

    def test_emit_narrows_the_csv_without_touching_the_report(self, tmp_path):
        """
        The audit report is never filtered. The complete set is the required argument so
        that a forgetful caller fails to filter rather than dropping rows silently.
        """
        everything = [extract("a AND b"), extract("a AND")]
        write_results(everything, tmp_path, "csv", emit=[everything[0]])

        body = (tmp_path / "keywords.csv").read_text(encoding="utf-8")
        report = json.loads((tmp_path / "full_report.json").read_text(encoding="utf-8"))

        assert body.count("\n") == 2, "header plus one emitted row"
        assert len(report["results"]) == 2

    def test_json_format_writes_only_the_report(self, tmp_path):
        written = write_results([extract("a")], tmp_path, "json")
        assert [path.name for path in written] == ["full_report.json"]

    def test_an_unknown_format_names_the_valid_ones(self, tmp_path):
        with pytest.raises(ValueError, match="use csv, json or excel"):
            write_results([extract("a")], tmp_path, "yaml")


class TestExtractCommand:
    def test_a_single_expression_prints_polarities(self, runner):
        result = runner.invoke(main, ["extract", "knife AND NOT blade"])
        assert result.exit_code == 0
        assert "required" in result.output
        assert "excluded" in result.output

    def test_the_tree_is_shown_for_a_single_expression(self, runner):
        result = runner.invoke(main, ["extract", "a AND (b OR c)"])
        assert "(a AND (b OR c))" in result.output

    def test_a_parse_error_exits_non_zero_with_a_caret(self, runner):
        result = runner.invoke(main, ["extract", "knife AND ("])
        assert result.exit_code == 1
        assert "^" in result.output

    def test_a_contradiction_exits_non_zero(self, runner):
        """So a pre-commit hook or pipeline step can gate on rule health."""
        result = runner.invoke(main, ["extract", "knife AND NOT knife"])
        assert result.exit_code == 1
        assert "contradiction" in result.output

    def test_a_file_is_processed_and_written(self, runner, expressions_csv, tmp_path):
        out = tmp_path / "out"
        result = runner.invoke(main, ["extract", "-i", str(expressions_csv), "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert (out / "keywords.csv").exists()
        assert (out / "full_report.json").exists()

    def test_the_summary_counts_every_row(self, runner, expressions_csv, tmp_path):
        result = runner.invoke(
            main, ["extract", "-i", str(expressions_csv), "-o", str(tmp_path / "out")]
        )
        assert "total           3" in result.output
        assert "parsed          3" in result.output

    def test_a_file_with_a_contradiction_exits_non_zero(self, runner, tmp_path):
        path = tmp_path / "rules.csv"
        path.write_text("id,expression\nR-1,a AND b\nR-2,knife AND NOT knife\n", encoding="utf-8")
        result = runner.invoke(main, ["extract", "-i", str(path), "-o", str(tmp_path / "out")])
        assert result.exit_code == 1
        assert "CONTRADICTORY" in result.output

    def test_ok_only_trims_the_csv_but_not_the_report(self, runner, tmp_path):
        path = tmp_path / "rules.csv"
        path.write_text("id,expression\nR-1,a AND b\nR-2,a AND\n", encoding="utf-8")
        out = tmp_path / "out"

        runner.invoke(main, ["extract", "-i", str(path), "-o", str(out), "--ok-only"])

        body = (out / "keywords.csv").read_text(encoding="utf-8")
        report = json.loads((out / "full_report.json").read_text(encoding="utf-8"))
        assert body.count("\n") == 2
        assert len(report["results"]) == 2

    def test_giving_neither_an_expression_nor_a_file_is_a_usage_error(self, runner):
        result = runner.invoke(main, ["extract"])
        assert result.exit_code != 0
        assert "give an EXPRESSION or use --input" in result.output

    def test_giving_both_is_a_usage_error(self, runner, expressions_csv):
        result = runner.invoke(main, ["extract", "a AND b", "-i", str(expressions_csv)])
        assert result.exit_code != 0
        assert "not both" in result.output

    def test_an_empty_file_is_a_clean_error(self, runner, tmp_path):
        path = tmp_path / "rules.csv"
        path.write_text("id,expression\n", encoding="utf-8")
        result = runner.invoke(main, ["extract", "-i", str(path)])
        assert result.exit_code != 0
        assert "no expressions found" in result.output


class TestDiffCommand:
    def test_identical_expressions_report_no_change(self, runner):
        result = runner.invoke(main, ["diff", "a AND b", "a AND b"])
        assert result.exit_code == 0
        assert "no change" in result.output

    def test_a_flip_is_reported_and_exits_non_zero(self, runner):
        result = runner.invoke(main, ["diff", "a AND NOT b", "a AND b"])
        assert result.exit_code == 1
        assert "polarity_flipped" in result.output
        assert "was forbidden, is now mandatory" in result.output

    def test_equivalent_grouping_is_silent_and_exits_zero(self, runner):
        result = runner.invoke(main, ["diff", "a AND (b AND c)", "(a AND b) AND c"])
        assert result.exit_code == 0
        assert "no change" in result.output

    def test_an_addition_is_reported(self, runner):
        result = runner.invoke(main, ["diff", "a OR b", "a OR b OR c"])
        assert "added" in result.output

    def test_a_contradiction_introduced_is_reported(self, runner):
        result = runner.invoke(main, ["diff", "knife", "knife AND NOT knife"])
        assert "contradiction" in result.output

    def test_trees_can_be_shown(self, runner):
        result = runner.invoke(main, ["diff", "a AND b", "a AND c", "--tree"])
        assert "(a AND b)" in result.output
        assert "(a AND c)" in result.output

    def test_a_bad_expression_is_a_clean_error(self, runner):
        result = runner.invoke(main, ["diff", "a AND", "a AND b"])
        assert result.exit_code != 0
        assert "left:" in result.output


class TestDoctorCommand:
    def test_reports_provider_availability(self, runner):
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "parser" in result.output
        assert "bedrock" in result.output

    def test_documents_the_grammar(self, runner):
        result = runner.invoke(main, ["doctor"])
        for token in ("OR", "AND", "NOT", "category:kitchen"):
            assert token in result.output

    def test_documents_all_four_polarities(self, runner):
        result = runner.invoke(main, ["doctor"])
        for polarity in ("required", "optional", "excluded", "conditional"):
            assert polarity in result.output

    def test_explains_de_morgan(self, runner):
        """The rule most likely to surprise someone reading the output."""
        result = runner.invoke(main, ["doctor"])
        assert "De Morgan" in result.output

    def test_needs_no_input_and_no_network(self, runner):
        assert runner.invoke(main, ["doctor"]).exit_code == 0


class TestCliSurface:
    def test_help_lists_every_command(self, runner):
        output = runner.invoke(main, ["--help"]).output
        for command in ("extract", "diff", "doctor"):
            assert command in output

    def test_version_is_reported(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output

    @pytest.mark.parametrize("command", ["extract", "diff", "doctor"])
    def test_every_command_has_help_text(self, runner, command):
        result = runner.invoke(main, [command, "--help"])
        assert result.exit_code == 0
        assert len(result.output.strip()) > 40

    def test_the_shipped_sample_file_parses_cleanly(self, runner, tmp_path):
        """
        The repo's own sample data is covered, so editing it and breaking it fails here
        rather than in somebody's first run.
        """
        from pathlib import Path

        sample = Path(__file__).resolve().parents[2] / "data" / "sample_expressions.csv"
        result = runner.invoke(main, ["extract", "-i", str(sample), "-o", str(tmp_path / "out")])
        assert "errors          0" in result.output
        # E-9 is deliberately self-contradictory, so a non-zero exit is expected.
        assert result.exit_code == 1
        assert "contradictory   1" in result.output
