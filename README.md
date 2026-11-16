# git-for-memory v2

A memory layer for Claude Code that learns from your session logs, keeps every fact versioned in git, and only remembers what you actually said.

This is an improved version of [akg745/git-for-memory](https://github.com/akg745/git-for-memory), built for MIT AI Studio.

**▶ Watch the demo run:** https://amangang3.github.io/gfm-demo-replay/

## What v2 adds to the original

- **No false memories.** Code checks that every fact's evidence quote really appears in the session and records who said it: the user, Claude, or a tool or file. A preference the user didn't state is rejected, so Claude's suggestions and instructions hidden in files don't become "your" rules (`gfm/trust.py`).
- **The right memory for each session.** The project comes from the session's folder, not a model's guess (`gfm/projects.py`). A new session gets only its project's facts plus user-wide ones, within a real size budget and with no file paths (`gfm/inject.py`).
- **Nothing changes memory behind your back.** Edits made directly to fact files are flagged and held as *pending* until you run `gfm approve`, and every step is a git commit. Extractor commits include only their own files.

Also new: `gfm inject | check | pending | approve | reject | correct`, the `GFM_STORE` and `GFM_DISABLED` switches, an end-to-end demo (`python3 demo/run_demo.py`, see [demo/README.md](demo/README.md)), and 22 offline tests.
The early experiments that motivated these changes are written up in [EXPLORATION_NOTES.md](EXPLORATION_NOTES.md).

---

*The original README follows. Its quick start and commands still apply.*

## Original README: git-for-memory

A memory layer for Claude Code that learns from what actually happens in your sessions, keeps facts current when they change, and records every change in git.

Built for the MIT AI Studio class project: one agent (Claude Code), one use case, end to end.

## The problem

Claude Code's built-in memory only keeps what the agent decides to write down. A lot of what matters never gets saved: you correcting it ("no, we deploy to Fly now"), a command that failed and the fix that worked, a tool call you rejected. And when a fact changes, the old version often sits next to the new one with no record of which is current or why.

git-for-memory reads the full session logs Claude Code already keeps on your machine (the "production traces"), not summaries, and after every turn decides what long-term memory should change.

## What it does

```
 Claude Code session log           Claude (Haiku)               git repo of facts
 (~/.claude/projects/*.jsonl)       decides what changed          (memory/)
 ┌───────────────────────┐        ┌─────────────────────┐       ┌──────────────────────┐
 │ your prompt           │        │ ADD        new fact │       │ facts/f-0003.md      │
 │ Claude's replies      │──────▶ │ UPDATE     replaces │ ────▶ │ one commit per turn, │
 │ tool calls + errors   │  new   │            an old   │       │ linked to the exact  │
 │ calls you rejected    │  turn  │ INVALIDATE no longer│       │ log line it came from│
 └───────────────────────┘        │            true     │       └──────────┬───────────┘
                                  │ (or nothing)        │                  │
                                  └─────────────────────┘                  │
             new session starts ◀──── current facts loaded as context ◀────┘
```

1. **Read.** Each turn (your prompt plus everything Claude did next) is pulled from the session log. Screenshots and file dumps are trimmed. Secrets (API keys, tokens, passwords) are masked, and your local folder paths are replaced with a label.
2. **Decide.** Claude Haiku sees the turn plus the existing facts most related to it, and returns changes: add, update, invalidate, or nothing (the usual answer). Each fact gets a type, a scope, a confidence score and a short quote from the log as evidence.
3. **Store.** Every fact is a small text file in a local git repo. Updates never delete: the old version is marked superseded and linked to the new one. Each turn's changes are one commit pointing back to the session and line they came from, so `git log` is the history of what the agent believes.
4. **Use.** With the hooks on, this runs automatically after every Claude reply, and each new session starts with your current facts loaded.

### What a fact looks like

```
---
id: "f-0003"
subject: "Taskly backend stack"
type: "project_fact"          # preference | project_fact | environment | lesson
scope: "project"              # private | project | shared (for sharing between agents later)
project: "taskly"
status: "active"              # active | superseded | invalidated
confidence: 0.95
supersedes: "f-0001"          # the older version this replaced
valid_from: "2026-10-08T14:00:00Z"
source: "demo-session#L5"     # session id + line in the log
evidence: "last week we moved deployment off EC2 to Fly.io"
---
Taskly's backend is FastAPI on Python 3.12, deployed to Fly.io (moved off AWS EC2).
```

## Requirements

- macOS or Linux (Windows isn't supported yet)
- Python 3.10 or newer, standard library only, nothing to `pip install`
- git
- [Claude Code](https://claude.com/claude-code), logged in. The extractor calls `claude -p` in headless mode with your existing login, so no API key is needed.

Check your login works first:

```bash
claude auth status
```

If later calls fail with "OAuth access token is invalid", run `claude auth login` once in a terminal.

## Quick start (about 5 minutes)

```bash
git clone https://github.com/amangang3/git-for-memory-v2.git
cd git-for-memory-v2
```

**1. Run the offline tests.** No Claude calls, no cost.

```bash
python3 -m unittest -v
```

**2. Replay the sample session.** `examples/demo-session.jsonl` is a short made-up session about a todo app, Taskly. Halfway through, the user says deployment moved from EC2 to Fly.io, a deploy fails and gets fixed, and the user rejects an edit. This makes 3 small Claude calls (about $0.05).

```bash
python3 -m gfm --store demo-memory replay examples/demo-session.jsonl
```

Expected output (wording varies a little between runs):

```
== demo-ses: 4 new turn(s) after line 0
   turn   1: add f-0001, add f-0002          'We're building Taskly, a small todo app...'
   turn   2: update f-0003, add f-0004       'Heads up: last week we moved deployment off EC2 to Fly.io...'
   turn   3: add f-0005                      'Can you also tidy up the comments in deploy.sh?'
   turn   4: skipped (too short to hold a fact)
```

Turn 2 is the interesting one: it **updates** the EC2 fact instead of adding a contradicting one.

**3. Look at the result.**

```bash
python3 -m gfm --store demo-memory facts --all     # every fact, including replaced versions
python3 -m gfm --store demo-memory history f-0003  # how one fact came to be, with evidence
git -C demo-memory log --stat                      # the raw git history
python3 -m gfm --store demo-memory view            # builds view-demo-memory.html
open view-demo-memory.html                         # macOS; use xdg-open on Linux
```

The viewer page has two tabs: a **timeline** of every change (what you said, then what was added, updated or invalidated, with the old wording crossed out), and **current memory** with filters by project, type and scope.

## Try it on your own sessions

List your recent Claude Code sessions:

```bash
python3 -m gfm sessions
```

Replay one into your own store (the default store is `memory/` in this folder):

```bash
python3 -m gfm replay ~/.claude/projects/<folder>/<session-id>.jsonl
python3 -m gfm view && open view-memory.html
```

Replaying is incremental: run it again later and only new turns are read. Use `--dry-run` to see what would be sent to Claude without calling it, and `--max-turns 5` to try a few turns first.

## Turn on automatic mode (hooks)

```bash
python3 -m gfm hook install
```

This adds two hooks to your user-level Claude Code settings (`~/.claude/settings.json`, backed up first as `settings.json.gfm-backup`). They apply to **every** Claude Code session on your machine:

- **After each Claude reply (Stop hook):** hands the session to a background job and returns in well under a second, so your chats never wait. The job reads only the new turns, updates `memory/`, and rebuilds `view-memory.html`. Results go to `memory/runs/hooks.log`.
- **When a session starts (SessionStart hook):** your current facts, grouped by project, are added to the new session's context (capped at about 2k tokens and shared fairly across projects).

The hooks load when a session starts, so test them in a new session:

1. In a new Claude Code chat, say something worth remembering, for example: "For AI Studio, our demo day is December 9."
2. About 30 seconds after Claude replies, check `tail -3 memory/runs/hooks.log`.
3. Say "Demo day moved to December 11." Rebuild the viewer: it shows an **Updated** fact with December 9 crossed out.
4. Open another new chat and ask "When is our AI Studio demo day?" It should answer from memory.

Pause without touching settings, resume, or remove completely:

```bash
touch PAUSED                     # pause
rm PAUSED                        # resume
python3 -m gfm hook uninstall    # remove the hooks
```

If you move this folder, run `python3 -m gfm hook install` again so the hooks point at the new location.

## Commands

| Command | What it does |
|---|---|
| `replay <log.jsonl>...` | Read new turns from session logs and update memory. `--dry-run`, `--max-turns N`, `--from-start`, `--model` |
| `sessions [--limit N]` | List recent Claude Code sessions on this machine |
| `facts [--all] [--project P]` | List current facts (or every version with `--all`) |
| `history <fact-id>` | Every version of a fact, with evidence and source |
| `log` | Memory commits |
| `view [--out FILE]` | Build the HTML viewer |
| `hook install` / `hook uninstall` | Turn automatic mode on or off |

Add `--store <folder>` before the command to use a different memory folder (for example `--store demo-memory`).

## Cost

The extractor uses Claude Haiku 5.5 through your Claude Code login, so on a Pro or Max plan it draws on your plan's usage rather than a bill. The CLI's list-price estimates:

- About **$0.02 to $0.07 per turn** read, depending on turn length. A busy coding turn is at the top of that range.
- Very short turns ("ok", "thanks") are skipped without calling Claude.
- Only the facts related to a turn are sent, so cost doesn't grow with the size of memory.

Two settings keep this low (both in `gfm/llm.py`): connected MCP servers and skills are turned off for extraction calls (they otherwise add about 119k tokens per call), and Claude writes its JSON reply directly instead of using the CLI's JSON mode, which costs about twice as much.

## Privacy

- Your memory store (`memory/`), viewer pages and run logs stay on your machine. They are git-ignored here, and the store is its own local git repo with no remote.
- What leaves your machine: the cleaned text of each turn and the related facts, sent to Claude through your own Claude Code login.
- Secret masking is pattern-based and best effort. Don't rely on it for highly sensitive sessions; use `touch PAUSED` while you work on those.

## Project layout

```
gfm/
  traces.py    read Claude Code session logs into turns
  redact.py    mask secrets before anything leaves the reader
  extract.py   the extraction prompt, output schema, and related-fact selection
  llm.py       calls headless Claude Code (claude -p)
  store.py     facts as files in a local git repo; supersede links; commits
  viewer.py    the HTML timeline and fact browser
  hooks.py     Stop / SessionStart hooks, background worker, install/uninstall
examples/
  demo-session.jsonl   sample session for testing
tests/
  test_offline.py      tests that need no Claude calls
```

## Where this fits in the team project

This repo is the Claude Code workstream: proof that memory can be pulled from real session traces and kept current as facts change. The fact file format is the hand-off point:

- **Agents writing memory** can write facts in the same format to their own store.
- **The memory control layer** can use the `scope` field (private, project, shared) and `project` to decide which facts each agent is allowed to see.

## Known limits and next steps

- **No cleanup pass yet.** Overlapping facts that slip through (for example two near-identical preferences) aren't merged automatically.
- **No formal comparison** against Claude Code's built-in memory yet (recall of facts, stale-fact rate, cost per turn).
- **Project detection** is done by Claude per fact, not from the folder a session ran in, so the same project can occasionally get two slightly different names.
- **macOS and Linux only** (the background worker uses a Unix file lock).
