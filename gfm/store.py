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
    "asserted_by", "evidence_check",
]
HUMAN_SOURCE = "human#L0"   # facts written or changed by a person, not the extractor


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
    asserted_by: str = ""           # user | assistant | tool | human (combinations joined by +)
    evidence_check: str = ""        # verbatim | spliced (checked in code against the turn)

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
                asserted_by=op.get("asserted_by", ""),
                evidence_check=op.get("evidence_check", ""),
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

    def commit(self, changes: list[Change], source: str, title: str, author: str | None = None) -> str | None:
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
        # Stage only the files this batch wrote, so nothing else (e.g. a hand edit) rides along.
        paths = sorted({f"facts/{f.id}.md" for c in changes for f in (c.fact, c.old) if f})
        self._git("add", "--", *paths)
        self._git("commit", "-q", "-m", msg, *(["--author", author] if author else []))
        return self._git("rev-parse", "--short", "HEAD").strip()

    # ---------- edits made outside gfm ----------
    def dirty(self) -> list[tuple[str, str]]:
        """(git status code, path) for fact files changed since the last memory commit."""
        out = self._git("status", "--porcelain", "--untracked-files=all", "--", "facts")
        return [(ln[:2], ln[3:].strip().strip('"')) for ln in out.splitlines() if ln.strip()]

    def absorb_hand_edits(self) -> str | None:
        """Turn edits made directly to fact files into proper, attributed versions.

        - edited statement/subject/etc. -> old file restored, new version supersedes it (asserted_by=human)
        - status set to "invalidated"     -> the fact is invalidated by a human
        - file deleted                    -> restored and invalidated (memory never loses history)
        - new file                        -> added as a human-written fact
        - unreadable edit                 -> reverted, and recorded in the commit message
        One commit, authored "human edit", so `git log` and the viewer show who changed what.
        """
        dirty = self.dirty()
        if not dirty:
            return None
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        changes: list[Change] = []
        notes: list[str] = []
        why = "edited by hand outside gfm; detected and recorded as a human change"
        for code, path in dirty:
            p = self.root / path
            fid = Path(path).stem
            if code.strip() == "??":
                try:
                    f = Fact.from_file(p.read_text())
                except Exception:
                    p.unlink()
                    notes.append(f"removed unreadable new file {path}")
                    continue
                f.id, f.source, f.asserted_by, f.valid_from = fid, HUMAN_SOURCE, "human", f.valid_from or now
                f.evidence = f.evidence or f"written by hand in {path}"
                self._write(f)
                changes.append(Change("ADD", f, reason=why))
                continue
            old_text = self._git("show", f"HEAD:{path}")
            old = Fact.from_file(old_text)
            try:
                new = Fact.from_file(p.read_text()) if p.exists() else None
            except Exception:
                new = None
                if p.exists():
                    notes.append(f"reverted unreadable hand edit to {path}")
                    p.write_text(old_text)
                    continue
            p.write_text(old_text)              # put the committed version back first
            if new is None or new.status == "invalidated":
                if old.status == "active":
                    old.status, old.invalid_at = "invalidated", now
                    self._write(old)
                    changes.append(Change("INVALIDATE", old, reason=why + (" (file was deleted)" if new is None else "")))
                continue
            if old.status != "active":
                notes.append(f"ignored hand edit to {fid}: only current facts can be edited ({fid} is {old.status})")
                continue
            if (new.statement, new.subject, new.type, new.scope, new.project) == (old.statement, old.subject, old.type, old.scope, old.project):
                continue
            ver = Fact(id=self._next_id(), subject=new.subject, statement=new.statement, type=new.type, scope=new.scope,
                       project=new.project, confidence=1.0, supersedes=old.id, valid_from=now, source=HUMAN_SOURCE,
                       evidence=f"hand edit to facts/{fid}.md", asserted_by="human", evidence_check="")
            old.status, old.superseded_by, old.invalid_at = "superseded", ver.id, now
            self._write(old)
            self._write(ver)
            changes.append(Change("UPDATE", ver, old=old, reason=why))
        if not changes:
            return None
        sha = self.commit(changes, source=HUMAN_SOURCE, title="session human turn 1", author="human edit <human@localhost>")
        if notes:
            self._git("commit", "-q", "--amend", "-m",
                      self._git("log", "-1", "--format=%B").strip() + "\n\nNotes:\n" + "\n".join(notes))
        return sha

    # ---------- state (how far each session log has been read) ----------
    def state(self) -> dict:
        p = self.root / "state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save_state(self, st: dict) -> None:
        (self.root / "state.json").write_text(json.dumps(st, indent=2))
