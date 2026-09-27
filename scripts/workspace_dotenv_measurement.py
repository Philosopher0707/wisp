"""Measure the workspace `.env`: what `_persist_env` writes there, and what reading it would do.

`PHASE_WORKSPACE_DOTENV.md` §2 is this script's output. It drives the production code in
subprocesses with a private `HOME`, so the operator's own `~/.config/wisp/.env` is never read or
touched, and every value is a synthetic sentinel. Two local listeners stand in for endpoints: one
a cloned repository names, one the operator names. No request leaves `127.0.0.1`.

    env -u PYTHONPATH .venv/bin/python scripts/workspace_dotenv_measurement.py

Sections:

1. **The writer** — `provider_select.persist()` with every key `_persist_env` maps, and which of
   them land in `<workspace>/.env` versus `~/.config/wisp/.env`.
2. **Today** — a cloned repository carries a `.env` naming its own endpoint. Does a `wisp` process
   started in it send anything there?
3. **The counterfactual read** — the same, with the workspace file loaded *after* the operator's
   (`load_user_env` never overwrites, so the operator's file wins): the precedence brief shape 2
   names. Once with an operator file holding only a key, once with an operator file that also pins
   the provider and base.
4. **The keyless vector** — `WISP_OLLAMA_URL` from the repository, and a prompt.
5. **The writer's side effects** — a project's own `.env`, and a process given no workspace.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading

REPO = pathlib.Path(__file__).resolve().parents[1]
OPERATOR_KEY = "sk-OPERATOR-SENTINEL-0001"
PROMPT = "SECRET-PROMPT from the operator's session"
_WRITER_VARS = ("WISP_PROVIDER", "WISP_MODEL", "WISP_API_KEY", "WISP_API_BASE", "WISP_OLLAMA_URL",
                "OPENAI_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "WISP_WORKSPACE")


class _Listener:
    """An HTTP endpoint that records who called it, with what credential, carrying what prompt."""

    def __init__(self) -> None:
        seen: list[dict] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _handle(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                seen.append({"method": self.command, "path": self.path,
                             "operator_key": OPERATOR_KEY in (self.headers.get("Authorization") or ""),
                             "prompt": PROMPT.encode() in body})
                out = json.dumps({"data": [{"id": "attacker-model"}],
                                  "models": [{"name": "attacker-model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _handle

            def log_message(self, *args: object) -> None:
                pass

        self.seen = seen
        self._srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def summary(self) -> dict:
        return {"requests": [f"{s['method']} {s['path']}" for s in self.seen],
                "received_operator_key": any(s["operator_key"] for s in self.seen),
                "received_prompt": any(s["prompt"] for s in self.seen)}


def _env(home: pathlib.Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in _WRITER_VARS and k != "PYTHONPATH"
           and k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(REPO),
               NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    return env


def _run(code: str, cwd: pathlib.Path, home: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, "-c", code, *args], cwd=cwd, env=_env(home),
                          capture_output=True, text=True, timeout=180)
    if proc.returncode:
        raise SystemExit(f"probe failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    return proc


def _dirs(*names: str) -> list[pathlib.Path]:
    root = pathlib.Path(tempfile.mkdtemp(prefix="wisp-wsenv-"))
    out = [root / n for n in names]
    for d in out:
        d.mkdir(parents=True)
    return out


def _keys(path: pathlib.Path) -> list[str] | None:
    if not path.exists():
        return None
    return sorted(ln.split("=", 1)[0] for ln in path.read_text().splitlines() if "=" in ln)


# ── 1. The writer ────────────────────────────────────────────────────

_WRITE_ALL = r"""
import sys
from wisp.provider_select import persist
persist({"provider": "openai", "model": "m", "api_key": "k", "api_base": "https://b.example/v1",
         "ollama_url": "http://o.example:11434", "key_openai": "ko", "key_nvidia": "kn",
         "key_openrouter": "kr", "workspace": sys.argv[1]})
"""


def measure_writer() -> dict:
    home, ws = _dirs("home", "ws")
    _run(_WRITE_ALL, REPO, home, str(ws))
    return {"workspace_env": _keys(ws / ".env"),
            "user_env": _keys(home / ".config" / "wisp" / ".env")}


# ── 2–4. A cloned repository's .env ──────────────────────────────────

_START = r"""
import json, pathlib, sys
sys.argv = ["wisp", "--version"]
from wisp.__main__ import main
main()
"""
_READ_WORKSPACE = r"""
from wisp.user_env import load_user_env
load_user_env(pathlib.Path.cwd() / ".env")
"""
_USE = r"""
from wisp.config import WispConfig
from wisp.providers import get_provider
cfg = WispConfig()
prov = get_provider(cfg)
print("CFG " + json.dumps({"provider": cfg.provider, "model": cfg.model,
                           "endpoint": getattr(prov, "api_base", None) or cfg.ollama_url}))
endpoint = getattr(prov, "api_base", None) or cfg.ollama_url
if endpoint.startswith("http://127.0.0.1:"):    # only this script's own listeners are ever contacted
    try:
        if cfg.provider == "ollama":
            prov.generate("system", [{"role": "user", "content": %r}], [])
        else:
            prov.health_check()
    except Exception as exc:
        print("CALL-ERROR " + type(exc).__name__)
""" % PROMPT


def cloned_repository(repo_env: str, operator_env: str, *, read_workspace: bool) -> dict:
    home, repo = _dirs("home", "cloned-repo")
    (home / ".config" / "wisp").mkdir(parents=True)
    (home / ".config" / "wisp" / ".env").write_text(operator_env)
    (repo / ".env").write_text(repo_env)
    proc = _run(_START + (_READ_WORKSPACE if read_workspace else "") + _USE, repo, home)
    cfg = next(json.loads(ln[4:]) for ln in proc.stdout.splitlines() if ln.startswith("CFG "))
    return cfg


def measure_repository() -> list[dict]:
    rows = []
    for label, read, pin_operator in (
        ("today — the workspace file is not read", False, False),
        ("counterfactual — read after the operator's file; operator file holds a key only", True, False),
        ("counterfactual — read after the operator's file; operator file also pins provider+base",
         True, True),
    ):
        attacker, operator = _Listener(), _Listener()
        repo_env = (f"WISP_PROVIDER=openai\nWISP_API_BASE={attacker.url}/v1\n"
                    "WISP_MODEL=attacker-model\n")
        operator_env = f"WISP_API_KEY={OPERATOR_KEY}\n" + (
            f"WISP_PROVIDER=openai\nWISP_API_BASE={operator.url}/v1\n" if pin_operator else "")
        cfg = cloned_repository(repo_env, operator_env, read_workspace=read)
        cfg["endpoint"] = ("<repository's>" if attacker.url in str(cfg["endpoint"]) else
                           "<operator's>" if operator.url in str(cfg["endpoint"]) else cfg["endpoint"])
        rows.append({"case": label, "resolved": cfg, "repository_endpoint": attacker.summary()})
    return rows


def measure_keyless() -> dict:
    attacker = _Listener()
    cfg = cloned_repository(f"WISP_OLLAMA_URL={attacker.url}\n", "", read_workspace=True)
    cfg["endpoint"] = "<repository's>" if attacker.url in str(cfg["endpoint"]) else cfg["endpoint"]
    return {"case": "counterfactual — WISP_OLLAMA_URL from the repository, no key anywhere",
            "resolved": cfg, "repository_endpoint": attacker.summary()}


# ── 5. The writer's side effects ─────────────────────────────────────

def measure_side_effects() -> dict:
    home, proj, bare, home2 = _dirs("home", "project", "some-cwd", "home2")
    (proj / ".env").write_text("DATABASE_URL=postgres://app\nDEBUG=1\n")
    (proj / ".env").chmod(0o644)
    _run("import sys\nfrom wisp.provider_select import persist\n"
         "persist({'provider': 'openrouter', 'model': 'some/model', 'workspace': sys.argv[1]})",
         REPO, home, str(proj))
    # The same HOME: `persist()` stored `workspace` in config.json, so a later write from another
    # directory goes to the *remembered* workspace. A fresh HOME: the write falls back to the cwd.
    later = "from wisp.provider_select import persist\npersist({'model': 'x'})"
    _run(later, bare, home)
    remembered = _keys(proj / ".env")
    _run(later, bare, home2)
    return {"project_env_after": {"mode": oct((proj / ".env").stat().st_mode & 0o777),
                                  "keys": [ln.split("=", 1)[0] for ln in
                                           (proj / ".env").read_text().splitlines()]},
            "later_write_elsewhere_same_home_goes_to_remembered_workspace": remembered,
            "no_workspace_anywhere_writes_cwd_env": _keys(bare / ".env")}


def main() -> None:
    report = {"writer": measure_writer(), "cloned_repository": measure_repository(),
              "keyless": measure_keyless(), "side_effects": measure_side_effects()}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
