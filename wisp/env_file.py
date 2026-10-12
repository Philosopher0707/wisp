"""The operator's `~/.config/wisp/.env`: what belongs in it, how it is linted, and how it is rewritten safely.

**Who owns what.** `config.json` owns WHICH provider, model and endpoint to use (`provider`, `model`, `api_base`, `ollama_url`); `.env` owns secrets (API keys) and the
operator's switches (`WISP_SANDBOX`, `WISP_TOOL_PROFILE`, ...). The loader lets the environment win over `config.json`, so a selection kept in BOTH places is a trap: the `.env`
copy goes stale and silently overrides every later choice (seen 2026-10-12: a `WISP_MODEL=qwen2.5-coder` from an earlier Ollama setup survived `/provider nvidia` and made every
prompt a 404). `provider_select.persist` therefore writes a selection to `config.json` only and removes any `.env` copy; this module finds and repairs the copies that already exist.

**What the loader does** (`wisp.user_env.load_user_env`, which this must agree with): one `KEY=VALUE` per line, `#` comments, no quotes, no `export`; the FIRST assignment of a key
is the one in effect (later ones are skipped because the key is already in the environment); a malformed line is skipped.

**Findings** carry a code, a line number and a key NAME, never a value: the file holds API keys. A selection's value is named in a message only for `provider` and `model`
(a model id is not a secret); an endpoint is never echoed because a URL can carry credentials.

**Fixing** is mechanical and idempotent: it removes duplicates, placeholders and blank selection lines, unquotes, drops `export`, moves a selection out of `.env`, and leaves every
other byte alone. It never chooses between two conflicting selections: the caller says `prefer="config"` or `prefer="env"`. Writing a file takes a private backup first, writes
`config.json` before removing the `.env` line, replaces the file atomically at mode 0600, and refuses a symbolic link.
"""

from __future__ import annotations

import functools
import json
import os
import re
import tempfile
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: `.env` variable -> the `config.json` setting that owns it.
SELECTION_KEYS: dict[str, str] = {
    "WISP_PROVIDER": "provider",
    "WISP_MODEL": "model",
    "WISP_API_BASE": "api_base",
    "WISP_OLLAMA_URL": "ollama_url",
}
#: The selection settings whose value may be shown in a message (an endpoint can carry credentials).
_SHOWABLE = frozenset({"provider", "model"})
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLACEHOLDERS = frozenset({"---", "--", "-", "none", "null", "nil", "n/a", "na", "unset", "changeme", "todo", "tbd", "xxx"})
_GENERIC_KEY = "WISP_API_KEY"
#: A provider's own key variable (the first entry of `provider_select.KEY_ENV_VARS`): the generic key duplicates one of these.
_SPECIFIC_KEYS = ("OPENAI_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY")
_PREFER = (None, "config", "env")
_MAX_PASSES = 6


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # "problem" (something is broken or will surprise) | "advice" (tidiness)
    line: int  # 1-based; 0 for a finding about the file
    key: str
    message: str
    fixable: bool = False


@dataclass
class FixResult:
    text: str
    applied: list[str] = field(default_factory=list)
    config_updates: dict[str, str] = field(default_factory=dict)
    remaining: list[Finding] = field(default_factory=list)


@dataclass
class Report:
    findings: list[Finding]
    applied: list[str]
    remaining: list[Finding]
    config_updates: dict[str, str]
    backup: Path | None = None
    changed: bool = False
    chmod: bool = False  # the file's mode is looser than 0600 (set by apply)


@dataclass
class _Line:
    number: int
    raw: str  # with its line ending
    kind: str  # "other" (blank/comment) | "assign" | "export" | "bad"
    key: str = ""
    value: str = ""


# ── parsing ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _ending(raw: str) -> str:
    return raw[len(raw.rstrip("\r\n")):]


def _parse(text: str) -> list[_Line]:
    out: list[_Line] = []
    for number, raw in enumerate(text.splitlines(keepends=True), start=1):
        body = raw.strip()
        if not body or body.startswith("#"):
            out.append(_Line(number, raw, "other"))
            continue
        key, sep, value = body.partition("=")
        key = key.strip()
        if sep and _KEY.fullmatch(key):
            out.append(_Line(number, raw, "assign", key, value.strip()))
        elif sep and key.startswith("export ") and _KEY.fullmatch(key[len("export "):].strip()):
            out.append(_Line(number, raw, "export", key[len("export "):].strip(), value.strip()))
        else:
            out.append(_Line(number, raw, "bad"))
    return out


def _is_placeholder(value: str) -> bool:
    low = value.strip().lower()
    return low in _PLACEHOLDERS or bool(re.fullmatch(r"-{2,}", low))


def _quoted(value: str) -> bool:
    return len(value) >= 2 and value[0] == value[-1] and value[0] in "'\""


def _is_secret_name(key: str) -> bool:
    return bool(re.search(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD)", key, re.I))


def _saved_text(saved: Mapping[str, Any], cfg_key: str) -> str:
    value = saved.get(cfg_key)
    return value.strip() if isinstance(value, str) else ""


# ── lint ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def lint(text: str, saved: Mapping[str, Any] | None = None, *, mode: int | None = None, known: Collection[str] | None = None) -> list[Finding]:
    """Everything wrong or untidy in `text`, given what config.json holds (`saved`), the file's permission bits (`mode`) and the set of known `WISP_*` names (`known`)."""
    saved = saved or {}
    lines = _parse(text)
    found: list[Finding] = []
    first: dict[str, _Line] = {}

    def add(code: str, severity: str, line: int, key: str, message: str, fixable: bool = False) -> None:
        found.append(Finding(code, severity, line, key, message, fixable))

    for ln in lines:
        if ln.kind == "bad":
            add("UNPARSEABLE_LINE", "problem", ln.number, "", "not KEY=VALUE, a comment or blank: the loader skips it")
            continue
        if ln.kind == "other":
            continue
        if ln.kind == "export":
            add("EXPORT_PREFIX", "problem", ln.number, ln.key, "starts with `export`: the loader does not read it", True)
            continue
        if ln.key in first:
            add("DUPLICATE_KEY", "problem", ln.number, ln.key, f"assigned again; the first assignment (line {first[ln.key].number}) is the one in effect", True)
            continue
        first[ln.key] = ln

        if _is_placeholder(ln.value):
            add("PLACEHOLDER_VALUE", "problem", ln.number, ln.key, "holds a placeholder, not a value", True)
            continue
        if _quoted(ln.value):
            add("QUOTED_VALUE", "problem", ln.number, ln.key, "the value is quoted, but the loader keeps the quotes as part of the value", True)

        cfg_key = SELECTION_KEYS.get(ln.key)
        if cfg_key is None:
            continue
        if not ln.value:
            add("BLANK_SELECTION", "advice", ln.number, ln.key, "is blank: it has no effect (a blank selection counts as unset)", True)
            continue
        saved_value = _saved_text(saved, cfg_key)
        shown = cfg_key in _SHOWABLE
        if not saved_value:
            add("SELECTION_IN_ENV", "advice", ln.number, ln.key, f"is a selection: it belongs in config.json ({cfg_key}), where /provider and /model keep it current", True)
        elif saved_value == ln.value.strip("'\""):
            add("SELECTION_IN_ENV", "advice", ln.number, ln.key, f"repeats config.json ({cfg_key}); the copy in .env can only go stale", True)
        else:
            detail = f" ({ln.value.strip(chr(39) + chr(34))!r}) overrides config.json {cfg_key} ({saved_value!r})" if shown else f" overrides config.json {cfg_key}"
            add("SHADOWS_SAVED_SELECTION", "problem", ln.number, ln.key,
                f"{ln.key}{detail}: the environment wins over config.json, so the setting you saved there is not the one in use")

    # the generic key is sent to any keyed provider that has no key of its own
    generic = first.get(_GENERIC_KEY)
    if generic is not None and generic.value and not _is_placeholder(generic.value):
        if any(first[k].value == generic.value for k in _SPECIFIC_KEYS if k in first):
            add("GENERIC_KEY_DUPLICATES_SPECIFIC", "advice", generic.number, _GENERIC_KEY,
                "repeats a provider's own key; the generic one would also be sent to any other provider with no key of its own", True)

    if known is not None:
        for key, ln in first.items():
            if key.startswith("WISP_") and key not in known:
                add("UNKNOWN_KEY", "advice", ln.number, key, "is not a setting this version of Wisp reads (a typo?)")

    if mode is not None and mode & 0o077:
        add("INSECURE_PERMISSIONS", "problem", 0, "", f"mode is {oct(mode & 0o777)}: the file holds API keys and should be 0600", True)

    found.sort(key=lambda f: (f.line, f.code))
    return found


# ── fix / upsert ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def fix(text: str, saved: Mapping[str, Any] | None = None, *, prefer: str | None = None) -> FixResult:
    """The text with every mechanical finding repaired. Conflicts between `.env` and config.json are left alone unless `prefer` is "config" or "env"."""
    if prefer not in _PREFER:
        raise ValueError(f"prefer must be one of: config, env (got {prefer!r})")
    view = dict(saved or {})
    result = FixResult(text)
    for _ in range(_MAX_PASSES):
        changed = False
        seen: set[str] = set()
        out: list[str] = []
        specific = {ln.key: ln.value for ln in _parse(result.text) if ln.kind == "assign" and ln.key in _SPECIFIC_KEYS and ln.value}
        for ln in _parse(result.text):
            if ln.kind == "bad" or ln.kind == "other":
                out.append(ln.raw)
                continue
            label = None  # the finding this line is removed for
            rewrite = None
            if ln.kind == "export":
                rewrite = f"{ln.key}={ln.value}{_ending(ln.raw)}"
                label = f"EXPORT_PREFIX:{ln.key}"
            elif ln.key in seen:
                label = f"DUPLICATE_KEY:{ln.key}"
                changed = True
                result.applied.append(label)
                continue
            else:
                seen.add(ln.key)
                if _is_placeholder(ln.value):
                    changed = True
                    result.applied.append(f"PLACEHOLDER_VALUE:{ln.key}")
                    continue
                cfg_key = SELECTION_KEYS.get(ln.key)
                value = ln.value.strip("'\"") if _quoted(ln.value) else ln.value
                if cfg_key is not None:
                    if not value:
                        changed = True
                        result.applied.append(f"BLANK_SELECTION:{ln.key}")
                        continue
                    saved_value = _saved_text(view, cfg_key)
                    if not saved_value:
                        result.config_updates[cfg_key] = value
                        view[cfg_key] = value
                        changed = True
                        result.applied.append(f"SELECTION_IN_ENV:{ln.key}")
                        continue
                    if saved_value == value:
                        changed = True
                        result.applied.append(f"SELECTION_IN_ENV:{ln.key}")
                        continue
                    if prefer == "config":
                        changed = True
                        result.applied.append(f"SHADOWS_SAVED_SELECTION:{ln.key}")
                        continue
                    if prefer == "env":
                        result.config_updates[cfg_key] = value
                        view[cfg_key] = value
                        changed = True
                        result.applied.append(f"SHADOWS_SAVED_SELECTION:{ln.key}")
                        continue
                if ln.key == _GENERIC_KEY and ln.value and any(v == ln.value for v in specific.values()):
                    changed = True
                    result.applied.append(f"GENERIC_KEY_DUPLICATES_SPECIFIC:{ln.key}")
                    continue
                if _quoted(ln.value):
                    rewrite = f"{ln.key}={value}{_ending(ln.raw)}"
                    label = f"QUOTED_VALUE:{ln.key}"
            if rewrite is not None:
                # an `export` line is not an assignment the loader reads, so it does not count as a duplicate of a later real one; once rewritten it is one
                if ln.kind == "export":
                    if ln.key in seen:
                        changed = True
                        result.applied.append(f"DUPLICATE_KEY:{ln.key}")
                        continue
                    seen.add(ln.key)
                out.append(rewrite)
                changed = True
                result.applied.append(label or "")
                continue
            out.append(ln.raw)
        result.text = "".join(out)
        if not changed:
            break
    result.remaining = [f for f in lint(result.text, view) if not f.fixable]
    return result


def upsert(text: str, updates: Mapping[str, str | None]) -> str:
    """`text` with each key set to its value (replacing the first assignment in place, dropping later duplicates, or appending) or, for None, removed. Nothing else changes."""
    for key, value in updates.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError(f"not a valid variable name: {key!r}")
        if value is not None and (not isinstance(value, str) or any(c in value for c in "\r\n\x00")):
            raise ValueError(f"the value for {key} cannot be written to a .env file")
    lines = text.splitlines(keepends=True)
    pending = dict(updates)
    done: set[str] = set()
    out: list[str] = []
    for ln in _parse(text):
        if ln.kind in ("assign",) and ln.key in pending:
            if ln.key in done or pending[ln.key] is None:
                continue
            out.append(f"{ln.key}={pending[ln.key]}{_ending(ln.raw) or chr(10)}")
            done.add(ln.key)
            continue
        out.append(ln.raw)
    del lines
    missing = [(k, v) for k, v in pending.items() if v is not None and k not in done]
    if missing:
        if out and not out[-1].endswith(("\n", "\r")):
            out[-1] += "\n"
        out.extend(f"{k}={v}\n" for k, v in missing)
    return "".join(out)


# ── files ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _default_paths() -> tuple[Path, Path]:
    import wisp.config as cfg_mod
    import wisp.user_env as user_env

    return user_env.user_env_path(), cfg_mod.get_config_path()


def _load_saved(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{config_path} cannot be read ({type(exc).__name__}); not judging the selection against it") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} must hold a JSON object")
    return data


@functools.lru_cache(maxsize=1)
def known_wisp_keys() -> frozenset[str]:
    """Every `WISP_*` name this version reads: the settings registry, every spelling in the package source, and every `get_setting("name")`, which
    `wisp.config.get_setting` reads from the environment as WISP_NAME without that spelling appearing anywhere."""
    import wisp
    from wisp.config import SETTINGS_SCHEMA

    names = {str(spec["env_var"]) for spec in SETTINGS_SCHEMA.values() if spec.get("env_var")}
    names.update(f"WISP_{key.upper()}" for key in SETTINGS_SCHEMA)
    spelled = re.compile(r"WISP_[A-Z0-9_]+")
    by_name = re.compile(r"""get_setting\(\s*["']([a-z0-9_]+)["']""")
    for path in Path(wisp.__file__).resolve().parent.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        names.update(spelled.findall(source))
        names.update(f"WISP_{key.upper()}" for key in by_name.findall(source))
    return frozenset(names)


def check_path(env_path: Path | None = None, config_path: Path | None = None) -> list[Finding]:
    default_env, default_config = _default_paths()
    env_path, config_path = Path(env_path or default_env), Path(config_path or default_config)
    if not env_path.exists():
        return []
    text = env_path.read_text(encoding="utf-8")
    mode = None if env_path.is_symlink() else env_path.stat().st_mode & 0o777
    try:
        known: Collection[str] | None = known_wisp_keys()
    except Exception:  # noqa: BLE001 — an advice check must not fail the rest
        known = None
    return lint(text, _load_saved(config_path), mode=mode, known=known)


def _write_config(config_path: Path, updates: Mapping[str, str]) -> None:
    from wisp.config import validate_config

    data = _load_saved(config_path)
    merged = {**data, **updates}
    errors = validate_config(merged)
    if errors:
        raise ValueError("config.json would be invalid: " + "; ".join(errors))
    _atomic_write(config_path, json.dumps(merged, indent=2) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 1
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(path.read_bytes())
    return target


def fix_path(env_path: Path | None = None, config_path: Path | None = None, *, apply: bool = False, prefer: str | None = None) -> Report:
    """Repair the file. Without `apply` nothing is written and the report says what would change."""
    default_env, default_config = _default_paths()
    env_path, config_path = Path(env_path or default_env), Path(config_path or default_config)
    if env_path.is_symlink():
        raise ValueError(f"{env_path} is a symbolic link; refusing to rewrite it (fix the target by hand)")
    if not env_path.exists():
        return Report([], [], [], {})
    text = env_path.read_text(encoding="utf-8")
    saved = _load_saved(config_path)
    mode = env_path.stat().st_mode & 0o777
    findings = lint(text, saved, mode=mode)
    result = fix(text, saved, prefer=prefer)
    loose = bool(mode & 0o077)
    report = Report(findings, list(result.applied), result.remaining, dict(result.config_updates), chmod=loose)
    if not apply:
        return report
    rewrite = result.text != text or bool(result.config_updates)
    if rewrite:
        report.backup = _backup(env_path)
        if result.config_updates:
            _write_config(config_path, result.config_updates)  # first: the value must exist before the .env line that held it goes
        _atomic_write(env_path, result.text)
    elif loose:
        os.chmod(env_path, 0o600)
    report.changed = rewrite or loose
    return report


# ── at boot ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def boot_notes(env_path: Path | None = None, config_path: Path | None = None) -> list[str]:
    """One line per `.env` selection that overrides a different one saved in config.json (names, provider and model values only; never a secret). Never raises."""
    try:
        findings = [f for f in check_path(env_path, config_path) if f.code == "SHADOWS_SAVED_SELECTION"]
    except Exception:  # noqa: BLE001 — a notice must never stop a boot
        return []
    return [f"note: ~/.config/wisp/.env line {f.line}: {f.message}. Run `wisp config env fix --prefer config --apply` (keeps config.json) or `--prefer env`." for f in findings]


# ── the command ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

USAGE = ("Usage: wisp config env check\n"
         "       wisp config env fix [--apply] [--prefer config|env]\n\n"
         "check  lists what is wrong or untidy in ~/.config/wisp/.env (names and line numbers, never values); exits 1 on a problem.\n"
         "fix    shows what it would change; --apply makes a private backup first, then rewrites the file. When .env and config.json\n"
         "       hold different values for the same selection it will not guess: --prefer config keeps config.json, --prefer env keeps .env.")


def _print_findings(findings: list[Finding], out) -> None:
    for f in findings:
        where = f"line {f.line}" if f.line else "file"
        who = f" {f.key}" if f.key else ""
        out(f"  [{f.severity}] {where}{who}: {f.code} - {f.message}")


def cli_main(argv: list[str], out=print) -> int:
    """`wisp config env ...`. 0 = nothing left that is a problem, 1 = problems remain, 2 = usage."""
    if not argv or argv[0] not in ("check", "fix"):
        out(USAGE)
        return 2
    verb, rest = argv[0], argv[1:]
    apply, prefer = False, None
    i = 0
    while i < len(rest):
        if rest[i] == "--apply" and verb == "fix":
            apply = True
            i += 1
        elif rest[i] == "--prefer" and verb == "fix" and i + 1 < len(rest):
            prefer = rest[i + 1]
            i += 2
        else:
            out(USAGE)
            return 2
    default_env, _ = _default_paths()
    try:
        if verb == "check":
            findings = check_path()
            if not findings:
                out(f"{default_env}: nothing to report.")
                return 0
            out(f"{default_env}:")
            _print_findings(findings, out)
            return 1 if any(f.severity == "problem" for f in findings) else 0
        if prefer not in _PREFER:
            out(USAGE)
            return 2
        report = fix_path(apply=apply, prefer=prefer)
    except (ValueError, OSError) as exc:
        out(f"error: {exc}")
        return 1
    if not report.applied and not report.remaining and not report.chmod:
        out(f"{default_env}: nothing to fix.")
        return 0
    verb_text = "changed" if apply else "would change (dry run)"
    for label in report.applied:
        out(f"  {verb_text}: {label}")
    if report.chmod:
        out(f"  {verb_text}: file mode to 0600")
    if report.config_updates:
        out(f"  config.json {'gets' if apply else 'would get'}: {', '.join(sorted(report.config_updates))}")
    if apply and report.backup:
        out(f"  backup: {report.backup}")
    if not apply:
        out("dry run - nothing written. Re-run with --apply.")
    unresolved = [f for f in report.remaining if f.severity == "problem"]
    if unresolved:
        _print_findings(report.remaining, out)
        if any(f.code == "SHADOWS_SAVED_SELECTION" for f in unresolved):
            out("  .env and config.json disagree: choose with --prefer config (keep config.json) or --prefer env (keep .env).")
        return 1
    return 0
