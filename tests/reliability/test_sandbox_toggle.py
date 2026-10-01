"""The sandbox toggle: one authority, one vocabulary, and a host-only writer.

The defect these pins exist for: `get_sandbox()` accepted six spellings of "off" while
`wisp/tools/bash.py` compared the literal `"off"`. So `WISP_SANDBOX=false` disabled the sandbox on
REST (`/api/bash`, `/api/diagnostics`) and left the agent's `run_bash` routing through the tier
router — **one variable, two meanings, two paths**. `sandbox_mode()` is now the single reader.

The second property is a permission one, not a style one: the toggle is a *host* surface. Nothing
model-reachable may write it, or the agent could grant itself host execution (ADR-0031's class).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from wisp.sandbox import (
    SANDBOX_AUTO_VALUES,
    SANDBOX_OFF_VALUES,
    get_sandbox,
    reset_sandbox,
    sandbox_mode,
    set_sandbox_mode,
)
from wisp.tools import bash as bash_mod

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Matches the exact env-var name and NOT `WISP_SANDBOX_IMAGE` (the closing quote does the work).
_ENV_READ = re.compile(r"""["']WISP_SANDBOX["']""")


@pytest.fixture(autouse=True)
def _isolate_sandbox(monkeypatch):
    """Every test starts with no override, no env var, and no cached provider."""
    monkeypatch.delenv("WISP_SANDBOX", raising=False)
    set_sandbox_mode(None)
    reset_sandbox()
    yield
    set_sandbox_mode(None)
    reset_sandbox()


# ── The default must be today's behaviour ────────────────────────────────────


def test_default_is_auto(monkeypatch):
    monkeypatch.delenv("WISP_SANDBOX", raising=False)
    assert sandbox_mode() == "auto"


def test_unrecognised_value_fails_closed(monkeypatch):
    """A typo must not disable confinement. `ofl` is not `off`."""
    monkeypatch.setenv("WISP_SANDBOX", "ofl")
    assert sandbox_mode() == "auto"


@pytest.mark.parametrize("value", sorted(SANDBOX_AUTO_VALUES))
def test_every_auto_spelling_is_auto(monkeypatch, value):
    monkeypatch.setenv("WISP_SANDBOX", value)
    assert sandbox_mode() == "auto"


@pytest.mark.parametrize("value", sorted(SANDBOX_OFF_VALUES))
def test_every_off_spelling_is_off(monkeypatch, value):
    monkeypatch.setenv("WISP_SANDBOX", value)
    assert sandbox_mode() == "off"


def test_case_and_whitespace_do_not_matter(monkeypatch):
    monkeypatch.setenv("WISP_SANDBOX", "  OFF  ")
    assert sandbox_mode() == "off"


def test_an_empty_env_value_is_auto(monkeypatch):
    """`load_user_env` can leave a key present-but-empty; that must read as "not set", not as off."""
    monkeypatch.setenv("WISP_SANDBOX", "")
    assert sandbox_mode() == "auto"


# ── The host override, and its precedence ────────────────────────────────────


def test_override_beats_the_environment(monkeypatch):
    """A host that just asked for "off" must not be overruled by a file it cannot see."""
    monkeypatch.setenv("WISP_SANDBOX", "auto")
    assert set_sandbox_mode("off") == "off"
    assert sandbox_mode() == "off"


def test_override_can_turn_confinement_back_on(monkeypatch):
    monkeypatch.setenv("WISP_SANDBOX", "off")
    assert set_sandbox_mode("auto") == "auto"
    assert sandbox_mode() == "auto"


def test_clearing_the_override_returns_to_the_environment(monkeypatch):
    monkeypatch.setenv("WISP_SANDBOX", "off")
    set_sandbox_mode("auto")
    assert sandbox_mode() == "auto"
    set_sandbox_mode(None)
    assert sandbox_mode() == "off", "clearing the override must expose WISP_SANDBOX again"


def test_set_rejects_an_unknown_mode():
    with pytest.raises(ValueError):
        set_sandbox_mode("maybe")


def test_empty_string_defers_rather_than_forcing(monkeypatch):
    """`""` is the third state — defer — not a synonym for "force confined"."""
    monkeypatch.setenv("WISP_SANDBOX", "off")
    assert set_sandbox_mode("") == "off", "an empty instruction must defer, not override"
    assert set_sandbox_mode("auto") == "auto", "an explicit 'auto' must override the config file"


# ── The cache: a toggle that does not take effect is worse than none ─────────


def test_toggling_drops_the_cached_provider(tmp_path):
    """`get_sandbox()` memoises per workspace. Without invalidation the mode reads "off" and the
    provider stays what it was — the toggle reports a change it did not make."""
    reset_sandbox()
    before = get_sandbox(str(tmp_path))
    assert getattr(before, "reason", None) != "explicit"

    set_sandbox_mode("off")
    after = get_sandbox(str(tmp_path))
    assert after.name == "host"
    assert getattr(after, "reason", None) == "explicit", (
        "the toggle reported 'off' but get_sandbox served its cached provider"
    )


# ── The production path: every spelling, on the agent's own tool ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", sorted(SANDBOX_OFF_VALUES))
async def test_the_agent_path_honours_every_off_spelling(tmp_path, monkeypatch, spelling):
    """This is the regression. With the router booby-trapped, a successful call proves the explicit
    branch was taken — i.e. that `run_bash` consults the same authority `get_sandbox` does."""
    monkeypatch.setenv("WISP_SANDBOX", spelling)
    reset_sandbox()

    def _explode(*_args, **_kwargs):
        raise AssertionError("tier router consulted: the explicit-off branch was not taken")

    monkeypatch.setattr(bash_mod, "get_router", _explode)
    out = await bash_mod.async_tool_run_bash("echo sandbox-toggle-ok", str(tmp_path), timeout=30)
    assert "sandbox-toggle-ok" in out


@pytest.mark.asyncio
async def test_the_agent_path_still_uses_the_router_when_auto(tmp_path, monkeypatch):
    """The positive control for the test above: with the switch off, the router IS the path."""
    monkeypatch.setenv("WISP_SANDBOX", "auto")
    reset_sandbox()
    seen: list[str] = []

    class _Recorder:
        name = "recorder"

        def is_available(self) -> bool:
            return True

        async def run(self, command, cwd="", timeout=60):
            seen.append(command)
            return (0, "routed", "")

    from wisp.sandbox.router import SandboxRouter

    monkeypatch.setattr(bash_mod, "get_router",
                        lambda workspace: SandboxRouter(workspace, tiers=[_Recorder()]))
    out = await bash_mod.async_tool_run_bash("echo anything", str(tmp_path), timeout=30)
    assert seen, "auto mode must route through the tier router"
    assert "routed" in out


# ── One reader, or the defect returns ───────────────────────────────────────


def test_the_env_var_has_exactly_one_reader():
    """`WISP_SANDBOX` may be read in exactly one place.

    Two readers is how the two-meanings defect happened; a third would be a fourth authority over
    one decision. (`WISP_SANDBOX_IMAGE` is a different variable and is excluded by the pattern.)
    """
    readers = [
        p.relative_to(REPO).as_posix()
        for p in (REPO / "wisp").rglob("*.py")
        if _ENV_READ.search(p.read_text(encoding="utf-8"))
    ]
    assert readers == ["wisp/sandbox/__init__.py"], (
        f"WISP_SANDBOX is read outside the single authority: {readers}"
    )


def test_the_tool_layer_does_not_respell_the_check():
    src = (REPO / "wisp" / "tools" / "bash.py").read_text(encoding="utf-8")
    assert _ENV_READ.search(src) is None, "bash.py must not read the env var itself"
    assert "sandbox_mode()" in src, "bash.py must consult the single authority"


# ── The permission property: the model cannot reach the toggle ──────────────


def test_no_tool_module_can_set_the_sandbox_mode():
    """`set_sandbox_mode` is the one writer of the override. A model-reachable surface referencing
    it could widen its own confinement, so nothing under `wisp/tools/` may name it."""
    offenders = [
        p.relative_to(REPO).as_posix()
        for p in (REPO / "wisp" / "tools").rglob("*.py")
        if "set_sandbox_mode" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"a model-reachable module can set the sandbox mode: {offenders}"


def test_no_tool_schema_is_named_for_the_sandbox():
    from wisp.tools.registry import TOOL_SCHEMAS

    names = {
        (schema.get("function") or {}).get("name", "")
        for schema in TOOL_SCHEMAS
        if isinstance(schema, dict)
    }
    named = sorted(n for n in names if "sandbox" in n.lower())
    assert not named, f"the sandbox toggle must not be a model-callable tool: {named}"


def test_the_writer_is_the_sandbox_module_and_not_the_dispatcher():
    """The slash command is a *caller*, not a second implementation: it must import the authority
    rather than mutate `os.environ` itself."""
    src = (REPO / "wisp" / "cli" / "dispatcher.py").read_text(encoding="utf-8")
    assert "set_sandbox_mode" in src
    assert 'os.environ["WISP_SANDBOX"]' not in src
    assert 'environ["WISP_SANDBOX"]' not in src


# ── The scope decision: operator-only, and it stays that way ────────────────


def test_no_server_route_can_set_the_sandbox_mode():
    """ADR-0076 R4: operator-only **by decision**, not by accident.

    Exposing this over REST would make holding an API key sufficient to run unconfined commands on the
    host — a privilege change on an authenticated surface, which is its own decision (BOUNDARIES §2),
    not an incidental field. If someone wants it, they must reopen R4 and say so.
    """
    offenders = [
        p.relative_to(REPO).as_posix()
        for p in (REPO / "wisp" / "server").rglob("*.py")
        if "set_sandbox_mode" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"a server route can set the sandbox mode: {offenders} — that reopens ADR-0076 R4"
    )


def test_prompt_request_has_no_sandbox_field():
    """Mechanical, not a source scan: the request model is the API contract."""
    from wisp.server.routes.prompt import PromptRequest

    fields = getattr(PromptRequest, "model_fields", None) or getattr(PromptRequest, "__fields__", {})
    assert "sandbox" not in set(fields), (
        "POST /api/prompt must not carry a sandbox field — see ADR-0076 R4"
    )
