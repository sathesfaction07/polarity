from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Extraction


class Provider(ABC):
    """
    Turns an expression into an Extraction.

    `extract` may return None, meaning "I could not do this", which lets the pipeline
    fall back rather than raise. The deterministic parser never returns None for a
    well-formed expression, so the fallback exists for the LLM path.
    """

    name: str = "provider"

    @abstractmethod
    def extract(self, expression: str) -> Extraction | None: ...

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def unavailable_reason(cls) -> str:
        return ""
