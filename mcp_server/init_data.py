"""
One-time (or repeatable) setup script: seed/ -> data/

Copies the tracked template files in mcp_server/seed/ into a fresh
mcp_server/data/ directory (gitignored -- this is *runtime* state that
changes as the agent commits config changes), and git-inits the
config_repo subfolder as its own tiny git repository.

Why a separate inner repo instead of just using the outer project's git repo?
Because the outer pricing-agent folder is what you publish to GitHub as your
project. The config_repo is meant to simulate a *separate*, internal config
management system (Trinity/MSM) that the agent commits pricing changes to --
keeping it as its own throwaway git repo means:
  - the agent's commit history for config changes doesn't pollute your real
    project's commit history
  - you can safely delete/reset data/ and start over at any time without
    touching your project's git history at all

Run this from the pricing-agent/ project root:

    python mcp_server/init_data.py

Safe to re-run: it will refuse to overwrite an existing data/ directory
unless you pass --reset.
"""

import argparse
import os
import shutil
import stat
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent
SEED_DIR = BASE_DIR / "seed"
DATA_DIR = BASE_DIR / "data"
CONFIG_REPO_DIR = DATA_DIR / "config_repo"


def _git(*args: str, cwd: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")


def _rmtree_even_if_readonly(path: Path) -> None:
    """shutil.rmtree can't delete read-only files on Windows by default --
    and git deliberately marks its internal object files read-only. Clear
    that attribute on everything under `path` first, then remove it."""
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Delete and recreate data/ if it already exists")
    args = parser.parse_args()

    if DATA_DIR.exists():
        if not args.reset:
            print(f"'{DATA_DIR}' already exists. Pass --reset to wipe and recreate it.")
            return
        _rmtree_even_if_readonly(DATA_DIR)

    DATA_DIR.mkdir(parents=True)
    CONFIG_REPO_DIR.mkdir(parents=True)

    shutil.copy(SEED_DIR / "tickets.json", DATA_DIR / "tickets.json")
    shutil.copy(SEED_DIR / "pricing_docs.md", DATA_DIR / "pricing_docs.md")
    shutil.copy(SEED_DIR / "pricing_config.json", CONFIG_REPO_DIR / "pricing_config.json")

    # git-init the config repo as its own standalone repo (NOT nested-tracked
    # by the outer project -- data/ is gitignored there).
    _git("init", "-q", cwd=CONFIG_REPO_DIR)
    _git("config", "user.email", "pricing-agent@example.local", cwd=CONFIG_REPO_DIR)
    _git("config", "user.name", "Pricing Agent (local sandbox)", cwd=CONFIG_REPO_DIR)
    _git("add", "pricing_config.json", cwd=CONFIG_REPO_DIR)
    _git("commit", "-q", "-m", "Initial seed pricing config", cwd=CONFIG_REPO_DIR)

    print(f"Initialized runtime data at: {DATA_DIR}")
    print(f"  - tickets.json")
    print(f"  - pricing_docs.md")
    print(f"  - config_repo/ (its own git repo, initial commit done)")
    print("\nYou can now run the MCP server or the agent.")


if __name__ == "__main__":
    main()
