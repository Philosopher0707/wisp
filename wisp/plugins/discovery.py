"""Auto-discover plugins at startup.

Scans multiple locations for plugins:
  1. .wisp/plugins/ in the workspace (project plugins)
  2. ~/.config/wisp/plugins/ (user plugins)
  3. Any skill directories that contain a plugin.json

Project plugins override user plugins with the same name.
Namespace conflicts are resolved by earliest-loaded-takes-priority.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from wisp.plugins.manifest import PluginManifest
from wisp.plugins.namespace import NamespaceManager

if TYPE_CHECKING:
    from wisp.config import WispConfig

logger = logging.getLogger(__name__)


async def discover_plugins(
    workspace: Path,
    config: "WispConfig",
) -> list[PluginManifest]:
    """Discover plugins from all standard locations.

    Discovery order (higher priority first):
      1. <workspace>/.wisp/plugins/  — project-specific plugins
      2. ~/.config/wisp/plugins/     — user-global plugins
      3. Skill directories with plugin.json (legacy/bridge support)

    Project plugins override user plugins with the same name.
    Only enabled plugins are returned.

    Args:
        workspace: The workspace/project root directory.
        config: The resolved WispConfig (unused currently, reserved
                for future plugin-related config settings).

    Returns:
        A list of PluginManifest instances ready for registration,
        sorted by priority (project plugins first).
    """
    ns_manager = NamespaceManager()
    discovered: dict[str, PluginManifest] = {}
    namespace_owners: dict[str, str] = {}

    # ── 1. Project plugins (.wisp/plugins/) ──────────────────────────
    project_plugins_dir = workspace / ".wisp" / "plugins"
    if project_plugins_dir.exists():
        _scan_plugin_dir(
            project_plugins_dir,
            discovered,
            namespace_owners,
            ns_manager,
            source="project",
        )

    # ── 2. User plugins (~/.config/wisp/plugins/) ────────────────────
    user_plugins_dir = Path.home() / ".config" / "wisp" / "plugins"
    if user_plugins_dir.exists():
        _scan_plugin_dir(
            user_plugins_dir,
            discovered,
            namespace_owners,
            ns_manager,
            source="user",
            # user plugins don't override project plugins
            skip_existing=True,
        )

    # ── 3. Skill directories with plugin.json (bridge support) ───────
    skill_dirs = getattr(config, "skill_dirs", [])
    ws_path = Path(workspace).resolve()
    for skill_dir_name in skill_dirs:
        skill_dir = ws_path / skill_dir_name
        if skill_dir.exists():
            _scan_skill_plugins(
                skill_dir,
                discovered,
                namespace_owners,
                ns_manager,
                skip_existing=True,
            )

    # ── filter to enabled only ───────────────────────────────────────
    from wisp.plugins.registry import PluginRegistry

    registry = PluginRegistry()
    enabled_plugins: list[PluginManifest] = []
    for name, manifest in discovered.items():
        if registry.is_enabled(name):
            enabled_plugins.append(manifest)
        else:
            logger.debug("Skipping disabled plugin '%s'", name)

    logger.info(
        "Discovered %d plugins (%d enabled, %d disabled)",
        len(discovered),
        len(enabled_plugins),
        len(discovered) - len(enabled_plugins),
    )

    return enabled_plugins


# ── internal scanners ─────────────────────────────────────────────────────


def _scan_plugin_dir(
    directory: Path,
    discovered: dict[str, PluginManifest],
    namespace_owners: dict[str, str],
    ns_manager: NamespaceManager,
    source: str,
    skip_existing: bool = False,
) -> None:
    """Scan a directory for plugin subdirectories with plugin.json files."""
    try:
        entries = sorted(directory.iterdir())
    except (PermissionError, OSError) as e:
        logger.warning("Cannot read plugin directory %s: %s", directory, e)
        return

    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue  # skip .backups, etc.

        manifest_file = entry / "plugin.json"
        if not manifest_file.exists():
            continue

        try:
            manifest = PluginManifest.from_file(manifest_file)
        except Exception as e:
            logger.warning(
                "Skipping plugin in '%s': failed to load manifest — %s",
                entry,
                e,
            )
            continue

        errors = manifest.validate()
        if errors:
            logger.warning(
                "Skipping plugin '%s': invalid manifest —\n  %s",
                entry.name,
                "\n  ".join(errors),
            )
            continue

        if skip_existing and manifest.name in discovered:
            logger.debug(
                "Plugin '%s' from %s skipped — already discovered from higher priority source",
                manifest.name,
                source,
            )
            continue

        # namespace conflict check
        if manifest.namespace in namespace_owners:
            existing_owner = namespace_owners[manifest.namespace]
            logger.warning(
                "Plugin '%s' namespace '%s' conflicts with '%s' — skipping",
                manifest.name,
                manifest.namespace,
                existing_owner,
            )
            continue

        try:
            ns_manager.register_plugin(manifest)
        except ValueError as e:
            logger.warning(
                "Cannot register plugin '%s': %s",
                manifest.name,
                e,
            )
            continue

        discovered[manifest.name] = manifest
        namespace_owners[manifest.namespace] = manifest.name
        logger.info(
            "Discovered %s plugin '%s' v%s (namespace: %s)",
            source,
            manifest.name,
            manifest.version,
            manifest.namespace,
        )


def _scan_skill_plugins(
    skill_dir: Path,
    discovered: dict[str, PluginManifest],
    namespace_owners: dict[str, str],
    ns_manager: NamespaceManager,
    skip_existing: bool = False,
) -> None:
    """Scan skill directories for subdirectories that contain plugin.json.

    This bridges the gap between Warp-style SKILL.md directories and
    the full plugin system — a skill directory can be augmented with a
    plugin.json to become a packageable plugin.
    """
    try:
        entries = sorted(skill_dir.iterdir())
    except (PermissionError, OSError) as e:
        logger.warning("Cannot read skill directory %s: %s", skill_dir, e)
        return

    for entry in entries:
        if not entry.is_dir():
            continue

        manifest_file = entry / "plugin.json"
        if not manifest_file.exists():
            continue

        try:
            manifest = PluginManifest.from_file(manifest_file)
        except Exception as e:
            logger.warning(
                "Skipping skill-plugin '%s': %s",
                entry.name,
                e,
            )
            continue

        errors = manifest.validate()
        if errors:
            logger.warning(
                "Skipping skill-plugin '%s': invalid manifest",
                entry.name,
            )
            continue

        if skip_existing and manifest.name in discovered:
            continue

        if manifest.namespace in namespace_owners:
            logger.warning(
                "Skill-plugin '%s' namespace '%s' conflicts with '%s' — skipping",
                manifest.name,
                manifest.namespace,
                namespace_owners[manifest.namespace],
            )
            continue

        try:
            ns_manager.register_plugin(manifest)
        except ValueError as e:
            logger.warning("Cannot register skill-plugin '%s': %s", manifest.name, e)
            continue

        discovered[manifest.name] = manifest
        namespace_owners[manifest.namespace] = manifest.name
        logger.info(
            "Discovered skill-plugin '%s' v%s (namespace: %s)",
            manifest.name,
            manifest.version,
            manifest.namespace,
        )
