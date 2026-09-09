# Pricing Change Request Agent (personal mini-rebuild)

A small, hands-on rebuild of the shape of PayPal's "Pricing Business
Enablement Automation using Claude" system (the `/pricing-analyst` skill),
using generic, non-proprietary stand-ins instead of Jira/Trinity/Confluence/
Dobby MCP. Built to close the "I directed it, I didn't personally build it"
gap before interviews -- this version I wrote and ran myself.

## How this maps to the real system

| Real system (PayPal)                  | This project                                   |
|----------------------------------------|-------------------------------------------------|
| Jira ticket / PRD                      | `mcp_server/seed/tickets.json`                   |
| Dobby MCP + Trinity (MSM config)       | `mcp_server/data/config_repo/` (git-backed JSON) |
| Confluence docs                        | `mcp_server/data/pricing_docs.md`                |
| PPS Calculate Price / Exchange Currency| `generate_test_cases` tool (simple fee formula)  |
| The `/pricing-analyst` Claude skill    | `agent/pricing_agent.py`                         |
| Maker/Checker human approval gates     | Terminal `input()` confirmation before commit/comment |

The real system's own documentation (see
`Pricing_BE_Automation_Overview_Consolidated.docx`) confirms it already used
an MCP-server pattern -- Dobby MCP sat between the Claude skill and PayPal's
internal config systems. This rebuild mirrors that shape exactly: an
MCP server exposing tools, and a Claude-based agent that decides which
tools to call and in what order, with human approval gates enforced in the
orchestration layer (not left to the model).

## Architecture

```
agent/pricing_agent.py  --(spawns as subprocess, talks MCP over stdio)-->  mcp_server/server.py
        |                                                                          |
        | Anthropic API (tool use loop)                                           | reads/writes
        v                                                                          v
   Claude model                                                    mcp_server/data/  (gitignored, runtime state)
```

- **`mcp_server/server.py`** -- a `FastMCP` server exposing 8 tools:
  `get_ticket`, `list_open_tickets`, `query_current_config`,
  `propose_config_change` (read-only diff), `commit_config_change` (writes +
  git-commits, requires `confirm=True`), `generate_test_cases`, `update_docs`,
  `post_ticket_comment`.
- **`mcp_server/seed/`** -- git-tracked starter data (one sample ticket, one
  sample pricing config, an empty docs page).
- **`mcp_server/data/`** -- gitignored *runtime* copy of the seed data, plus
  its own tiny standalone git repo at `data/config_repo/` that the agent
  actually commits pricing changes into (kept separate from this project's
  own git history on purpose -- see the comment in `init_data.py`).
- **`agent/pricing_agent.py`** -- connects to the MCP server as a real
  client, converts its tool schemas to Anthropic's tool-use format, and runs
  the standard agent loop (Claude responds -> maybe asks for tools -> we run
  them -> feed results back -> repeat). `commit_config_change` and
  `post_ticket_comment` always pause for a real typed "yes" at the terminal,
  regardless of what the model passes as arguments.

## What was tested where, and why

This project was built in a cloud sandbox that could install the `mcp`
Python package but was **blocked from installing `anthropic` from PyPI**
(network egress policy denial on pypi.org -- a sandbox restriction, not
something wrong with the code). So:

- `mcp_server/server.py` was fully tested in-sandbox, end-to-end, through
  the **real** MCP client/server stdio protocol -- see
  `mcp_server/test_server_standalone.py`. Every tool (including the
  `confirm=False` rejection path) was exercised and verified working.
- `agent/pricing_agent.py` (the part that needs `anthropic`) was written
  carefully against the Anthropic SDK's documented tool-use API, but its
  first actual run will be on your machine, where `pip install` from PyPI
  works normally.

## Setup (run these yourself, on your own machine)

**Windows (PowerShell -- this is what opens by default from the Start menu, or from VS Code's integrated terminal):**

```powershell
cd pricing-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# edit .env in Notepad/VS Code and put your real Anthropic API key in it

python mcp_server\init_data.py    # seeds mcp_server\data\ from mcp_server\seed\
```

If `.venv\Scripts\Activate.ps1` refuses to run with a "running scripts is
disabled on this system" error, PowerShell's execution policy is blocking
it (a Windows security default, not a bug in this project). Run this once
in that same PowerShell window, then retry the activate command:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

That only relaxes the policy for the current window/session -- nothing
permanent, nothing outside this project.

**Windows (Command Prompt / `cmd.exe`, if that's what you're using instead):**

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

A note on paths in the rest of this README: everything below is written
with forward slashes (`mcp_server/server.py`) the way Mac/Linux and Python
itself write paths. Python accepts forward slashes fine on Windows too, so
you can type commands exactly as shown -- just remember Windows *file
Explorer* and `cmd`/PowerShell's own commands (like `copy`, `cd`, viewing
`.gitignore`d folders) use backslashes when you're not passing them to
Python.

## Try the MCP server on its own first (recommended)

Before touching the `anthropic`-dependent agent, sanity-check the tool
layer exactly the way it was verified in the sandbox:

```bash
python mcp_server/test_server_standalone.py
```

You should see all 8 tools discovered and each one exercised, including a
committed git change inside `mcp_server/data/config_repo/` and a rejected
`commit_config_change` call when `confirm=False`.

If you want to look around by hand, `cd mcp_server/data/config_repo` then
`git log` shows the commit the test made. (Windows PowerShell 5.1, the
version that ships by default on most Windows 10/11 machines, doesn't
support chaining commands with `&&` the way `cmd.exe`/Mac/Linux do -- so
run those as two separate lines rather than one `cd ... && git log` line.)

Reset back to a clean slate any time with:

```bash
python mcp_server/init_data.py --reset
```

## Run the actual agent

```bash
python agent/pricing_agent.py
```

This will work `TICKET-101` (raise DE/STANDARD's base fee to 2.90%) through
all three phases: analysis (read ticket, query config, propose diff),
configuration (commit -- **after you type `yes` to approve it**), and
validation (generate test cases, update docs, post a ticket comment --
which also asks for your approval).

You can also point it at a different instruction:

```bash
python agent/pricing_agent.py "Look at TICKET-101 and just tell me what it's asking for, don't change anything yet."
```

## Extending it (optional, if you want to go further)

- Add a second seed ticket with a *structural* change (e.g. a `remap` or
  `split`, mirroring the real system's structural-change operations) and
  see how the agent's phase-1 questions change.
- Add a `list_config_history` tool that shells out to `git log` in
  `config_repo/` -- a natural audit-trail feature.
- Swap the terminal `input()` gate for a simple Flask/CLI "approval queue"
  file, closer to how a real async approval step might work.

## Publishing to GitHub

This folder is meant to be published as its own repo under `rsundararajan24`
using VS Code's built-in "Publish to GitHub" (Source Control view). The
`.gitignore` already excludes `mcp_server/data/` (runtime state) and `.env`
(your API key) so neither ends up in the repo.
