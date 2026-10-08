"""Record/replay backend: ``LLM_MODE=replay`` costs $0; ``LLM_MODE=live LLM_RECORD=1`` records.

Recordings are keyed by the sha256 of the canonical request JSON (``LLMRequest.request_hash``),
so any change to the prompt, schema, model or params is a miss rather than a stale answer. The
request body holds no secrets (the API key lives in the SDK client), and only the completion
plus a little routing metadata is written to disk. Recording is "once": a hit is replayed even
when recording, so re-running a recorded suite spends nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from claimpilot.llm.base import BaseLLM, Ledger
from claimpilot.llm.errors import ReplayMissError
from claimpilot.llm.params import LLMRequest
from claimpilot.llm.registry import ModelRegistry
from claimpilot.llm.types import Completion

logger = logging.getLogger(__name__)


class RecordReplayLLM(BaseLLM):
    def __init__(
        self,
        registry: ModelRegistry,
        replay_dir: Path,
        *,
        inner: BaseLLM | None = None,
        ledger: Ledger | None = None,
    ) -> None:
        # Replay-only, or recording around a live backend (misses go live and get recorded).
        super().__init__(registry, mode="replay" if inner is None else inner.mode, ledger=ledger)
        self.replay_dir = replay_dir
        self._inner = inner

    async def complete(self, request: LLMRequest) -> Completion:
        path = self.replay_dir / f"{request.request_hash}.json"
        recording = await asyncio.to_thread(_load, path)
        if recording is not None:
            try:
                completion = Completion.model_validate(recording["completion"])
            except (KeyError, TypeError, ValidationError) as exc:
                raise ReplayMissError(f"corrupt recording {path}: re-record it ({exc})") from exc
            return completion.model_copy(update={"replayed": True})
        if self._inner is None:
            raise ReplayMissError(_miss_message(request, path))
        completion = await self._inner.complete(request)
        await _save_quietly(path, _recording(request, completion))  # never lose a paid result
        return completion

    async def count(self, request: LLMRequest) -> int:
        body = json.dumps(request.count_tokens_body(), sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        path = self.replay_dir / f"count-{digest}.json"
        recording = await asyncio.to_thread(_load, path)
        if recording is not None:
            return int(recording["input_tokens"])
        if self._inner is None:
            raise ReplayMissError(_miss_message(request, path))
        tokens = await self._inner.count(request)
        await _save_quietly(path, {"route": request.route, "input_tokens": tokens})
        return tokens


def _recording(request: LLMRequest, completion: Completion) -> dict[str, Any]:
    return {
        "request_hash": request.request_hash,
        "route": request.route,
        "model_key": request.model_key,
        "model_id": request.body["model"],
        "output_model": request.output_model.__name__ if request.output_model else None,
        "completion": completion.model_dump(mode="json", exclude={"replayed"}),
    }


def _miss_message(request: LLMRequest, path: Path) -> str:
    return (
        f"no recording for route '{request.route}' (model {request.body['model']}) at {path}. "
        "The request changed or was never recorded. Record it with "
        "LLM_MODE=live LLM_RECORD=1 (costs money), or use LLM_MODE=fake."
    )


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReplayMissError(f"unreadable recording {path}: re-record it ({exc})") from exc


def _save(path: Path, data: dict[str, Any]) -> None:
    """Atomic write: a crash mid-write never leaves a truncated recording behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


async def _save_quietly(path: Path, data: dict[str, Any]) -> None:
    try:
        await asyncio.to_thread(_save, path, data)
    except OSError as exc:  # the call already happened (and is in the ledger): just warn
        logger.warning("could not write recording %s: %s", path, exc)
