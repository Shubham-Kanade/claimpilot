"""Receipt extraction: one document in, one validated ``ExtractedReceipt`` out.

Flow: prepare the document → check the cache → call the ``extraction`` route (structured
output) → ask for a **second opinion** from the stronger ``extraction_retry`` route when the
first read looks suspicious → cache the result.

A first read is suspicious when its own arithmetic or GST figures do not hold together, or when it
says the document talks to an AI. Both are exactly what a misread digit, a swapped row or an
over-eager "this is an injection" produces on a genuine receipt, and exactly what a forged or
hostile one produces too. Only a second look can tell them apart, so the stronger model reads the
document again before anybody is accused (ADR-031). Most receipts are never re-read: the average
cost stays close to the cheap model's.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from claimpilot.domain import CRITICAL_FIELDS, ExtractedReceipt, Severity
from claimpilot.extraction.cache import ExtractionCache, cache_key
from claimpilot.extraction.preprocess import PreparedDocument
from claimpilot.extraction.schema import WireReceipt, to_domain
from claimpilot.llm.client import LLMClient
from claimpilot.llm.types import LLMResult, ThinkingMode
from claimpilot.trust.gst import check_gst

PROMPT_VERSION = "extract_v3"
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
    calls: tuple[LLMResult[WireReceipt], ...] = ()

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def model_key(self) -> str | None:
        return self.calls[-1].model_key if self.calls else None


def needs_escalation(receipt: ExtractedReceipt) -> bool:
    """A critical field the model itself was unsure about is worth a stronger second read."""
    return bool(set(receipt.low_confidence_fields) & set(CRITICAL_FIELDS))


# What a re-read can change. ``gstin_missing`` is excluded: a bill without a GSTIN stays without.
_UNREAD_AS_PRINTED = frozenset({"gstin_missing"})
# Figures two agreeing reads confirm: they are printed that way, however hard they were to read.
_CONFIRMED_BY_AGREEMENT = ("total", "subtotal", "line_items", "taxes", "merchant_gstin")
AGREEMENT_TOLERANCE = 0.5


def looks_suspicious(receipt: ExtractedReceipt) -> bool:
    """Does this first read deserve a second opinion before anything is concluded from it?

    Yes when it says the document talks to an AI, when it is unsure of the total (a handwritten
    "Rs 260/-" read as 2601 would put 2,341 rupees on a claim and trip no arithmetic check), or when
    its own arithmetic and GST figures do not hold together.
    """
    if receipt.contains_instructions or "total" in receipt.low_confidence_fields:
        return True  # the flag, or the amount being claimed: the one figure that must be right
    return any(
        f.severity is not Severity.info and f.code not in _UNREAD_AS_PRINTED
        for f in check_gst(receipt)
    )


def _same_figures(a: ExtractedReceipt, b: ExtractedReceipt) -> bool:
    def close(x: float | None, y: float | None) -> bool:
        return x == y or (x is not None and y is not None and abs(x - y) <= AGREEMENT_TOLERANCE)

    items = (sum(i.amount for i in a.line_items), sum(i.amount for i in b.line_items))
    gstin = ((a.merchant_gstin or "").upper(), (b.merchant_gstin or "").upper())
    return (
        close(a.total, b.total)
        and close(a.subtotal, b.subtotal)
        and close(*items)
        and (gstin[0] == gstin[1])
    )


def reconcile(first: ExtractedReceipt, second: ExtractedReceipt) -> ExtractedReceipt:
    """The stronger read wins. Where both reads agree on the figures, the document says what both
    say, so the "hard to read" hedge is dropped and a mismatch that survives is real."""
    if not _same_figures(first, second):
        return second
    keep = [f for f in second.low_confidence_fields if f not in _CONFIRMED_BY_AGREEMENT]
    return second.model_copy(update={"low_confidence_fields": keep})


class ReceiptExtractor:
    def __init__(
        self,
        llm: LLMClient,
        *,
        cache: ExtractionCache | None = None,
        escalate: bool = False,  # ADR-018: unsure critical fields alone do not justify a re-read
        second_opinion: bool = True,  # ADR-031: re-read what looks suspicious, nothing else
        thinking: ThinkingMode = "off",
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        self._llm = llm
        self._cache = cache
        self._escalate = escalate
        self._second_opinion = second_opinion
        self._thinking: ThinkingMode = thinking
        self._prompt_version = prompt_version

    async def extract(
        self, document: PreparedDocument, *, may_reread: Callable[[], bool] | None = None
    ) -> Extraction:
        """``may_reread`` is asked just before a second read is made; a ``False`` keeps the first.

        It lets the caller cap how many documents of one upload may be re-read, because the
        stronger model costs about twenty times more and a hostile upload can make every
        receipt look suspicious.
        """
        key = self._key(document, PRIMARY_ROUTE)
        if self._cache is not None and (hit := await self._cache.get(key)) is not None:
            return Extraction(receipt=hit, cached=True, escalated=False)

        first = await self._call(PRIMARY_ROUTE, document)
        calls = [first]
        receipt = to_domain(first.parsed)
        escalated = False
        wanted = (self._escalate and needs_escalation(receipt)) or (
            self._second_opinion and looks_suspicious(receipt)
        )
        if wanted and (may_reread is None or may_reread()):
            second = await self._call(RETRY_ROUTE, document)
            calls.append(second)
            receipt, escalated = reconcile(receipt, to_domain(second.parsed)), True

        if self._cache is not None:
            await self._cache.set(key, receipt)
        return Extraction(receipt=receipt, cached=False, escalated=escalated, calls=tuple(calls))

    async def _call(self, route: str, document: PreparedDocument) -> LLMResult[WireReceipt]:
        return await self._llm.parse(
            route,
            system=system_prompt(self._prompt_version),
            content=[*document.blocks, {"type": "text", "text": USER_INSTRUCTION}],
            output_model=WireReceipt,
            thinking=self._thinking,
        )

    def _key(self, document: PreparedDocument, route: str) -> str:
        resolved = self._llm.resolve(route)
        return cache_key(document.sha256, self._prompt_version, resolved.model.key, resolved.effort)
