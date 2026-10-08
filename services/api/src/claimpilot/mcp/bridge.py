"""Bridge between MCP servers and Claude tool use (what the chat agent uses).

* ``tools_for_claude(client)``: a server's tools as Claude tool definitions.
* ``McpBridge``: the same for several servers, plus ``call(tool_use)`` which runs a ``tool_use``
  block the model produced against the server that owns the tool and returns the ``tool_result``
  block to send back.

Tool definitions are strict where the schema allows it: ``strict: true`` with a closed
(``additionalProperties: false``) input schema, so the model's arguments always validate. Keywords
the strict subset does not support (``minLength``, ``minimum``, defaults...) are folded into the
description by ``anthropic.transform_schema``; a schema that cannot be made strict at all is sent
as a normal, root-closed tool instead.

Only tools in ``allow`` are offered *and* executable: a model that asks for anything else (an
approver action it was never shown, say) gets an error result, never a call. The bridge returns
errors as results so the model can react; it does not raise ``McpError``.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Collection, Mapping
from typing import Any, Protocol

import structlog
from anthropic import transform_schema

from claimpilot.mcp.client import (
    McpError,
    McpToolClient,
    McpToolError,
    McpToolSpec,
    McpUnavailableError,
)

log = structlog.get_logger(__name__)

TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")

# What the employee-facing chat agent may use. start_review, decide_claim and list_claims are
# approver actions, and list_employees would hand the whole directory to the model.
EMPLOYEE_AGENT_TOOLS: frozenset[str] = frozenset(
    {"submit_claim", "get_claim_status", "get_employee", "search_calendar", "get_policy"}
)

_SCHEMA_VALUED = ("items", "additionalProperties", "not", "contains", "if", "then", "else")
_SCHEMA_MAPS = ("properties", "$defs", "definitions", "patternProperties")
_SCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")


class ToolUseLike(Protocol):
    """A ``tool_use`` content block (the Anthropic SDK's ``ToolUseBlock`` fits)."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def input(self) -> object: ...


def _strip_titles(node: Any) -> Any:
    """Drop the ``title`` annotations pydantic adds ('Claim Id'): noise that costs tokens.

    Only schema positions are walked, so a *property* called ``title`` and values inside
    ``enum`` / ``const`` / ``default`` are left alone.
    """
    if not isinstance(node, dict):
        return node
    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" and isinstance(value, str):
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            cleaned[key] = {name: _strip_titles(sub) for name, sub in value.items()}
        elif key in _SCHEMA_VALUED:
            cleaned[key] = _strip_titles(value)
        elif key in _SCHEMA_LISTS and isinstance(value, list):
            cleaned[key] = [_strip_titles(sub) for sub in value]
        else:
            cleaned[key] = value
    return cleaned


def claude_tool(spec: McpToolSpec, *, strict: bool = True) -> dict[str, Any]:
    """One MCP tool as a Claude tool definition."""
    if not TOOL_NAME.fullmatch(spec.name):
        raise ValueError(f"MCP tool name {spec.name!r} is not a valid Claude tool name")
    schema = _strip_titles(spec.input_schema)
    tool: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description or spec.title or spec.name,
    }
    if strict:
        try:
            tool["input_schema"] = transform_schema(schema)
        except (ValueError, AssertionError):
            pass  # not representable in the strict subset: fall back to a closed plain tool
        else:
            tool["strict"] = True
            return tool
    closed = dict(schema)
    if closed.get("type") == "object":
        closed["additionalProperties"] = False
    tool["input_schema"] = closed
    return tool


async def tools_for_claude(
    client: McpToolClient, *, allow: Collection[str] | None = None, strict: bool = True
) -> list[dict[str, Any]]:
    """The client's tools (optionally only those named in ``allow``) as Claude tool definitions."""
    return [
        claude_tool(spec, strict=strict)
        for spec in await client.list_tools()
        if allow is None or spec.name in allow
    ]


def _tool_result(tool_use_id: str, content: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def _render(output: Mapping[str, Any]) -> str:
    """Tool output as text for the model: a bare string stays a string, the rest is compact JSON."""
    if len(output) == 1:
        ((key, value),) = output.items()
        if key in ("result", "text"):
            return value if isinstance(value, str) else _dumps(value)
    return _dumps(output)


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _read(tool_use: Mapping[str, Any] | ToolUseLike) -> tuple[str, str, object]:
    if isinstance(tool_use, Mapping):
        return str(tool_use["id"]), str(tool_use["name"]), tool_use.get("input")
    return tool_use.id, tool_use.name, tool_use.input


class McpBridge:
    """Claude tool definitions for several MCP servers, and execution of the model's tool calls."""

    def __init__(
        self,
        servers: Mapping[str, McpToolClient],
        *,
        allow: Collection[str] | None = None,
        strict: bool = True,
    ) -> None:
        self._servers = dict(servers)
        self._allow = None if allow is None else frozenset(allow)
        self._strict = strict
        self._tools: list[dict[str, Any]] | None = None
        self._routes: dict[str, str] = {}

    async def _discover(self) -> None:
        if self._tools is not None:
            return
        routes: dict[str, str] = {}
        tools: list[dict[str, Any]] = []
        for server, client in self._servers.items():
            for spec in await client.list_tools():
                if self._allow is not None and spec.name not in self._allow:
                    continue
                if spec.name in routes:
                    other = routes[spec.name]
                    raise ValueError(
                        f"tool {spec.name!r} is offered by both {other!r} and {server!r}"
                    )
                routes[spec.name] = server
                tools.append(claude_tool(spec, strict=self._strict))
        self._routes, self._tools = routes, tools

    async def tools(self) -> list[dict[str, Any]]:
        """Tool definitions for every allowed tool, in server order (stable between calls).

        Discovered once and cached; the result is a copy, so callers may add ``cache_control``.
        """
        await self._discover()
        return copy.deepcopy(self._tools or [])

    async def call(self, tool_use: Mapping[str, Any] | ToolUseLike) -> dict[str, Any]:
        """Run one ``tool_use`` block and return the ``tool_result`` block for the next turn."""
        tool_use_id, name, arguments = _read(tool_use)
        try:
            await self._discover()
        except McpError as exc:
            return _tool_result(
                tool_use_id, f"Tools are unavailable right now: {exc}", is_error=True
            )
        server = self._routes.get(name)
        if server is None:
            return _tool_result(tool_use_id, f"Unknown tool: {name}", is_error=True)
        if not isinstance(arguments, Mapping):
            return _tool_result(tool_use_id, "Tool input must be a JSON object", is_error=True)
        try:
            output = await self._servers[server].call_tool(name, dict(arguments))
        except McpToolError as exc:
            log.info("mcp.tool_rejected", server=server, tool=name)
            return _tool_result(tool_use_id, str(exc), is_error=True)
        except McpUnavailableError:
            return _tool_result(
                tool_use_id,
                f"The {server} system is unavailable right now. Tell the user it is temporarily "
                "down and to try again later.",
                is_error=True,
            )
        except McpError:
            return _tool_result(
                tool_use_id,
                f"The {server} system returned an unexpected response. Do not retry this call.",
                is_error=True,
            )
        log.info("mcp.tool_call", server=server, tool=name)
        return _tool_result(tool_use_id, _render(output))
