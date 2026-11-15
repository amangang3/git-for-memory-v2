"""Build a single local HTML page to browse memory: a timeline of every change, and the current facts.

Everything comes from the store itself (fact files + git commits) and the local
session logs (for your prompt and the session title). Nothing is uploaded.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .redact import redact
from .store import Store
from .traces import find_log, hide_folder

_OP_LINE = re.compile(r"^([+~?x-]) (f-\d+)(?: -> (f-\d+))? \[")


def _commits(store: Store) -> list[dict]:
    raw = store._git("log", "--reverse", "--format=%h%x1f%B%x1e")
    out = []
    for chunk in raw.split("\x1e"):
        if "\x1f" not in chunk:
            continue
        sha, body = chunk.strip().split("\x1f", 1)
        lines = body.strip().splitlines()
        m = re.search(r"\(session (\w+) turn (\d+)\)", lines[0] if lines else "")
        src = re.search(r"^Source: (\S+)", body, re.M)
        if not (m and src):
            continue
        changes, last = [], None
        for ln in lines[1:]:
            om = _OP_LINE.match(ln)
            if om:
                kind = {"+": "added", "~": "updated", "-": "invalidated", "?": "pending", "x": "rejected"}[om.group(1)]
                last = {"kind": kind, "id": om.group(3) or om.group(2), "old": om.group(2) if om.group(3) else None, "why": ""}
                changes.append(last)
            elif ln.strip().startswith("why:") and last:
                last["why"] = ln.strip()[4:].strip()
        out.append({"sha": sha, "session": m.group(1), "turn": int(m.group(2)), "source": src.group(1), "changes": changes})
    return out


def _session_info(session_id: str, cache: dict, known: dict | None = None) -> dict:
    """Title and per-line prompts for a session, read from its local log."""
    if session_id in cache:
        return cache[session_id]
    info = {"title": "", "lines": {}, "cwd": ""}
    path = find_log(session_id, known)
    if path:
        with path.open(errors="ignore") as fh:
            for n, raw in enumerate(fh, start=1):
                if '"custom-title"' in raw or '"type":"user"' in raw:
                    try:
                        d = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") == "custom-title":
                        info["title"] = d.get("customTitle") or info["title"]
                    elif d.get("type") == "user":
                        info["cwd"] = info["cwd"] or d.get("cwd", "")
                        c = d.get("message", {}).get("content")
                        if isinstance(c, list):
                            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                        if isinstance(c, str):
                            info["lines"][n] = c
    cache[session_id] = info
    return info


def build(store: Store, out: Path) -> Path:
    facts = {f.id: f for f in store.all()}
    cache: dict = {}
    known = store.state().get("_logs")
    turns = []
    for c in _commits(store):
        sid, _, line = c["source"].partition("#L")
        info = _session_info(sid, cache, known)
        if sid == "human":
            info = {"title": "Changes made outside sessions (outside edits, approvals)", "lines": {}, "cwd": ""}
        prompt = info["lines"].get(int(line or 0), "")
        prompt = redact(hide_folder(prompt, info["cwd"])).strip()
        first = facts.get(c["changes"][0]["id"]) if c["changes"] else None
        turns.append({
            "session": sid[:8],
            "title": info["title"] or f"Session {sid[:8]}",
            "turn": c["turn"],
            "time": first.valid_from if first else "",
            "prompt": prompt[:400] + (" …" if len(prompt) > 400 else ""),
            "commit": c["sha"],
            "changes": c["changes"],
        })
    turns.sort(key=lambda t: t["time"])

    fact_rows = []
    for f in facts.values():
        sid, _, line = f.source.partition("#L")
        info = _session_info(sid, cache, known)
        fact_rows.append({
            "id": f.id, "subject": f.subject, "statement": f.statement, "type": f.type, "scope": f.scope,
            "project": f.project, "status": f.status, "confidence": f.confidence, "supersedes": f.supersedes,
            "superseded_by": f.superseded_by, "valid_from": f.valid_from, "evidence": f.evidence,
            "session": sid[:8], "title": "an outside edit or approval" if sid == "human" else info["title"] or f"Session {sid[:8]}",
            "asserted_by": f.asserted_by,
        })

    data = json.dumps({"facts": fact_rows, "turns": turns, "store": store.root.name}, ensure_ascii=False)
    page = TEMPLATE.replace("/*DATA*/null", data.replace("</", "<\\/")).replace("{{STORE}}", html.escape(store.root.name))
    out.write_text(page)
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Memory Viewer</title>
<style>
:root{--bg:#f7f6f3;--panel:#fff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;--soft:#efede8;
--add:#1f7a4d;--add-bg:#e5f3ec;--upd:#9a5b00;--upd-bg:#fbf0dc;--inv:#a8322d;--inv-bg:#f8e4e2;--accent:#2f5bd3;--accent-bg:#e8eefc}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--panel:#1f1f1d;--ink:#ecebe6;--muted:#9b9a94;--line:#33322f;--soft:#2a2927;
--add:#5cc792;--add-bg:#173326;--upd:#e3a94a;--upd-bg:#36290f;--inv:#ef7d76;--inv-bg:#3a1b19;--accent:#8aa8ff;--accent-bg:#1d2747}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{padding:28px 24px 8px;max-width:1080px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);font-size:14px}
.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:18px 0 6px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.stat b{display:block;font-size:22px}.stat span{color:var(--muted);font-size:13px;line-height:1.3;display:block}
nav{max-width:1080px;margin:0 auto;padding:10px 24px;box-sizing:border-box;display:flex;gap:6px;position:sticky;top:0;background:var(--bg);z-index:2;border-bottom:1px solid var(--line)}
nav button{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:7px 14px;border-radius:999px;cursor:pointer;font:inherit;font-size:14px}
nav button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
main{max-width:1080px;margin:0 auto;padding:16px 24px 60px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
select,input{font:inherit;font-size:14px;height:36px;padding:0 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink)}
input{flex:1;min-width:180px}
.session{margin:26px 0 8px;font-weight:600;font-size:16px}.session small{color:var(--muted);font-weight:400;margin-left:6px}
.turn{display:grid;grid-template-columns:110px 1fr;gap:14px;padding:14px 0;border-top:1px solid var(--line)}
.when{color:var(--muted);font-size:13px}.when b{display:block;color:var(--ink);font-size:14px}
.prompt{background:var(--soft);border-radius:8px;padding:8px 12px;font-size:14px;margin-bottom:10px;white-space:pre-wrap;word-break:break-word}
.prompt:before{content:"You said";display:block;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:2px}
.change{border-left:3px solid var(--line);padding:4px 0 6px 12px;margin:8px 0}
.change.added{border-color:var(--add)}.change.updated{border-color:var(--upd)}.change.invalidated{border-color:var(--inv)}
.badge{display:inline-block;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;padding:1px 7px;border-radius:5px;margin-right:6px}
.added .badge,.badge.added{background:var(--add-bg);color:var(--add)}.updated .badge,.badge.updated{background:var(--upd-bg);color:var(--upd)}
.invalidated .badge,.badge.invalidated{background:var(--inv-bg);color:var(--inv)}
.change.pending,.change.rejected{border-color:var(--inv)}.pending .badge,.rejected .badge{background:var(--inv);color:#fff}
.flag{background:var(--inv-bg);color:var(--inv);border:1px solid var(--inv);border-radius:10px;padding:10px 14px;margin-bottom:14px}
.subj{font-weight:600}.old{color:var(--muted);text-decoration:line-through;font-size:14px;margin-top:4px}
.why{color:var(--muted);font-size:13px;margin-top:3px}.why:before{content:"Why: ";font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.card{overflow-wrap:anywhere;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;gap:8px}
.card .top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.card .top .subj{flex:1;min-width:0}
.id{color:var(--muted);font-size:12px;font-family:ui-monospace,Menlo,monospace;white-space:nowrap;flex-shrink:0;padding-top:2px}
.tags{display:flex;gap:6px;flex-wrap:wrap}.tag{font-size:12px;padding:2px 8px;border-radius:999px;background:var(--soft);color:var(--muted)}
.tag.scope-private{background:var(--inv-bg);color:var(--inv)}.tag.scope-shared{background:var(--accent-bg);color:var(--accent)}
.conf{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}
.bar{flex:0 0 80px;height:6px;border-radius:3px;background:var(--soft);overflow:hidden}.bar i{display:block;height:100%;background:var(--accent)}
.evidence{font-size:13px;color:var(--muted);border-left:2px solid var(--line);padding-left:10px;font-style:italic;word-break:break-word}
.src{font-size:12px;color:var(--muted)}
details{font-size:13px}summary{cursor:pointer;color:var(--accent)}
.ver{border-left:2px solid var(--line);padding:4px 0 4px 10px;margin:6px 0}.ver.cur{border-color:var(--accent)}
.ver small{color:var(--muted);display:block}
.proj{margin:22px 0 10px;font-size:16px;font-weight:600}.empty{color:var(--muted);padding:30px 0}
@media (max-width:640px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.stat:last-child{grid-column:span 2}.turn{grid-template-columns:1fr;gap:4px}header,nav,main{padding-left:16px;padding-right:16px}}
</style></head><body>
<header><h1>Memory Viewer</h1>
<div class="sub">Store: <b>{{STORE}}</b>. Built from the fact files and git history on this Mac.</div>
<div class="stats" id="stats"></div></header>
<nav><button class="on" data-tab="timeline">Timeline of changes</button><button data-tab="facts">Current memory</button></nav>
<main>
<div class="filters"><select id="fp"><option value="">All projects</option></select>
<select id="ft"><option value="">All types</option><option value="preference">Preference</option><option value="project_fact">Project fact</option><option value="environment">Environment</option><option value="lesson">Lesson</option></select>
<select id="fs"><option value="">All scopes</option><option value="private">Private</option><option value="project">Project</option><option value="shared">Shared</option></select>
<input id="q" placeholder="Search facts"></div>
<div id="view"></div></main>
<script>
const D=/*DATA*/null;
const F=Object.fromEntries(D.facts.map(f=>[f.id,f]));
const esc=s=>(s??"").toString().replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const day=t=>t?new Date(t).toLocaleDateString(undefined,{month:"short",day:"numeric"}):"";
const hm=t=>t?new Date(t).toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"}):"";
let tab="timeline";
const projects=[...new Set(D.facts.map(f=>f.project))].sort();
projects.forEach(p=>fp.insertAdjacentHTML("beforeend",`<option>${esc(p)}</option>`));
const active=D.facts.filter(f=>f.status==="active");
const counts={added:0,updated:0,invalidated:0,pending:0,rejected:0};D.turns.forEach(t=>t.changes.forEach(c=>counts[c.kind]++));
stats.innerHTML=[[active.length,"facts in memory now"],[D.facts.length,"versions kept"],[D.turns.length,"turns that changed memory"],[counts.updated,"updates to existing facts"],[projects.length,"projects"]]
 .map(([n,l])=>`<div class="stat"><b>${n}</b><span>${l}</span></div>`).join("");
function chain(id){let f=F[id];while(f&&f.supersedes&&F[f.supersedes])f=F[f.supersedes];const out=[];while(f){out.push(f);f=f.superseded_by?F[f.superseded_by]:null}return out}
function match(f){const q=document.getElementById("q").value.toLowerCase();
 return(!fp.value||f.project===fp.value)&&(!ft.value||f.type===ft.value)&&(!fs.value||f.scope===fs.value)&&(!q||(f.subject+" "+f.statement+" "+f.evidence).toLowerCase().includes(q))}
function timeline(){let html="",last="";
 for(const t of D.turns){const ch=t.changes.filter(c=>F[c.id]&&match(F[c.id]));if(!ch.length)continue;
  if(t.title!==last){html+=`<div class="session">${esc(t.title)}<small>session ${esc(t.session)}</small></div>`;last=t.title}
  html+=`<div class="turn"><div class="when"><b>Turn ${t.turn}</b>${day(t.time)} ${hm(t.time)}</div><div>`+
  (t.prompt?`<div class="prompt">${esc(t.prompt)}</div>`:"")+
  ch.map(c=>{const f=F[c.id],o=c.old&&F[c.old];return `<div class="change ${c.kind}"><span class="badge">${c.kind}</span><span class="subj">${esc(f.subject)}</span>
   <span class="tag">${esc(f.project)}</span>
   ${o?(c.kind==="pending"?`<div class="why">Still current, edit not applied: ${esc(o.statement)}</div>`:`<div class="old">${esc(o.statement)}</div>`):""}<div>${esc(f.statement)}</div>${c.why?`<div class="why">${esc(c.why)}</div>`:""}</div>`}).join("")+`</div></div>`}
 return html||`<div class="empty">No changes match these filters.</div>`}
function facts(){const pend=D.facts.filter(f=>f.status==="pending");
 const flag=pend.length?`<div class="flag"><b>⚠ ${pend.length} outside edit${pend.length>1?"s":""} waiting for approval, not used by Claude:</b>`+pend.map(f=>`<div>${f.id}: ${esc(f.statement)}${f.supersedes?` (would replace ${f.supersedes})`:""}</div>`).join("")+`</div>`:"";
 const rows=active.filter(match);if(!rows.length)return flag+`<div class="empty">No facts match these filters.</div>`;
 const by={};rows.forEach(f=>(by[f.project]??=[]).push(f));
 return flag+Object.keys(by).sort().map(p=>`<div class="proj">${esc(p)} <span class="sub">(${by[p].length})</span></div><div class="grid">`+by[p].map(f=>{const vs=chain(f.id);
  return `<div class="card"><div class="top"><span class="subj">${esc(f.subject)}</span><span class="id">${f.id}</span></div>
  <div>${esc(f.statement)}</div>
  <div class="tags"><span class="tag">${esc(f.type.replace("_"," "))}</span><span class="tag scope-${f.scope}">${esc(f.scope)} scope</span>${f.asserted_by?`<span class="tag">said by ${esc(f.asserted_by)}</span>`:""}${vs.length>1?`<span class="tag">version ${vs.length}</span>`:""}</div>
  <div class="conf">Confidence <span class="bar"><i style="width:${Math.round(f.confidence*100)}%"></i></span>${Math.round(f.confidence*100)}%</div>
  ${f.evidence?`<div class="evidence">“${esc(f.evidence)}”</div>`:""}
  <div class="src">From ${esc(f.title)}, ${day(f.valid_from)}</div>
  ${vs.length>1?`<details><summary>Show how this changed (${vs.length} versions)</summary>${vs.map((v,i)=>`<div class="ver ${v.id===f.id?"cur":""}"><small>${v.id}, ${day(v.valid_from)} ${hm(v.valid_from)}${v.id===f.id?", current":""}</small>${esc(v.statement)}</div>`).join("")}</details>`:""}
  </div>`}).join("")+`</div>`).join("")}
function render(){view.innerHTML=tab==="timeline"?timeline():facts()}
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{document.querySelectorAll("nav button").forEach(x=>x.classList.toggle("on",x===b));tab=b.dataset.tab;render()});
[fp,ft,fs].forEach(e=>e.onchange=render);q.oninput=render;render();
</script></body></html>"""
