"""Configurations and panels, stored in Home Assistant.

A configuration is a named layout (pages, buttons, entities). Each panel uses one
configuration; several panels can share one, a change goes to all of them. The mounting
(rotate_180) belongs to the panel, not to the configuration.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import ENTITY_KEYS, MAX_LAYOUT_BYTES, PASSWORD_LENGTH, STORAGE_KEY, STORAGE_VERSION

EMPTY_LAYOUT: dict[str, Any] = {"buttons": [], "presets": {"entity_id": "", "scenes": []}}
# Set per panel, never part of a configuration.
PANEL_KEYS = ("name", "_managed", "rotate_180")


class LayoutError(ValueError):
    """A layout the panel cannot use."""


def layout_revision(layout: dict[str, Any]) -> str:
    """Short hash the panel stores with its layout, so unchanged layouts are not resent."""
    text = json.dumps(layout, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def clean_layout(layout: Any) -> dict[str, Any]:
    """Checks a layout from the editor or a panel and drops what belongs to the panel."""
    if not isinstance(layout, dict) or not isinstance(layout.get("buttons"), list):
        raise LayoutError("layout needs a buttons list")
    layout = {k: v for k, v in layout.items() if k not in PANEL_KEYS}
    size = len(json.dumps(layout, separators=(",", ":"), ensure_ascii=False).encode())
    if size > MAX_LAYOUT_BYTES:
        raise LayoutError(f"layout has {size} bytes, the panel takes {MAX_LAYOUT_BYTES}")
    return layout


def layout_entities(value: Any) -> set[str]:
    """All entities a layout names: the only ones its panel may control."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ENTITY_KEYS and isinstance(item, str) and "." in item:
                found.add(item)
            else:
                found |= layout_entities(item)
    elif isinstance(value, list):
        for item in value:
            found |= layout_entities(item)
    return found


class _VersionedStore(Store[dict[str, Any]]):
    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if old_major_version == 1:
            # 0.6: one layout per panel. Each becomes its own configuration.
            configs, panels = {}, {}
            for panel_id, entry in old_data.get("panels", {}).items():
                layout = dict(entry["layout"])
                rotate = bool(layout.pop("rotate_180", False))
                configs[panel_id] = {"name": f"Panel {panel_id[-6:]}", "layout": layout}
                panels[panel_id] = {"config": panel_id, "rotate_180": rotate}
            return {"password": old_data.get("password", ""), "configs": configs, "panels": panels}
        return old_data


class LayoutStore:
    """All configurations and panel assignments of this HA."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = _VersionedStore(hass, STORAGE_VERSION, STORAGE_KEY)
        self.configs: dict[str, dict[str, Any]] = {}
        self.panels: dict[str, dict[str, Any]] = {}
        self.password: str = ""

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.configs = data.get("configs", {})
        self.panels = data.get("panels", {})
        self.password = data.get("password", "")
        if not self.password:
            # Panels are protected from the start, the admin sees it in the editor.
            self.password = secrets.token_urlsafe(PASSWORD_LENGTH)[:PASSWORD_LENGTH]
            self._save()

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"password": self.password, "configs": self.configs, "panels": self.panels}, 1
        )

    def set_password(self, password: str) -> None:
        self.password = password
        self._save()

    # ── Configurations ──

    def create_config(self, name: str, layout: dict[str, Any] | None = None) -> str:
        config_id = secrets.token_hex(4)
        self.configs[config_id] = {"name": name, "layout": copy.deepcopy(layout or EMPTY_LAYOUT)}
        self._save()
        return config_id

    def update_config(self, config_id: str, *, name: str | None = None, layout: dict[str, Any] | None = None) -> None:
        config = self.configs[config_id]
        if name is not None:
            config["name"] = name
        if layout is not None:
            config["layout"] = layout
        self._save()

    def delete_config(self, config_id: str) -> None:
        if self.users(config_id):
            raise LayoutError("configuration is still used by a panel")
        del self.configs[config_id]
        self._save()

    def users(self, config_id: str) -> list[str]:
        return [pid for pid, panel in self.panels.items() if panel.get("config") == config_id]

    # ── Panels ──

    def has_panel(self, panel_id: str) -> bool:
        return panel_id in self.panels and self.panels[panel_id].get("config") in self.configs

    def adopt_panel(self, panel_id: str, name: str, layout: dict[str, Any], rotate_180: bool) -> None:
        """First contact: the panel's own layout becomes its configuration, nothing gets lost."""
        config_id = self.create_config(name, layout)
        self.panels[panel_id] = {"config": config_id, "rotate_180": rotate_180}
        self._save()

    def panel(self, panel_id: str) -> dict[str, Any]:
        return self.panels[panel_id]

    def set_panel(self, panel_id: str, *, config: str | None = None, rotate_180: bool | None = None) -> None:
        panel = self.panels[panel_id]
        if config is not None:
            if config not in self.configs:
                raise LayoutError("unknown configuration")
            panel["config"] = config
        if rotate_180 is not None:
            panel["rotate_180"] = rotate_180
        self._save()

    def remove_panel(self, panel_id: str) -> None:
        """The configuration stays: other panels may use it, or it serves as template."""
        if self.panels.pop(panel_id, None) is not None:
            self._save()

    def layout_for(self, panel_id: str) -> dict[str, Any]:
        """What the panel gets: its configuration plus its mounting."""
        panel = self.panels[panel_id]
        layout = copy.deepcopy(self.configs[panel["config"]]["layout"])
        if panel.get("rotate_180"):
            layout["rotate_180"] = True
        return layout

    def revision(self, panel_id: str) -> str:
        return layout_revision(self.layout_for(panel_id))
