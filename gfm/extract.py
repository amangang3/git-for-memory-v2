"""Turn one session turn into memory operations, using Claude as the memory writer."""
from __future__ import annotations

import re
from pathlib import Path

from . import llm
from .store import Fact
from .traces import Turn

MAX_CANDIDATES = 15     # below this, send every fact; above it, send only related ones
PER_PROJECT = 10        # closest facts from the project the turn is most about
ANYWHERE = 6            # plus the closest facts from any project

SYSTEM = """You maintain the long-term memory of a coding agent (Claude Code) for one user.
You read one turn of a real session log: the user's prompt and what the agent did next
(its replies, tool calls, tool output, errors, and tool calls the user rejected).
You also get the facts already in memory. Decide what memory should change.

Save only things a future session, days from now, would be glad to know:
- preference: how the user wants things done, taste, things they rejected or disliked
- project_fact: names, decisions, architecture, locations, versions, status that will still hold next week
- environment: machine, tools, paths, accounts, commands that work or don't
- lesson: a mistake that happened and what fixed it, so it is not repeated

Do NOT save: step-by-step progress of the current task, things only true for the next few minutes,
general knowledge any model already has, guesses, or anything secret (keys, passwords, tokens).
Do NOT save facts about the agent's own harness (how its tools, cards, panes or system prompts behave);
save facts about the user's projects, machine and preferences.
Do NOT save facts about how this memory system labels or hides folder names, or about names the user
wants kept out of memory; those are settings, not memories.

Status of a deliverable (e.g. "icon round 3: rejected"): keep ONE fact per deliverable with a stable subject,
and UPDATE that fact when its status changes. Never add a separate fact that only says another fact was replaced.

Operations:
- ADD: a new fact not already in memory.
- UPDATE: the turn shows an existing fact changed or was wrong. Give target_id and the full new statement.
  Use UPDATE, not ADD, whenever a new fact would contradict or replace an existing one.
- INVALIDATE: an existing fact is no longer true and nothing replaces it. Give target_id.
If an existing fact already says the same thing, do nothing. Return an empty list when nothing is worth saving;
that is the right answer for most turns.

Rules:
- Each statement stands on its own: name the project and the thing (not "it" or "the file").
- What the user says beats what the agent claims. Agent claims count only if tool output backs them.
- A user correcting the agent ("no", "too old school", "that's wrong") is strong evidence of a preference.
- evidence: a short quote (under 200 characters) from the turn that supports the fact.
- confidence: 0.9+ if the user said it plainly, 0.6-0.8 if inferred, below 0.6 don't save it.
- project: a short slug. Reuse one from the known list when it fits; use "general" for facts about the user overall.
- scope: "project" for anything tied to one project, including the user's preferences for that project;
  "private" for personal details about the user or preferences that apply across all their work;
  "shared" for lessons and environment facts that would help any agent the user works with.
  Keep the scope of an UPDATE the same as the fact it replaces unless the turn shows it widened or narrowed.
- At most 6 operations per turn. Prefer fewer, sharper facts."""

SCHEMA = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["ADD", "UPDATE", "INVALIDATE"]},
                    "target_id": {"type": "string", "description": "existing fact id for UPDATE/INVALIDATE, else empty"},
                    "subject": {"type": "string", "description": "2-6 word label, e.g. 'Taskly deploy target'"},
                    "statement": {"type": "string"},
                    "type": {"type": "string", "enum": ["preference", "project_fact", "environment", "lesson"]},
                    "scope": {"type": "string", "enum": ["private", "project", "shared"]},
                    "project": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string"},
                    "reason": {"type": "string", "description": "one short line: why this change"},
                },
                "required": ["op", "subject", "statement", "type", "scope", "project", "confidence", "evidence", "reason"],
            },
        }
    },
    "required": ["operations"],
}

_WORD = re.compile(r"[a-z0-9]{3,}")


def _words(s: str) -> set[str]:
    return set(_WORD.findall(s.lower()))


STOP = set("""about after again also another because been before being both could does doing done each
from have here into just like make more most much need only other over same should some such than that
their them then there these they this those through very want were what when where which while will
with would your user agent claude session turn file files folder""".split())


def _keywords(s: str) -> set[str]:
    return {w for w in _WORD.findall(s.lower()) if len(w) >= 4 and w not in STOP}


def pick_candidates(facts: list[Fact], turn_text: str) -> list[Fact]:
    """Pick the existing facts this turn could plausibly add to, change or contradict.

    Claude only sees what we send, so the aim is generous recall at a fraction of the size:
    the best-matching project's closest facts, plus the closest facts from anywhere.
    """
    if len(facts) <= MAX_CANDIDATES:
        return facts
    words = _keywords(turn_text)
    scored = [(len(words & _keywords(f.subject + " " + f.statement)), f) for f in facts]
    by_project: dict[str, int] = {}
    for s, f in scored:
        by_project[f.project] = by_project.get(f.project, 0) + s
    top_project = max(by_project, key=by_project.get) if by_project else None
    same = sorted([x for x in scored if x[1].project == top_project], key=lambda x: -x[0])[:PER_PROJECT]
    anywhere = sorted([x for x in scored if x[0] > 0], key=lambda x: -x[0])[:ANYWHERE]
    picked, seen = [], set()
    for _, f in same + anywhere:
        if f.id not in seen:
            seen.add(f.id)
            picked.append(f)
    return picked


def build_prompt(turn: Turn, candidates: list[Fact], known_projects: list[str]) -> str:
    mem = "\n".join(f"- {f.id} [{f.project}/{f.type}] {f.subject}: {f.statement}" for f in candidates) or "(empty)"
    return (
        f"KNOWN PROJECTS: {', '.join(known_projects) or '(none yet)'}\n"
        f"SESSION WORKING FOLDER: {Path(turn.cwd).name or '(unknown)'} (shown in the turn as [session folder])\n"
        f"TURN TIME: {turn.timestamp}\n\n"
        f"FACTS ALREADY IN MEMORY:\n{mem}\n\n"
        f"=== SESSION TURN {turn.index} ===\n{turn.render()}\n=== END TURN ===\n\n"
        "Return the memory operations for this turn."
    )


def extract(turn: Turn, active: list[Fact], known_projects: list[str], model: str = llm.DEFAULT_MODEL) -> tuple[list[dict], float]:
    text = turn.render()
    prompt = build_prompt(turn, pick_candidates(active, text), known_projects)
    data, cost = llm.structured(SYSTEM, prompt, SCHEMA, model=model)
    ops = [o for o in data.get("operations", []) if float(o.get("confidence", 0)) >= 0.6]
    return ops, cost
