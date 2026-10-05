# Pricing Change Request Agent

A small, hands-on agentic AI project: a Python MCP (Model Context Protocol)
server with 8 tools, and a Claude-based agent that uses those tools to take a
pricing-change ticket from analysis to a committed, tested and documented
change. Any step that changes state goes through a human approval gate.

It models a common enterprise workflow (ticket -> config change -> validation
-> audit trail) using simple, self-contained stand-ins, so it runs on a laptop
with no external systems.

## How it maps to a typical enterprise setup

| Typical enterprise system                 | This project                                          |
|-------------------------------------------|-------------------------------------------------------|
| Ticketing system (e.g. Jira)              | `mcp_server/data/tickets.json`                        |
| Pricing configuration system              | `mcp_server/data/config_repo/` (git-backed JSON)      |
| Wiki / documentation (e.g. Confluence)    | `mcp_server/data/pricing_docs.md`                     |
| Pricing calculation API                   | `generate_test_cases` tool (simple fee formula)       |
| The agent that orchestrates the workflow  | `agent/pricing_agent.py`                              |
| Maker/checker human approval              | Typed "yes" at the terminal before commit and comment |

The design principle: the MCP server exposes tools and knows nothing about
approvals. The agent decides which tools to call and in what order. The
approval gates are enforced in the orchestration code, not left to the model.

## Architecture

```
agent/pricing_agent.py  --(spawns as subprocess, talks MCP over stdio)-->  mcp_server/server.py
        |                                                                          |
        | Anthropic API (tool-use loop)                                            | reads/writes
        v                                                                          v
   Claude model                                                    mcp_server/data/  (gitignored, runtime state)
```

- **`mcp_server/server.py`** -- a `FastMCP` server exposing 8 tools:
  `get_ticket`, `list_open_tickets`, `query_current_config`,
  `propose_config_change` (read-only diff), `commit_config_change` (writes and
  git-commits, requires `confirm=True`), `generate_test_cases`, `update_docs`,
  `post_ticket_comment`.
- **`mcp_server/seed/`** -- git-tracked starter data (one sample ticket, one
  sample pricing config, an empty docs page).
- **`mcp_server/data/`** -- gitignored *runtime* copy of the seed data, plus
  its own small standalone git repo at `data/config_repo/` that the agent
  commits pricing changes into. It is kept separate from this project's own
  git history on purpose (see the comment in `init_data.py`).
- **`agent/pricing_agent.py`** -- connects to the MCP server as a real client,
  converts its tool schemas to Anthropic's tool-use format, and runs the
  standard agent loop (Claude responds -> may ask for tools -> the agent runs
  them -> results go back -> repeat). `commit_config_change` and
  `post_ticket_comment` always pause for a typed "yes" at the terminal,
  regardless of what the model passes as arguments.

## The three phases

1. **Analysis** -- read the ticket, query the current config, and show a
   before/after diff. Nothing is written.
2. **Configuration** -- commit the change to the config repo, only after a
   human approves it.
3. **Validation** -- generate test cases against the new config, update the
   docs, and post a summary comment on the ticket (also after approval).

## Setup

**Windows (PowerShell):**

```powershell
cd pricing-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# edit .env and put your Anthropic API key in it

python mcp_server\init_data.py    # seeds mcp_server\data\ from mcp_server\seed\
```

If `.venv\Scripts\Activate.ps1` fails with a "running scripts is disabled on
this system" error, PowerShell's execution policy is blocking it. Run this
once in the same window, then retry the activate command:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This only relaxes the policy for the current window.

**Windows (Command Prompt):**

```bat
cd pricing-agent
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt

copy .env.example .env
python mcp_server\init_data.py
```

**Mac / Linux:**

```bash
cd pricing-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
python mcp_server/init_data.py
```

Paths below use forward slashes. Python accepts them on Windows too.

## Try the MCP server on its own first (recommended)

Before running the agent, check the tool layer by itself. This does not need
an API key:

```bash
python mcp_server/test_server_standalone.py
```

It talks to the server through the real MCP client/server stdio protocol. You
should see all 8 tools discovered and each one exercised, including a
committed git change inside `mcp_server/data/config_repo/` and a rejected
`commit_config_change` call when `confirm=False`.

To look around by hand, go to `mcp_server/data/config_repo` and run `git log`
to see the commit the test made.

Reset to a clean slate any time with:

```bash
python mcp_server/init_data.py --reset
```

## Run the agent

```bash
python agent/pricing_agent.py
```

This works `TICKET-101` (raise DE/STANDARD's base fee to 2.90%) through all
three phases. It stops and asks for your approval before the commit and
before the ticket comment.

You can also give it a different instruction:

```bash
python agent/pricing_agent.py "Look at TICKET-101 and just tell me what it's asking for, don't change anything yet."
```

## A note on reliability

`commit_config_change` does file I/O and spawns `git` as a subprocess. Running
that blocking work directly on the server's event loop can stall the stdio
transport that carries the response back to the client. The tool is therefore
async and offloads the work to a worker thread with `asyncio.to_thread`.

The broader lesson: an agent is a distributed system with an LLM in the
middle. The usual reliability concerns (blocking calls, timeouts, retries,
tests) still apply.

## Ideas for extending it

- Add a second seed ticket with a *structural* change (for example a remap or
  a split) and see how the agent's analysis-phase questions change.
- Add a `list_config_history` tool that calls `git log` in `config_repo/` as
  an audit-trail feature.
- Replace the terminal approval with a simple approval queue, closer to how
  an asynchronous approval step works in practice.

## Notes

- `.gitignore` excludes `mcp_server/data/` (runtime state) and `.env` (your
  API key).
- Built with Claude as a development tool.
