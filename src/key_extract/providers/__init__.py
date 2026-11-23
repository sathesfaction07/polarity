from __future__ import annotations

from ..config import Settings
from .base import Provider
from .llm import BedrockProvider, VertexProvider
from .rules import ParserProvider

PROVIDERS: dict[str, type[Provider]] = {
    "parser": ParserProvider,
    "bedrock": BedrockProvider,
    "vertex": VertexProvider,
}


def get_provider(name: str, settings: Settings | None = None) -> Provider:
    """
    Build a provider, failing with an instruction rather than a traceback.

    Availability is checked before construction so a missing optional dependency reads
    as "here is the command" instead of an ImportError from inside a call.
    """
    config = settings or Settings()
    key = (name or "parser").strip().lower()

    cls = PROVIDERS.get(key)
    if cls is None:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown provider '{name}'; known providers: {known}")
    if not cls.is_available():
        raise ValueError(cls.unavailable_reason())

    if cls is BedrockProvider:
        return BedrockProvider(config.aws_region, config.bedrock_model)
    if cls is VertexProvider:
        return VertexProvider(config.gcp_project, config.gcp_location, config.gemini_model)
    return ParserProvider()


def availability() -> dict[str, tuple[bool, str]]:
    """Per-provider `(available, reason_if_not)`, for the `doctor` command."""
    return {name: (cls.is_available(), cls.unavailable_reason()) for name, cls in PROVIDERS.items()}


__all__ = [
    "PROVIDERS",
    "BedrockProvider",
    "ParserProvider",
    "Provider",
    "VertexProvider",
    "availability",
    "get_provider",
]
