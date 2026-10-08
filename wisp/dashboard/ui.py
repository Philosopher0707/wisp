"""The single page the dashboard serves. Vanilla JS and SVG, no dependency, no network request except to its own /api. Every piece of data reaches the page through
`textContent` or an attribute set by the DOM API, never through `innerHTML`, so nothing in a result or a log line can become markup."""

from __future__ import annotations

_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wisp Dashboard</title>
<style nonce="__NONCE__">
:root{--bg:#f6f7f9;--panel:#fff;--ink:#16181d;--mute:#5d6573;--line:#e1e4ea;--accent:#2457d6;--good:#17803d;--bad:#c0362c;--warn:#a86a00;--info:#2457d6;--track:#e9ecf2;--ci:#9db4f0}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--panel:#171a21;--ink:#e8eaee;--mute:#9aa3b2;--line:#272c36;--accent:#7aa2ff;--good:#52c27a;--bad:#ff7b6e;--warn:#e3b04b;--info:#7aa2ff;--track:#242936;--ci:#35508f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:2}
h1{font-size:16px;margin:0}.chip{font:12px ui-monospace,Menlo,monospace;padding:2px 8px;border:1px solid var(--line);border-radius:99px;color:var(--mute)}
nav{display:flex;gap:4px;flex-wrap:wrap;margin-left:auto}nav button{background:none;border:1px solid transparent;color:var(--mute);padding:6px 10px;border-radius:8px;cursor:pointer;font:inherit}
nav button[aria-selected=true]{color:var(--ink);border-color:var(--line);background:var(--bg)}nav button:focus-visible{outline:2px solid var(--accent)}
main{max-width:1180px;margin:0 auto;padding:16px}section{display:none}section.on{display:block}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}
.card h2{font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--mute);margin:0 0 8px;font-weight:600}.big{font-size:34px;font-weight:650;line-height:1.1}
a{color:var(--accent)}.mute{color:var(--mute)}.small{font-size:12px}.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.alert{display:flex;gap:10px;padding:9px 12px;border-radius:10px;border:1px solid var(--line);background:var(--panel);margin-bottom:8px}.alert b{min-width:64px;text-transform:uppercase;font-size:11px;letter-spacing:.05em;padding-top:2px}
.alert.critical{border-color:var(--bad)}.alert.critical b{color:var(--bad)}.alert.warn b{color:var(--warn)}.alert.info b{color:var(--info)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--mute);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}.wrap{overflow-x:auto}code{font:12px ui-monospace,Menlo,monospace;background:var(--bg);padding:1px 5px;border-radius:5px}
.bar{height:8px;background:var(--track);border-radius:4px;position:relative;min-width:80px}.bar>i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--accent)}
.legend{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}
.tag{font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--line)}.tag.open{color:var(--bad);border-color:var(--bad)}.tag.fixed{color:var(--good);border-color:var(--good)}
.filters{display:flex;gap:6px;margin-bottom:8px}.filters button{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:3px 10px;border-radius:99px;cursor:pointer;font:inherit}.filters button[aria-pressed=true]{border-color:var(--accent);color:var(--accent)}
.note{border-left:3px solid var(--warn);padding:6px 10px;background:var(--panel);margin:10px 0;border-radius:0 8px 8px 0}
svg text{fill:var(--mute);font:11px -apple-system,sans-serif}.err{color:var(--bad)}
</style></head><body>
<header><h1>Wisp Dashboard</h1><span class="chip" id="sha">…</span><span class="chip" id="stamp">loading</span>
<nav role="tablist" id="tabs"></nav></header>
<main id="main"></main>
<script nonce="__NONCE__">
"use strict";
const $=(s,r=document)=>r.querySelector(s);
function h(tag,attrs,...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs||{})){if(v==null||v===false)continue;if(k==="class")e.className=v;else if(k==="text")e.textContent=v;else if(k==="style")e.style.cssText=v;else e.setAttribute(k,v===true?"":String(v));}
 for(const c of kids.flat()){if(c==null||c===false)continue;e.append(c.nodeType?c:document.createTextNode(String(c)));}return e;}
function s(tag,attrs,...kids){const e=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const[k,v]of Object.entries(attrs||{}))e.setAttribute(k,String(v));for(const c of kids.flat()){if(c==null)continue;e.append(c.nodeType?c:document.createTextNode(String(c)));}return e;}
const pct=(x,d=0)=>x==null?"–":(x*100).toFixed(d)+"%";const num=x=>x==null?"–":Number(x).toLocaleString();
const when=t=>t?String(t).replace("T"," ").slice(0,16):"–";const secs=x=>x==null?"–":(x<90?x.toFixed(0)+" s":(x/60).toFixed(1)+" min");
const TABS=[["overview","Overview"],["accuracy","Accuracy"],["models","Models"],["usage","Real use"],["harness","Harness"],["findings","Findings"],["learning","Learning"]];
const data={};let current=(location.hash||"#overview").slice(1);
async function load(name){try{const r=await fetch("/api/"+name,{cache:"no-store"});data[name]=await r.json();}catch(e){data[name]={error:String(e)};}return data[name];}
function errBox(o){return o&&o.error?h("div",{class:"note err",text:"This section could not be computed: "+o.error}):null;}
function card(title,...body){return h("div",{class:"card"},h("h2",{text:title}),...body);}
function table(cols,rows,opts={}){return h("div",{class:"wrap"},h("table",{},h("thead",{},h("tr",{},cols.map(c=>h("th",{class:c.num?"num":"",text:c.h})))),
 h("tbody",{},rows.map(r=>h("tr",{},cols.map(c=>{const v=c.f(r);return h("td",{class:c.num?"num":""},v);}))))));}
function ciBar(sm,w=240){const g=s("svg",{width:w,height:26,viewBox:`0 0 ${w} 26`,role:"img","aria-label":"pass rate with 95% interval"});
 g.append(s("rect",{x:0,y:10,width:w,height:6,rx:3,fill:"var(--track)"}));
 if(sm.pass_rate==null)return g;const x=v=>Math.round(v*(w-2))+1;
 g.append(s("rect",{x:x(sm.ci_low),y:7,width:Math.max(2,x(sm.ci_high)-x(sm.ci_low)),height:12,rx:3,fill:"var(--ci)"}));
 g.append(s("rect",{x:x(sm.pass_rate)-2,y:3,width:4,height:20,rx:2,fill:"var(--accent)"}));
 g.append(s("text",{x:0,y:25},"0%"));g.append(s("text",{x:w-24,y:25},"100%"));return g;}
function bars(entries,{color="var(--accent)",height=90,label=k=>k}={}){const w=Math.max(260,entries.length*34+30);const mx=Math.max(1,...entries.map(e=>e[1]));
 const g=s("svg",{width:w,height:height+22,viewBox:`0 0 ${w} ${height+22}`,role:"img"});
 entries.forEach(([k,v],i)=>{const bh=Math.round(v/mx*height);const x=10+i*34;g.append(s("rect",{x,y:height-bh,width:24,height:bh,rx:3,fill:color},s("title",{},label(k)+": "+v)));
  g.append(s("text",{x:x+12,y:height+14,"text-anchor":"middle"},String(k).slice(5)));});return h("div",{class:"wrap"},g);}
function share(label,v,mx,color){return h("div",{},h("div",{class:"small"},label+"  ",h("span",{class:"mute",text:v})),h("div",{class:"bar"},h("i",{style:`width:${Math.max(1,v/mx*100)}%;background:${color||"var(--accent)"}`})));}

let showAll=false;
function overview(){const o=data.overview||{};const root=h("div",{});const e=errBox(o);if(e)root.append(e);
 const grid=h("div",{class:"grid"});
 if(!(o.headline||[]).length)grid.append(card("Measured accuracy",h("div",{class:"big bad",text:"no number yet"}),h("p",{class:"mute"},"Nothing has been benchmarked, so how well the agent codes is not known. Run, then reload:"),h("code",{text:o.bench_command||""})));
 (o.headline||[]).forEach(g=>{const sm=g.summary;const small=sm.scored<10;grid.append(card("Measured accuracy · "+g.model+" · "+g.label,
  h("div",{class:"big"+(small?" mute":""),text:sm.pass_rate==null?"–":(small?`${sm.solved}/${sm.scored}`:pct(sm.pass_rate))}),
  h("div",{class:"small "+(small?"warn":"mute"),text:small?`pass rate ${pct(sm.pass_rate)} on only ${sm.scored} scored runs: too few to trust`:`${sm.solved} of ${sm.scored} scored runs solved`}),
  h("div",{class:"small mute",text:sm.ci_low!=null?`95% interval ${pct(sm.ci_low)}–${pct(sm.ci_high)}`:""}),
  ciBar(sm),h("div",{class:"small "+(sm.infra_rate>0.3?"warn":"mute"),text:`${sm.infra_attempts} of ${sm.attempts} attempts were infrastructure trouble · over-claims ${sm.overclaims}`}),
  h("div",{class:"small mute",text:g.trust+" · code "+(g.harness_shas||[]).join(", ")})));});
 (o.in_progress||[]).forEach(r=>grid.append(card("Benchmark running",h("div",{class:"big",text:`${r.done}/${r.expected||"?"}`}),
  h("div",{class:"bar"},h("i",{style:`width:${r.expected?Math.min(100,r.done/r.expected*100):0}%`})),h("div",{class:"small mute",text:`${r.model} · ${r.label} · ${r.attempts} attempts so far · started ${when(r.started)}`}))));
 const f=o.findings||{};grid.append(card("Findings",h("div",{class:"big",text:num((f.open||0))}),h("div",{class:"small mute",text:`open · ${f.fixed||0} fixed · ${f.refuted||0} refuted`}),h("a",{href:"#findings",text:"see the register →"})));
 const u=data.usage||{};const tc=u.turn_completion||{};grid.append(card("Turns that ended with “done”",h("div",{class:"big",text:pct(tc.ratio)}),h("div",{class:"small mute",text:`${num(tc.done)} of ${num(tc.user_messages)} user messages · ${num(u.tool_calls)} tool calls`})));
 const ov=data.overhead||{};grid.append(card("Tokens carried by every request",h("div",{class:"big",text:num(ov.total_tokens)}),h("div",{class:"small mute",text:`${num(ov.core_tokens)} core tools · ${num(ov.skill_tool_tokens)} skill tools · ${num(ov.skills_menu_tokens)} skills menu`})));
 const l=data.learning||{};grid.append(card("Does it keep what it learns?",h("div",{class:"big "+(l.facts===0?"bad":""),text:l.facts==null?"–":num(l.facts)}),h("div",{class:"small mute",text:`facts stored · ${l.captured_skills?l.captured_skills.total:0} captured skills · remember ok ${l.remember?l.remember.ok:0}/${l.remember?l.remember.calls:0}`})));
 root.append(grid);
 const al=o.alerts||[];if(al.length){const box=h("div",{style:"margin-top:16px"},h("h2",{class:"small mute",text:`WHAT TO LOOK AT (${al.length})`}));
  const shown=showAll?al:al.slice(0,4);const where={accuracy:"accuracy",usage:"usage",learning:"learning",harness:"harness",overview:"overview"};
  shown.forEach(a=>box.append(h("div",{class:"alert "+a.level},h("b",{text:a.level}),h("div",{},a.text," ",h("a",{href:"#"+(where[a.where]||"overview"),text:"→ "+a.where})))));
  if(al.length>4){const b=h("button",{class:"chip",text:showAll?"show fewer":`show ${al.length-4} more`});b.addEventListener("click",()=>{showAll=!showAll;render();});box.append(b);}root.append(box);}
 root.append(h("p",{class:"small mute",text:`Workspaces scanned: ${(o.workspaces||[]).length}. Read-only; nothing here starts a run or spends a token.`}));return root;}

function accuracy(){const b=data.bench||{};const root=h("div",{});const e=errBox(b);if(e)root.append(e);
 root.append(h("div",{class:"note",text:b.scope_note||""}));
 if(!(b.groups||[]).length){root.append(card("No benchmark data",h("p",{class:"mute"},"Directory: ",h("code",{text:b.dir||""})),h("p",{},"Start one (it spends tokens, so only you do):"),h("code",{text:"python -m wisp.dashboard bench --model <model> --provider <provider> --key-from <ENV_NAME> --label default --repeats 3"})));return root;}
 root.append(card("Pass rate by model and configuration",table([{h:"model · config",f:g=>g.group},{h:"solved/scored",num:1,f:g=>`${g.summary.solved}/${g.summary.scored}`},
  {h:"pass rate",num:1,f:g=>pct(g.summary.pass_rate)},{h:"95% interval",f:g=>ciBar(g.summary,200)},{h:"infra: outcomes / of attempts",num:1,f:g=>`${g.summary.counts.INFRA+g.summary.counts.BROKEN} / ${pct(g.summary.infra_rate)}`},
  {h:"failed / no-op / gamed",num:1,f:g=>`${g.summary.counts.FAILED} / ${g.summary.counts["NO-OP"]} / ${g.summary.counts.GAMED}`},{h:"over / under-claims",num:1,f:g=>`${g.summary.overclaims} / ${g.summary.underclaims}`},
  {h:"median · p90",num:1,f:g=>`${secs(g.summary.median_s)} · ${secs(g.summary.p90_s)}`},{h:"code",f:g=>g.harness_shas.join(", ")},{h:"how far to trust",f:g=>g.trust}],b.groups)));
 (b.comparisons||[]).length&&root.append(card("Configuration against configuration (same model)",table([{h:"model",f:c=>c.model},{h:"A",f:c=>c.a},{h:"B",f:c=>c.b},{h:"A − B",num:1,f:c=>(c.diff*100).toFixed(0)+" pts"},
  {h:"95% interval",num:1,f:c=>`${(c.low*100).toFixed(0)} to ${(c.high*100).toFixed(0)}`},{h:"verdict",f:c=>c.significant?"a real difference":"not distinguishable from noise"}],b.comparisons)));
 const groups=b.groups.map(g=>g.group);const cell=(t,gn)=>{const c=((b.per_task||{})[t]||{})[gn];if(!c)return h("td",{text:"·",class:"mute"});const rate=c.scored?c.solved/c.scored:null;
  const bg=rate==null?"transparent":`color-mix(in srgb, var(--good) ${Math.round(rate*100)}%, var(--bad))`;return h("td",{style:`background:${bg};color:#fff;text-align:center`,title:`${c.solved}/${c.scored} solved, ${c.infra} infra`},rate==null?"infra":`${c.solved}/${c.scored}`);};
 root.append(card("Per task (green solved · red failed · “infra” = never got a fair try)",h("div",{class:"wrap"},h("table",{},h("thead",{},h("tr",{},h("th",{text:"task"}),groups.map(g=>h("th",{text:g})))),
  h("tbody",{},(b.tasks||[]).map(t=>h("tr",{},h("td",{text:t}),groups.map(g=>cell(t,g)))))))));
 const days=Object.entries(b.daily||{});days.length&&root.append(card("Scored runs per day (solved in green)",bars(days.map(([d,v])=>[d,v.scored]),{label:d=>d}),h("div",{class:"small mute",text:days.map(([d,v])=>`${d}: ${v.solved}/${v.scored} solved, ${v.infra} infra`).join(" · ")})));
 root.append(card("Runs",table([{h:"run",f:r=>r.run_id},{h:"model",f:r=>r.model},{h:"config",f:r=>r.label},{h:"code",f:r=>(r.harness.sha||"?")+(r.harness.dirty?" (dirty)":"")},{h:"started",f:r=>when(r.started)},
  {h:"finished",f:r=>r.in_progress?"running…":when(r.finished)},{h:"outcomes",num:1,f:r=>`${r.done}/${r.expected||"?"}`},{h:"attempts",num:1,f:r=>r.attempts}],b.runs)));return root;}

function models(){const u=data.usage||{};const b=data.bench||{};const root=h("div",{});const e=errBox(u);if(e)root.append(e);
 root.append(card("Models seen in real sessions",table([{h:"model",f:m=>m.test_double?h("span",{},m.model," ",h("span",{class:"tag",text:"test double"})):m.model},{h:"sessions",num:1,f:m=>num(m.sessions)},{h:"messages",num:1,f:m=>num(m.messages)},{h:"first",f:m=>when(m.first)},{h:"last",f:m=>when(m.last)}],u.models||[]),
  h("p",{class:"small mute",text:"Per workspace database; a model that only ran in a workspace the dashboard cannot see is missing. Success per model comes from the benchmark, not from here."})));
 const mm={};(b.groups||[]).forEach(g=>{(mm[g.model]=mm[g.model]||[]).push(g);});
 root.append(card("Measured accuracy by model",Object.keys(mm).length?table([{h:"model",f:r=>r[0]},{h:"configurations",f:r=>r[1].map(g=>`${g.label}: ${pct(g.summary.pass_rate)} (${g.summary.solved}/${g.summary.scored})`).join(" · ")}],Object.entries(mm)):h("p",{class:"mute",text:"No benchmark data yet."})));return root;}

function usage(){const u=data.usage||{};const rt=data.runtime||{};const root=h("div",{});const e=errBox(u);if(e)root.append(e);const grid=h("div",{class:"grid"});
 const tools=(u.tools||[]).slice(0,14);const mx=Math.max(1,...tools.map(t=>t.calls));
 grid.append(card("Tools: calls and share not ok",...tools.map(t=>share(`${t.tool}  ${num(t.calls)} calls · ${t.error_rate==null?"–":pct(t.error_rate,1)} not ok`,t.calls,mx,t.error_rate>0.2?"var(--bad)":"var(--accent)")),
  h("p",{class:"small mute",text:"“not ok” is the tool's own status. A shell command that exits non-zero is still an ok tool call."})));
 grid.append(card("Tool calls per day",bars(Object.entries(u.per_day||{}),{label:d=>d})));
 const tc=u.turn_completion||{};grid.append(card("Turns",h("div",{class:"big",text:pct(tc.ratio)}),h("div",{class:"small mute",text:tc.note||""}),h("p",{class:"small",text:`${num(u.tool_calls)} tool calls · about ${u.tool_calls_per_user_message==null?"–":u.tool_calls_per_user_message.toFixed(0)} per user message · ${num(u.sessions)} sessions`})));
 grid.append(card("Background and graph runs",h("div",{},"background: ",h("code",{text:JSON.stringify(u.background_runs||{})})),h("div",{},"graph: ",h("code",{text:JSON.stringify(u.graph_runs||{})})),h("div",{},"decisions: ",h("code",{text:JSON.stringify(u.decisions||{})}))));
 grid.append(card("Session events",h("div",{class:"small"},h("code",{text:JSON.stringify(u.event_types||{})}))));
 root.append(grid);
 root.append(card("Harness events counted in runtime logs",Object.keys(rt.total||{}).length?table([{h:"event",f:r=>r[0]},{h:"count",num:1,f:r=>num(r[1])}],Object.entries(rt.total)):h("p",{class:"mute",text:"none recorded"})));
 root.append(card("Per workspace",table([{h:"workspace",f:w=>w.workspace},{h:"tool calls",num:1,f:w=>num(w.tool_calls)},{h:"sessions",num:1,f:w=>num(w.sessions)},{h:"note",f:w=>w.error||""}],u.workspaces||[])));return root;}

function harness(){const hd=data.harness||{};const ov=data.overhead||{};const root=h("div",{});const e=errBox(hd);if(e)root.append(e);const grid=h("div",{class:"grid"});
 grid.append(card("Checkout",h("div",{},h("code",{text:hd.checkout||""})),h("div",{class:"small mute",text:`${hd.branch} @ ${hd.sha}${hd.dirty?" · uncommitted changes":""}`})));
 const dk=hd.disk||{};grid.append(card("Disk",h("div",{class:"big "+(dk.free_gb!=null&&dk.free_gb<1?"bad":""),text:dk.free_gb==null?"–":dk.free_gb+" GB free"}),h("div",{class:"small mute",text:dk.total_gb?`${dk.used_pct}% of ${dk.total_gb} GB used`:""})));
 grid.append(card("Flags a new session would use",table([{h:"flag",f:r=>r[0]},{h:"value",f:r=>r[1]}],Object.entries(hd.flags||{})),h("p",{class:"small mute",text:hd.flags_note||hd.flags_error||""})));
 const tot=Math.max(1,ov.total_tokens||0);grid.append(card("Tokens carried by every request",h("div",{class:"big",text:num(ov.total_tokens)}),
  ...[["core tools ("+(ov.core_tools||0)+")",ov.core_tokens,"var(--accent)"],["skill tools ("+(ov.skill_tools||0)+")",ov.skill_tool_tokens,"var(--warn)"],["skills menu ("+(ov.skills||0)+")",ov.skills_menu_tokens,"var(--bad)"]].map(r=>share(r[0],num(r[1]),tot,r[2])),
  h("p",{class:"small mute",text:(ov.note||ov.error||"")+" · "+(ov.workspace||"")})));root.append(grid);
 root.append(card("Merged pull requests (from git, newest first)",table([{h:"date",f:m=>m.date},{h:"PR",f:m=>"#"+m.pr},{h:"branch",f:m=>m.branch},{h:"commit",f:m=>m.sha}],hd.merged||[])));return root;}

let filter="all";
function findings(){const f=data.findings||{};const root=h("div",{});const e=errBox(f);if(e)root.append(e);
 const bar=h("div",{class:"filters",role:"group","aria-label":"filter by status"});["all","open","fixed","refuted","n/a"].forEach(k=>{const n=k==="all"?f.total:(f.counts||{})[k]||0;
  const b=h("button",{"aria-pressed":filter===k,text:`${k} (${n||0})`});b.addEventListener("click",()=>{filter=k;render();});bar.append(b);});root.append(bar);
 const rows=(f.items||[]).filter(i=>filter==="all"||i.status===filter);
 root.append(card("Findings registers",table([{h:"id",f:i=>i.id},{h:"status",f:i=>h("span",{class:"tag "+i.status,text:i.status})},{h:"what",f:i=>i.summary},{h:"status text",f:i=>i.status_text},{h:"source",f:i=>i.source}],rows)));return root;}

function learning(){const l=data.learning||{};const root=h("div",{});const e=errBox(l);if(e)root.append(e);const grid=h("div",{class:"grid"});
 grid.append(card("Fact store",h("div",{class:"big "+(l.facts===0?"bad":""),text:l.facts==null?"–":num(l.facts)}),h("div",{class:"small mute",text:`${l.workspace_fact_buckets??"–"} workspace buckets · ${num(l.fact_store_bytes)} bytes · modified ${when(l.fact_store_modified)}`}),
  ...(l.backups||[]).map(b=>h("div",{class:"small"},h("code",{text:b.name})," holds ",h("b",{text:b.facts==null?"?":num(b.facts)})," facts"))));
 const rm=l.remember||{calls:0,ok:0,error:0};grid.append(card("remember tool",h("div",{class:"big "+(rm.calls&&rm.error/rm.calls>0.3?"bad":""),text:rm.calls?pct(rm.ok/rm.calls):"–"}),h("div",{class:"small mute",text:`${rm.ok} ok · ${rm.error} errors of ${rm.calls} calls`})));
 grid.append(card("Session summaries",h("div",{class:"big",text:num(l.session_summaries)}),h("div",{class:"small mute",text:"rows kept (cap 100); each records what was asked, not what was learned"})));
 const cs=l.captured_skills||{total:0,by_day:{},by_workspace:{}};grid.append(card("Auto-captured skills",h("div",{class:"big",text:num(cs.total)}),bars(Object.entries(cs.by_day||{}),{label:d=>d}),
  h("div",{class:"small mute",text:`global skills installed: ${num(l.global_skills)} · whether captured skills are ever used is not measured`})));root.append(grid);return root;}

const VIEWS={overview,accuracy,models,usage,harness,findings,learning};
const NEEDS={overview:["overview","bench","usage","harness","learning","findings","overhead"],accuracy:["bench","harness"],models:["usage","bench","harness"],usage:["usage","runtime","harness"],harness:["harness","overhead"],findings:["findings","harness"],learning:["learning","harness"]};
function render(){const main=$("#main");main.replaceChildren();const sec=h("section",{class:"on"},VIEWS[current]?VIEWS[current]():h("p",{text:"unknown view"}));main.append(sec);
 document.querySelectorAll("#tabs button").forEach(b=>b.setAttribute("aria-selected",b.dataset.k===current));
 const hd=data.harness||{};$("#sha").textContent=(hd.sha||"?")+(hd.dirty?" · dirty":"");$("#stamp").textContent="updated "+new Date().toLocaleTimeString();}
async function refresh(){await Promise.all((NEEDS[current]||[]).map(load));render();}
const tabs=$("#tabs");TABS.forEach(([k,label])=>{const b=h("button",{role:"tab","data-k":k,text:label});b.addEventListener("click",()=>{current=k;location.hash=k;refresh();});tabs.append(b);});
window.addEventListener("hashchange",()=>{const k=location.hash.slice(1);if(VIEWS[k]&&k!==current){current=k;refresh();}});
refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);
</script></body></html>
"""


def render_index(nonce: str) -> str:
    return _PAGE.replace("__NONCE__", nonce)
