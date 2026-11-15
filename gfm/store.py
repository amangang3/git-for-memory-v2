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
    status: str = "active"          # active | superseded | invalidated | pending | rejected
    confidence: float = 0.8
    supersedes: str | None = None
    superseded_by: str | None = None
    valid_from: str = ""
    invalid_at: str | None = None
    source: str = ""                # <session-id>:L<line>
    evidence: str = ""
    asserted_by: str = ""           # user | assistant | tool | human | outside-edit[+approved] (joined by +)
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

    def commit(self, changes: list[Change], source: str, title: str, author: str | None = None, notes: list[str] = ()) -> str | None:
        if not changes and not notes:
            return None
        counts = {k: sum(c.op == k for c in changes) for k in ("ADD", "UPDATE", "INVALIDATE", "PENDING", "REJECT")}
        summary = f"+{counts['ADD']} added, ~{counts['UPDATE']} updated, -{counts['INVALIDATE']} invalidated"
        if counts["PENDING"] or notes:
            summary = f"!{counts['PENDING'] + len(notes)} outside edit(s) flagged, not applied"
        if counts["REJECT"]:
            summary = f"x{counts['REJECT']} outside edit(s) rejected"
        lines = []
        for c in changes:
            if c.op == "UPDATE":
                lines.append(f"~ {c.old.id} -> {c.fact.id} [{c.fact.project}] {c.fact.subject}\n    was: {c.old.statement}\n    now: {c.fact.statement}")
            elif c.op == "INVALIDATE":
                lines.append(f"- {c.fact.id} [{c.fact.project}] {c.fact.subject}: {c.fact.statement}")
            elif c.op == "PENDING":
                tgt = f"{c.old.id} -> " if c.old else ""
                lines.append(f"? {tgt}{c.fact.id} [{c.fact.project}] {c.fact.subject}" +
                             (f"\n    current: {c.old.statement}" if c.old else "") + f"\n    proposed: {c.fact.statement}")
            elif c.op == "REJECT":
                lines.append(f"x {c.fact.id} [{c.fact.project}] {c.fact.subject}: {c.fact.statement}")
            else:
                lines.append(f"+ {c.fact.id} [{c.fact.project}] {c.fact.subject}: {c.fact.statement}")
            if c.reason:
                lines.append(f"    why: {c.reason}")
        lines += [f"! {n}" for n in notes]
        msg = f"memory: {summary} ({title})\n\n" + "\n".join(lines) + f"\n\nSource: {source}"
        # Stage only the files this batch wrote, so nothing else (e.g. a hand edit) rides along.
        paths = sorted({f"facts/{f.id}.md" for c in changes for f in (c.fact, c.old) if f})
        if paths:
            self._git("add", "--", *paths)
        self._git("commit", "-q", "--allow-empty", "-m", msg, *(["--author", author] if author else []))
        return self._git("rev-parse", "--short", "HEAD").strip()

    # ---------- edits made outside gfm ----------
    def dirty(self) -> list[tuple[str, str]]:
        """(git status code, path) for fact files changed since the last memory commit."""
        out = self._git("status", "--porcelain", "--untracked-files=all", "--", "facts")
        return [(ln[:2], ln[3:].strip().strip('"')) for ln in out.splitlines() if ln.strip()]

    def pending(self) -> list[Fact]:
        return [f for f in self.all() if f.status == "pending"]

    def absorb_hand_edits(self) -> str | None:
        """Catch edits made directly to fact files and hold them for approval. Nothing is applied.

        A file edit has no quote from the user and no known author: any person, script, sync conflict
        or other AI tool could have written it. So it gets the same treatment as text found in a tool's
        output: it is never trusted on its own.
        - changed statement/subject/etc. -> the committed version is put back and stays active; the edit
                                            becomes a separate PENDING fact (not used by Claude)
        - new file                       -> becomes a PENDING fact
        - deleted file, status changed,
          or unreadable edit             -> the committed version is put back, and the attempt is flagged
        `gfm approve <id>` applies a pending fact; `gfm reject <id>` discards it.
        """
        dirty = self.dirty()
        if not dirty:
            return None
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        changes: list[Change] = []
        notes: list[str] = []
        why = "file was changed outside gfm; author unknown and no evidence from a session, so it is held until approved"
        for code, path in dirty:
            p = self.root / path
            fid = Path(path).stem
            if code.strip() == "??":
                try:
                    f = Fact.from_file(p.read_text())
                except Exception:
                    f = None
                p.unlink()
                if f is None or not f.statement:
                    notes.append(f"removed unreadable new file {path}")
                    continue
                pend = Fact(id=self._next_id(), subject=f.subject, statement=f.statement, type=f.type, scope=f.scope,
                            project=f.project, confidence=0.0, status="pending", valid_from=now, source=HUMAN_SOURCE,
                            evidence=f"new file {path} written outside gfm (unverified)", asserted_by="outside-edit")
                self._write(pend)
                changes.append(Change("PENDING", pend, reason=why))
                continue
            old_text = self._git("show", f"HEAD:{path}")
            old = Fact.from_file(old_text)
            try:
                new = Fact.from_file(p.read_text()) if p.exists() else None
            except Exception:
                new = "unreadable"
            p.write_text(old_text)              # the committed version always comes back
            if new is None:
                notes.append(f"{fid} was deleted outside gfm; restored. To retire a fact, invalidate it through gfm.")
                continue
            if new == "unreadable":
                notes.append(f"{fid} was edited outside gfm into an unreadable file; restored.")
                continue
            if new.status != old.status:
                notes.append(f"{fid} status was changed outside gfm ({old.status} -> {new.status}); restored.")
                continue
            if old.status != "active":
                notes.append(f"{fid} is {old.status}; an outside edit to it was reverted (history is read-only).")
                continue
            if (new.statement, new.subject, new.type, new.scope, new.project) == (old.statement, old.subject, old.type, old.scope, old.project):
                continue
            pend = Fact(id=self._next_id(), subject=new.subject, statement=new.statement, type=new.type, scope=new.scope,
                        project=new.project, confidence=0.0, status="pending", supersedes=old.id, valid_from=now,
                        source=HUMAN_SOURCE, evidence=f"outside edit to facts/{fid}.md (unverified)", asserted_by="outside-edit")
            self._write(pend)
            changes.append(Change("PENDING", pend, old=old, reason=why))
        if not changes and not notes:
            return None
        return self.commit(changes, source=HUMAN_SOURCE, title="session human turn 1",
                           author="outside edit (unverified) <unverified@localhost>", notes=notes)

    def approve(self, fact_id: str, reason: str = "") -> str:
        """Apply a pending outside edit. The decision is recorded as its own commit."""
        f = self.get(fact_id)
        if not f or f.status != "pending":
            raise ValueError(f"{fact_id} is not a pending edit")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = self.get(f.supersedes) if f.supersedes else None
        if old and old.status != "active":
            raise ValueError(f"{fact_id} would replace {old.id}, which is no longer current ({old.status}); reject it and edit again")
        f.status, f.valid_from, f.confidence, f.asserted_by = "active", now, 1.0, "outside-edit+approved"
        f.evidence = f"{f.evidence}; approved: {reason or 'approved with gfm approve'}"
        self._write(f)
        if old:
            old.status, old.superseded_by, old.invalid_at = "superseded", f.id, now
            self._write(old)
        change = Change("UPDATE" if old else "ADD", f, old=old, reason=f"outside edit approved: {reason or 'no reason given'}")
        return self.commit([change], source=HUMAN_SOURCE, title="session human turn 2", author="approved (gfm approve) <approved@localhost>")

    def reject(self, fact_id: str, reason: str = "") -> str:
        f = self.get(fact_id)
        if not f or f.status != "pending":
            raise ValueError(f"{fact_id} is not a pending edit")
        f.status, f.invalid_at = "rejected", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write(f)
        return self.commit([Change("REJECT", f, reason=reason or "rejected with gfm reject")], source=HUMAN_SOURCE,
                           title="session human turn 2", author="rejected (gfm reject) <rejected@localhost>")

    # ---------- state (how far each session log has been read) ----------
    def state(self) -> dict:
        p = self.root / "state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save_state(self, st: dict) -> None:
        (self.root / "state.json").write_text(json.dumps(st, indent=2))
