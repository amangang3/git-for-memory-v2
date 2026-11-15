"""Seed a scratch store with hand-written facts (no Claude calls).

  python3 probes/seed.py <store> busy    # 18 unrelated facts, so pick_candidates switches to keyword filtering
  python3 probes/seed.py <store> prefs   # coding preferences for the P8/P9 before/after test
"""
import sys

from gfm.store import Store

BUSY = [
    ("orchard", "preference", "Orchard commit style", "Orchard commits use conventional commit prefixes (feat:, fix:, chore:)."),
    ("orchard", "preference", "Orchard signed commits", "Every Orchard commit must be GPG-signed."),
    ("orchard", "project_fact", "Orchard stack", "Orchard is a Go 1.23 service with a SQLite store."),
    ("orchard", "project_fact", "Orchard deploy target", "Orchard deploys to a single Hetzner VM via Ansible."),
    ("orchard", "environment", "Orchard CI", "Orchard CI runs on GitHub Actions with a self-hosted ARM runner."),
    ("orchard", "lesson", "Orchard migration lock", "Orchard migrations hang if the dev server holds the SQLite lock; stop it first."),
    ("orchard", "project_fact", "Orchard owners", "Orchard is co-owned with a classmate; reviews need one approval."),
    ("orchard", "preference", "Orchard error wrapping", "In Orchard, wrap errors with fmt.Errorf and %w, never pkg/errors."),
    ("orchard", "project_fact", "Orchard API versioning", "Orchard's public API is versioned under /v2; /v1 is frozen."),
    ("general", "preference", "Editor", "The user edits in Neovim with LazyVim."),
    ("general", "environment", "Laptop", "The user works on an M3 MacBook Air with 16 GB RAM."),
    ("general", "preference", "Reply style", "The user prefers short answers with the command first, explanation after."),
    ("general", "environment", "Shell", "The user's shell is zsh with starship prompt."),
    ("general", "preference", "Branch naming", "Branches are named <initials>/<topic>, e.g. ag/export."),
    ("fernway", "project_fact", "Fernway purpose", "Fernway is a personal bookmarking web app."),
    ("fernway", "project_fact", "Fernway docs tool", "Fernway documentation is built with mkdocs-material."),
    ("fernway", "environment", "Fernway local port", "Fernway's dev server runs on port 8040."),
    ("fernway", "lesson", "Fernway flaky test", "test_import_html is flaky on CI due to a timezone assumption; run it with TZ=UTC."),
]

PREFS = [
    ("general", "preference", "Python package manager", "Use uv for all Python projects (uv init, uv add, uv run). Never use pip or requirements.txt."),
    ("general", "preference", "Python test framework", "Write Python tests with pytest (plain assert, fixtures), never unittest."),
    ("general", "preference", "Python project layout", "Use a src/ layout with pyproject.toml; no setup.py."),
    ("general", "preference", "Lint and format", "Use ruff for both linting and formatting; configure it in pyproject.toml."),
    ("general", "preference", "CLI framework", "For Python CLIs, use argparse from the standard library; avoid click and typer."),
    ("general", "preference", "Reply style", "Keep explanations short; lead with what was done."),
]


def seed(path: str, which: str) -> None:
    rows = {"busy": BUSY, "prefs": PREFS}[which]
    s = Store(path)
    s.init()
    ops = [{"op": "ADD", "project": p, "type": t, "subject": sub, "statement": st, "scope": "private" if p == "general" else "project",
            "confidence": 0.95, "evidence": "(seeded)"} for p, t, sub, st in rows]
    s.commit(s.apply(ops, source="seed#L0", when="2026-10-15T00:00:00Z"), "seed#L0", f"session seed turn 0")
    print(f"seeded {len(rows)} facts into {path}")


if __name__ == "__main__":
    seed(sys.argv[1], sys.argv[2])
