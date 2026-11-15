"""Read Claude Code session logs (~/.claude/projects/<slug>/<session>.jsonl) into turns.

A turn is one real user prompt plus everything Claude did until the next prompt:
its replies, tool calls, tool errors, and any tool calls the user rejected.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from .redact import redact

# How much of each piece we keep. Logs are mostly file contents and screenshots,
# which carry little memory value, so tool output is cut hard.
MAX_PROMPT = 4000
MAX_REPLY = 2500
MAX_TOOL_INPUT = 300
MAX_TOOL_OUTPUT = 250
MAX_ERROR = 500
MAX_TURN = 14000

REJECTION_MARKERS = (
    "the user doesn't want to proceed",
    "user rejected",
    "permission to use",
    "was denied",
)
FOLDER_LABEL = "[session folder]"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

SKIP_PROMPT_PREFIXES = ("<command-", "<local-command", "<system-reminder>", "Caveat:", "<task-notification>")


@dataclass
class Turn:
    session_id: str
    index: int            # 1-based turn number within the session
    start_line: int       # line of the user prompt in the log (1-based)
    end_line: int
    timestamp: str
    cwd: str
    prompt: str
    events: list[str] = field(default_factory=list)  # replies, tool calls, errors, rejections

    def render(self) -> str:
        head = f"USER PROMPT:\n{self.prompt}\n\nWHAT HAPPENED NEXT:\n"
        body = "\n".join(self.events)
        text = head + body
        if len(text) <= MAX_TURN:
            return text
        # Too long: keep the prompt, every error/rejection/correction, and the last replies.
        keep = [e for e in self.events if e.startswith(("[TOOL ERROR", "[USER REJECTED", "[USER INTERRUPTED"))]
        tail, size = [], 0
        for e in reversed(self.events):
            if e in keep:
                continue
            size += len(e)
            if size > MAX_TURN // 2:
                break
            tail.append(e)
        tail.reverse()
        return head + "\n".join(keep + ["[... middle of turn trimmed ...]"] + tail)


def _cut(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + " …"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "\n".join(parts)
    return ""


def _tool_input_summary(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    for key in ("command", "file_path", "url", "pattern", "query", "prompt", "description", "skill"):
        if key in inp and isinstance(inp[key], str):
            extra = f" ({inp['description']})" if key == "command" and inp.get("description") else ""
            return f"{key}={_cut(inp[key], MAX_TOOL_INPUT)}{extra}"
    return _cut(json.dumps(inp, ensure_ascii=False), MAX_TOOL_INPUT)


def _is_real_prompt(rec: dict) -> bool:
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return False
    content = rec.get("message", {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    text = _text_of(content).strip()
    if not text or text.startswith(SKIP_PROMPT_PREFIXES):
        return False
    if text.startswith("[Request interrupted"):
        return False
    return True


def find_log(session_id: str, known: dict | None = None) -> Path | None:
    """Locate a session's log by its id, so stored sources never need the full path.

    `known` maps session ids to paths seen during replay (for logs kept outside ~/.claude).
    """
    if known and session_id in known and Path(known[session_id]).exists():
        return Path(known[session_id])
    hits = list(PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def hide_folder(text: str, cwd: str) -> str:
    """Show the session's working folder as a neutral label."""
    if not cwd:
        return text
    home = str(Path.home())
    # The same folder appears as a plain path, ~ path, file:// URL, and the dashed
    # name Claude Code uses under ~/.claude/projects and temp folders.
    forms = {cwd, cwd.replace(home, "~", 1), quote(cwd), re.sub(r"[^A-Za-z0-9]", "-", cwd)}
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, FOLDER_LABEL)
        # Log output is often cut mid-path ("…/Desktop/Proj …"); hide partial paths too,
        # down to the user's Desktop/home, which say nothing about the project.
        floor = len(form) - len(form.split(home.strip("/").split("/")[-1], 1)[-1]) + len("/Desktop/")
        for k in range(len(form) - 1, floor, -1):
            if form[:k] + " …" in text:
                text = text.replace(form[:k] + " …", FOLDER_LABEL + " …")
    return text


def read_turns(path: str | Path, start_line: int = 0) -> list[Turn]:
    """Parse turns whose prompt sits after `start_line` (0 = from the top)."""
    path = Path(path)
    turns: list[Turn] = []
    tool_names: dict[str, str] = {}
    current: Turn | None = None
    count = 0

    with path.open(errors="ignore") as fh:
        for lineno, raw in enumerate(fh, start=1):
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("isSidechain"):
                continue

            if _is_real_prompt(rec):
                count += 1
                if current:
                    turns.append(current)
                current = None
                if lineno > start_line:
                    current = Turn(
                        session_id=rec.get("sessionId", path.stem),
                        index=count,
                        start_line=lineno,
                        end_line=lineno,
                        timestamp=rec.get("timestamp", ""),
                        cwd=rec.get("cwd", ""),
                        prompt=redact(_cut(_text_of(rec["message"]["content"]), MAX_PROMPT)),
                    )
                continue

            if current is None:
                continue
            current.end_line = lineno
            msg = rec.get("message") or {}
            content = msg.get("content")

            if rec.get("type") == "assistant" and isinstance(content, list):
                for b in content:
                    t = b.get("type")
                    if t == "text" and b.get("text", "").strip():
                        current.events.append("[CLAUDE] " + redact(_cut(b["text"], MAX_REPLY)))
                    elif t == "tool_use":
                        tool_names[b.get("id", "")] = b.get("name", "?")
                        current.events.append(
                            f"[TOOL CALL {b.get('name')}] " + redact(_tool_input_summary(b.get("name", ""), b.get("input", {})))
                        )

            elif rec.get("type") == "user":
                text = _text_of(content)
                if text.startswith("[Request interrupted"):
                    current.events.append("[USER INTERRUPTED Claude]")
                elif text.startswith("<task-notification>"):
                    m = re.search(r"<summary>(.*?)</summary>", text, re.S)
                    current.events.append("[BACKGROUND TASK DONE] " + redact(_cut(m.group(1) if m else text, MAX_TOOL_OUTPUT)))
                if isinstance(content, list):
                    for b in content:
                        if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                            continue
                        name = tool_names.get(b.get("tool_use_id", ""), "?")
                        out = _text_of(b.get("content"))
                        low = out.lower()
                        if b.get("is_error") and any(m in low for m in REJECTION_MARKERS):
                            current.events.append(f"[USER REJECTED {name}] " + redact(_cut(out, MAX_ERROR)))
                        elif b.get("is_error"):
                            current.events.append(f"[TOOL ERROR {name}] " + redact(_cut(out, MAX_ERROR)))
                        elif out.strip() and name not in ("Read", "Write", "Edit", "TodoWrite"):
                            current.events.append(f"[TOOL OUTPUT {name}] " + redact(_cut(out, MAX_TOOL_OUTPUT)))

    if current:
        turns.append(current)
    for t in turns:
        t.prompt = hide_folder(t.prompt, t.cwd)
        t.events = [hide_folder(e, t.cwd) for e in t.events]
    return turns
