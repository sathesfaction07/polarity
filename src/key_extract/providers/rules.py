from __future__ import annotations

from ..core.extract import extract as parse_extract
from ..models import Extraction
from .base import Provider


class ParserProvider(Provider):
    """
    The deterministic parser. Always available, no credentials, no network.

    This is the default and it is not a fallback for something better. Once an
    expression parses, extraction is exact -- there is no judgement left for a model to
    contribute. The LLM path exists only for input the grammar cannot accept.
    """

    name = "parser"

    def extract(self, expression: str) -> Extraction:
        return parse_extract(expression)
