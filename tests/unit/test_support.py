from __future__ import annotations

import pytest

from key_extract.core.normalize import is_cosmetic_change, normalize
from key_extract.models import (
    ROW_FIELDS,
    Extraction,
    Keyword,
    Polarity,
    as_row,
)
from key_extract.providers import PROVIDERS, availability, get_provider
from key_extract.providers.llm import BedrockProvider, VertexProvider, _extract_json
from key_extract.providers.rules import ParserProvider


class TestNormalize:
    def test_casefolds(self):
        assert normalize("KNIFE") == "knife"

    def test_casefold_handles_the_german_sharp_s(self):
        """`lower()` leaves ß alone; `casefold` maps it to ss, which is what matching wants."""
        assert normalize("STRASSE") == normalize("Straße")

    def test_nfkc_folds_fullwidth_characters(self):
        assert normalize("ｋｎｉｆｅ") == "knife"

    def test_nfkc_folds_ligatures(self):
        assert normalize("ﬁle") == "file"

    def test_internal_whitespace_collapses(self):
        assert normalize("butter   knife") == "butter knife"

    def test_surrounding_whitespace_is_stripped(self):
        assert normalize("  knife  ") == "knife"

    def test_accents_are_preserved(self):
        """
        NFKC is compatibility composition, not decomposition. `couteau` and `coûteau` are
        different words and must not be merged.
        """
        assert normalize("coûteau") != normalize("couteau")

    def test_normalising_is_idempotent(self):
        once = normalize("ＫＮＩＦＥ  Blade")
        assert normalize(once) == once


class TestIsCosmeticChange:
    @pytest.mark.parametrize(
        ("left", "right"),
        [("Blade", "blade"), ("ｋｎｉｆｅ", "knife"), ("butter  knife", "butter knife")],
    )
    def test_differences_normalize_away(self, left, right):
        assert is_cosmetic_change(left, right) is True

    def test_identical_text_is_not_a_change(self):
        assert is_cosmetic_change("blade", "blade") is False

    def test_a_real_difference_is_not_cosmetic(self):
        assert is_cosmetic_change("blade", "edge") is False


class TestKeyword:
    def test_key_is_field_plus_normalised_value(self):
        keyword = Keyword(surface="Kitchen", normalized="kitchen", field="category")
        assert keyword.key == ("category", "kitchen")

    def test_render_shows_the_source_form(self):
        assert Keyword(surface="Blade", normalized="blade").render() == "Blade"

    def test_render_quotes_a_phrase(self):
        keyword = Keyword(surface="butter knife", normalized="butter knife", quoted=True)
        assert keyword.render() == '"butter knife"'

    def test_render_shows_a_wildcard(self):
        keyword = Keyword(surface="tit", normalized="tit", wildcard=True)
        assert keyword.render() == "tit*"

    def test_render_shows_a_field(self):
        keyword = Keyword(surface="kitchen", normalized="kitchen", field="category")
        assert keyword.render() == "category:kitchen"


class TestExtractionModel:
    def make(self, *pairs: tuple[str, Polarity]) -> Extraction:
        return Extraction(
            expression="synthetic",
            keywords=[
                Keyword(surface=surface, normalized=surface, polarity=polarity)
                for surface, polarity in pairs
            ],
        )

    def test_ok_is_false_when_an_error_is_set(self):
        assert Extraction(expression="x", error="boom").ok is False

    def test_by_polarity_filters(self):
        result = self.make(("a", Polarity.REQUIRED), ("b", Polarity.EXCLUDED))
        assert [k.surface for k in result.by_polarity(Polarity.REQUIRED)] == ["a"]

    def test_counts_cover_every_polarity_even_at_zero(self):
        counts = self.make(("a", Polarity.REQUIRED)).counts()
        assert set(counts) == {polarity.value for polarity in Polarity}
        assert counts["optional"] == 0

    def test_grouped_keeps_every_occurrence(self):
        result = self.make(("a", Polarity.REQUIRED), ("a", Polarity.EXCLUDED))
        assert len(result.grouped()[("", "a")]) == 2

    def test_polarities_returns_a_set_per_keyword(self):
        result = self.make(("a", Polarity.REQUIRED), ("a", Polarity.EXCLUDED))
        assert result.polarities()[("", "a")] == frozenset({Polarity.REQUIRED, Polarity.EXCLUDED})

    def test_contradictions_finds_required_plus_excluded(self):
        result = self.make(("a", Polarity.REQUIRED), ("a", Polarity.EXCLUDED))
        assert result.contradictions == ["a"]

    def test_optional_plus_conditional_is_not_a_contradiction(self):
        result = self.make(("a", Polarity.OPTIONAL), ("a", Polarity.CONDITIONAL))
        assert result.contradictions == []


class TestRowShape:
    def test_as_row_covers_every_declared_field(self):
        from key_extract.core.extract import extract

        assert set(as_row(extract("a AND b"))) == set(ROW_FIELDS)

    def test_status_reflects_success(self):
        from key_extract.core.extract import extract

        assert as_row(extract("a AND b"))["status"] == "ok"
        assert as_row(extract("a AND"))["status"] == "error"

    def test_keywords_are_rendered_with_their_polarity(self):
        from key_extract.core.extract import extract

        row = as_row(extract("a AND NOT b"))
        assert "a[required]" in row["keywords"]
        assert "b[excluded]" in row["keywords"]

    def test_counts_are_present_per_polarity(self):
        from key_extract.core.extract import extract

        row = as_row(extract("a AND (b OR c)"))
        assert row["required"] == 1
        assert row["optional"] == 2


class TestProviderRegistry:
    def test_the_parser_provider_is_always_available(self):
        assert ParserProvider.is_available() is True
        assert ParserProvider.unavailable_reason() == ""

    def test_the_default_provider_is_the_parser(self):
        assert isinstance(get_provider("parser"), ParserProvider)

    def test_an_empty_name_falls_back_to_the_parser(self):
        assert isinstance(get_provider(""), ParserProvider)

    def test_an_unknown_provider_lists_the_known_ones(self):
        with pytest.raises(ValueError, match="known providers"):
            get_provider("telepathy")

    def test_availability_covers_every_registered_provider(self):
        assert set(availability()) == set(PROVIDERS)

    def test_an_unavailable_provider_explains_how_to_install_it(self):
        for name, (ready, reason) in availability().items():
            if not ready:
                assert reason, f"{name} is unavailable with no explanation"
                assert "pip install" in reason

    def test_requesting_an_unavailable_provider_gives_the_install_command(self):
        if BedrockProvider.is_available():
            pytest.skip("boto3 is installed in this environment")
        with pytest.raises(ValueError, match="pip install"):
            get_provider("bedrock")

    def test_the_parser_provider_returns_a_populated_extraction(self):
        result = ParserProvider().extract("a AND NOT b")
        assert result.ok is True
        assert result.source == "parser"
        assert len(result.keywords) == 2

    def test_llm_providers_declare_their_names_without_their_sdks(self):
        """`doctor` must work on a machine with neither SDK installed."""
        assert BedrockProvider.name == "bedrock"
        assert VertexProvider.name == "vertex"


class TestLLMJsonHandling:
    """The parsing side of the LLM path, which is testable without a network."""

    def test_plain_json_is_parsed(self):
        assert _extract_json('{"keywords": []}') == {"keywords": []}

    def test_json_wrapped_in_prose_is_recovered(self):
        assert _extract_json('Sure! {"keywords": []} Hope that helps.') == {"keywords": []}

    def test_json_in_a_code_fence_is_recovered(self):
        assert _extract_json('```json\n{"keywords": []}\n```') == {"keywords": []}

    def test_unparseable_text_returns_none(self):
        assert _extract_json("I could not do that") is None

    def test_malformed_json_returns_none(self):
        assert _extract_json('{"keywords": [') is None

    def test_an_unknown_polarity_rejects_the_whole_response(self):
        """
        Coercing an unrecognised label to `required` would turn a model's confusion into
        a confident wrong answer. Returning None lets the caller fall back to the parser.
        """
        provider = BedrockProvider("us-east-1", "model")
        provider._complete = lambda expression: (
            '{"keywords": [{"surface": "a", "polarity": "mandatory"}]}'
        )
        assert provider.extract("a") is None

    def test_a_valid_response_becomes_an_extraction(self):
        provider = BedrockProvider("us-east-1", "model")
        provider._complete = lambda expression: (
            '{"keywords": [{"surface": "Knife", "field": "", "polarity": "excluded"}]}'
        )
        result = provider.extract("NOT Knife")
        assert result is not None
        assert result.source == "bedrock"
        assert result.keywords[0].polarity is Polarity.EXCLUDED
        assert result.keywords[0].normalized == "knife"

    def test_a_reported_error_becomes_a_failed_extraction(self):
        provider = BedrockProvider("us-east-1", "model")
        provider._complete = lambda expression: '{"keywords": [], "error": "too weird"}'
        result = provider.extract("???")
        assert result is not None
        assert result.ok is False
        assert "too weird" in result.error

    def test_an_empty_keyword_list_returns_none(self):
        provider = BedrockProvider("us-east-1", "model")
        provider._complete = lambda expression: '{"keywords": []}'
        assert provider.extract("a") is None

    def test_a_failed_call_returns_none(self):
        provider = BedrockProvider("us-east-1", "model")
        provider._complete = lambda expression: None
        assert provider.extract("a") is None

    def test_the_tree_is_marked_as_interpreted_not_parsed(self):
        """Nobody should mistake a model's reading for a parse."""
        provider = BedrockProvider("us-east-1", "model")
        provider._complete = lambda expression: (
            '{"keywords": [{"surface": "a", "polarity": "required"}]}'
        )
        assert "interpreted" in provider.extract("a").tree
