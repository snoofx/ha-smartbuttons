"""Identify: the panel shows where it hangs."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartButtonsConfigEntry
from .const import DOMAIN, SIGNAL_PANEL_UPDATED
from .link import PanelLink


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartButtonsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PanelIdentify(entry.runtime_data)])


class PanelIdentify(ButtonEntity):
    """Ping pong in the top row, name, IP and ID below. A key on the panel ends it."""

    _attr_has_entity_name = True
    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, link: PanelLink) -> None:
        self._link = link
        self._attr_unique_id = f"{link.panel_id}_identify"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, link.panel_id)})

    @property
    def available(self) -> bool:
        return self._link.connected

    async def async_press(self) -> None:
        self._link.locate()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PANEL_UPDATED.format(self._link.entry.entry_id),
                self.async_write_ha_state,
            )
        )
