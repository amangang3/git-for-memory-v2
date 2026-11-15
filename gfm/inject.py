"""Build the memory text a new session receives: only the facts that belong to it.

A session in folder X gets:
  1. facts of X's project (preferences, lessons, environment, then project facts; newest first)
  2. "general" facts about the user (apply everywhere)
Other projects' facts are left out. The whole text, headers included, stays under the budget,
and it never contains file-system paths.
"""
from __future__ import annotations

from .projects import GENERAL, project_for
from .store import Store

DEFAULT_BUDGET = 6000   # characters (~1.5k tokens)
ORDER = {"preference": 0, "lesson": 1, "environment": 2, "project_fact": 3}
HEADER = ("Long-term memory (git-for-memory), learned from earlier sessions. Follow these preferences "
          "unless the user says otherwise; facts can be out of date, so check paths and status before relying on them.")


def _line(f) -> str:
    return f"- ({f.type.replace('_', ' ')}) {f.subject}: {f.statement}"


def build_context(store: Store, cwd: str = "", project: str | None = None, budget: int = DEFAULT_BUDGET) -> str:
    project = project or project_for(cwd, store.root)
    facts = store.active()
    groups = [(project, [f for f in facts if f.project == project])] if project != GENERAL else []
    groups.append(("about the user, all projects", [f for f in facts if f.project == GENERAL]))
    text = HEADER
    for name, items in groups:
        items.sort(key=lambda f: (ORDER.get(f.type, 9), -int(f.id.split("-")[1])))
        if not items:
            continue
        head = f"\n\n[{name}]"
        if len(text) + len(head) > budget:
            break
        text += head
        left = len(items)
        for f in items:
            ln = "\n" + _line(f)
            more = f"\n- ({left} more {name} facts not shown)"
            if len(text) + len(ln) + len(more) > budget:
                if len(text) + len(more) <= budget:
                    text += more
                break
            text += ln
            left -= 1
    return text if "\n- (" in text else ""
