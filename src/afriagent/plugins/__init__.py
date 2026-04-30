"""Plugin system — extensible tool and adapter registration.

Inspired by Hermes Agent's plugin architecture:
- Plugins are discovered from ~/.afriagent/plugins/ and the project's plugins/ dir
- Each plugin registers tools, hooks, or adapters through a context API
- Plugins are loaded at startup and can be enabled/disabled per config

Usage:
    from afriagent.plugins import PluginManager

    manager = PluginManager()
    manager.discover()  # Scan plugin directories
    manager.load_all()  # Load enabled plugins

Plugin structure (my_plugin/):
    __init__.py         # Must define register(manager) function
    plugin.yaml         # Metadata: name, version, description, tools
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from afriagent.config.logging import get_logger

log = get_logger(__name__)


@dataclass
class PluginMeta:
    """Plugin metadata."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    tools: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class PluginContext:
    """Context API exposed to plugins for registration."""

    _tools: dict[str, Any] = field(default_factory=dict)
    _hooks: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)
    _adapters: dict[str, Any] = field(default_factory=dict)

    def register_tool(self, name: str, handler: Any, metadata: dict[str, Any] | None = None) -> None:
        """Register a tool with the agent."""
        self._tools[name] = {"handler": handler, "metadata": metadata or {}}
        log.info("Plugin registered tool", tool=name)

    def register_hook(self, event: str, handler: Callable[..., Any]) -> None:
        """Register a hook for a lifecycle event."""
        self._hooks.setdefault(event, []).append(handler)
        log.info("Plugin registered hook", event=event)

    def register_adapter(self, channel: str, adapter: Any) -> None:
        """Register a channel adapter."""
        self._adapters[channel] = adapter
        log.info("Plugin registered adapter", channel=channel)

    def get_tools(self) -> dict[str, Any]:
        return dict(self._tools)

    def get_hooks(self) -> dict[str, list[Callable[..., Any]]]:
        return dict(self._hooks)

    def get_adapters(self) -> dict[str, Any]:
        return dict(self._adapters)


class PluginManager:
    """Discovers, loads, and manages plugins.

    Plugin directories (searched in order):
    1. ~/.afriagent/plugins/ (user plugins)
    2. Project-level plugins/ directory
    3. pip entry points (afriagent.plugins)
    """

    def __init__(self) -> None:
        self._plugins: dict[str, PluginMeta] = {}
        self._context = PluginContext()
        self._loaded_modules: dict[str, Any] = {}

    @property
    def context(self) -> PluginContext:
        return self._context

    def discover(self) -> list[PluginMeta]:
        """Scan plugin directories and return discovered plugin metadata."""
        discovered: list[PluginMeta] = []

        # User plugins
        user_dir = Path.home() / ".afriagent" / "plugins"
        if user_dir.is_dir():
            discovered.extend(self._scan_directory(user_dir))

        # Project plugins
        project_dir = Path.cwd() / "plugins"
        if project_dir.is_dir():
            discovered.extend(self._scan_directory(project_dir))

        log.info("Plugin discovery complete", count=len(discovered))
        return discovered

    def _scan_directory(self, directory: Path) -> list[PluginMeta]:
        """Scan a directory for plugins."""
        plugins: list[PluginMeta] = []

        for entry in directory.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith("_"):
                continue

            init_file = entry / "__init__.py"
            if not init_file.exists():
                continue

            # Try to load plugin.yaml for metadata
            meta = self._load_meta(entry)
            if meta:
                plugins.append(meta)
                self._plugins[meta.name] = meta

        return plugins

    def _load_meta(self, plugin_dir: Path) -> PluginMeta | None:
        """Load plugin metadata from plugin.yaml or infer from __init__.py."""
        yaml_path = plugin_dir / "plugin.yaml"

        if yaml_path.exists():
            try:
                import yaml
                with open(yaml_path) as f:
                    data = yaml.safe_load(f)
                return PluginMeta(
                    name=data.get("name", plugin_dir.name),
                    version=data.get("version", "0.1.0"),
                    description=data.get("description", ""),
                    author=data.get("author", ""),
                    tools=data.get("tools", []),
                    hooks=data.get("hooks", []),
                    enabled=data.get("enabled", True),
                )
            except Exception as e:
                log.warning("Failed to load plugin metadata", path=str(yaml_path), error=str(e))

        # Fallback: infer from directory name
        return PluginMeta(name=plugin_dir.name)

    def load_all(self) -> None:
        """Load all discovered and enabled plugins."""
        for name, meta in self._plugins.items():
            if not meta.enabled:
                log.debug("Skipping disabled plugin", name=name)
                continue
            try:
                self._load_plugin(name)
            except Exception as e:
                log.error("Failed to load plugin", name=name, error=str(e))

    def _load_plugin(self, name: str) -> None:
        """Load a single plugin by name."""
        if name in self._loaded_modules:
            return

        # Try importing from user plugins dir
        user_dir = Path.home() / ".afriagent" / "plugins"
        if user_dir.is_dir():
            plugin_path = user_dir / name
            if plugin_path.is_dir():
                sys.path.insert(0, str(user_dir))
                try:
                    module = importlib.import_module(name)
                    if hasattr(module, "register"):
                        module.register(self._context)
                    self._loaded_modules[name] = module
                    log.info("Plugin loaded", name=name, source="user")
                    return
                finally:
                    sys.path.pop(0)

        # Try project plugins
        project_dir = Path.cwd() / "plugins"
        if project_dir.is_dir():
            plugin_path = project_dir / name
            if plugin_path.is_dir():
                sys.path.insert(0, str(project_dir))
                try:
                    module = importlib.import_module(name)
                    if hasattr(module, "register"):
                        module.register(self._context)
                    self._loaded_modules[name] = module
                    log.info("Plugin loaded", name=name, source="project")
                    return
                finally:
                    sys.path.pop(0)

        log.warning("Plugin not found", name=name)

    def get_plugin(self, name: str) -> Any | None:
        """Get a loaded plugin module by name."""
        return self._loaded_modules.get(name)

    def list_plugins(self) -> list[dict[str, Any]]:
        """List all discovered plugins with their status."""
        return [
            {
                "name": meta.name,
                "version": meta.version,
                "description": meta.description,
                "enabled": meta.enabled,
                "loaded": meta.name in self._loaded_modules,
                "tools": meta.tools,
            }
            for meta in self._plugins.values()
        ]
