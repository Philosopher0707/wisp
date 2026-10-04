# Run from a wisp checkout: PYTHONPATH=$PWD python context_build_probe.py <workspace>  (prints per-section token counts, never content)
import re, sys, time
from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.infra.token_counter import TokenCounter

ws = sys.argv[1]
cfg = WispConfig().replace(workspace=ws)
core = WispAgentCore(provider=None, config=cfg) if False else None
try:
    from wisp.providers.mock import MockProvider
    prov = MockProvider()
except Exception:
    prov = None
core = WispAgentCore(provider=prov, config=cfg)
t0 = time.perf_counter()
prompt = core._build_system_prompt({"id": "s", "workspace": ws, "messages": []}, query="fix the failing login test")
dt = time.perf_counter() - t0
tc = TokenCounter(chars_per_token=3)
total = tc.count(prompt)
print(f"workspace={ws}\ntotal: {len(prompt)} chars, ~{total} tokens, built in {dt*1000:.0f} ms\n")
# split on '## ' headings (top-level sections)
parts = re.split(r"(?m)^(?=## )", prompt)
rows = []
for p in parts:
    if not p.strip(): continue
    head = p.split("\n", 1)[0][:60] + ("   <<" + p[:90].replace("\n"," | ") + ">>" if len(p.split("\n",1)[0]) < 12 else "")
    rows.append((tc.count(p), head))
from wisp.skills import discover_skills
sk = discover_skills(ws)
print(f"skills discovered: {len(sk)} (model-invocable {sum(1 for x in sk if x.model_invocable)})")
for n, h in rows:
    print(f"{n:6d} tok  {n*100//max(total,1):3d}%  {h}")
