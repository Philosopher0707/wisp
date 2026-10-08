"""A local, read-only dashboard over what Wisp measures about itself: the benchmark (how often the agent actually solves tasks, per model and
harness configuration), the harness (flags, token overhead, findings), the models in real use, and the learning stores.

Standard library only; it binds to 127.0.0.1 and serves aggregates, never prompts or file contents. `python -m wisp.dashboard serve` shows,
`python -m wisp.dashboard bench` measures. The dashboard never starts a benchmark and never spends money.
"""
