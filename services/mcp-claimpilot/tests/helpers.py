"""Helpers shared by the tool tests: drive a tool through the SDK client, read what came back."""

from __future__ import annotations

from typing import Any

import httpx
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult

from tests import factories as f
from tests.fakeapi import FakeApi

# The first bytes real files start with (the API and this server sniff the content, not the name).
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"0" * 32


async def call(server: MCPServer, name: str, **arguments: Any) -> CallToolResult:
    """One tool call in its own in-process session (as the HTTP server does: no long sessions)."""
    async with Client(server) as mcp:
        return await mcp.call_tool(name, arguments)


def text_of(result: CallToolResult) -> str:
    return " ".join(block.text for block in result.content if block.type == "text")


def data(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, text_of(result)
    assert result.structured_content is not None
    return result.structured_content


def error_of(result: CallToolResult) -> str:
    assert result.is_error
    return text_of(result)


def script_claim(api: FakeApi, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Serve one claim and its documents; return the claim payload."""
    claim = payload or f.claim()
    api.json("GET", "/v1/claims/{claim_id}", claim)
    api.respond(
        "GET",
        "/v1/documents/{document_id}",
        lambda call: httpx.Response(200, json=f.document(call.args["document_id"])),
    )
    return claim


def script_persona(api: FakeApi, *, approver: bool) -> None:
    api.json("GET", "/v1/me", f.me(approver=approver))
