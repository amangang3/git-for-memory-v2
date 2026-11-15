"""Command line: python3 -m gfm <command>

  replay <session.jsonl>...   read new turns from session logs and update memory
  facts [--all] [--project P] list facts in memory
  history <fact-id>           show how a fact came to be (git history + supersede chain)
  log                         show memory commits
  sessions [--limit N]        list recent Claude Code sessions on this machine
  view [--out FILE]           build a local HTML page: timeline of changes + current facts
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import llm
from .extract import extract
from .store import HUMAN_SOURCE, Store
from .traces import find_log, hide_folder, read_turns
from . import projects, trust

import os
DEFAULT_STORE = Path(os.environ.get("GFM_STORE") or Path(__file__).resolve().parent.parent / "memory")


def cmd_replay(store: Store, args) -> None:
    store.init()
    state = store.state()
    runs = store.root / "runs"
    runs.mkdir(exist_ok=True)
    total_cost = 0.0
    for path in args.sessions:
        path = Path(path).expanduser()
        sid = path.stem
        state.setdefault("_logs", {})[sid] = str(path.resolve())
        done_line = 0 if args.from_start else state.get(sid, 0)
        turns = read_turns(path, start_line=done_line)
        if args.max_turns:
            turns = turns[: args.max_turns]
        print(f"\n== {sid[:8]}: {len(turns)} new turn(s) after line {done_line}")
        for t in turns:
            if args.dry_run:
                print(f"--- turn {t.index} (L{t.start_line}, {len(t.render())} chars)\n{t.render()[:args.show]}\n")
                continue
            t0 = time.time()
            if len(t.prompt) < 40 and len(t.events) < 3 and not any(e.startswith(("[TOOL ERROR", "[USER REJECTED")) for e in t.events):
                state[sid] = t.end_line
                store.save_state(state)
                print(f"   turn {t.index:>3}: skipped (too short to hold a fact)")
                continue
            human = store.absorb_hand_edits()
            if human:
                print(f"   ! outside edits to fact files flagged and held for approval (commit {human}); see `gfm pending`")
            active = store.active()
            cwd_project = projects.project_for(t.cwd, store.root)
            try:
                ops, cost = extract(t, active, store.projects(), model=args.model, project=cwd_project)
            except llm.LLMError as e:
                # Stop here so this turn is retried next time; skipping ahead would lose it for good.
                print(f"   turn {t.index}: extractor failed, will retry on the next run: {e}")
                if "login" in str(e):
                    sys.exit(1)
                break
            total_cost += cost
            source = f"{sid}#L{t.start_line}"   # session id + line; no folder names
            for op in ops:   # the model can echo folder paths back; hide them here too
                for k in ("subject", "statement", "evidence", "reason"):
                    if isinstance(op.get(k), str):
                        op[k] = hide_folder(op[k], t.cwd)
            kept, rejected = [], []
            for op in ops:
                op["project"] = projects.resolve(op.get("project", ""), cwd_project, store.projects())
                if op.get("op", "").upper() == "INVALIDATE":
                    kept.append(op)
                    continue
                ok, why, fields = trust.check(op, t)
                op.update(fields)
                (kept if ok else rejected).append(op)
                if not ok:
                    op["rejected"] = why
                    print(f"   turn {t.index:>3}: REJECTED {op.get('type')} {op.get('statement', '')[:70]!r}: {why}")
            changes = store.apply(kept, source=source, when=t.timestamp)
            sha = store.commit(changes, source=source, title=f"session {sid[:8]} turn {t.index}")
            with (runs / f"{sid}.jsonl").open("a") as fh:
                fh.write(json.dumps({"turn": t.index, "line": t.start_line, "project": cwd_project, "ops": kept,
                                     "rejected": rejected, "cost": cost, "commit": sha}) + "\n")
            state[sid] = t.end_line
            store.save_state(state)
            label = ", ".join(f"{c.op.lower()} {c.fact.id}" for c in changes) or "no change"
            print(f"   turn {t.index:>3}: {label}  ({time.time() - t0:.0f}s, ${cost:.4f})  {t.prompt[:60]!r}")
    if not args.dry_run:
        print(f"\nTotal extraction cost: ${total_cost:.4f}")


def cmd_sessions(store: Store, args) -> None:
    """List recent Claude Code sessions on this machine, newest first, with their first prompt."""
    from .traces import PROJECTS_DIR, _is_real_prompt, _text_of
    logs = sorted(PROJECTS_DIR.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[: args.limit]
    state = store.state() if (store.root / "state.json").exists() else {}
    for p in logs:
        first, prompts = "", 0
        with p.open(errors="ignore") as fh:
            for raw in fh:
                if '"type":"user"' not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if _is_real_prompt(rec):
                    prompts += 1
                    first = first or _text_of(rec["message"]["content"]).strip().replace("\n", " ")
        seen = "read" if p.stem in state else "new "
        when = time.strftime("%b %d %H:%M", time.localtime(p.stat().st_mtime))
        print(f"{when}  {seen}  {prompts:>3} prompts  {p.stem[:8]}  {first[:70]}")
        print(f"      {p}")


def cmd_facts(store: Store, args) -> None:
    facts = store.all() if args.all else store.active()
    if args.project:
        facts = [f for f in facts if f.project == args.project]
    by_project: dict[str, list] = {}
    for f in facts:
        by_project.setdefault(f.project, []).append(f)
    for proj, items in sorted(by_project.items()):
        print(f"\n[{proj}]")
        for f in items:
            flag = "" if f.status == "active" else f"  ({f.status} -> {f.superseded_by or ''})"
            print(f"  {f.id} {f.type:<12} {f.scope:<8} {f.subject}: {f.statement}{flag}")


def cmd_history(store: Store, args) -> None:
    chain, f = [], store.get(args.fact_id)
    while f and f.supersedes:             # walk back to the first version
        f = store.get(f.supersedes)
    while f:
        chain.append(f)
        f = store.get(f.superseded_by) if f.superseded_by else None
    for f in chain:
        sid, _, line = f.source.partition("#L")
        found = "log found" if find_log(sid, store.state().get("_logs")) else "log not on this machine"
        print(f"{f.id} [{f.status}] from {f.valid_from[:16]}  {f.statement}\n    evidence: {f.evidence}\n    source: session {sid[:8]}, line {line} ({found})")
    print()
    print(store._git("log", "--format=%h %ad %s", "--date=short", "--", f"facts/{args.fact_id}.md"))


def cmd_inject(store: Store, args) -> None:
    from .inject import build_context
    if not (store.root / "facts").exists():
        return
    print(build_context(store, cwd=args.cwd or "", project=args.project, budget=args.budget))


def cmd_check(store: Store, args) -> None:
    store.init()
    sha = store.absorb_hand_edits()
    print(f"! outside edits found: flagged and held for approval in commit {sha}" if sha else "no outside edits: fact files match git")
    if sha:
        print(store._git("show", "--stat", "--format=%an%n%B", sha))
        for f in store.pending():
            print(f"   pending {f.id}: gfm approve {f.id} --reason '...'   or   gfm reject {f.id}")


def cmd_pending(store: Store, args) -> None:
    items = store.pending()
    if not items:
        print("no pending outside edits")
    for f in items:
        cur = store.get(f.supersedes) if f.supersedes else None
        print(f"{f.id} [{f.project}] {f.subject}  ({f.evidence})")
        if cur:
            print(f"    current ({cur.id}): {cur.statement}")
        print(f"    proposed:      {f.statement}")


def cmd_approve(store: Store, args) -> None:
    try:
        fn = store.approve if args.cmd == "approve" else store.reject
        sha = fn(args.fact_id, args.reason)
    except ValueError as e:
        sys.exit(str(e))
    print(f"{args.fact_id} {'approved and applied' if args.cmd == 'approve' else 'rejected'} (commit {sha})")


def cmd_correct(store: Store, args) -> None:
    """The supported way for a person to change a fact: a new version, attributed to a human."""
    store.init()
    old = store.get(args.fact_id)
    if not old or old.status != "active":
        sys.exit(f"{args.fact_id} is not a current fact")
    op = {"op": "UPDATE", "target_id": old.id, "subject": old.subject, "statement": args.statement, "type": old.type,
          "scope": old.scope, "project": old.project, "confidence": 1.0, "evidence": args.reason or "corrected by hand",
          "reason": args.reason or "corrected by a person", "asserted_by": "human"}
    changes = store.apply([op], source=HUMAN_SOURCE, when="")
    sha = store.commit(changes, source=HUMAN_SOURCE, title="session human turn 1", author="human edit <human@localhost>")
    print(f"{old.id} -> {changes[0].fact.id} (commit {sha})")


def main() -> None:
    p = argparse.ArgumentParser(prog="gfm")
    p.add_argument("--store", default=str(DEFAULT_STORE))
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay")
    r.add_argument("sessions", nargs="+")
    r.add_argument("--model", default=llm.DEFAULT_MODEL)
    r.add_argument("--max-turns", type=int, default=0)
    r.add_argument("--from-start", action="store_true", help="ignore saved progress")
    r.add_argument("--dry-run", action="store_true", help="print turns, don't call Claude")
    r.add_argument("--show", type=int, default=1500)
    fa = sub.add_parser("facts")
    fa.add_argument("--all", action="store_true")
    fa.add_argument("--project")
    h = sub.add_parser("history")
    h.add_argument("fact_id")
    sub.add_parser("log")
    inj = sub.add_parser("inject", help="print the memory a new session in a folder would receive")
    inj.add_argument("--cwd", default="")
    inj.add_argument("--project")
    inj.add_argument("--budget", type=int, default=6000)
    sub.add_parser("check", help="find edits made directly to fact files and hold them for approval")
    sub.add_parser("pending", help="list outside edits waiting for approval")
    for name in ("approve", "reject"):
        ap_ = sub.add_parser(name, help=f"{name} a pending outside edit")
        ap_.add_argument("fact_id")
        ap_.add_argument("--reason", default="")
    co = sub.add_parser("correct", help="replace a fact's statement, recorded as a human change")
    co.add_argument("fact_id")
    co.add_argument("statement")
    co.add_argument("--reason", default="")
    se = sub.add_parser("sessions")
    se.add_argument("--limit", type=int, default=15)
    hk = sub.add_parser("hook", help="entry points used by Claude Code hooks")
    hk.add_argument("action", choices=["stop", "start", "worker", "install", "uninstall"])
    hk.add_argument("path", nargs="?")
    v = sub.add_parser("view")
    v.add_argument("--out", help="where to write the page (default: view-<store>.html next to the store)")
    args = p.parse_args()
    store = Store(args.store)
    if args.cmd == "replay":
        cmd_replay(store, args)
    elif args.cmd == "facts":
        cmd_facts(store, args)
    elif args.cmd == "history":
        cmd_history(store, args)
    elif args.cmd == "hook":
        from . import hooks
        fn = getattr(hooks, args.action)
        fn(args.path) if args.action == "worker" else fn()
    elif args.cmd == "view":
        from .viewer import build
        out = Path(args.out) if args.out else store.root.parent / f"view-{store.root.name}.html"
        print(build(store, out))
    elif args.cmd == "sessions":
        cmd_sessions(store, args)
    elif args.cmd == "inject":
        cmd_inject(store, args)
    elif args.cmd == "check":
        cmd_check(store, args)
    elif args.cmd == "pending":
        cmd_pending(store, args)
    elif args.cmd in ("approve", "reject"):
        cmd_approve(store, args)
    elif args.cmd == "correct":
        cmd_correct(store, args)
    elif args.cmd == "log":
        print(store._git("log", "--format=%h %ad %s", "--date=iso"))


if __name__ == "__main__":
    main()
