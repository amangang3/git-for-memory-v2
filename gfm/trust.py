"""Check every proposed fact against the turn it came from, in code, before it is stored.

1. Evidence must really be in the turn: verbatim, or verbatim pieces joined by "..." / "…" / ";".
2. We record who said the evidence: the user (prompt or a rejection message), the assistant,
   or tool output (files, web pages, command output).
3. Policy:
   - evidence not found in the turn                 -> rejected
   - a preference not said by the user              -> rejected (assistant suggestions and text
                                                       inside files/web pages are not preferences)
   - anything said only by the assistant            -> kept, but confidence capped at 0.7
                                                       and marked asserted_by=assistant
"""
from __future__ import annotations

import re

from .traces import Turn

ASSISTANT_CAP = 0.7

_TAGS = [
    ("[CLAUDE]", "assistant"), ("[TOOL CALL", "assistant"),
    ("[TOOL OUTPUT", "tool"), ("[TOOL ERROR", "tool"), ("[BACKGROUND TASK", "tool"),
    ("[USER REJECTED", "user"), ("[USER INTERRUPTED", "user"),
]
_TAG_RE = re.compile(r"\[(?:CLAUDE|TOOL (?:CALL|OUTPUT|ERROR)[^\]]*|USER (?:REJECTED|INTERRUPTED)[^\]]*|BACKGROUND TASK DONE)\]\s*")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def segments(turn: Turn) -> list[tuple[str, str]]:
    """(speaker, normalised text) for each part of the turn the extractor saw."""
    out = [("user", _norm(turn.prompt))]
    for e in turn.events:
        speaker = next((s for tag, s in _TAGS if e.startswith(tag)), "tool")
        out.append((speaker, _norm(e)))
    return out


def pieces(evidence: str) -> list[str]:
    ev = _TAG_RE.sub("", evidence)
    return [p.strip(" .\"'“”") for p in re.split(r"\.\.\.|…|;", _norm(ev)) if len(p.strip(" .\"'“”")) >= 3]


def check(op: dict, turn: Turn) -> tuple[bool, str, dict]:
    """Return (keep?, reason, fields to set on the fact)."""
    segs = segments(turn)
    parts = pieces(op.get("evidence", ""))
    if not parts:
        return False, "no evidence quote", {}
    speakers: set[str] = set()
    for p in parts:
        found = {s for s, text in segs if p in text}
        if not found:
            return False, f"evidence not found in the turn: {p[:80]!r}", {}
        # Prefer the most trustworthy speaker that said this piece.
        speakers.add("user" if "user" in found else "tool" if "tool" in found else "assistant")
    who = "+".join(sorted(speakers))
    fields = {"asserted_by": who, "evidence_check": "verbatim" if len(parts) == 1 else "spliced"}
    if op.get("type") == "preference" and "user" not in speakers:
        return False, f"preference not stated by the user (evidence from {who})", fields
    if speakers == {"assistant"}:
        fields["confidence"] = min(float(op.get("confidence", 0.8)), ASSISTANT_CAP)
    return True, "", fields
