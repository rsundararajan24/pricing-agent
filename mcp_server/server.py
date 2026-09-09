"""
Pricing Change Request MCP Server
==================================

A small, self-contained MCP server that stands in for the internal PayPal
systems (Jira, Dolby MCP / Price Config System, Confluence) referenced in the
real "Pricing Business Enablement Automation using Claude" system.

It exposes the same *shape* of tools the real system used, backed by plain
JSON files and a git-tracked config repo instead of proprietary services:

    get_ticket(ticket_id)              -- read a "Jira" ticket
    list_open_tickets()                -- list tickets needing action
    query_current_config(country, category_code)
                                        -- read current pricing config ("Dolby" stand-in)
    propose_config_change(...)         -- compute a before/after diff, NO write (Gate 1 material)
    commit_config_change(...)          -- actually write + git commit the change (Gate 2, requires confirm=True)
    generate_test_cases(country, category_code)
                                        -- build simple functional test cases against the new config
    update_docs(ticket_id, summary)    -- append a section to the local "Confluence" docs file
    post_ticket_comment(ticket_id, comment)
                                        -- append an audit-trail comment to the ticket ("Jira" stand-in)

Human-in-the-loop is enforced in the AGENT layer (agent/pricing_agent.py),
not here: this server will happily execute any single call it's given, the
same way a real MCP server does. The agent is what pauses and asks the
human before calling propose -> commit, or before calling
post_ticket_comment with a "closing" summary. That separation mirrors the
real system: Dolby MCP does not know about approval gates; the Claude
skill orchestrating it does.

Runtime state lives in mcp_server/data/ (gitignored, generated). Seed
templates live in mcp_server/seed/ (git-tracked). Run
`python mcp_server/init_data.py` once before first use to populate data/
from seed/ and git-init the config repo.
"""

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CONFIG_REPO_DIR = DATA_DIR / "config_repo"
CONFIG_FILE = CONFIG_REPO_DIR / "pricing_config.json"
TICKETS_FILE = DATA_DIR / "tickets.json"
DOCS_FILE = DATA_DIR / "pricing_docs.md"

mcp = FastMCP("pricing-change-agent")


# ---------------------------------------------------------------------------
# small file helpers
# ---------------------------------------------------------------------------

def _require_data_dir():
    if not DATA_DIR.exists():
        raise RuntimeError(
            "data/ directory not found. Run 'python mcp_server/init_data.py' "
            "first to seed runtime data from mcp_server/seed/."
        )


def _load_json(path: Path) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def _save_json(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(CONFIG_REPO_DIR), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ---------------------------------------------------------------------------
# Jira stand-in
# ---------------------------------------------------------------------------

@mcp.tool()
def get_ticket(ticket_id: str) -> dict:
    """Fetch a single pricing-change ticket by ID (Jira stand-in)."""
    _require_data_dir()
    tickets = _load_json(TICKETS_FILE)
    if ticket_id not in tickets:
        return {"error": f"Ticket {ticket_id} not found"}
    return {"ticket_id": ticket_id, **tickets[ticket_id]}


@mcp.tool()
def list_open_tickets() -> list:
    """List all tickets whose status is 'Open' (Jira stand-in)."""
    _require_data_dir()
    tickets = _load_json(TICKETS_FILE)
    return [
        {"ticket_id": tid, "title": t["title"], "status": t["status"]}
        for tid, t in tickets.items()
        if t.get("status") == "Open"
    ]


@mcp.tool()
def post_ticket_comment(ticket_id: str, comment: str) -> dict:
    """Append an audit-trail comment to a ticket (Jira stand-in).

    In the real system this is one of the Gate-3 actions: the agent should
    only call this after the human has confirmed the comment's content
    (e.g. a test-result summary, or a note that config was committed).
    """
    _require_data_dir()
    tickets = _load_json(TICKETS_FILE)
    if ticket_id not in tickets:
        return {"error": f"Ticket {ticket_id} not found"}
    entry = {"timestamp": _now(), "comment": comment}
    tickets[ticket_id].setdefault("comments", []).append(entry)
    _save_json(TICKETS_FILE, tickets)
    return {"status": "ok", "ticket_id": ticket_id, "comment_added": entry}


# ---------------------------------------------------------------------------
# Dolby MCP / Price Config System stand-in (git-backed config repo)
# ---------------------------------------------------------------------------

@mcp.tool()
def query_current_config(country: str, category_code: str) -> dict:
    """Read the current live pricing config for a country + category (Dolby MCP stand-in)."""
    _require_data_dir()
    config = _load_json(CONFIG_FILE)
    country_cfg = config.get(country)
    if country_cfg is None:
        return {"error": f"No config found for country={country}"}
    cat_cfg = country_cfg.get(category_code)
    if cat_cfg is None:
        return {"error": f"No config found for country={country}, category={category_code}"}
    return {"country": country, "category_code": category_code, "config": cat_cfg}


@mcp.tool()
def propose_config_change(country: str, category_code: str, field: str, new_value: float) -> dict:
    """Compute a before/after diff for a proposed pricing config change.

    Does NOT write anything. This is the tool the agent calls to produce the
    material a human reviews at the analysis-phase gate, before any commit.
    """
    _require_data_dir()
    config = _load_json(CONFIG_FILE)
    country_cfg = config.get(country, {})
    cat_cfg = country_cfg.get(category_code)
    if cat_cfg is None:
        return {"error": f"No config found for country={country}, category={category_code}"}
    if field not in cat_cfg:
        return {"error": f"Unknown field '{field}' for {country}/{category_code}. Known fields: {list(cat_cfg.keys())}"}

    old_value = cat_cfg[field]
    return {
        "country": country,
        "category_code": category_code,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "delta": round(new_value - old_value, 6) if isinstance(old_value, (int, float)) else None,
        "note": "This is a proposal only. Nothing has been written. "
                "Call commit_config_change with confirm=True after human approval.",
    }


def _do_commit_config_change(country: str, category_code: str, field: str, new_value: float,
                              ticket_id: str, confirm: bool) -> dict:
    """The actual (blocking) work for commit_config_change -- see that function's
    docstring. Kept separate so it can be run in a worker thread via
    asyncio.to_thread rather than blocking the server's event loop directly."""
    _require_data_dir()
    if not confirm:
        return {"error": "confirm=False: refusing to write. This action requires explicit human approval."}

    config = _load_json(CONFIG_FILE)
    country_cfg = config.get(country, {})
    cat_cfg = country_cfg.get(category_code)
    if cat_cfg is None:
        return {"error": f"No config found for country={country}, category={category_code}"}
    if field not in cat_cfg:
        return {"error": f"Unknown field '{field}' for {country}/{category_code}."}

    old_value = cat_cfg[field]
    cat_cfg[field] = new_value
    _save_json(CONFIG_FILE, config)

    _git("add", "pricing_config.json")
    commit_msg = f"{ticket_id}: {country}/{category_code}.{field} {old_value} -> {new_value}"
    _git("commit", "-m", commit_msg)
    commit_hash = _git("rev-parse", "--short", "HEAD")

    return {
        "status": "committed",
        "commit": commit_hash,
        "message": commit_msg,
        "country": country,
        "category_code": category_code,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
    }


@mcp.tool()
async def commit_config_change(country: str, category_code: str, field: str, new_value: float,
                                ticket_id: str, confirm: bool) -> dict:
    """Write a pricing config change and git-commit it (Price Config System work-order stand-in).

    Requires confirm=True. The agent must only pass confirm=True after the
    human has explicitly approved the diff shown by propose_config_change --
    this mirrors the real system's Maker/Checker gate before touching the
    Staging Environment.

    This tool is async and runs its actual work (file I/O + spawning `git`
    as a subprocess) in a worker thread via asyncio.to_thread, rather than
    directly on the server's event loop. Running a blocking subprocess call
    straight on the event loop thread can, on some platforms, stall the
    event loop long enough to disrupt the stdio transport that's carrying
    this very response back to the client -- offloading it avoids that
    class of problem entirely.
    """
    return await asyncio.to_thread(
        _do_commit_config_change, country, category_code, field, new_value, ticket_id, confirm
    )


# ---------------------------------------------------------------------------
# Test-case generation (PPS Calculate Price stand-in)
# ---------------------------------------------------------------------------

@mcp.tool()
def generate_test_cases(country: str, category_code: str) -> dict:
    """Generate simple functional test cases against the current config.

    Stand-in for the real system's calls to the PPS Calculate Price /
    Exchange Currency APIs to build a functional test matrix.
    """
    _require_data_dir()
    config = _load_json(CONFIG_FILE)
    cat_cfg = config.get(country, {}).get(category_code)
    if cat_cfg is None:
        return {"error": f"No config found for country={country}, category={category_code}"}

    base_pct = cat_cfg["base_fee_pct"]
    fixed = cat_cfg["fixed_fee"]
    currency = cat_cfg.get("currency", "USD")

    def calc(amount: float) -> float:
        return round(amount * (base_pct / 100.0) + fixed, 4)

    sample_amounts = [10.0, 100.0, 1000.0]
    cases = [
        {
            "case_id": f"{country}-{category_code}-{i+1}",
            "input_amount": amt,
            "currency": currency,
            "expected_fee": calc(amt),
        }
        for i, amt in enumerate(sample_amounts)
    ]
    return {"country": country, "category_code": category_code, "test_cases": cases}


# ---------------------------------------------------------------------------
# Confluence stand-in
# ---------------------------------------------------------------------------

@mcp.tool()
def update_docs(ticket_id: str, summary: str) -> dict:
    """Append a dated section documenting a change (Confluence stand-in)."""
    _require_data_dir()
    section = f"\n## {ticket_id} -- {_now()}\n\n{summary}\n"
    with open(DOCS_FILE, "a") as f:
        f.write(section)
    return {"status": "ok", "appended_to": str(DOCS_FILE), "section": section.strip()}


if __name__ == "__main__":
    mcp.run()
