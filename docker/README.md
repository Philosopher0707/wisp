# Sandbox images

`run_bash` and `run_tests` execute **inside a Docker container** — the agent's toolchain is the
image's, not the host's. Three constraints on whatever image is used, each learned the hard way:

1. **It must have an interpreter.** The default was `ubuntu:22.04`, which has none: inside the
   sandbox `python3` was *command not found* while the prompt advertised a Python and suggested
   `python -m pytest`. The suggested verification command could not run in the environment the agent
   actually ran it in, so the loop could never close.
2. **It must carry the project's dependencies.** The container runs `--network none`, so nothing can
   be installed at run time.
3. **It must not carry a copy of the source.** The real source arrives through
   `-v <workspace>:/workspace` and wins on `sys.path`; a stale copy baked into the image would
   shadow it.

Build and point Wisp at it:

```bash
docker build -f docker/wisp-sandbox.Dockerfile -t wisp-sandbox:py311 .
# then, in ~/.config/wisp/.env:
#   WISP_SANDBOX_IMAGE=wisp-sandbox:py311
```

The dependencies are read from `pyproject.toml` at build time, so the image cannot drift from the
declared sets. `WISP_SANDBOX_IMAGE` overrides the code default (`python:3.11-slim`, which has an
interpreter but not this project's dependencies).
