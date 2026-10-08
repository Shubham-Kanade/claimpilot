"""The expense policy document, served verbatim.

The canonical file is ``services/api/config/policy.yaml``. The Docker image copies it to
``seed/policy.yaml`` at build time; in a monorepo checkout the provider falls back to it directly.
"""

from __future__ import annotations

from pathlib import Path

MAX_POLICY_BYTES = 1_000_000


class PolicyUnavailableError(Exception):
    """The policy file is missing or unreadable; the message is safe to show a model."""


class PolicyProvider:
    """Reads the first existing candidate file on every call, so edits show up without a restart."""

    def __init__(self, *candidates: Path) -> None:
        self._candidates = candidates

    def locate(self) -> Path | None:
        return next((path for path in self._candidates if path.is_file()), None)

    def available(self) -> bool:
        return self.locate() is not None

    def text(self) -> str:
        path = self.locate()
        if path is None:
            raise PolicyUnavailableError("policy not available: no policy file on this server")
        try:
            if path.stat().st_size > MAX_POLICY_BYTES:
                raise PolicyUnavailableError("policy not available: the policy file is too large")
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PolicyUnavailableError(
                "policy not available: the policy file is unreadable"
            ) from exc
