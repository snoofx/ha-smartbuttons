"""SmartButtons: LCD key panels managed by Home Assistant.

Panels are found by mDNS. Their pages are edited in the sidebar panel "SmartButtons" and stored
here; HA sends each panel its layout, the states it shows, and carries out its key presses.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import async_register_commands, async_register_panel
from .const import DOMAIN
from .link import PanelLink
from .store import LayoutStore

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR]

type SmartButtonsConfigEntry = ConfigEntry[PanelLink]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    store = LayoutStore(hass)
    await store.async_load()
    hass.data[DOMAIN] = store
    async_register_commands(hass)
    await async_register_panel(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmartButtonsConfigEntry) -> bool:
    link = PanelLink(hass, entry, hass.data[DOMAIN])
    entry.runtime_data = link
    link.update_device()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    link.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartButtonsConfigEntry) -> bool:
    # The connection is a background task of the entry and ends with it.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SmartButtonsConfigEntry) -> None:
    """A removed panel loses its stored layout. The panel itself keeps its copy."""
    store: LayoutStore | None = hass.data.get(DOMAIN)
    if store is not None and entry.unique_id:
        store.remove(entry.unique_id)
