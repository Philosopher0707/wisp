# Wisp sandbox — the agent's working environment for `run_bash` / `run_tests`.
#
# Three things this image must satisfy, each learned the hard way:
#
#  1. **It must have an interpreter.** The default was `ubuntu:22.04`, which has none — `python3`
#     was *command not found* while the prompt advertised a Python and suggested `python -m pytest`.
#     The suggested verification command could not run in the environment the agent ran it in.
#  2. **It must carry the dependencies**, because the container runs `--network none` and can never
#     install anything at run time. They are read from `pyproject.toml` rather than retyped, so this
#     cannot drift from the project's declared sets.
#  3. **It must NOT carry a copy of the source.** The real source arrives through
#     `-v <workspace>:/workspace` at run time and wins on `sys.path`; a stale copy baked in here
#     would shadow it. Hence deps-only, no `pip install -e .`.
FROM python:3.11-slim

# The project's own test suite shells out to `git` (e.g. `collect_environment` reads the branch
# and commit), so the sandbox needs it — without it those tests fail inside the container for a
# reason that has nothing to do with the change under test.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /tmp/pyproject.toml
RUN python -c "import tomllib, pathlib; d = tomllib.loads(pathlib.Path('/tmp/pyproject.toml').read_text())['project']; req = d['dependencies'] + d.get('optional-dependencies', {}).get('dev', []); pathlib.Path('/tmp/requirements.txt').write_text('\n'.join(req) + '\n'); print(len(req), 'requirements')" \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt /tmp/pyproject.toml \
 && python -c "import pytest, pytest_asyncio, jsonschema, pydantic, requests, numpy, cryptography; print('sandbox deps ok')"
