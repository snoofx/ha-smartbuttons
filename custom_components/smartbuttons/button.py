"""Buttons of a panel: identify (locate mode) and wake up (screensaver off)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartButtonsConfigEntry
from .const import DOMAIN, SIGNAL_PANEL_UPDATED
from .link import PanelLink


@dataclass(frozen=True, kw_only=True)
class PanelButtonDescription(ButtonEntityDescription):
    feature: str                        # FEATURE_PROTOCOL: older firmware lacks the message
    press: Callable[[PanelLink], bool]


BUTTONS = (
    # Ping pong in the top row, name, IP and ID below. A key on the panel ends it.
    PanelButtonDescription(
        key="identify",
        device_class=ButtonDeviceClass.IDENTIFY,
        entity_category=EntityCategory.DIAGNOSTIC,
        feature="locate",
        press=PanelLink.locate,
    ),
    # For automations, e.g. motion in the room: screensaver off, on for its time from now.
    PanelButtonDescription(
        key="wake",
        translation_key="wake",
        icon="mdi:monitor-shimmer",
        feature="wake",
        press=PanelLink.wake_screen,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartButtonsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(PanelButton(entry.runtime_data, description) for description in BUTTONS)


class PanelButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: PanelButtonDescription

    def __init__(self, link: PanelLink, description: PanelButtonDescription) -> None:
        self._link = link
        self.entity_description = description
        self._attr_unique_id = f"{link.panel_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, link.panel_id)})

    @property
    def available(self) -> bool:
        """Offline or firmware without this message: unavailable, not a silent no-op."""
        return self._link.connected and self._link.supports(self.entity_description.feature)

    async def async_press(self) -> None:
        self.entity_description.press(self._link)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PANEL_UPDATED.format(self._link.entry.entry_id),
                self.async_write_ha_state,
            )
        )
