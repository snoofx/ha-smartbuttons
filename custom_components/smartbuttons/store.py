"""Panel layouts, stored in Home Assistant. The panels get them from here."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import ENTITY_KEYS, MAX_LAYOUT_BYTES, PASSWORD_LENGTH, STORAGE_KEY, STORAGE_VERSION


class LayoutError(ValueError):
    """A layout the panel cannot use."""


def layout_revision(layout: dict[str, Any]) -> str:
    """Short hash the panel stores with its layout, so unchanged layouts are not resent."""
    text = json.dumps(layout, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def clean_layout(layout: Any) -> dict[str, Any]:
    """Checks a layout from the editor or a panel. The hostname stays on the panel."""
    if not isinstance(layout, dict) or not isinstance(layout.get("buttons"), list):
        raise LayoutError("layout needs a buttons list")
    layout = {k: v for k, v in layout.items() if k not in ("name", "_managed")}
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


class LayoutStore:
    """Layouts of all panels, keyed by panel id (MAC)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._panels: dict[str, dict[str, Any]] = {}
        self.password: str = ""

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._panels = data.get("panels", {})
        self.password = data.get("password", "")
        if not self.password:
            # Panels are protected from the start, the admin sees it in the editor.
            self.password = secrets.token_urlsafe(PASSWORD_LENGTH)[:PASSWORD_LENGTH]
            self._save()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"panels": self._panels, "password": self.password}, 1)

    def set_password(self, password: str) -> None:
        self.password = password
        self._save()

    def get(self, panel_id: str) -> dict[str, Any] | None:
        """The stored layout, or None while HA has none for this panel."""
        entry = self._panels.get(panel_id)
        return entry["layout"] if entry else None

    def revision(self, panel_id: str) -> str | None:
        entry = self._panels.get(panel_id)
        return entry["rev"] if entry else None

    def set(self, panel_id: str, layout: dict[str, Any]) -> str:
        """Stores a checked layout and returns its revision."""
        rev = layout_revision(layout)
        self._panels[panel_id] = {"layout": layout, "rev": rev}
        self._save()
        return rev

    def remove(self, panel_id: str) -> None:
        if self._panels.pop(panel_id, None) is not None:
            self._save()
