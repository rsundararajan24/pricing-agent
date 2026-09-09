"""
Pricing Change Request Agent
=============================

This is the piece that turns the MCP server's tools into an actual AGENT
(in the "Building Effective Agents" sense: the LLM dynamically decides
which tools to call and in what order, rather than us hard-coding the
sequence) -- with human-in-the-loop approval gates enforced HERE, in code,
not left to the model's discretion.

It connects to mcp_server/server.py as a real MCP client (spawns it as a
subprocess over stdio, using the same protocol any MCP host -- Claude
Desktop, Claude Code, etc. -- would use), converts the server's tool
definitions into Anthropic's tool-use format, and runs a standard
agent loop: send messages -> Claude responds, possibly with tool_use
blocks -> we execute the requested tools -> feed results back -> repeat
until Claude stops asking for tools.

Two tools are treated specially and ALWAYS pause for a real typed human
confirmation at the terminal, regardless of what Claude passes as
arguments:
    - commit_config_change   (writes + git-commits a pricing change)
    - post_ticket_comment    (writes an audit trail comment)
This mirrors the real system's Maker/Checker gates: the agent can reason
about and PROPOSE these actions freely, but a human must approve the
literal content before it executes.

IMPORTANT -- sandbox note:
This script depends on the `anthropic` PyPI package. In the cloud sandbox
that built this project, installing `anthropic` was blocked by network
policy (pypi.org is not in that sandbox's egress allowlist), so this file
was written carefully but has NOT been executed there. The MCP server
itself (server.py) WAS fully tested in-sandbox via test_server_standalone.py
using the real MCP client/server protocol, so the tool layer underneath
this agent is verified. Run this file on your own machine (where pip
installs from PyPI normally) to do the first live end-to-end run:

    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...    (or put it in a .env file, see .env.example)
    python agent/pricing_agent.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

try:
    from dotenv import load_dotenv
    load_dotenv()  # picks up ANTHROPIC_API_KEY from a local .env file, if present
except ImportError:
    pass  # python-dotenv is optional -- exporting the env var directly works fine too

SERVER_SCRIPT = Path(__file__).parent.parent / "mcp_server" / "server.py"
MODEL = "claude-sonnet-4-5"  # change freely; any current Claude model with tool use works

GATED_TOOLS = {"commit_config_change", "post_ticket_comment"}

SYSTEM_PROMPT = """\
You are a Pricing Change Request agent, modeled on PayPal's internal
"/pricing-analyst" Claude skill. Your job is to take a pricing-change
ticket through three phases, using the tools available to you:

PHASE 1 -- Analysis
  - Read the ticket (get_ticket) to understand what's being asked.
  - Query the current config (query_current_config) for the affected
    country/category.
  - Call propose_config_change to compute the before/after diff. This is
    read-only -- it does NOT write anything. Present the diff clearly and
    say you are waiting for approval before touching anything.

PHASE 2 -- Configuration
  - Only after the diff has been approved, call commit_config_change to
    write and git-commit the change. Always pass confirm=True only when
    you have clearly stated what you are about to commit and it has been
    approved in the conversation.

PHASE 3 -- Validation
  - Call generate_test_cases against the NEW config to produce a small
    functional test matrix.
  - Call update_docs to record what changed and why.
  - Call post_ticket_comment to post a summary back to the ticket.

Rules:
  - Never skip straight to commit_config_change without first showing a
    propose_config_change diff in this conversation.
  - Be explicit about which phase you are in and what you are waiting on.
  - If the ticket is ambiguous (e.g. no explicit target value), say so and
    ask a clarifying question instead of guessing.
"""


def mcp_tool_to_anthropic(tool) -> dict:
    """Convert an MCP tool definition to Anthropic's tool-use schema.

    Both are JSON-Schema-based, so this is close to a passthrough -- the
    field names just differ slightly (MCP: inputSchema, Anthropic: input_schema).
    """
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema,
    }


def confirm_gated_action(tool_name: str, tool_input: dict) -> bool:
    """Block and ask a real human at the terminal before a gated tool runs."""
    print(f"\n{'=' * 70}")
    print(f"APPROVAL NEEDED before calling: {tool_name}")
    print(json.dumps(tool_input, indent=2))
    print(f"{'=' * 70}")
    answer = input("Type 'yes' to approve, anything else to reject: ").strip().lower()
    return answer == "yes"


async def run_agent(user_task: str) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Export it or put it in a .env file (see .env.example).")
        sys.exit(1)

    client = Anthropic()  # picks up ANTHROPIC_API_KEY from the environment

    server_params = StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools = (await session.list_tools()).tools
            anthropic_tools = [mcp_tool_to_anthropic(t) for t in mcp_tools]
            print(f"Loaded {len(anthropic_tools)} tools from MCP server: {[t.name for t in mcp_tools]}")

            messages = [{"role": "user", "content": user_task}]

            while True:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    tools=anthropic_tools,
                    messages=messages,
                )

                # Print any text the model produced this turn.
                for block in response.content:
                    if block.type == "text":
                        print(f"\n[agent] {block.text}")

                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason != "tool_use":
                    break  # model is done -- no more tools requested

                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    if block.name in GATED_TOOLS:
                        approved = confirm_gated_action(block.name, block.input)
                        if not approved:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": "Human rejected this action. Do not retry it without changes; "
                                           "ask the human what they'd like to do instead.",
                                "is_error": True,
                            })
                            continue

                    result = await session.call_tool(block.name, block.input)
                    result_text = "\n".join(
                        c.text for c in result.content if hasattr(c, "text")
                    )
                    print(f"\n[tool:{block.name}] -> {result_text}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

                messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    default_task = (
        "Work TICKET-101 through all three phases of the pricing change process. "
        "Stop and show me the proposed diff before committing anything."
    )
    task = sys.argv[1] if len(sys.argv) > 1 else default_task
    asyncio.run(run_agent(task))
