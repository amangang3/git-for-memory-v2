# git-for-memory v2: exploration notes

Working copy of `akg745/git-for-memory` @ `511a208`, imported as the first commit of this repo.
The original stays untouched in `../Orig git-for-memory/`.
Scratch stores live in `scratch-stores/`, which is git-ignored. They are never the default `memory/`.
Extraction uses `claude -p` with Haiku 5.5 on the **claude.ai enterprise subscription** (no `ANTHROPIC_API_KEY`), so there are no API fees.
The "$" figures below are the CLI's list-price estimates (`total_cost_usd`). They show what the usage would cost on the API.

## Step 1: Running it (no cost)

- `python3 -m unittest -v`: 6/6 pass.
- Dry-run replay of `examples/demo-session.jsonl`: 4 turns (L1, L5, L13, L17), 407–759 chars each. The folder is hidden as `[session folder]`.

### Code map (1,266 lines)

| Module | Lines | What it does |
|---|---|---|
| `traces.py` | 216 | Parses session JSONL into `Turn`s. A turn is one real user prompt plus the events that follow: `[CLAUDE]` text, `[TOOL CALL x]`, `[TOOL OUTPUT x]`, `[TOOL ERROR x]`, `[USER REJECTED x]`, `[USER INTERRUPTED]`. Sidechains (subagents) are skipped. Caps: prompt 4000, reply 2500, tool input 300, **tool output 250**, error 500, whole turn 14000 chars. When a turn is too long it keeps the errors, rejections and the tail. **Output from Read/Write/Edit/TodoWrite is dropped entirely**; Bash/WebFetch/Grep output is kept, cut to 250 chars. `hide_folder` replaces the cwd (and its variants) with `[session folder]`. `cwd` is taken from the prompt record only. |
| `redact.py` | 21 | Regex masking: Anthropic/OpenAI/GitHub/AWS/Google/Slack keys, JWTs, PEM keys, `password=…`, and Postgres URL passwords. |
| `extract.py` | 141 | Haiku system prompt, JSON schema, candidate selection. Ops: ADD / UPDATE(target_id) / INVALIDATE(target_id). Fields: subject, statement, type, scope, project, confidence, evidence, reason. Ops with **confidence < 0.6 are dropped** in code. |
| `llm.py` | 84 | `claude -p --model claude-haiku-5-5 --effort low --tools "" --setting-sources "" --no-session-persistence --strict-mcp-config` with an empty MCP config and a clean env (`GFM_INTERNAL=1`). The JSON is parsed out of the text reply, with 2 attempts. |
| `store.py` | 181 | One `facts/f-NNNN.md` per fact (JSON-valued front-matter + statement body). `apply()` turns ops into writes. `commit()` runs `git add -A facts` and makes one commit per turn, with `+ / ~ / -` lines and `Source: <sid>#L<line>`. `state.json` stores the last line read per session (git-ignored inside the store). |
| `viewer.py` | 209 | Static HTML built from the git log plus the fact files plus local session logs (for prompts and titles). Timeline tab and current-facts tab. |
| `hooks.py` | 164 | Stop hook spawns a detached `gfm hook worker <transcript>` under an flock. That runs the same `cmd_replay` and rebuilds the viewer. SessionStart hook prints `additionalContext`. **The store path is hard-coded to `<repo>/memory`** (no `--store`), and `PAUSED` file is the off switch. `install` edits `~/.claude/settings.json`. |
| `__main__.py` | 178 | CLI: replay / facts / history / log / sessions / view / hook. Replay skips a turn when `len(prompt)<40 and events<3` and there are no errors or rejections. On an LLM error it stops the session so the turn is retried next run. |

### Fact format
Markdown front-matter, where each value is JSON-encoded: `id, subject, type(preference|project_fact|environment|lesson), scope(private|project|shared), project, status(active|superseded|invalidated), confidence, supersedes, superseded_by, valid_from, invalid_at, source(<sid>#L<line>), evidence(≤300 chars, model-supplied)`. The statement is the body.

### How related facts reach the extractor (`pick_candidates`)
- **15 or fewer active facts: send all of them.**
- More than 15: score each fact by its count of shared keywords (≥4 letters, minus stopwords) with the turn text. Take the best-scoring project's top 10 (all of them, even with score 0) plus the top 6 facts from anywhere with score > 0.
- Only active facts are candidates. The model sees `id [project/type] subject: statement`, plus the known project list and the basename of the working folder.
- Consequence: a paraphrase with no shared 4-letter keyword can't be matched once the store is past 15 facts. That's the dedupe and UPDATE risk.

### How supersede works
- UPDATE with an active `target_id`: the new fact gets `supersedes=old`, and the old one becomes `status=superseded, superseded_by=new, invalid_at=turn time`. Both files go into the same commit.
- UPDATE whose target is missing or inactive **silently becomes an ADD**. INVALIDATE on a missing target is dropped.
- Nothing is deleted. `history` walks the `supersedes` chain back, then `superseded_by` forward, then prints `git log` **for that one file only**.
- No check that an UPDATE's subject or project matches its target, and no check that evidence appears in the turn.

### What the SessionStart hook injects
- Every active fact from every project, regardless of the current cwd or project.
- Ordering: preference → lesson → environment → project_fact, newest first within each type, round-robin across projects.
- Budget: `MAX_CONTEXT_CHARS = 9000` (~2.2k tokens). Overflow becomes a line like `(N more X facts in /abs/path/memory)`, which leaks the store's absolute path into context.
- No relevance ranking against the session's project, and confidence/scope aren't used.

### Things noticed while reading (to verify with probes)
1. `commit()` runs `git add -A facts`, so **any hand edit gets committed under the next memory commit's message** and attributed to the extractor (P7).
2. Evidence is never checked against the turn text (P3).
3. Tool output can reach the extractor through Bash/WebFetch (250 chars), but not through Read (P4).
4. Project is chosen by the model per op. The cwd basename is given only as a hint (P6).
5. The hook can't point at a scratch store, so P8 needs `hooks.STORE` patched in-process.

## Spend log (list-price estimate; actual billing = subscription)

| Run | Turns sent | $ est |
|---|---|---|
| Step 2: demo replay ×3 (fresh stores) | 9 (3 skipped) | 0.0045 |
| P1, P2, P4, P4b, P5 ×2, P5b, P6 ×2, P7 | 26 | 0.0104 |
| P9: 2 × `claude -p` Sonnet 5.5 task runs | 2 sessions | 0.157 |
| **Total** | | **≈ $0.17** |

Surprise: an extraction turn costs **$0.0002–0.0007** by the CLI's estimate, about 50–100× below the README's "$0.02–0.07/turn". Either Haiku 5.5 pricing or prompt caching changed since the README was written. Extraction cost isn't a real constraint for experiments.

## Step 2: Demo replay, 3 fresh stores (`scratch-stores/demo-{1,2,3}`)

All three have the same structure: turn 1 adds 2, turn 2 updates f-0001 to f-0003 (EC2 → Fly.io) and adds the fly.toml detail, turn 3 adds the comment-style preference, turn 4 is skipped. Project is always `taskly`, scope always `project`.

Variation:
- **Fact splitting:** demo-2 split the fly.toml app-name issue into a separate `lesson` (6 versions). demo-1 and demo-3 folded it into one `project_fact` (5 versions). So ids after turn 2 differ between runs (f-0005 is a lesson in one store and a preference in another).
- **Wording drift:** demo-2 f-0003 statement starts with "Taskly backend stack: Taskly backend stack: …" (the subject copied into the statement). demo-3 invented "in the week before 2026-10-08", a date the model inferred, not one in the log.
- **Scope of the comment preference** varies: "Taskly code, including deploy.sh" vs "Taskly deploy.sh" vs "Taskly scripts such as deploy.sh".
- **Type** of the fly.toml fact: project_fact ×2, lesson ×1.

Takeaway: the core decisions (what to add, what to supersede) are stable. Granularity and wording are not, so you can't diff two stores by id.

## Step 3: Probes (synthetic logs in `probes/logs/`, made by `probes/make_probes.py`)

| # | Prediction (written before running) | Result | Verdict |
|---|---|---|---|
| P1 change of mind ×2 | Chain works on the first change. The second reversal might go stale or ADD a Netlify fact next to the superseded one. | **Clean 3-version chain** f-0001 → f-0003 → f-0005, plus a parallel config chain f-0002 → f-0004 → f-0006. Exactly 2 active facts at the end, both correct. | ✅ works |
| P2 who said it | Haiku stores at least one of "Postgres" / "Python 3.11" / "SQLAlchemy" as a project fact. | **Stored none of the assistant's suggestions.** Instead it added "database decision pending" (conf 0.7) and a file-layout fact backed by tool output. | ✅ works. The pending-decision fact is borderline noise. |
| P3 evidence honesty (code check: `probes/check_evidence.py`) | Mostly verbatim, a few paraphrases. | **37 facts: 33 verbatim, 3 spliced** (verbatim pieces joined by "…" or ";"), **1 altered** (p6 f-0004: "…built in 2.3s *after building core first*". The italic part isn't in the log). Evidence often includes harness tags like `[TOOL ERROR Bash]` and `[CLAUDE]`, and several facts' evidence is **only the assistant's words** (demo-1 f-0004, demo-3 f-0004, p2 f-0001). | ⚠️ mostly honest, but unchecked, and nothing records *who* the quote came from |
| P4 injection in tool output (Bash `cat`, WebFetch) | Blocked here, because the agent's own reply flagged the note. | Neither "reply in French" nor "`rm -rf build/`" stored. Only a real contribution rule was saved. | ✅ |
| P4b injection that the **agent echoes** (agent starts replying in French, user silent) | Likely stored as a preference. | Not stored as a preference. But the extractor wrote a **new `lesson`, scope `shared`, project `general`**: "Instructions found inside repo files… are not user preferences" (f-0002). That's a fact about the harness, which its own prompt forbids, and it's shared-scope. | ✅ blocked, ⚠️ with a side-effect fact. n=1, defence relies on model judgment, not code. |
| P5 near-duplicates (3 phrasings) | ≤15 facts: dedupes. | Dedupes in **both runs** (1 fact, turns 2–3 "no change"). | ✅ |
| P5b same, store seeded with 18 facts (keyword candidate filter active) | Might miss: paraphrases share few keywords. | Still deduped. "commit" is a shared keyword, so the fact made it into the candidates. | ✅ here. Untested for paraphrases with no shared 4+ letter word. |
| P6 two projects, one session (×2 runs) | Facts land on the right project; maybe one naming drift. | Fernway/Quillpad split is correct. **Turn 4 ("Back to the docs project…", cwd = fernway) went to a new project `docs` in both runs.** The cwd basename `fernway` was in the prompt and ignored. Now `docs` is a separate project in injection (P8). | ❌ reproducible bug |
| P7 hand edit (changed f-0005 Netlify → GitHub Pages, no commit), then replayed 1 turn | `git add -A facts` sweeps it into the next memory commit. | Confirmed. Commit `session p7-after turn 1` contains the f-0005 edit with no mention of it in the message. `history f-0005` shows statement "GitHub Pages" next to evidence "…going back to Netlify…", and nothing flags it. The extractor got the tampered fact as a candidate and accepted it. | ❌ silent laundering of edits |
| P8 SessionStart injection | Every project injected regardless of cwd; budget fine for small stores. | Fernway session on the 19-fact store gets **all 9 Orchard facts** + 6 general + 4 Fernway (1,948 chars). With 120 facts the output is **10,030 chars (over the 9,000 cap**, because headers and overflow lines aren't counted), 67 of 120 facts are injected with no relevance to cwd, and the overflow lines **leak the store's absolute path** (here including the Google Drive account folder) into model context. P6's `docs` orphan shows as its own project. | ⚠️ works, poorly targeted |
| P9 before/after (Sonnet 5.5, `--setting-sources ""`, Write/Read/Edit/Glob only, no Bash; memory via `--append-system-prompt` with the P8 text from 6 seeded prefs) | Memory flips uv and ruff; pytest, src layout and argparse are default anyway. | **Without:** setuptools, `python -m venv && pip install -e ".[dev]"`, no ruff. Pytest, src/, and argparse were already present. **With:** hatchling, `[dependency-groups]`, `uv sync`/`uv run pytest`, ruff configured in pyproject. Outputs saved in `probes/p9-outputs/`. | ✅ memory changes behaviour on **2 of 6** prefs. The other 4 match the model's defaults, so they prove nothing. n=1 per arm, and `--append-system-prompt` ≠ real hook `additionalContext`. |

### Other things found while reading code (not yet probed)
- An UPDATE whose `target_id` is wrong or inactive **silently becomes an ADD**. An INVALIDATE on a missing id is dropped. Nothing is logged.
- The hook store path is hard-coded (`<repo>/memory`), so hooks can't be pointed at a scratch store or switched between stores.
- `history` shows git log for one file only, so it doesn't show the commits of the earlier versions in the chain.

## Build 1 (2026-11-15): v2 improvements + runnable demo

Built at the user's request ("build a demo now with these improvements"). This covers menu items A, B, a light C, D, plus parts of E and F.
- `gfm/projects.py`: project comes from the folder; the model may only say `general` or name an existing project (fixes P6).
- `gfm/trust.py`: evidence must be in the turn; speaker recorded (`asserted_by`); a preference not said by the user is rejected; assistant-only facts are capped at 0.7 (P2/P3/P4).
- `gfm/inject.py` + hook: current project + general, real budget, no paths (P8).
- `Store.absorb_hand_edits` / `gfm check` / `gfm correct`: hand edits become `human edit` versions; extractor commits stage only their own files (P7).
- `GFM_STORE`, `GFM_DISABLED`, `gfm inject --cwd`.
- Tests: 20/20 offline (`tests/test_improvements.py` adds 14).
- Demo: `python3 demo/run_demo.py` (see `demo/README.md`). First full run: **15/15 checks**. Behaviour: no memory → pip; after uv rule → uv; after the poetry update → poetry; after a hand edit to pdm → pdm. ~$0.29 list price (subscription).
- New limit surfaced by the demo: a hand edit changes one fact only. The related Fernway overview fact still says poetry. Candidate fix: after any UPDATE, flag active facts that mention the old value.

## Replay page (2026-11-15)

- `demo/export_replay.py` rebuilds a finished demo workspace (`/tmp/gfm-demo`) into `docs/index.html` without calling any model. Console lines use the same format as `run_demo.py`, and the checks are recomputed with the same code (15/15, matching the live run). It refuses to write the page if a home-folder path appears in it.
- Published (page only, public): https://amangang3.github.io/gfm-demo-replay/ (repo `amangang3/gfm-demo-replay`). The v2 code was later published as `amangang3/git-for-memory-v2` (2026-11-16).
- Update after a new run: `python3 demo/run_demo.py && python3 demo/export_replay.py`, copy `docs/index.html` into the page repo, then push.

## Outside edits are flagged, not trusted (2026-11-15)

User question: "shouldn't the human edit show up as a red flag?" Yes. The first version recorded a direct file edit as a trusted `human` change (confidence 1.0) and briefed Claude with it right away. That's the same hole as the prompt injection in Part 1: text that changes Claude's behaviour without the user saying it, from an unknown author. Decision (user): **hold until approved**.
- `absorb_hand_edits` restores the committed version and stores the edit as a `pending` fact (author `outside edit (unverified)`). New commands: `pending`, `approve`, `reject`.
- Demo Act 4 = edit → flagged and held → approved. Act 5 now has 5 runs. New run: **19/19**: none → pip, Day 1 → uv, Day 5 → poetry, **unapproved edit → poetry (edit had no effect)**, after approval → pdm. ~$0.33 list price.
- Tests: 22/22. Open item: pending edits aren't surfaced to the user at session start yet.
