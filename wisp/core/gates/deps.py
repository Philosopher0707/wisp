"""Layer 4: dependency lock. Pure.

INVARIANT D1: while the lock is closed (the default), nothing adds, upgrades or fetches a library the project does not already
declare, and nothing writes a package manifest or lockfile. Installing what the lockfile already pins (`npm ci`, `uv sync`,
`pip install -r requirements.txt`, `poetry install`) is not a new dependency and stays allowed.

The lock is opened only by the operator (config / environment), never by a tool argument, so the model cannot unlock itself.
"""

from __future__ import annotations

import os
import posixpath

from wisp.core.gates.invocations import Invocation
from wisp.core.gates.shellparse import Word

MANIFEST_NAMES = frozenset({
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "pnpm-workspace.yaml", "bun.lockb", "bun.lock",
    "Cargo.toml", "Cargo.lock",
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "requirements.in", "constraints.txt", "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock", "setup.py", "setup.cfg", "environment.yml",
    "go.mod", "go.sum", "Gemfile", "Gemfile.lock", "composer.json", "composer.lock", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "gradle.lockfile",
    "Package.swift", "Package.resolved", "Podfile", "Podfile.lock", "mix.exs", "mix.lock", "pubspec.yaml", "pubspec.lock",
})


def is_manifest_path(path: str) -> bool:
    trimmed = path.rstrip("/")
    base = posixpath.basename(trimmed)
    if base in MANIFEST_NAMES or (base.startswith("requirements") and base.endswith(".txt")):
        return True
    return base.endswith((".txt", ".in")) and posixpath.basename(posixpath.dirname(trimmed)) in ("requirements", "reqs")  # requirements/base.txt


# (tool, subcommands that ADD or CHANGE dependencies even with no package operand)
_ALWAYS_CHANGES = {
    "npm": frozenset({"update", "upgrade", "up", "audit-fix", "dedupe", "link", "uninstall", "remove", "rm", "un", "unlink"}),
    "yarn": frozenset({"upgrade", "up", "upgrade-interactive", "remove", "dedupe", "link"}),
    "pnpm": frozenset({"update", "up", "upgrade", "remove", "rm", "uninstall", "un", "dedupe", "link", "import"}),
    "bun": frozenset({"update", "remove", "rm", "link"}),
    "cargo": frozenset({"add", "install", "update", "remove", "rm", "upgrade", "generate-lockfile"}),
    "poetry": frozenset({"add", "update", "remove", "lock", "self"}),
    "pipenv": frozenset({"update", "uninstall", "lock", "upgrade"}),
    "uv": frozenset({"add", "remove", "lock", "tool", "self"}),
    "go": frozenset({"get"}),
    "gem": frozenset({"install", "update", "uninstall"}),
    "bundle": frozenset({"add", "update", "remove", "lock"}),
    "composer": frozenset({"require", "update", "remove", "global"}),
    "dotnet": frozenset({"add"}),
    "swift": frozenset({"update"}),
    "brew": frozenset({"install", "upgrade", "reinstall", "tap", "uninstall", "remove", "link", "unlink", "bundle"}),
    "apt": frozenset({"install", "upgrade", "full-upgrade", "dist-upgrade", "remove", "purge", "autoremove"}),
    "apt-get": frozenset({"install", "upgrade", "dist-upgrade", "remove", "purge", "autoremove"}),
    "yum": frozenset({"install", "update", "upgrade", "remove", "erase"}),
    "dnf": frozenset({"install", "update", "upgrade", "remove", "erase"}),
    "apk": frozenset({"add", "upgrade", "del"}),
    "pacman": frozenset({"-S", "-Syu", "-U", "-R"}),
    "conda": frozenset({"install", "update", "upgrade", "remove", "uninstall", "create", "env"}),
    "mamba": frozenset({"install", "update", "upgrade", "remove", "uninstall", "create"}),
    "pipx": frozenset({"install", "upgrade", "upgrade-all", "uninstall", "inject", "reinstall", "reinstall-all"}),
}
# Tools where `install`/`add`/`i` with a package OPERAND adds a dependency, but with none installs what is already declared.
_INSTALL_VERBS = {
    "npm": frozenset({"install", "i", "add", "isntall"}),
    "yarn": frozenset({"add", "install"}),
    "pnpm": frozenset({"add", "install", "i"}),
    "bun": frozenset({"add", "install", "i"}),
    "pip": frozenset({"install"}),
    "pip3": frozenset({"install"}),
    "gem": frozenset(),
    "go": frozenset({"install"}),
    "pipenv": frozenset({"install"}),
}
_PIP_VALUE_OPTS = frozenset({
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-t", "--target", "-i", "--index-url", "--extra-index-url", "-f", "--find-links",
    "--prefix", "--root", "--python", "--platform", "--abi", "--implementation", "--python-version", "--src", "--upgrade-strategy", "--only-binary",
    "--no-binary", "--progress-bar", "--proxy", "--retries", "--timeout", "--cert", "--client-cert", "--trusted-host", "--cache-dir", "--log", "-C", "--config-settings",
})
_NODE_VALUE_OPTS = frozenset({"--prefix", "--workspace", "-w", "--registry", "--cwd", "--filter", "-C", "--dir", "--tag", "--save-prefix"})
_RUNNERS_THAT_FETCH = frozenset({"npx", "bunx", "pnpx"})


def _operands(args: tuple[Word, ...], value_opts: frozenset[str]) -> list[Word]:
    out: list[Word] = []
    i = 0
    while i < len(args):
        t = args[i].text
        if t == "--":
            out += list(args[i + 1:])
            break
        if t.startswith("-") and t != "-" and not args[i].dynamic:
            if t in value_opts:
                i += 1
        else:
            out.append(args[i])
        i += 1
    return out


def _is_local_target(w: Word) -> bool:
    t = w.text
    if "://" in t and not t.startswith("file:"):
        return False
    return t in (".", "..") or t.startswith(("./", "../", "/", "~/", "file:")) or t.endswith((".whl", ".tar.gz", ".zip"))


def _is_remote_go_target(w: Word) -> bool:
    t = w.text
    if t.startswith((".", "/")):
        return False
    return "@" in t or "." in t.split("/", 1)[0]


def _python_module_pip(inv: Invocation) -> tuple[str, tuple[Word, ...]] | None:
    """`python -m pip install x` is `pip install x`."""
    if inv.name.startswith("python"):
        for i, w in enumerate(inv.args):
            if w.text == "-m" and i + 1 < len(inv.args) and inv.args[i + 1].text in ("pip", "pip3"):
                return "pip", inv.args[i + 2:]
            if not w.text.startswith("-"):
                break
    return None


def check_install(inv: Invocation, workspace: str) -> str | None:
    """The reason `inv` would add or change a dependency, else None."""
    if not inv.argv:
        return None
    name, args = inv.name, inv.args
    alias = _python_module_pip(inv)
    if alias:
        name, args = alias
    if name == "uv" and args and args[0].text == "pip":
        name, args = "pip", args[1:]
    if name == "uv" and args and args[0].text in ("sync", "run"):
        if args[0].text == "run" and any(a.text == "--with" or a.text.startswith("--with=") for a in args[1:]):
            return "`uv run --with` installs a package that the project does not declare"
        return None
    if name in _RUNNERS_THAT_FETCH:
        ops = _operands(args, _NODE_VALUE_OPTS)
        if any(a.text in ("--no-install", "--no") for a in args):
            return None
        if ops and os.path.exists(os.path.join(workspace, "node_modules", ".bin", posixpath.basename(ops[0].text))):
            return None
        return f"`{name}` would download and run a package that is not installed in this project"
    if name == "npm" and args and args[0].text in ("exec", "x") and not any(a.text in ("--no-install", "--no") for a in args):
        ops = _operands(args[1:], _NODE_VALUE_OPTS)
        if not (ops and os.path.exists(os.path.join(workspace, "node_modules", ".bin", posixpath.basename(ops[0].text)))):
            return "`npm exec` would download and run a package that is not installed in this project"
    if not args:
        return None
    sub = args[0].text
    if name in _ALWAYS_CHANGES and sub in _ALWAYS_CHANGES[name]:
        return f"`{name} {sub}` adds, upgrades or removes a dependency"
    if name in ("npm", "yarn", "pnpm", "bun") and sub in _INSTALL_VERBS.get(name, frozenset()):
        ops = _operands(args[1:], _NODE_VALUE_OPTS)
        if any(a.text in ("-g", "--global") for a in args[1:]):
            return f"`{name} {sub} -g` installs a package outside the project"
        if ops and not all(_is_local_target(o) for o in ops):
            return f"`{name} {sub} {ops[0].text}` adds a dependency the project does not declare"
        if name == "yarn" and sub == "add":
            return "`yarn add` adds a dependency"
        return None
    if name in ("pip", "pip3") and sub in ("install", "download"):
        ops = _operands(args[1:], _PIP_VALUE_OPTS)
        remote = [o for o in ops if not _is_local_target(o)]
        if remote:
            return f"`pip {sub} {remote[0].text}` installs a package the project does not declare"
        return None
    if name == "pipenv" and sub == "install":
        ops = _operands(args[1:], frozenset())
        return f"`pipenv install {ops[0].text}` adds a dependency" if ops else None
    if name == "go" and sub == "install":
        ops = _operands(args[1:], frozenset())
        remote = [o for o in ops if _is_remote_go_target(o)]
        return f"`go install {remote[0].text}` fetches a module the project does not declare" if remote else None
    if name == "go" and sub == "mod" and len(args) > 1 and args[1].text in ("tidy", "edit", "vendor", "init"):
        return f"`go mod {args[1].text}` changes go.mod / go.sum"
    if name in ("apt", "apt-get", "yum", "dnf", "apk") and sub in _ALWAYS_CHANGES[name]:
        return f"`{name} {sub}` changes system packages"
    if name == "conda" and sub == "env" and len(args) > 1 and args[1].text in ("create", "update", "remove"):
        return f"`conda env {args[1].text}` changes environments"
    if name == "swift" and sub == "package" and len(args) > 1 and args[1].text in ("add-dependency", "update", "resolve", "edit", "unedit"):
        return f"`swift package {args[1].text}` changes Package.swift / Package.resolved"
    if name == "dotnet" and sub == "add":
        return "`dotnet add` adds a dependency"
    if name == "cargo" and sub == "install":
        return "`cargo install` installs a crate the project does not declare"
    if name.startswith("python") and "setup.py" in "".join(a.text for a in inv.args[:1]) and any(a.text in ("install", "develop") for a in args[1:2]):
        return "`setup.py install` changes the environment"
    return None
