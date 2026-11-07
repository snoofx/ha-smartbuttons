"""Whether HA is connected to the panel."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
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
    async_add_entities([PanelConnected(entry.runtime_data)])


class PanelConnected(BinarySensorEntity):
    """On while the panel gets states and sends key presses through HA."""

    _attr_has_entity_name = True
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, link: PanelLink) -> None:
        self._link = link
        self._attr_unique_id = f"{link.panel_id}_connected"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, link.panel_id)})

    @property
    def is_on(self) -> bool:
        return self._link.connected

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"management": self._link.mode, "host": self._link.host}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PANEL_UPDATED.format(self._link.entry.entry_id),
                self.async_write_ha_state,
            )
        )
