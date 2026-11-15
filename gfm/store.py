"""A memory store made of plain files in a local git repo.

Each fact is one Markdown file with a small header. Facts are never deleted:
an update marks the old fact `superseded` and links it to the new one, and
every batch of changes is one git commit pointing back to the session log line
it came from. `git log`, `git diff` and `git blame` then answer "what changed,
when, and why does the agent believe this?".
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

FIELDS = [
    "id", "subject", "type", "scope", "project", "status", "confidence",
    "supersedes", "superseded_by", "valid_from", "invalid_at", "source", "evidence",
]


@dataclass
class Fact:
    id: str
    subject: str
    statement: str
    type: str = "project_fact"
    scope: str = "project"
    project: str = "general"
    status: str = "active"          # active | superseded | invalidated
    confidence: float = 0.8
    supersedes: str | None = None
    superseded_by: str | None = None
    valid_from: str = ""
    invalid_at: str | None = None
    source: str = ""                # <session-id>:L<line>
    evidence: str = ""

    def to_file(self) -> str:
        d = asdict(self)
        head = "\n".join(f"{k}: {json.dumps(d[k], ensure_ascii=False)}" for k in FIELDS)
        return f"---\n{head}\n---\n{self.statement}\n"

    @classmethod
    def from_file(cls, text: str) -> "Fact":
        m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
        if not m:
            raise ValueError("bad fact file")
        d = {}
        for line in m.group(1).splitlines():
            k, _, v = line.partition(": ")
            d[k] = json.loads(v)
        d["statement"] = m.group(2).strip()
        return cls(**{k: d.get(k) for k in [*FIELDS, "statement"] if k in d})


@dataclass
class Change:
    op: str          # ADD | UPDATE | INVALIDATE
    fact: Fact
    old: Fact | None = None
    reason: str = ""


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()
        self.facts_dir = self.root / "facts"

    # ---------- setup ----------
    def init(self) -> None:
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            self._git("init", "-q", "-b", "main")
            (self.root / ".gitignore").write_text("state.json\nruns/\n")
            self._git("add", ".gitignore")
            self._git("commit", "-q", "-m", "memory: empty store")

    def _git(self, *args: str) -> str:
        out = subprocess.run(
            ["git", "-C", str(self.root),
             "-c", "user.name=gfm memory writer", "-c", "user.email=gfm@localhost",
             "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
        return out.stdout

    # ---------- reading ----------
    def all(self) -> list[Fact]:
        return [Fact.from_file(p.read_text()) for p in sorted(self.facts_dir.glob("*.md"))]

    def active(self, projects: set[str] | None = None) -> list[Fact]:
        return [f for f in self.all() if f.status == "active" and (projects is None or f.project in projects)]

    def get(self, fact_id: str) -> Fact | None:
        p = self.facts_dir / f"{fact_id}.md"
        return Fact.from_file(p.read_text()) if p.exists() else None

    def projects(self) -> list[str]:
        return sorted({f.project for f in self.all()})

    def _next_id(self) -> str:
        nums = [int(p.stem.split("-")[1]) for p in self.facts_dir.glob("f-*.md")]
        return f"f-{(max(nums) + 1 if nums else 1):04d}"

    def _write(self, f: Fact) -> None:
        (self.facts_dir / f"{f.id}.md").write_text(f.to_file())

    # ---------- writing ----------
    def apply(self, ops: list[dict], source: str, when: str) -> list[Change]:
        """Apply extractor operations. Unknown or unsafe ops are skipped, not guessed at."""
        when = when or datetime.now(timezone.utc).isoformat()
        changes: list[Change] = []
        for op in ops:
            kind = (op.get("op") or "").upper()
            target = self.get(op["target_id"]) if op.get("target_id") else None

            if kind in ("UPDATE", "INVALIDATE") and (target is None or target.status != "active"):
                kind = "ADD" if kind == "UPDATE" else ""   # target vanished: treat update as new fact
            if not kind:
                continue

            if kind == "INVALIDATE":
                target.status, target.invalid_at = "invalidated", when
                self._write(target)
                changes.append(Change("INVALIDATE", target, reason=op.get("reason", "")))
                continue

            new = Fact(
                id=self._next_id(),
                subject=op.get("subject", "").strip(),
                statement=op.get("statement", "").strip(),
                type=op.get("type", "project_fact"),
                scope=op.get("scope", "project"),
                project=op.get("project", "general"),
                confidence=float(op.get("confidence", 0.8)),
                valid_from=when,
                source=source,
                evidence=op.get("evidence", "")[:300],
            )
            if not new.statement:
                continue
            if kind == "UPDATE":
                new.supersedes = target.id
                target.status, target.superseded_by, target.invalid_at = "superseded", new.id, when
                self._write(target)
            self._write(new)
            changes.append(Change(kind, new, old=target if kind == "UPDATE" else None, reason=op.get("reason", "")))
        return changes

    def commit(self, changes: list[Change], source: str, title: str) -> str | None:
        if not changes:
            return None
        counts = {k: sum(c.op == k for c in changes) for k in ("ADD", "UPDATE", "INVALIDATE")}
        summary = f"+{counts['ADD']} added, ~{counts['UPDATE']} updated, -{counts['INVALIDATE']} invalidated"
        lines = []
        for c in changes:
            if c.op == "UPDATE":
                lines.append(f"~ {c.old.id} -> {c.fact.id} [{c.fact.project}] {c.fact.subject}\n    was: {c.old.statement}\n    now: {c.fact.statement}")
            elif c.op == "INVALIDATE":
                lines.append(f"- {c.fact.id} [{c.fact.project}] {c.fact.subject}: {c.fact.statement}")
            else:
                lines.append(f"+ {c.fact.id} [{c.fact.project}] {c.fact.subject}: {c.fact.statement}")
            if c.reason:
                lines.append(f"    why: {c.reason}")
        msg = f"memory: {summary} ({title})\n\n" + "\n".join(lines) + f"\n\nSource: {source}"
        self._git("add", "-A", "facts")
        self._git("commit", "-q", "-m", msg)
        return self._git("rev-parse", "--short", "HEAD").strip()

    # ---------- state (how far each session log has been read) ----------
    def state(self) -> dict:
        p = self.root / "state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save_state(self, st: dict) -> None:
        (self.root / "state.json").write_text(json.dumps(st, indent=2))
