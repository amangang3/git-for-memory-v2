"""P3: does each fact's evidence quote actually appear in the turn it cites?

  PYTHONPATH=. python3 probes/check_evidence.py <store>...

For each fact, re-read the cited session log, rebuild the exact turn text the extractor saw
(Turn.render), and classify the evidence:
  VERBATIM   whole quote appears (whitespace-normalised)
  SPLICED    quote is several verbatim pieces joined by '...', '…' or ';'
  ALTERED    otherwise; best-matching span similarity shown
  SEEDED     hand-seeded fact, skipped
"""
import difflib
import json
import re
import sys
from pathlib import Path

from gfm.store import Store
from gfm.traces import find_log, read_turns


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def classify(ev: str, text: str) -> tuple[str, float]:
    e, t = norm(ev), norm(text)
    if e in t:
        return "VERBATIM", 1.0
    parts = [p.strip(" .") for p in re.split(r"\.\.\.|…|;", e) if p.strip(" .")]
    if len(parts) > 1 and all(p in t for p in parts):
        return "SPLICED", 1.0
    sm = difflib.SequenceMatcher(None, t, e, autojunk=False)
    best = max((b.size for b in sm.get_matching_blocks()), default=0)
    return "ALTERED", round(best / max(len(e), 1), 2)


def main(stores):
    tally = {}
    for sp in stores:
        store = Store(sp)
        known = store.state().get("_logs", {})
        cache = {}
        for f in store.all():
            sid, _, line = f.source.partition("#L")
            if sid == "seed":
                kind, score = "SEEDED", 1.0
            else:
                if sid not in cache:
                    p = find_log(sid, known)
                    cache[sid] = {t.start_line: t.render() for t in read_turns(p)} if p else {}
                text = cache[sid].get(int(line), "")
                kind, score = classify(f.evidence, text) if text else ("NO-LOG", 0)
            tally[kind] = tally.get(kind, 0) + 1
            if kind not in ("VERBATIM", "SEEDED"):
                print(f"{Path(sp).name:>9} {f.id} {kind:<8} longest-verbatim-run={score:.0%}  {f.evidence!r}")
    print("\nTOTAL:", json.dumps(tally))


if __name__ == "__main__":
    main(sys.argv[1:])
