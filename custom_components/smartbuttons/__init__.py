"""SmartButtons: LCD key panels managed by Home Assistant.

Panels are found by mDNS. Their pages are edited in the sidebar panel "SmartButtons" and stored
here; HA sends each panel its layout, the states it shows, and carries out its key presses.
"""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import async_register_commands, async_register_panel
from .const import CONF_LINK_KEY, DOMAIN
from .link import PanelLink, async_unpair
from .store import LayoutStore

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.UPDATE]

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
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: SmartButtonsConfigEntry) -> None:
    """Renamed in HA (entry title): the panel follows."""
    if entry.runtime_data.name != entry.title:
        entry.runtime_data.push_name()


async def async_unload_entry(hass: HomeAssistant, entry: SmartButtonsConfigEntry) -> bool:
    # The connection is a background task of the entry and ends with it.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: SmartButtonsConfigEntry) -> None:
    """A removed panel loses its stored layout and is unpaired, so it can be added again.
    The panel keeps its copy of the layout."""
    store: LayoutStore | None = hass.data.get(DOMAIN)
    if store is not None and entry.unique_id:
        store.remove_panel(entry.unique_id)
    try:
        await async_unpair(hass, entry.data[CONF_HOST], entry.data[CONF_LINK_KEY])
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as err:
        _LOGGER.warning(
            "%s could not be unpaired (%s). Hold SW6 and SW10 at power-on, or use its web UI",
            entry.title, err,
        )
