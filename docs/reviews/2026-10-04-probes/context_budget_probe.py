# Run from a wisp checkout: HOME=<empty dir> PYTHONPATH=$PWD python context_budget_probe.py  (pure function, no files touched)
from wisp.context_assembler import ContextAssembler
a = ContextAssembler()
budget = 1000
est = a._estimate_tokens
sysmsg = "You are Wisp, a careful coding agent. " * 55        # a large critical section
memory = "remember: the user prefers tabs. " * 400            # over budget on its own
project = "project note about the build system. " * 24         # priority 3, fits only if room is real
print("tokens: system", est(sysmsg), "| memory", est(memory), "| project", est(project))
text, used = a._fit_sections([("default_system", 0, sysmsg), ("memory_block", 2, memory), ("project_context", 3, project)], budget)
print(f"budget={budget} measured={used} ratio={used/budget:.2f}")
print("project_context admitted AFTER memory was truncated:", "build system" in text)
