"""MCP layer: typed clients for the mocked enterprise systems and the bridge to Claude tool use.

* ``get_finance_client(settings)`` / ``get_corp_client(settings)``: the typed ``FinanceClient`` and
  ``CorpClient`` over streamable HTTP. Every failure is an ``McpError`` subclass.
* ``McpDirectory`` / ``McpCalendar`` / ``McpFinance`` (``get_mcp_ports(settings)``): the pipeline's
  ports (``claimpilot.ports``) implemented on those clients.
* ``McpBridge`` / ``tools_for_claude``: MCP tools as strict Claude tool definitions, and execution
  of the model's ``tool_use`` blocks. ``EMPLOYEE_AGENT_TOOLS`` is what the chat agent may expose.
* ``FakeMcpClient``: in-memory ``McpToolClient`` for tests.
"""

from claimpilot.mcp.adapters import (
    McpCalendar,
    McpDirectory,
    McpFinance,
    McpPorts,
    get_mcp_ports,
)
from claimpilot.mcp.bridge import (
    EMPLOYEE_AGENT_TOOLS,
    McpBridge,
    ToolUseLike,
    claude_tool,
    tools_for_claude,
)
from claimpilot.mcp.client import (
    FakeMcpClient,
    McpError,
    McpProtocolError,
    McpToolClient,
    McpToolError,
    McpToolSpec,
    McpUnavailableError,
    StreamableHttpClient,
    TypedClient,
)
from claimpilot.mcp.corp import (
    POLICY_URI,
    CalendarEvent,
    CorpClient,
    EmployeeNotFoundError,
    EmployeeProfile,
    get_corp_client,
)
from claimpilot.mcp.finance import (
    Decision,
    FinanceClaim,
    FinanceClaimSummary,
    FinanceClient,
    FinanceStatus,
    StatusEvent,
    SubmissionReceipt,
    get_finance_client,
)

__all__ = [
    "EMPLOYEE_AGENT_TOOLS",
    "POLICY_URI",
    "CalendarEvent",
    "CorpClient",
    "Decision",
    "EmployeeNotFoundError",
    "EmployeeProfile",
    "FakeMcpClient",
    "FinanceClaim",
    "FinanceClaimSummary",
    "FinanceClient",
    "FinanceStatus",
    "McpBridge",
    "McpCalendar",
    "McpDirectory",
    "McpError",
    "McpFinance",
    "McpPorts",
    "McpProtocolError",
    "McpToolClient",
    "McpToolError",
    "McpToolSpec",
    "McpUnavailableError",
    "StatusEvent",
    "StreamableHttpClient",
    "SubmissionReceipt",
    "ToolUseLike",
    "TypedClient",
    "claude_tool",
    "get_corp_client",
    "get_finance_client",
    "get_mcp_ports",
    "tools_for_claude",
]
