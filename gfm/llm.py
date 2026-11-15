"""Call Claude through the locally installed Claude Code CLI (headless mode).

This uses the user's own Claude login, so no API key is needed. The CLI runs
with no tools, no MCP servers, no skills, no settings, and without saving a session log, so extraction
calls never show up as new sessions to extract from.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

DEFAULT_MODEL = os.environ.get("GFM_MODEL", "claude-haiku-5-5")


class LLMError(RuntimeError):
    pass


def _clean_env() -> dict:
    # A CLI launched from inside another Claude Code session inherits that
    # session's auth variables, which the child can't use. Start clean.
    keep = ("HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["GFM_INTERNAL"] = "1"   # our own hooks skip calls made by the extractor
    return env


def _parse_json(text: str):
    """Pull the JSON object out of a reply, tolerating ```json fences or a stray sentence."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    i, j = text.find("{"), text.rfind("}")
    return json.loads(text[i:j + 1]) if i >= 0 and j > i else None


def structured(system: str, user: str, schema: dict, model: str = DEFAULT_MODEL, timeout: int = 240) -> tuple[dict, float]:
    """Return (parsed JSON matching `schema`, cost in USD).

    Claude writes the JSON directly. The CLI's --json-schema mode costs about twice as much:
    it adds its own scaffolding and a second model pass just to format the answer.
    """
    claude = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    system = (system + "\n\nReply with only a JSON object matching this JSON Schema, and no other text:\n"
              + json.dumps(schema, separators=(",", ":")))
    cmd = [
        claude, "-p",
        "--model", model,
        "--effort", "low",
        "--tools", "",
        "--setting-sources", "",
        "--no-session-persistence",
        # Without these, every connected MCP server's tool list rides along
        # (~119k tokens, ~$0.74 per call).
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--system-prompt", system,
        "--output-format", "json",
    ]
    last = ""
    for _ in range(2):
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=timeout, env=_clean_env())
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last = (proc.stdout or proc.stderr)[:500]
            continue
        if out.get("is_error"):
            last = str(out.get("result", ""))[:500]
            if "authenticate" in last.lower():
                raise LLMError("Claude CLI login failed. Run `claude auth login` in a terminal, then retry.\n" + last)
            continue
        try:
            data = _parse_json(out.get("result", ""))
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            last = "reply was not JSON: " + str(out.get("result", ""))[:300]
            continue
        return data, float(out.get("total_cost_usd") or 0)
    raise LLMError(last)
