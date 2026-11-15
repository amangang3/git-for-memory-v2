"""Work out which project a session belongs to from its working folder, not from the model.

The project is the name of the git repository root containing the folder (or the folder's own
name if it isn't in a repo), turned into a slug. A store can override this with
`projects.json`: {"<folder name or absolute path>": "<slug>"}.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

GENERAL = "general"   # facts about the user across all projects


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or GENERAL


def _repo_root(folder: Path) -> Path:
    for p in [folder, *folder.parents]:
        if (p / ".git").exists():
            return p
    return folder


def aliases(store_root: Path) -> dict:
    p = Path(store_root) / "projects.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def project_for(cwd: str, store_root: Path | None = None) -> str:
    if not cwd:
        return GENERAL
    folder = Path(cwd).expanduser()
    root = _repo_root(folder) if folder.exists() else folder
    al = aliases(store_root) if store_root else {}
    for key in (str(folder), str(root), root.name, folder.name):
        if key in al:
            return slug(al[key])
    return slug(root.name)


def resolve(model_project: str, cwd_project: str, known: list[str]) -> str:
    """Pick the project for one extracted fact.

    The working folder decides, with two exceptions the model is good at:
    - "general": a fact about the user across all their work;
    - an existing project the user explicitly talked about from another folder.
    Any other name the model invents (e.g. "docs") is replaced by the folder's project.
    """
    m = slug(model_project or "")
    if m == GENERAL or (m in known and m != cwd_project):
        return m
    return cwd_project
