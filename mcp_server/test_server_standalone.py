"""
Standalone smoke test for the MCP server -- exercises every tool through the
REAL MCP client/server stdio protocol (not by importing functions directly).

This is a development/verification script, not part of the shipped project
behavior. Run it after `python mcp_server/init_data.py` to confirm the
server works end-to-end before wiring up the Claude agent on top of it.

Usage (from the pricing-agent/ project root):
    python mcp_server/test_server_standalone.py
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_SCRIPT = Path(__file__).parent / "server.py"


def show(label: str, result) -> None:
    print(f"\n--- {label} ---")
    for block in result.content:
        if hasattr(block, "text"):
            print(block.text)
        else:
            print(block)


async def main() -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("Discovered tools:", [t.name for t in tools.tools])

            r = await session.call_tool("list_open_tickets", {})
            show("list_open_tickets", r)

            r = await session.call_tool("get_ticket", {"ticket_id": "TICKET-101"})
            show("get_ticket(TICKET-101)", r)

            r = await session.call_tool(
                "query_current_config", {"country": "DE", "category_code": "STANDARD"}
            )
            show("query_current_config(DE, STANDARD)", r)

            r = await session.call_tool(
                "propose_config_change",
                {"country": "DE", "category_code": "STANDARD", "field": "base_fee_pct", "new_value": 2.90},
            )
            show("propose_config_change -> 2.90%", r)

            # Simulate the human approving the gate, then commit.
            r = await session.call_tool(
                "commit_config_change",
                {
                    "country": "DE",
                    "category_code": "STANDARD",
                    "field": "base_fee_pct",
                    "new_value": 2.90,
                    "ticket_id": "TICKET-101",
                    "confirm": True,
                },
            )
            show("commit_config_change (confirm=True)", r)

            r = await session.call_tool(
                "query_current_config", {"country": "DE", "category_code": "STANDARD"}
            )
            show("query_current_config AFTER commit", r)

            r = await session.call_tool(
                "generate_test_cases", {"country": "DE", "category_code": "STANDARD"}
            )
            show("generate_test_cases", r)

            r = await session.call_tool(
                "update_docs",
                {
                    "ticket_id": "TICKET-101",
                    "summary": "Base fee for DE/STANDARD updated from 2.50% to 2.90% per PRD. "
                               "Verified with 3 functional test cases.",
                },
            )
            show("update_docs", r)

            r = await session.call_tool(
                "post_ticket_comment",
                {
                    "ticket_id": "TICKET-101",
                    "comment": "Config committed (see config_repo git log) and test cases passed. "
                               "Docs updated. Ready for review.",
                },
            )
            show("post_ticket_comment", r)

            r = await session.call_tool("get_ticket", {"ticket_id": "TICKET-101"})
            show("get_ticket AFTER comment", r)

            # Negative test: commit without confirm should be refused.
            r = await session.call_tool(
                "commit_config_change",
                {
                    "country": "US",
                    "category_code": "STANDARD",
                    "field": "base_fee_pct",
                    "new_value": 9.99,
                    "ticket_id": "TICKET-999",
                    "confirm": False,
                },
            )
            show("commit_config_change (confirm=False, should be refused)", r)

    print("\nAll tool calls completed.")


if __name__ == "__main__":
    asyncio.run(main())
