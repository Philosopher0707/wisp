# Run from a wisp checkout: PYTHONPATH=$PWD python context_skills_probe.py <workspace>  (prints counts only; reads the real HOME unless HOME is overridden)
import json, sys
from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.infra.token_counter import TokenCounter
from wisp.extensions.skills import SkillExtension
ws = sys.argv[1]
core = WispAgentCore(provider=None, config=WispConfig().replace(workspace=ws))
tc = TokenCounter(chars_per_token=3)
block = core._build_skills_block(ws)
print("skills block alone:", tc.count(block), "tokens,", block.count("\n- ") + 1, "skills listed")
prompt = core._build_system_prompt({"id": "s", "workspace": ws, "messages": []})
print("'## Skills' in prompt:", "## Skills" in prompt)
print("omission NOTE in prompt:", "[NOTE: Some sections" in prompt, "| any mention of 'omitted':", "omitted" in prompt.lower())
ext = SkillExtension(ws); ext.start()
tools = ext.tools()
print("skill__ tools offered to the model:", len(tools), "| schema size:", tc.count(json.dumps(tools)), "tokens (sent as tool schemas, not in the prompt text)")
