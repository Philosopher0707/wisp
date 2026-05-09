"""Plugin registry — local install management and remote marketplace."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from wisp.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────


def _copytree_plugin(src: Path, dst: Path) -> None:
    """Copy a plugin directory tree, skipping __pycache__ and .git."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", ".git", "*.pyc", ".DS_Store"),
    )


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via a temp file rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


# ── PluginRegistry ─────────────────────────────────────────────────────────


class PluginRegistry:
    """Local plugin registry.

    Plugins are stored in the config directory (default
    ~/.config/wisp/plugins/). Each plugin lives in its own
    directory with a plugin.json manifest at the root.

    All install/uninstall operations are idempotent.
    """

    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            config_dir = Path.home() / ".config" / "wisp" / "plugins"
        self._root = Path(config_dir)
        self._state_file = self._root / "state.json"

    # ── internal helpers ──────────────────────────────────────────────

    def _plugin_dir(self, name: str) -> Path:
        return self._root / name

    def _read_state(self) -> dict:
        """Read the registry state file (enabled/disabled tracking)."""
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _write_state(self, state: dict) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._state_file, json.dumps(state, indent=2) + "\n")

    # ── public API ────────────────────────────────────────────────────

    def install(self, plugin_path: Path) -> PluginManifest:
        """Install a plugin from a directory path.

        Validates the manifest, copies the plugin into the registry,
        and records install state. Idempotent — installing the same
        plugin twice is a no-op.

        Args:
            plugin_path: Path to the plugin directory containing
                         plugin.json at its root.

        Returns:
            The loaded PluginManifest.

        Raises:
            FileNotFoundError: If plugin.json is missing.
            ValueError: If the manifest is invalid or dependencies
                        are unsatisfied.
        """
        manifest_file = plugin_path / "plugin.json"
        if not manifest_file.exists():
            raise FileNotFoundError(
                f"No plugin.json found in {plugin_path}"
            )

        manifest = PluginManifest.from_file(manifest_file)

        # validate
        errors = manifest.validate()
        if errors:
            raise ValueError(
                f"Invalid plugin manifest for {manifest.name}:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        # check dependencies
        missing = self.resolve_dependencies(manifest)
        if missing:
            raise ValueError(
                f"Plugin '{manifest.name}' has unsatisfied dependencies: {missing}"
            )

        dest = self._plugin_dir(manifest.name)

        # idempotent — skip if already installed with same version
        existing_manifest = self._plugin_dir(manifest.name) / "plugin.json"
        if existing_manifest.exists():
            try:
                existing = PluginManifest.from_file(existing_manifest)
                if existing.version == manifest.version:
                    logger.info(
                        "Plugin '%s' v%s already installed, skipping.",
                        manifest.name,
                        manifest.version,
                    )
                    return existing
            except Exception:
                pass  # corrupt existing install, overwrite it

        logger.info("Installing plugin '%s' v%s", manifest.name, manifest.version)

        _copytree_plugin(plugin_path, dest)

        # update state
        state = self._read_state()
        state[manifest.name] = {
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "version": manifest.version,
            "enabled": True,
        }
        self._write_state(state)

        return manifest

    def uninstall(self, plugin_name: str) -> bool:
        """Remove a plugin from the registry.

        Idempotent — returns False if the plugin was not installed.

        Returns:
            True if the plugin was removed, False if it was not found.
        """
        dest = self._plugin_dir(plugin_name)
        if not dest.exists():
            logger.debug("Plugin '%s' not installed, nothing to uninstall.", plugin_name)
            return False

        logger.info("Uninstalling plugin '%s'", plugin_name)
        shutil.rmtree(dest)

        # remove from state
        state = self._read_state()
        state.pop(plugin_name, None)
        self._write_state(state)

        return True

    def list_installed(self) -> list[PluginManifest]:
        """List all installed plugins (both enabled and disabled).

        Returns manifests sorted by plugin name.
        """
        if not self._root.exists():
            return []

        manifests: list[PluginManifest] = []
        for entry in sorted(self._root.iterdir()):
            if not entry.is_dir():
                continue
            manifest_file = entry / "plugin.json"
            if manifest_file.exists():
                try:
                    manifest = PluginManifest.from_file(manifest_file)
                    manifests.append(manifest)
                except Exception as e:
                    logger.warning(
                        "Skipping corrupt plugin '%s': %s", entry.name, e
                    )

        return manifests

    def get(self, plugin_name: str) -> PluginManifest | None:
        """Get a specific plugin's manifest.

        Returns None if the plugin is not installed.
        """
        manifest_file = self._plugin_dir(plugin_name) / "plugin.json"
        if not manifest_file.exists():
            return None
        try:
            return PluginManifest.from_file(manifest_file)
        except Exception as e:
            logger.warning("Failed to read manifest for '%s': %s", plugin_name, e)
            return None

    def enable(self, plugin_name: str) -> None:
        """Enable a previously disabled plugin.

        No-op if the plugin is not installed or already enabled.
        """
        state = self._read_state()
        if plugin_name not in state:
            logger.warning(
                "Cannot enable '%s': plugin not installed.", plugin_name
            )
            return
        if state[plugin_name].get("enabled", True):
            logger.debug("Plugin '%s' is already enabled.", plugin_name)
            return
        state[plugin_name]["enabled"] = True
        self._write_state(state)
        logger.info("Enabled plugin '%s'", plugin_name)

    def disable(self, plugin_name: str) -> None:
        """Disable a plugin without uninstalling it.

        No-op if the plugin is not installed or already disabled.
        """
        state = self._read_state()
        if plugin_name not in state:
            logger.warning(
                "Cannot disable '%s': plugin not installed.", plugin_name
            )
            return
        if not state[plugin_name].get("enabled", True):
            logger.debug("Plugin '%s' is already disabled.", plugin_name)
            return
        state[plugin_name]["enabled"] = False
        self._write_state(state)
        logger.info("Disabled plugin '%s'", plugin_name)

    def upgrade(self, plugin_name: str, new_path: Path) -> PluginManifest:
        """Upgrade a plugin to a newer version.

        Creates a backup of the old version in
        ~/.config/wisp/plugins/.backups/<name>/<old-version>/
        before replacing with the new version.

        Args:
            plugin_name: Name of the installed plugin to upgrade.
            new_path: Path to the new plugin directory containing
                      plugin.json.

        Returns:
            The new PluginManifest.

        Raises:
            FileNotFoundError: If the old plugin or new manifest is not found.
            ValueError: If the new manifest is invalid.
        """
        old_dir = self._plugin_dir(plugin_name)
        if not old_dir.exists():
            raise FileNotFoundError(
                f"Plugin '{plugin_name}' is not installed."
            )

        new_manifest_file = new_path / "plugin.json"
        if not new_manifest_file.exists():
            raise FileNotFoundError(
                f"No plugin.json found in {new_path}"
            )

        new_manifest = PluginManifest.from_file(new_manifest_file)

        errors = new_manifest.validate()
        if errors:
            raise ValueError(
                f"Invalid manifest for upgrade:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        # backup old version
        old_manifest = self.get(plugin_name)
        if old_manifest is not None:
            backup_dir = self._root / ".backups" / plugin_name / old_manifest.version
            backup_dir.mkdir(parents=True, exist_ok=True)
            _copytree_plugin(old_dir, backup_dir)
            logger.info(
                "Backed up '%s' v%s to %s",
                plugin_name,
                old_manifest.version,
                backup_dir,
            )

        # replace with new version
        logger.info(
            "Upgrading '%s' from v%s to v%s",
            plugin_name,
            old_manifest.version if old_manifest else "?",
            new_manifest.version,
        )
        _copytree_plugin(new_path, old_dir)

        # update state
        state = self._read_state()
        state[plugin_name] = {
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "version": new_manifest.version,
            "enabled": state.get(plugin_name, {}).get("enabled", True),
        }
        self._write_state(state)

        return new_manifest

    def resolve_dependencies(self, manifest: PluginManifest) -> list[str]:
        """Check that a plugin's dependencies are satisfied.

        Returns a list of missing dependency names (empty if all satisfied).
        Only checks that dependent plugins are installed —
        version constraint checking (>=, ~=, etc.) is a TODO.
        """
        if not manifest.plugin_dependencies:
            return []

        missing: list[str] = []
        for dep_name in manifest.plugin_dependencies:
            dep = self.get(dep_name)
            if dep is None:
                missing.append(dep_name)
            else:
                # TODO: implement proper semver constraint checking.
                # For now we only verify presence, not version satisfaction.
                pass

        return missing

    def is_enabled(self, plugin_name: str) -> bool:
        """Check if a plugin is enabled.

        Returns False for uninstalled plugins.
        """
        state = self._read_state()
        entry = state.get(plugin_name)
        if entry is None:
            return False
        return entry.get("enabled", True)


# ── MarketplaceRegistry ────────────────────────────────────────────────────


class MarketplaceRegistry:
    """Remote marketplace for discovering and downloading community plugins.

    Connects to the Wisp plugin marketplace API.
    """

    def __init__(self, registry_url: str = "https://plugins.wisp.ai/v1"):
        self._base_url = registry_url.rstrip("/")
        self._session = None

    async def _get_session(self):
        """Lazy-initialize an aiohttp session."""
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession()
        return self._session

    async def search(self, query: str) -> list[dict]:
        """Search the marketplace for plugins matching a query string.

        Args:
            query: Free-text search query.

        Returns:
            A list of plugin summary dicts (name, version, description, author).
        """
        session = await self._get_session()
        async with session.get(
            f"{self._base_url}/search",
            params={"q": query},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("results", [])

    async def get_plugin_info(self, name: str) -> dict:
        """Get full plugin details from the marketplace.

        Args:
            name: Plugin name.

        Returns:
            Full plugin metadata dict including versions, dependencies, stats.

        Raises:
            aiohttp.ClientResponseError: If the plugin is not found or
                                          the request fails.
        """
        session = await self._get_session()
        async with session.get(f"{self._base_url}/plugins/{name}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def download(self, name: str, version: str | None = None) -> Path:
        """Download a plugin tarball/zip to a temporary directory.

        Args:
            name: Plugin name.
            version: Specific version to download (latest if not specified).

        Returns:
            Path to the extracted plugin directory ready for installation.

        Raises:
            ValueError: If the plugin is not found.
            aiohttp.ClientResponseError: On HTTP errors.
        """
        import tempfile
        import tarfile
        import zipfile

        params = {}
        if version is not None:
            params["version"] = version

        session = await self._get_session()
        async with session.get(
            f"{self._base_url}/plugins/{name}/download",
            params=params,
        ) as resp:
            resp.raise_for_status()
            content = await resp.read()

        # determine format from content-type header
        content_type = resp.headers.get("Content-Type", "")
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"wisp-plugin-{name}-"))
        tmp_file = tmp_dir / f"{name}.archive"

        tmp_file.write_bytes(content)

        extract_dir = tmp_dir / name
        extract_dir.mkdir(exist_ok=True)

        if "zip" in content_type or tmp_file.suffix == ".zip":
            with zipfile.ZipFile(tmp_file, "r") as zf:
                zf.extractall(extract_dir)
        else:
            with tarfile.open(tmp_file, "r:*") as tf:
                tf.extractall(extract_dir)

        # If the archive has a single top-level directory, use that
        entries = list(extract_dir.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            extract_dir = entries[0]

        logger.debug("Downloaded plugin '%s' to %s", name, extract_dir)
        return extract_dir

    async def list_popular(self, limit: int = 20) -> list[dict]:
        """Get the most popular/trending plugins from the marketplace.

        Args:
            limit: Maximum number of results (default 20).

        Returns:
            List of plugin summary dicts sorted by popularity.
        """
        session = await self._get_session()
        async with session.get(
            f"{self._base_url}/plugins/popular",
            params={"limit": str(limit)},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("results", [])

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None
