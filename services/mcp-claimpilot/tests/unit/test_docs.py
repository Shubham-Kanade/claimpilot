"""The README and the Dockerfile say what the code does: names, variables, ports, commands."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from claimpilot_mcp.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
SETTING_NAMES = {name.upper() for name in Settings.model_fields}


def table_after(heading: str) -> list[list[str]]:
    """The body rows of the first markdown table after a heading (cells, trimmed)."""
    rows: list[str] = []
    for line in README.split(heading, 1)[1].splitlines():
        if line.startswith("|"):
            rows.append(line)
        elif rows:
            break  # the table ended
    return [[cell.strip() for cell in row.strip("|").split("|")] for row in rows[2:]]


def backticked(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def test_the_claude_desktop_snippet_is_valid_json_that_launches_the_installed_script():
    block = re.search(r"```json\n(.*?)\n```", README, re.S)
    assert block is not None
    server = json.loads(block.group(1))["mcpServers"]["claimpilot"]
    script = next(iter(PYPROJECT["project"]["scripts"]))
    assert server["command"] == "uv"
    assert server["args"][0] == "run"
    assert server["args"][-1] == script == "claimpilot-mcp"
    assert set(server["env"]) <= SETTING_NAMES


def test_the_claude_code_one_liner_uses_the_same_script_and_real_variables():
    line = next(
        row for row in README.splitlines() if row.startswith("claude mcp add claimpilot -e")
    )
    assert line.endswith("claimpilot-mcp")
    assert set(re.findall(r"-e (\w+)=", line)) <= SETTING_NAMES


def test_every_variable_in_the_configuration_table_is_a_setting_and_every_setting_is_listed():
    documented = {name for row in table_after("### Configuration") for name in backticked(row[0])}
    assert documented == SETTING_NAMES


def test_the_defaults_in_the_configuration_table_are_the_real_defaults():
    settings = Settings()
    rows = {name: row[1] for row in table_after("### Configuration") for name in backticked(row[0])}
    assert rows["CLAIMPILOT_API_URL"] == f"`{settings.claimpilot_api_url}`"
    assert rows["CLAIMPILOT_PERSONA"] == f"`{settings.claimpilot_persona}`"
    assert rows["MCP_PORT"] == f"`{settings.mcp_host}` / `{settings.mcp_port}`"
    assert rows["CLAIMPILOT_MAX_FILES"] == (
        f"`{settings.claimpilot_max_files}` / `{settings.claimpilot_max_file_mb}`"
    )
    assert rows["CLAIMPILOT_TIMEOUT_S"] == (
        f"`{settings.claimpilot_timeout_s:g}` / `{settings.claimpilot_upload_timeout_s:g}`"
    )


def test_the_readme_says_the_limits_come_from_the_api_and_the_settings_are_the_fallback():
    row = next(r for r in table_after("### Configuration") if "CLAIMPILOT_MAX_FILES" in r[0])
    assert "GET /v1/meta" in row[2]
    assert "max_batch_files" in row[2] and "max_upload_mb" in row[2]
    assert "fallback" in row[2]


async def test_the_tool_table_lists_exactly_the_tools_the_server_has(server: MCPServer):
    documented = {backticked(row[0])[0] for row in table_after("## Tools")}
    async with Client(server) as mcp:
        assert documented == {tool.name for tool in (await mcp.list_tools()).tools}


async def test_the_documented_arguments_are_the_real_ones(server: MCPServer):
    async with Client(server) as mcp:
        tools = {t.name: t for t in (await mcp.list_tools()).tools}
    for row in table_after("## Tools"):
        name = backticked(row[0])[0]
        shown = {arg.split("=")[0].rstrip("?").rstrip("[]") for arg in backticked(row[1])}
        assert shown == set(tools[name].input_schema.get("properties", {})), name


async def test_the_documented_prompt_exists(server: MCPServer):
    async with Client(server) as mcp:
        names = [p.name for p in (await mcp.list_prompts()).prompts]
    for name in names:
        assert f"`{name}`" in README


def test_the_dockerfile_matches_the_service():
    default_port = Settings().mcp_port
    assert f"EXPOSE {default_port}" in DOCKERFILE
    assert f"MCP_PORT={default_port}" in DOCKERFILE
    assert f"127.0.0.1:{default_port}/healthz" in DOCKERFILE
    assert "MCP_HOST=0.0.0.0" in DOCKERFILE
    assert 'CMD ["python", "-m", "claimpilot_mcp", "--transport", "http"]' in DOCKERFILE
    variables = set(re.findall(r"^\s+([A-Z][A-Z_]+)=", DOCKERFILE, re.M))
    assert {"CLAIMPILOT_API_URL", "MCP_HOST", "MCP_PORT"} <= variables & SETTING_NAMES
    assert "uv sync --locked" in DOCKERFILE


@pytest.mark.parametrize("doc", [README, DOCKERFILE])
def test_the_documents_stay_plain_ascii_so_every_terminal_shows_them(doc: str):
    assert doc.isascii()
