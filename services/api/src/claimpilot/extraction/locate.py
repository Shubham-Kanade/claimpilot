"""Where on the page each key value was read (click-to-verify).

A second, cheap vision call (Claude Haiku 5.5) points at the printed value of each field the
extraction found, so a person can check the extraction against the original. The boxes are
approximate: the model estimates fractions of the page, and an upright rectangle around a tilted
photo is a little loose, so the interface calls them "about here". Failing to locate never fails a
document (no boxes, no highlight), and the reply is parsed as numbers only, so whatever text the
receipt carries has no way to travel through here.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files

from pydantic import BaseModel, ConfigDict, Field

from claimpilot.domain import ExtractedReceipt
from claimpilot.domain.claims import Box
from claimpilot.extraction.preprocess import PreparedDocument
from claimpilot.llm.client import LLMClient
from claimpilot.llm.errors import LLMError

logger = logging.getLogger(__name__)

ROUTE = "locate"
PROMPT_VERSION = "locate_v1"
USER_INSTRUCTION = "Locate each of these values on the page:"

_BOX = "page,x,y,w,h as fractions of the page, or an empty string"


class LocatedFields(BaseModel):
    """Flat and all-required, like the extraction schema (structured-output limits, ADR-017)."""

    model_config = ConfigDict(extra="forbid")

    merchant_name: str = Field(description=f"Where the seller's name is printed: {_BOX}")
    date: str = Field(description=f"Where the bill date is printed: {_BOX}")
    total: str = Field(description=f"Where the grand total amount is printed: {_BOX}")
    subtotal: str = Field(description=f"Where the subtotal amount is printed: {_BOX}")
    merchant_gstin: str = Field(description=f"Where the seller's GSTIN is printed: {_BOX}")
    invoice_number: str = Field(
        description=f"Where the bill, invoice or PNR number is printed: {_BOX}"
    )


FIELDS = tuple(LocatedFields.model_fields)


@dataclass(frozen=True, slots=True)
class Located:
    boxes: dict[str, Box] = field(default_factory=dict)
    cost_usd: float = 0.0


@cache
def system_prompt(version: str = PROMPT_VERSION) -> str:
    return (files("claimpilot.extraction") / "prompts" / f"{version}.md").read_text("utf-8")


def parse_box(text: str, pages: int) -> Box | None:
    """``"1,0.42,0.10,0.40,0.04"`` (or without the page) to a Box; ``None`` when it is not one.

    Strict on purpose: four or five numbers, a page that exists, a region that has an area and lies
    on the page. A box that runs slightly over an edge is clipped to it.
    """
    parts = [p.strip() for p in text.split(",")]
    if len(parts) == 4:
        parts = ["1", *parts]
    if len(parts) != 5:
        return None
    try:
        page = int(float(parts[0]))
        x, y, w, h = (float(p) for p in parts[1:])
    except ValueError:
        return None
    if not all(math.isfinite(n) for n in (x, y, w, h)) or not 1 <= page <= max(pages, 1):
        return None
    if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0):
        return None
    return Box(page=page - 1, x=x, y=y, w=min(w, 1 - x), h=min(h, 1 - y))


def _values(receipt: ExtractedReceipt) -> dict[str, object]:
    values = {name: getattr(receipt, name) for name in FIELDS}
    return {name: value for name, value in values.items() if value not in (None, "", [])}


class FieldLocator:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def locate(self, document: PreparedDocument, receipt: ExtractedReceipt) -> Located:
        """Boxes for the values the extraction found; empty when the call fails."""
        values = _values(receipt)
        if not values:
            return Located()
        text = f"{USER_INSTRUCTION}\n{json.dumps(values, ensure_ascii=False)}"
        try:
            result = await self._llm.parse(
                ROUTE,
                system=system_prompt(),
                content=[*document.blocks, {"type": "text", "text": text}],
                output_model=LocatedFields,
                thinking="off",
            )
        except LLMError as exc:  # unavailable, refused, a recording that is missing ...
            logger.warning("could not locate fields: %s", type(exc).__name__)
            return Located()
        boxes: dict[str, Box] = {}
        for name in values:  # only fields that were asked about
            box = parse_box(getattr(result.parsed, name), document.pages)
            if box is not None:
                boxes[name] = box
        return Located(boxes=boxes, cost_usd=result.cost_usd)
