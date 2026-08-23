"""Local-model benchmark harness.

Measures whether Wisp actually works on local models: a deterministic
task suite driven headless through ``AgentRuntime.run_turn``, scored
from the event stream itself — no LLM judge, no cloud dependency.
"""
