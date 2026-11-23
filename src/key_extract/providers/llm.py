from __future__ import annotations

import json
import logging
import re

from ..core.normalize import normalize
from ..models import Extraction, Keyword, Polarity
from .base import Provider

logger = logging.getLogger(__name__)

SYSTEM = """You interpret product classification expressions and report what each keyword means.

Rules:
1. Return JSON only. No prose, no code fences.
2. Every keyword in the expression must appear exactly once in your output.
3. Assign each keyword one polarity:
   required    - the rule cannot match unless this keyword is present
   optional    - one of several alternatives; the rule can match without it
   excluded    - the rule cannot match if this keyword is present
   conditional - negated, but only as part of a combination, so its presence alone
                 does not prevent a match
4. NOT distributes by De Morgan. NOT (a OR b) excludes a and b individually.
   NOT (a AND b) excludes only the combination, so a and b are conditional.
5. If you cannot interpret the expression, return {"keywords": [], "error": "reason"}.

Output shape:
{"keywords": [{"surface": "knife", "field": "", "polarity": "required"}]}"""

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_VALID = {polarity.value for polarity in Polarity}


class _LLMProvider(Provider):
    """
    Shared prompt handling for the hosted models.

    **This path is deliberately narrow.** Extraction from a parsed expression is exact,
    so a model adds nothing there. It is here for expressions the grammar rejects --
    a dialect with an operator this parser does not know, or syntax mangled by a
    round trip through a spreadsheet. The result is marked with its source so nobody
    mistakes an interpretation for a parse.

    Any failure returns None rather than raising, so the caller falls back to reporting
    the original parse error, which is more useful than a model's guess about why.
    """

    def _complete(self, expression: str) -> str | None:
        raise NotImplementedError

    def extract(self, expression: str) -> Extraction | None:
        raw = self._complete(expression)
        if not raw:
            return None

        payload = _extract_json(raw)
        if payload is None:
            logger.warning("%s returned no parseable JSON", self.name)
            return None

        if payload.get("error"):
            return Extraction(
                expression=expression,
                error=f"{self.name}: {payload['error']}",
                source=self.name,
            )

        entries = payload.get("keywords")
        if not isinstance(entries, list):
            return None

        keywords: list[Keyword] = []
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            surface = str(entry.get("surface", "")).strip()
            if not surface:
                continue
            polarity = str(entry.get("polarity", "")).strip().lower()
            if polarity not in _VALID:
                # An unknown label is not silently coerced to required, because that
                # would turn a model's confusion into a confident wrong answer.
                logger.warning("%s returned unknown polarity %r", self.name, polarity)
                return None
            keywords.append(
                Keyword(
                    surface=surface,
                    normalized=normalize(surface),
                    field=str(entry.get("field", "")).strip(),
                    polarity=Polarity(polarity),
                    position=position,
                )
            )

        if not keywords:
            return None

        return Extraction(
            expression=expression,
            keywords=keywords,
            tree="(interpreted, not parsed)",
            depth=0,
            source=self.name,
        )


class BedrockProvider(_LLMProvider):
    name = "bedrock"

    def __init__(self, region: str, model_id: str) -> None:
        self.region = region
        self.model_id = model_id

    @classmethod
    def is_available(cls) -> bool:
        try:
            import boto3  # noqa: F401
        except ImportError:
            return False
        return True

    @classmethod
    def unavailable_reason(cls) -> str:
        if cls.is_available():
            return ""
        return "boto3 is not installed - run: pip install 'polarity[bedrock]'"

    def _complete(self, expression: str) -> str | None:
        try:
            import boto3
        except ImportError:
            return None
        try:
            client = boto3.client("bedrock-runtime", region_name=self.region)
            response = client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(
                    {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 1500,
                        "temperature": 0,
                        "system": SYSTEM,
                        "messages": [{"role": "user", "content": expression}],
                    }
                ),
            )
            body = json.loads(response["body"].read())
            return body["content"][0]["text"]
        except Exception as exc:  # noqa: BLE001 - any failure means fall back
            logger.warning("bedrock call failed: %s", exc)
            return None


class VertexProvider(_LLMProvider):
    name = "vertex"

    def __init__(self, project: str, location: str, model_name: str) -> None:
        self.project = project
        self.location = location
        self.model_name = model_name

    @classmethod
    def is_available(cls) -> bool:
        try:
            import vertexai  # noqa: F401
        except ImportError:
            return False
        return True

    @classmethod
    def unavailable_reason(cls) -> str:
        if cls.is_available():
            return ""
        return "google-cloud-aiplatform is not installed - run: pip install 'polarity[vertex]'"

    def _complete(self, expression: str) -> str | None:
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel
        except ImportError:
            return None
        if not self.project:
            logger.warning("vertex needs KE_GCP_PROJECT to be set")
            return None
        try:
            vertexai.init(project=self.project, location=self.location)
            model = GenerativeModel(self.model_name, system_instruction=SYSTEM)
            response = model.generate_content(
                expression, generation_config={"temperature": 0, "max_output_tokens": 1500}
            )
            return response.text
        except Exception as exc:  # noqa: BLE001 - any failure means fall back
            logger.warning("vertex call failed: %s", exc)
            return None


def _extract_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if match is None:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None
