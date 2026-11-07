"""Panel firmware: the integration package carries the matching firmware and installs it
through the web upload of the panel (POST /update), like the browser does."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import aiohttp

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmartButtonsConfigEntry
from .const import DOMAIN, SIGNAL_PANEL_UPDATED
from .link import PanelLink

# Written by tools/publish_ha.sh: firmware.json with version and md5, and the image.
FIRMWARE_DIR = Path(__file__).parent / "firmware"
WEB_PORT = 80
UPLOAD_TIMEOUT = 120
RESTART_TIMEOUT = 90
RELEASES_URL = "https://github.com/snoofx/ha-smartbuttons/releases"


def plain_version(version: str | None) -> str | None:
    """"v0.6.0+7a0643f" -> "0.6.0"."""
    if not version:
        return None
    return version.removeprefix("v").split("+", 1)[0]


def _read_manifest() -> dict[str, Any] | None:
    path = FIRMWARE_DIR / "firmware.json"
    if not path.exists():
        return None  # development checkout without published firmware
    return json.loads(path.read_text())


async def async_upload_firmware(hass: HomeAssistant, host: str, password: str | None, image: bytes) -> None:
    """Sends the image to the panel. It restarts into it; without 30 s of network it rolls back."""
    form = aiohttp.FormData()
    form.add_field("firmware", image, filename="firmware.bin", content_type="application/octet-stream")
    headers = {}
    if password:
        headers["Authorization"] = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(UPLOAD_TIMEOUT):
            async with session.post(f"http://{host}:{WEB_PORT}/update", data=form, headers=headers) as response:
                text = (await response.text()).strip()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise HomeAssistantError(f"Upload to the panel failed: {err}") from err
    if response.status == 401:
        raise HomeAssistantError("The panel refused the password. Locally managed panels: update in their web UI")
    if response.status != 200:
        raise HomeAssistantError(text or f"HTTP {response.status}")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartButtonsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    manifest = await hass.async_add_executor_job(_read_manifest)
    async_add_entities([PanelFirmware(entry.runtime_data, manifest)])


class PanelFirmware(UpdateEntity):
    """Offers the firmware that came with this version of the integration."""

    _attr_has_entity_name = True
    _attr_translation_key = "firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    _attr_should_poll = False
    _attr_title = "SmartButtons Firmware"
    _attr_release_url = RELEASES_URL

    def __init__(self, link: PanelLink, manifest: dict[str, Any] | None) -> None:
        self._link = link
        self._manifest = manifest
        self._attr_unique_id = f"{link.panel_id}_firmware"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, link.panel_id)})

    @property
    def available(self) -> bool:
        return self._link.version is not None

    @property
    def installed_version(self) -> str | None:
        return plain_version(self._link.version)

    @property
    def latest_version(self) -> str | None:
        if self._manifest is None:
            return self.installed_version
        return self._manifest["version"]

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        if self._manifest is None:
            raise HomeAssistantError("This installation of the integration carries no firmware")
        image = await self.hass.async_add_executor_job((FIRMWARE_DIR / self._manifest["file"]).read_bytes)
        if hashlib.md5(image).hexdigest() != self._manifest["md5"]:
            raise HomeAssistantError("Firmware file is damaged, reinstall the integration")

        link = self._link
        target = self._manifest["version"]
        hellos = link.hellos
        self._attr_in_progress = True
        self.async_write_ha_state()
        try:
            # Managed panels have the password from HA; local ones only work without password.
            password = link.store.password if link.mode == "ha" else None
            await async_upload_firmware(self.hass, link.host, password, image)
            # Done when the panel is back with the new version.
            async with asyncio.timeout(RESTART_TIMEOUT):
                while link.hellos == hellos or plain_version(link.version) != target:
                    await asyncio.sleep(1)
        except TimeoutError as err:
            raise HomeAssistantError(
                f"The panel did not come back with {target}, it still reports {link.version}"
            ) from err
        finally:
            self._attr_in_progress = False
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PANEL_UPDATED.format(self._link.entry.entry_id),
                self.async_write_ha_state,
            )
        )
