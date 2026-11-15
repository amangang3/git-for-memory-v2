"""Claude Code hooks: update memory after every turn, load memory when a session starts.

  stop    (Stop hook)          hands the session to a background worker and returns at once
  worker  (run by stop)        reads new turns, updates the store, rebuilds the viewer page
  start   (SessionStart hook)  prints current facts as extra context for the new session

Turn it off without touching settings: create a file named PAUSED in the project folder.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
STORE = PROJECT / "memory"
LOG = STORE / "runs" / "hooks.log"
PAUSE_FILE = PROJECT / "PAUSED"
MAX_CONTEXT_CHARS = 9000


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def _payload() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def _skip() -> bool:
    # Calls made by our own extractor, or memory paused by the user.
    return bool(os.environ.get("GFM_INTERNAL")) or PAUSE_FILE.exists()


def stop() -> None:
    data = _payload()
    path = data.get("transcript_path")
    if _skip() or not path or not Path(path).exists():
        return
    # Detach so the chat never waits, and the work survives the session closing.
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as out:
        subprocess.Popen(
            [sys.executable, "-m", "gfm", "hook", "worker", path],
            cwd=PROJECT, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True,
        )


def worker(path: str) -> None:
    from .store import Store
    from .viewer import build
    from . import __main__ as cli

    STORE.mkdir(exist_ok=True)
    with open(STORE / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)          # one writer at a time; others wait their turn
        store = Store(STORE)
        sid = Path(path).stem
        before = len(store.all()) if (STORE / "facts").exists() else 0
        args = type("A", (), dict(sessions=[path], from_start=False, max_turns=0, dry_run=False,
                                  show=0, model=cli.llm.DEFAULT_MODEL))()
        try:
            cli.cmd_replay(store, args)
            build(store, PROJECT / "view-memory.html")
            _log(f"session {sid[:8]}: ok, versions {before} -> {len(store.all())}")
        except SystemExit as e:
            _log(f"session {sid[:8]}: stopped ({e})")
        except Exception as e:  # never let a memory failure surface in the user's chat
            _log(f"session {sid[:8]}: error {type(e).__name__}: {e}")


def start() -> None:
    _payload()
    if _skip() or not (STORE / "facts").exists():
        return
    from .store import Store

    facts = Store(STORE).active()
    if not facts:
        return
    # Share the budget across projects: take one fact from each project in turn
    # (preferences and lessons first, newest first), so no project is crowded out.
    order = {"preference": 0, "lesson": 1, "environment": 2, "project_fact": 3}
    queues: dict[str, list] = {}
    for f in sorted(facts, key=lambda f: (order.get(f.type, 9), -int(f.id.split("-")[1]))):
        queues.setdefault(f.project, []).append(f)
    picked: dict[str, list] = {p: [] for p in queues}
    size, left = 0, sum(len(q) for q in queues.values())
    while left:
        added = False
        for p, q in queues.items():
            if not q:
                continue
            f = q[0]
            line = f"- ({f.type.replace('_', ' ')}) {f.subject}: {f.statement}"
            if size + len(line) > MAX_CONTEXT_CHARS:
                continue
            q.pop(0)
            left -= 1
            picked[p].append(line)
            size += len(line)
            added = True
        if not added:
            break
    lines = []
    for p in sorted(picked):
        lines.append(f"\n[{p}]")
        lines += picked[p]
        if queues[p]:
            lines.append(f"- ({len(queues[p])} more {p} facts in {STORE})")
    text = (
        "Long-term memory (git-for-memory). These facts were learned from earlier sessions. "
        "Prefer them over guesses, but they can be out of date: check before relying on paths or status."
        + "\n".join(lines)
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))


SETTINGS = Path.home() / ".claude" / "settings.json"
MARKER = "-m gfm hook"


def _hook_entries() -> dict:
    base = f'cd "{PROJECT}" && "{sys.executable}" -m gfm hook'
    return {
        "Stop": {"hooks": [{"type": "command", "command": f"{base} stop", "timeout": 10}]},
        "SessionStart": {"hooks": [{"type": "command", "command": f"{base} start", "timeout": 15}]},
    }


def install() -> None:
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    (SETTINGS.parent / "settings.json.gfm-backup").write_text(json.dumps(settings, indent=2))
    hooks = settings.setdefault("hooks", {})
    for event, entry in _hook_entries().items():
        groups = [g for g in hooks.get(event, []) if not any(MARKER in h.get("command", "") for h in g.get("hooks", []))]
        groups.append(entry)
        hooks[event] = groups
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"installed in {SETTINGS} (backup: settings.json.gfm-backup)")


def uninstall() -> None:
    if not SETTINGS.exists():
        return
    settings = json.loads(SETTINGS.read_text())
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not any(MARKER in h.get("command", "") for h in g.get("hooks", []))]
        if not hooks[event]:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"removed from {SETTINGS}")
