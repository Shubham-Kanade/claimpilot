"""Receipt extraction: one document in, one validated ``ExtractedReceipt`` out.

Flow: prepare the document → check the cache → call the ``extraction`` route (structured
output) → optionally escalate to ``extraction_retry`` when a critical field was read with low
confidence (the cheap-model-first cascade measured in the bake-off) → cache the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from claimpilot.domain import CRITICAL_FIELDS, ExtractedReceipt
from claimpilot.extraction.cache import ExtractionCache, cache_key
from claimpilot.extraction.preprocess import PreparedDocument
from claimpilot.llm.client import LLMClient
from claimpilot.llm.types import LLMResult, ThinkingMode

PROMPT_VERSION = "extract_v1"
PRIMARY_ROUTE = "extraction"
RETRY_ROUTE = "extraction_retry"
USER_INSTRUCTION = "Extract this document into the schema."


@cache
def system_prompt(version: str = PROMPT_VERSION) -> str:
    return (files("claimpilot.extraction") / "prompts" / f"{version}.md").read_text("utf-8")


@dataclass(frozen=True, slots=True)
class Extraction:
    receipt: ExtractedReceipt
    cached: bool
    escalated: bool
    calls: tuple[LLMResult[ExtractedReceipt], ...] = ()

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def model_key(self) -> str | None:
        return self.calls[-1].model_key if self.calls else None


def needs_escalation(receipt: ExtractedReceipt) -> bool:
    """A critical field the model itself was unsure about is worth a stronger second read."""
    return bool(set(receipt.low_confidence_fields) & set(CRITICAL_FIELDS))


class ReceiptExtractor:
    def __init__(
        self,
        llm: LLMClient,
        *,
        cache: ExtractionCache | None = None,
        escalate: bool = True,
        thinking: ThinkingMode = "off",
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        self._llm = llm
        self._cache = cache
        self._escalate = escalate
        self._thinking: ThinkingMode = thinking
        self._prompt_version = prompt_version

    async def extract(self, document: PreparedDocument) -> Extraction:
        key = self._key(document, PRIMARY_ROUTE)
        if self._cache is not None and (hit := await self._cache.get(key)) is not None:
            return Extraction(receipt=hit, cached=True, escalated=False)

        first = await self._call(PRIMARY_ROUTE, document)
        calls = [first]
        receipt = first.parsed
        escalated = False
        if self._escalate and needs_escalation(receipt):
            second = await self._call(RETRY_ROUTE, document)
            calls.append(second)
            receipt, escalated = second.parsed, True

        if self._cache is not None:
            await self._cache.set(key, receipt)
        return Extraction(receipt=receipt, cached=False, escalated=escalated, calls=tuple(calls))

    async def _call(self, route: str, document: PreparedDocument) -> LLMResult[ExtractedReceipt]:
        return await self._llm.parse(
            route,
            system=system_prompt(self._prompt_version),
            content=[*document.blocks, {"type": "text", "text": USER_INSTRUCTION}],
            output_model=ExtractedReceipt,
            thinking=self._thinking,
        )

    def _key(self, document: PreparedDocument, route: str) -> str:
        resolved = self._llm.resolve(route)
        return cache_key(document.sha256, self._prompt_version, resolved.model.key, resolved.effort)
